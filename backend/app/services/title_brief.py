"""Title-centric brief generator (C3) — the title analogue of the pair brief
in ``insight_engine``.

Reuses the shared foundation:
- ``BRIEF_VOICE`` (C1) — same Cutter-Deutsch tone (#222/#223), prepended to a
  title-specific task/schema (TITLE_BRIEF_TASK names ONLY title field names).
- ``_run_brief_llm`` (C2) — the shared tool-use call + retry + truncation-guard
  + cost-accounting loop.
- ``aggregate_title`` (PR #225) — the title data block.

Read-only on the DB (aggregation only); the LLM call produces a
``TitleInsightReport``. Persistence + endpoint land in C4/C5. The pair path is
untouched — this is a new, additive module.

Citation v1 = SOFT mode: ``_validate_title_citations`` only logs whether the
model's ``cited_post_ids`` are covered by the title's top-post URLs; it never
forces a retry.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Union
from uuid import UUID

from sqlmodel import Session

from app.schemas.insights import TitleInsightReport, TitleLLMReport
from app.models.entities import TitleInsightReport as TitleInsightReportRow
from app.services.anthropic_client import AnthropicAuthError, is_anthropic_configured
from app.services.insight_engine import (
    BRIEF_VOICE,
    OPUS_MODEL_ALIAS,
    _run_brief_llm,
)
from app.services.title_aggregation import TitleAggregation, _resolve_title, aggregate_title

logger = logging.getLogger(__name__)

_TITLE_TOOL_NAME = "submit_title_brief"
_TITLE_TOOL_DESCRIPTION = (
    "Submit the structured title brief. Call this tool exactly once. Pass the "
    "report fields DIRECTLY as the top-level tool arguments (headline, tldr, "
    "plattform_vergleich, data_caveats, plus the optional sections) — do NOT "
    "nest them under any wrapper key. Do not return any prose outside the tool call."
)


def _inline_tool_schema(schema: dict) -> dict:
    """Inline ``$defs`` ``$ref``s and collapse ``anyOf:[T, null]`` -> ``T`` so
    the forced tool-use input_schema presents nested objects DIRECTLY.

    Why: Mortal-Kombat's first title brief came back with ``fuer_cutter`` as a
    STRING carrying ``<parameter name="schnitt_pace">…`` XML instead of a JSON
    object — Claude's known tool-use leak when a nested optional object is
    expressed as ``anyOf:[{"$ref": …}, {"type":"null"}]`` (the Pydantic default
    for ``Optional[FuerCutter]``). Claude fills *inline* nested objects
    reliably but improvises the XML format on the ``$ref`` indirection. The
    content was good; only the schema shape sent to the API was fragile.

    Optionality is preserved via the top-level ``required`` list (Pydantic
    lists only the non-default fields there), so dropping the ``null`` union
    does NOT make a field mandatory. The pair brief carries the same latent
    risk but is left untouched (separate path); only the title tool schema is
    hardened here.
    """
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"].split("/")[-1]
                return resolve(dict(defs.get(ref, {})))
            if "anyOf" in node:
                variants = [v for v in node["anyOf"] if v.get("type") != "null"]
                if len(variants) == 1:
                    merged = resolve(variants[0])
                    for carry in ("default", "description", "title"):
                        if carry in node and carry not in merged:
                            merged[carry] = node[carry]
                    return merged
                return {**node, "anyOf": [resolve(v) for v in node["anyOf"]]}
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    out = resolve(dict(schema))
    out.pop("$defs", None)
    return out


_TITLE_TOOL_INPUT_SCHEMA: dict[str, Any] = _inline_tool_schema(TitleLLMReport.model_json_schema())


# Title-specific task + output schema, appended to the shared BRIEF_VOICE.
# Names ONLY title field names — the pair brief's cross_market_insight /
# aktuell_im_fokus do not exist here.
TITLE_BRIEF_TASK = """

TITEL-BRIEF — AUFGABE:
Du beschreibst EINEN Titel über alle Channels, Plattformen und Märkte hinweg, in denen er diese Woche vorkam. Achse ist der Titel, nicht ein Studio-Pair. Erzähl die Geschichte des Titels: wo trägt er (welche Plattform, welcher Markt), wo läuft er ins Leere, was lernen wir für eigene Cuts.

WICHTIG: Die Voice-, Glossar- und Anti-Pattern-Regeln oben gelten unverändert. Aber dieser Brief hat KEINE Pair-Felder — die ``cross_market_insight``- und ``aktuell_im_fokus``-Erwähnungen oben gehören dem Pair-Brief. Dein Plattform- und Markt-Vergleich ist titel-intern (derselbe Titel auf TikTok vs Instagram vs YouTube, bzw. in DE vs US vs UK).

OUTPUT — Gib das Ergebnis ausschließlich über das Tool ``submit_title_brief`` zurück, Felder DIREKT auf oberster Ebene (kein Wrapper-Key). Schema:

{
  "headline": "1 Satz: der Titel + die Kern-Geschichte der Woche. Aktiv, konkret, für GF/CD lesbar. Filmtitel darf genannt werden.",
  "tldr": "Max 3 Sätze, Erzähl-Bogen: Hauptaussage zuerst, Beleg dahinter. Eine Zahl mit Einordnung, kein nacktes Zahlenpaar.",
  "plattform_vergleich": "PFLICHT. Was trägt wo für DIESEN Titel — TikTok vs Instagram vs YouTube, mit konkreten Zahlen (Reaktionen, Aufrufe). Wo zieht er, wo bleibt er ohne Bindung. Beschreibend, kein erfundenes Fachwort.",
  "markt_vergleich": "DE vs US vs UK für diesen Titel, mit Zahlen — oder null, wenn der Titel nur in einem Markt vorkam.",
  "verlauf": "Kampagnen-Bogen über die Wochen (Anlauf, Spitze, Abflachen), abgeleitet aus den Wochen-Buckets — oder null bei zu wenig Wochen-Daten.",
  "top_post_kommentar": "Was an den stärksten Posts auffällt (Format, Länge, was den Cut trägt) — oder null.",
  "fuer_cutter": {
    "schnitt_pace": "Rhythmus-Beobachtung aus den Top-Posts dieses Titels, in Cutter-Sprache — oder null.",
    "hook_strategie": "welche Anfangs-Form bei diesem Titel wirkt — oder null.",
    "empfohlene_laengen": "z.B. '20-25s primär' — oder null.",
    "was_diese_woche": "3-4 Sätze Fließtext: was der Titel diese Woche zeigt, was man für eigene Cuts mitnimmt — oder null."
  },
  "data_caveats": ["Lautstärke-/Lücken-Hinweise: wie viele Posts, welche Plattformen/Märkte fehlen, was den Brief relativiert."],
  "cited_post_ids": ["post_url-Strings aus den Top-Posts unten, auf denen deine Zahlen beruhen. Niemals erfinden — nur URLs, die im Datenblock stehen."]
}

Antworte ausschließlich über das Tool, kein Fließtext außerhalb.
"""

TITLE_SYSTEM_PROMPT = BRIEF_VOICE + TITLE_BRIEF_TASK


def _fmt_dt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)[:10]


def _build_title_user_prompt(agg: TitleAggregation) -> str:
    """Markdown data block + JSON appendix for the title brief. Mirrors
    ``_build_user_prompt`` structurally (framing, per-platform/market blocks,
    top posts with URLs, weekly timeline, JSON appendix)."""
    lines: list[str] = []
    lines.append(
        f"Generiere den Titel-Brief für *{agg.title_original}*"
        + (f" ({agg.content_type})" if agg.content_type else "")
        + f", Datenfenster {agg.window_days} Tage "
        f"({_fmt_dt(agg.window_start)} bis {_fmt_dt(agg.window_end)})."
    )
    lines.append(
        "Halte dich an Voice, Glossar und Anti-Pattern aus dem System-Prompt. "
        "Plattform-/Markt-Vergleich ist titel-intern. Nenne Zahlen konkret, "
        "erfinde keine Fachwörter.\n"
    )

    # Stammdaten
    lines.append("## Stammdaten")
    lines.append(f"- Titel: {agg.title_original}" + (f" / lokal: {agg.title_local}" if agg.title_local else ""))
    if agg.franchise:
        lines.append(f"- Franchise: {agg.franchise}")
    if agg.content_type:
        lines.append(f"- Typ: {agg.content_type}")
    if agg.release_date_de or agg.release_date_us:
        lines.append(f"- Release DE: {_fmt_dt(agg.release_date_de)} | US: {_fmt_dt(agg.release_date_us)}")
    lines.append(
        f"- Posts im Fenster: {agg.total_posts} (gesamt je gesehen: {agg.total_posts_all_time}) | "
        f"Σ Reaktionen {agg.total_engagement} | Σ Aufrufe {agg.total_views} | "
        f"⌀ Aktivierung {agg.activation_rate_avg}"
    )
    lines.append(f"- Erster/letzter Post (gesamt): {_fmt_dt(agg.first_post_at)} – {_fmt_dt(agg.last_post_at)}")
    lines.append("")

    # Per platform
    lines.append("## Plattformen")
    if agg.platforms:
        for p in agg.platforms:
            tp = p.top_post
            tp_str = f" | Top: {tp.post_url} ({tp.engagement_sum} Reakt.)" if tp else ""
            lines.append(
                f"### {p.platform}: {p.post_count} Posts | Σ Reakt. {p.engagement_sum} "
                f"(⌀ {p.engagement_avg}) | Σ Aufrufe {p.views_sum} | ⌀ Aktivierung {p.activation_rate_avg}{tp_str}"
            )
    else:
        lines.append("(keine Plattform-Daten im Fenster)")
    lines.append("")

    # Per market
    lines.append("## Märkte")
    if agg.markets:
        for m in agg.markets:
            lines.append(
                f"### {m.market}: {m.post_count} Posts | Σ Reakt. {m.engagement_sum} "
                f"(⌀ {m.engagement_avg}) | Σ Aufrufe {m.views_sum} | ⌀ Aktivierung {m.activation_rate_avg}"
            )
    else:
        lines.append("(keine Markt-Daten im Fenster)")
    lines.append("")

    # Channels
    if agg.channels:
        lines.append("## Channels (wo der Titel lief)")
        for c in agg.channels[:15]:
            pairs = f" [Pairs: {', '.join(c.pair_keys)}]" if c.pair_keys else ""
            lines.append(
                f"- @{c.channel_handle} ({c.platform}/{c.market}): {c.post_count} Posts, "
                f"Σ Reakt. {c.engagement_sum}{pairs}"
            )
        lines.append("")

    # Top posts
    if agg.top_posts:
        lines.append("## Top-Posts (gesamt)")
        for tp in agg.top_posts:
            lines.append(
                f"- {tp.post_url} | {tp.platform}/{tp.market} @{tp.channel_handle} | "
                f"{tp.engagement_sum} Reakt., {tp.views or 0} Aufrufe, Aktivierung {tp.activation_rate}"
            )
        lines.append("")

    # Weekly timeline + verlauf hint
    if len(agg.weekly) >= 2:
        lines.append("## Wochen-Verlauf")
        for w in agg.weekly:
            lines.append(f"- KW {w.iso_week}/{w.iso_year}: {w.post_count} Posts, Σ Reakt. {w.engagement_sum}")
        lines.append("→ Fülle ``verlauf`` mit dem Kampagnen-Bogen über diese Wochen.")
    else:
        lines.append("## Wochen-Verlauf")
        lines.append("→ Zu wenig Wochen-Daten (<2 Buckets): setze ``verlauf`` auf null.")
    lines.append("")

    # JSON appendix
    lines.append("## JSON-Datenanhang (vollständige Titel-Aggregation)")
    lines.append(json.dumps(dataclasses.asdict(agg), ensure_ascii=False, default=str))

    return "\n".join(lines)


def _title_citation_allow_set(agg: TitleAggregation) -> set[str]:
    return {tp.post_url for tp in agg.top_posts if tp.post_url}


def _validate_title_citations(llm_output: TitleLLMReport, agg: TitleAggregation) -> bool:
    """SOFT citation check (v1): log whether the model's cited_post_ids are
    covered by the title's top-post URLs. Always returns the coverage bool but
    the caller runs in soft mode (no strict retry)."""
    cited = list(getattr(llm_output, "cited_post_ids", []) or [])
    if not cited:
        return True
    allow = _title_citation_allow_set(agg)
    unknown = [c for c in cited if c not in allow]
    ok = not unknown
    if not ok:
        logger.info(
            "title-brief-citation-soft",
            extra={
                "title_id": str(agg.title_id),
                "cited": len(cited),
                "unknown": len(unknown),
                "unknown_sample": unknown[:3],
            },
        )
    return ok


def generate_title_brief(
    session: Session,
    title_ref: Union[str, UUID],
    *,
    window_days: int = 30,
    now: Optional[datetime] = None,
    model: str = OPUS_MODEL_ALIAS,
    max_tokens: int = 20000,
    dry_run: bool = False,
) -> Optional[TitleInsightReport]:
    """Generate a title brief via the shared LLM kernel. Returns ``None`` if
    no title matches ``title_ref`` (caller -> 404). ``dry_run`` returns the
    aggregation only (no LLM call, no cost). No persistence (C4)."""
    agg = aggregate_title(session, title_ref, window_days=window_days, now=now)
    if agg is None:
        return None

    ref_now = now or datetime.now(timezone.utc)
    iso = ref_now.isocalendar()
    generated_at = datetime.now(timezone.utc)
    # JSON-safe (datetime/UUID -> str) so the dict round-trips through the
    # JSON persistence column and Pydantic without custom encoders.
    agg_dict = json.loads(json.dumps(dataclasses.asdict(agg), default=str))

    if dry_run:
        return TitleInsightReport(
            title_id=str(agg.title_id),
            title_original=agg.title_original,
            iso_week=iso.week,
            iso_year=iso.year,
            window_days=window_days,
            generated_at=generated_at,
            model=model,
            dry_run=True,
            llm_output=None,
            aggregation=agg_dict,
            cost_usd_estimate=0.0,
        )

    if not is_anthropic_configured():
        raise AnthropicAuthError(
            "ANTHROPIC_API_KEY ist nicht gesetzt — Titel-Brief kann nicht generieren. "
            "Setze den Schlüssel in Railway oder ruf den Endpoint mit ?dry_run=true auf."
        )

    user_prompt = _build_title_user_prompt(agg)
    result = _run_brief_llm(
        system_prompt=TITLE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        tool_name=_TITLE_TOOL_NAME,
        tool_description=_TITLE_TOOL_DESCRIPTION,
        input_schema=_TITLE_TOOL_INPUT_SCHEMA,
        validate=TitleLLMReport.model_validate,
        model=model,
        max_tokens=max_tokens,
        log_subject=str(agg.title_id),
        call_extra={
            "title_id": str(agg.title_id),
            "title": agg.title_original,
            "window_days": window_days,
            "model": model,
            "prompt_chars": len(user_prompt),
        },
        record_meta={
            "title_id": str(agg.title_id),
            "iso_week": iso.week,
            "iso_year": iso.year,
        },
        operation="title_brief",
        citation_validate=lambda out: _validate_title_citations(out, agg),
        strict_citations=False,  # v1 soft
    )

    return TitleInsightReport(
        title_id=str(agg.title_id),
        title_original=agg.title_original,
        iso_week=iso.week,
        iso_year=iso.year,
        window_days=window_days,
        generated_at=generated_at,
        model=model,
        dry_run=False,
        llm_output=result.llm_output,
        aggregation=agg_dict,
        cost_usd_estimate=result.cost,
        input_tokens=result.input_tokens or None,
        output_tokens=result.output_tokens or None,
        raw_llm_text=result.raw_text,
    )


def _persist_title_report(session: Session, report: TitleInsightReport) -> None:
    """Upsert one ``title_insight_report`` row keyed by (title_id, iso_year,
    iso_week). Last-Write-Wins via delete-then-insert (SQLite has no portable
    composite UPSERT) — same approach as ``_persist_report`` for the pair."""
    if report.llm_output is None:
        logger.warning(
            "title-insight-report-persist-skipped: title=%s week=%d/%d (no llm_output)",
            report.title_id, report.iso_year, report.iso_week,
        )
        return

    cost_cents: Optional[int] = (
        int(round(report.cost_usd_estimate * 100)) if report.cost_usd_estimate else None
    )
    title_uuid = UUID(report.title_id)
    existing = session.get(
        TitleInsightReportRow, (title_uuid, report.iso_year, report.iso_week)
    )
    if existing is not None:
        session.delete(existing)
        session.flush()

    row = TitleInsightReportRow(
        title_id=title_uuid,
        iso_year=report.iso_year,
        iso_week=report.iso_week,
        window_days=report.window_days,
        aggregation=report.aggregation,
        llm_output=report.llm_output.model_dump(mode="json"),
        generated_at=report.generated_at,
        model=report.model,
        cost_usd_cents=cost_cents,
        input_tokens=report.input_tokens,
        output_tokens=report.output_tokens,
    )
    session.add(row)
    session.commit()


def _hydrate_title_from_persisted(
    row: TitleInsightReportRow, *, window_days: int
) -> TitleInsightReport:
    """Rebuild a Pydantic ``TitleInsightReport`` from a stored row (cache-hit
    path). ``aggregation``/``llm_output`` round-trip through the JSON blobs."""
    llm_output = TitleLLMReport.model_validate(row.llm_output) if row.llm_output else None
    cost_usd_estimate: Optional[float] = (
        round(row.cost_usd_cents / 100.0, 4) if row.cost_usd_cents is not None else None
    )
    title_original = (row.aggregation or {}).get("title_original", "")
    return TitleInsightReport(
        title_id=str(row.title_id),
        title_original=title_original,
        iso_week=row.iso_week,
        iso_year=row.iso_year,
        window_days=row.window_days,
        generated_at=row.generated_at,
        model=row.model,
        dry_run=False,
        llm_output=llm_output,
        aggregation=row.aggregation,
        cost_usd_estimate=cost_usd_estimate,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
    )


def generate_and_persist_title_brief(
    session: Session,
    title_ref: Union[str, UUID],
    *,
    window_days: int = 30,
    force: bool = False,
    replace: bool = False,
    model: str = OPUS_MODEL_ALIAS,
    max_tokens: int = 20000,
    now: Optional[datetime] = None,
) -> Optional[TitleInsightReport]:
    """Cache-aware title-brief generation + persistence (C4).

    Returns ``None`` if no title matches ``title_ref`` (caller -> 404).
    Cache pre-check on (title_id, iso_year, iso_week): an existing row is
    returned unless ``replace=True``. ``replace=True`` regenerates and
    overwrites (Last-Write-Wins). A generation that fails (parse/schema/
    truncation -> llm_output None) is NOT persisted; the report is returned
    so the caller can surface the failure.

    v1 note: no Postgres advisory lock (unlike the pair pipeline). This is a
    manual single-title endpoint with no cron concurrency; parallel
    regenerate clicks could double-spend, which is acceptable for the
    operator-triggered path.
    """
    title = _resolve_title(session, title_ref)
    if title is None:
        return None

    ref_now = now or datetime.now(timezone.utc)
    iso = ref_now.isocalendar()

    existing = session.get(TitleInsightReportRow, (title.id, iso.year, iso.week))
    if existing is not None and not replace:
        return _hydrate_title_from_persisted(existing, window_days=window_days)

    report = generate_title_brief(
        session, title.id, window_days=window_days, now=now,
        model=model, max_tokens=max_tokens,
    )
    if report is None:
        return None
    if report.llm_output is None:
        # Generation failed (parse/schema/truncation) — skip persist, surface.
        return report

    _persist_title_report(session, report)
    return report
