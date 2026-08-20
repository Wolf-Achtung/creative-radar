"""Pattern-Briefing — Text-Bausteine aus dem Muster-Bericht (Stufe 1,
Schritt 3, 20.08.2026).

Macht aus den belastbaren Zellen der Muster-Aggregation
(``trailer_patterns.compute_trailer_patterns``) sofort verwendbare
Text-Bausteine: Hooks, Captions und Hashtags auf Deutsch und Englisch,
je Muster begruendet mit den gemessenen Zahlen und belegt mit den
Beispiel-Posts, aus denen sie abgeleitet sind.

Arbeitsteilung (Cutter-Weekly-Prinzip, wortgleich uebernommen): **die
Code-Pruefung entscheidet, was ein Muster ist — das LLM formuliert
ausschliesslich, was diese Pruefung freigegeben hat.** Konkret:

1. ``build_pattern_evidence`` waehlt deterministisch aus: nur Zellen,
   deren ``breakout_verdict`` nicht ``insufficient`` ist (also >= 5
   Posts aus >= 3 Kanaelen), sortiert nach ``breakout_z``, gedeckelt
   auf ``MAX_PATTERNS_PER_BRIEFING``. Je Zelle die
   ``EXAMPLES_PER_PATTERN`` staerksten Posts (hoechster Lift) mit
   Original-Caption und URL — das Rohmaterial, aus dem das LLM die
   Hook-Mechanik lernt.
2. Das LLM bekommt NUR diese Auswahl. Jeder Baustein muss in
   ``cited_post_ids`` die Beispiel-Posts nennen, aus denen er
   abgeleitet ist.
3. Die Citation-Pruefung laeuft im Code: ein Baustein, dessen
   ``cited_post_ids`` nicht vollstaendig im Allow-Set der mitgegebenen
   Beispiel-URLs liegen, wird verworfen (``citation_dropped``-Zaehler,
   eigene Spalte in der Row). ID-Raum sind die Post-URLs — dieselbe
   Konvention wie beim Wochen-Brief; die Citation-Auswertung vom
   20.08.2026 (0,19 % Falsch-Zitat-Rate) hat gezeigt, dass ein
   zweiter ID-Raum die einzige nennenswerte Fehlerquelle war.

Modus ``"genre"``: die Muster sind die Genre-Zellen (Wolf-Entscheidung
20.08.2026 "Beides, Genre zuerst" — der Titel-Modus folgt als eigener
PR und bekommt einen eigenen ``mode``-Wert, die Tabelle traegt das im
PK schon).

Leerlauf ist der erwartete Anfangszustand: ``title.genres`` fuellt sich
erst mit dem naechsten Title-Sync. Ohne belastbares Genre-Muster gibt
es KEINEN LLM-Call (``model="none"``); persistiert wird trotzdem, mit
einem deterministischen ``data_caveat`` — Konvention wie beim
Cutter-Weekly-Leerlauf.

Doppelter ``build_lift_context``-Lauf, bewusst: ``compute_trailer_
patterns`` kapselt den Kontext und gibt nur Zellen-Aggregate zurueck;
fuer die Beispiel-Posts brauchen wir die Post-Ebene (Lift je Post).
Beide Laeufe sind deterministisch und bekommen identische Parameter —
dieselben Zahlen, zweimal gerechnet statt einmal kopiert. Die
Alternative (Report um Post-Listen erweitern) wuerde den Admin- und
den Public-Endpoint aufblaehen, die beide nur Aggregate brauchen.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from app.models.entities import Channel, PatternBriefing as PatternBriefingRow
from app.schemas.insights import (
    PatternBriefingEvidence,
    PatternBriefingLLMReport,
    PatternBriefingReport,
    PatternEvidenceCell,
    PatternExamplePost,
    PatternTextBaustein,
)
from app.services.anthropic_client import (
    AnthropicAuthError,
    _unwrap_single_key,
    call_with_json_retry,
    is_anthropic_configured,
)
from app.services.cost_log import record_anthropic_call
from app.services.insight_engine import (
    OPUS_MODEL_ALIAS,
    _estimate_cost_usd,
)
from app.services.trailer_patterns import (
    DEFAULT_WINDOW_DAYS,
    _genre_by_post,
    build_lift_context,
    compute_trailer_patterns,
)

logger = logging.getLogger(__name__)


BRIEFING_MODE_GENRE = "genre"

# Kosten- und Fokus-Deckel: mehr als 6 Muster verwaessern den Brief und
# verlaengern den Prompt linear. Die Auswahl ist breakout_z-sortiert —
# gekappt wird also das schwaechste Signal, nie das staerkste.
MAX_PATTERNS_PER_BRIEFING = 6

# 3-5 Beispiel-Posts je Muster (Design-Review mit Wolf, 20.08.2026):
# genug Material fuer die Hook-Mechanik, wenig genug, dass jede Caption
# wirklich gelesen wird. Obergrenze, nicht Pflicht — duenne Zellen
# liefern, was sie haben.
EXAMPLES_PER_PATTERN = 5

# Captions im Prompt kappen: das LLM soll die Eroeffnungs-Mechanik
# lernen, nicht Hashtag-Waende am Caption-Ende zitieren.
MAX_CAPTION_CHARS = 240

PATTERN_BRIEFING_MAX_TOKENS = 8000


# Der System-Prompt ist der Entwurf aus dem Design-Review mit Wolf
# (20.08.2026), unveraendert — vorgelegt, nicht beanstandet. Bewusst
# OHNE BRIEF_VOICE: das hier ist kein Bericht, sondern Arbeitsmaterial
# (Hooks/Captions zum direkten Verwenden); die Berichts-Verbotslisten
# wuerden genau die Sprache verbieten, die Social-Captions brauchen.
PATTERN_BRIEFING_SYSTEM_PROMPT = """Du bist Creative-Stratege für Kino-Marketing auf Social Media (DE/US/UK). Du bekommst gemessene Reichweiten-Muster aus dem eigenen Kanalbestand und die Original-Captions der stärksten Posts je Muster.

Deine Aufgabe: je Muster konkrete, sofort verwendbare Text-Bausteine — Hooks, Captions, Hashtags — auf Deutsch und Englisch.

Regeln:
1. Jede Aussage über Wirkung stützt sich auf die mitgelieferten Zahlen. Erfinde keine Statistik und runde keine auf.
2. Jede Empfehlung nennt in cited_post_ids die Beispiel-Posts (ihre URLs, wortwörtlich), aus denen sie abgeleitet ist. Ohne Beleg keine Empfehlung.
3. Schreibe Hooks, wie sie in den belegten Captions tatsächlich klingen — übernimm die Mechanik (Frage, Zitat, Countdown, Kontrast), nie den Wortlaut. Keine Spoiler, keine erfundenen Filmtitel, keine Superlative ohne Beleg.
4. Deutsch ist nicht übersetztes Englisch: DE-Hooks folgen deutscher Social-Sprache, EN-Hooks englischer.
5. Wenn ein Muster zu dünn belegt ist, sag das in data_caveats statt zu liefern."""


def build_pattern_evidence(
    session: Session,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: Optional[datetime] = None,
    max_patterns: int = MAX_PATTERNS_PER_BRIEFING,
    examples_per_pattern: int = EXAMPLES_PER_PATTERN,
) -> PatternBriefingEvidence:
    """Deterministische Muster-Auswahl + Beleg-Posts — der Teil, der
    entscheidet, worueber das LLM ueberhaupt sprechen darf.

    Freigegeben sind Genre-Zellen mit ``breakout_verdict !=
    "insufficient"`` — also solche, die Mindest-Stichprobe (5 Posts)
    und Mindest-Kanalzahl (3) erfuellen. Auch ``neutral``-Zellen sind
    dabei: ein Genre, das durchschnittlich laeuft, traegt trotzdem
    verwertbare Hook-Mechanik in seinen staerksten Posts; die Zahlen
    im Baustein sagen ehrlich, dass es kein Ausreisser-Muster ist.
    Sortiert nach ``breakout_z`` absteigend (staerkstes Signal zuerst),
    gekappt auf ``max_patterns``.
    """
    now = now or datetime.now(timezone.utc)
    iso_year, iso_week, _ = now.isocalendar()

    report = compute_trailer_patterns(session, window_days=window_days, now=now)

    # Post-Ebene fuer die Beispiel-Auswahl — gleiche Parameter, gleiche
    # deterministische Rechnung wie im Report (s. Modul-Docstring).
    ctx = build_lift_context(session, window_days=window_days, now=now)
    genre_by_post = _genre_by_post(session, ctx.usable)

    handle_by_channel: dict = {}
    if ctx.usable:
        channel_ids = {p.channel_id for p in ctx.usable}
        for ch in session.exec(
            select(Channel).where(Channel.id.in_(channel_ids))
        ).all():
            handle_by_channel[ch.id] = ch.handle or ch.name

    genre_cells = [
        c
        for c in report.dimensions.get("genre", [])
        if c.breakout_verdict != "insufficient"
    ]
    genre_cells.sort(
        key=lambda c: c.breakout_z if c.breakout_z is not None else float("-inf"),
        reverse=True,
    )
    genre_cells = genre_cells[:max_patterns]

    patterns: list[PatternEvidenceCell] = []
    for cell in genre_cells:
        cell_posts = [
            p for p in ctx.usable if genre_by_post.get(p.id) == cell.value
        ]
        cell_posts.sort(key=lambda p: ctx.lift_by_post[p.id], reverse=True)
        examples = [
            PatternExamplePost(
                post_url=p.post_url,
                platform=ctx.platform_by_channel.get(p.channel_id, "unknown"),
                channel_handle=handle_by_channel.get(p.channel_id, "?"),
                lift=round(ctx.lift_by_post[p.id], 2),
                views=int(p.visible_views) if p.visible_views else None,
                likes=int(p.visible_likes) if p.visible_likes else None,
                duration_seconds=(
                    int(p.duration_seconds)
                    if p.duration_seconds is not None
                    else None
                ),
                caption=(p.caption or "")[:MAX_CAPTION_CHARS],
            )
            for p in cell_posts[:examples_per_pattern]
        ]
        patterns.append(
            PatternEvidenceCell(
                value=cell.value,
                sample_size=cell.sample_size,
                channel_count=cell.channel_count,
                median_lift=round(cell.median_lift, 3),
                breakout_rate=round(cell.breakout_rate, 4),
                expected_breakout_rate=round(cell.expected_breakout_rate, 4),
                breakout_z=(
                    round(cell.breakout_z, 2) if cell.breakout_z is not None else None
                ),
                breakout_verdict=cell.breakout_verdict,
                platform_mix=dict(cell.platform_mix),
                examples=examples,
            )
        )

    usable_count = len(ctx.usable)
    genre_coverage = (
        len([p for p in ctx.usable if p.id in genre_by_post]) / usable_count
        if usable_count
        else 0.0
    )

    return PatternBriefingEvidence(
        mode=BRIEFING_MODE_GENRE,
        iso_year=iso_year,
        iso_week=iso_week,
        window_days=window_days,
        window_start=report.window_start,
        window_end=report.window_end,
        posts_with_baseline=report.posts_with_baseline,
        channels_covered=report.channels_covered,
        genre_coverage=round(genre_coverage, 4),
        baseline_breakout_rate=round(report.baseline_breakout_rate, 4),
        patterns=patterns,
        notes=list(report.notes),
    )


def _format_example_line(idx: int, ex: PatternExamplePost) -> str:
    parts = [f"{ex.lift}x Kanal-Schnitt"]
    if ex.views:
        parts.append(f"{ex.views:,} Views")
    if ex.likes:
        parts.append(f"{ex.likes:,} Likes")
    if ex.duration_seconds:
        parts.append(f"{ex.duration_seconds}s")
    line = f"  {idx}. {', '.join(parts)} — {ex.platform} @{ex.channel_handle}"
    caption = ex.caption.strip()
    if caption:
        line += f'\n     Caption: "{caption}"'
    # URL auf eigener Zeile mit klarem Praefix — dieselbe Mechanik wie im
    # Roundup-Prompt: das LLM braucht sie wortwoertlich fuer
    # ``cited_post_ids``, und ein Mittensatz-Match schlaegt haeufiger
    # fehl als ein ``URL:``-Praefix.
    line += f"\n     URL: {ex.post_url}"
    return line


def _format_pattern_block(idx: int, cell: PatternEvidenceCell) -> str:
    mix = ", ".join(f"{pl} {n}" for pl, n in sorted(cell.platform_mix.items()))
    z = f"z={cell.breakout_z}" if cell.breakout_z is not None else "z=–"
    header = (
        f"## Muster {idx}: Genre \"{cell.value}\" — Befund: {cell.breakout_verdict}\n"
        f"Zahlen: {cell.sample_size} Posts von {cell.channel_count} Kanaelen, "
        f"Breakout-Quote {cell.breakout_rate * 100:.1f} % "
        f"(erwartet nach Plattform-Mischung {cell.expected_breakout_rate * 100:.1f} %, {z}), "
        f"Median-Lift {cell.median_lift}x. Plattform-Mix: {mix}."
    )
    examples = "\n".join(
        _format_example_line(i + 1, ex) for i, ex in enumerate(cell.examples)
    )
    return f"{header}\nBeispiel-Posts (Belege):\n{examples}"


def _build_user_prompt(evidence: PatternBriefingEvidence) -> str:
    header = (
        f"# Gemessene Reichweiten-Muster — Genre-Ebene, "
        f"KW {evidence.iso_week}/{evidence.iso_year}\n\n"
        f"Fenster: {evidence.window_days} Tage "
        f"({evidence.window_start.date().isoformat()} bis "
        f"{evidence.window_end.date().isoformat()}). "
        f"Datenbasis: {evidence.posts_with_baseline} Posts mit Kanal-Baseline "
        f"aus {evidence.channels_covered} Kanaelen; Genre-Abdeckung "
        f"{evidence.genre_coverage * 100:.0f} %. "
        f"Breakout = Post mit mindestens 2x der ueblichen Aktivierung "
        f"seines eigenen Kanals; Basisquote "
        f"{evidence.baseline_breakout_rate * 100:.1f} %.\n"
    )
    blocks = "\n\n".join(
        _format_pattern_block(i + 1, cell)
        for i, cell in enumerate(evidence.patterns)
    )
    schema = """

OUTPUT — AUSSCHLIESSLICH ein JSON-Objekt nach folgendem Schema. Kein
Vorspann, kein Markdown-Codefence, keine Erklaerung — nur das JSON:

{
  "bausteine": [
    {
      "muster": "Kurzname des Musters, z. B. 'Romance auf TikTok'",
      "begruendung": "2-3 Saetze mit den mitgelieferten Zahlen: warum dieses Muster, was ist der Befund. Nur Zahlen aus den Muster-Bloecken oben, wortgetreu.",
      "hooks_de": ["3 Eroeffnungszeilen auf Deutsch, je max. 1 Satz — Mechanik aus den Beispiel-Captions, nie deren Wortlaut"],
      "hooks_en": ["3 opening lines in English — same mechanics, native English social voice"],
      "captions_de": ["2 vollstaendige Caption-Vorlagen auf Deutsch, mit [TITEL]-Platzhalter statt erfundener Filmtitel"],
      "captions_en": ["2 full caption templates in English, [TITLE] placeholder"],
      "hashtags": ["bis zu 10 Hashtag-Vorschlaege ohne #-Praefix, gemischt DE/EN, nur thematisch zum Muster passende"],
      "cited_post_ids": ["URLs der Beispiel-Posts aus diesem Muster-Block, wortwoertlich aus den URL:-Zeilen"]
    }
  ],
  "data_caveats": ["Was den Brief relativiert: duenne Muster, die du deshalb NICHT beliefert hast (Regel 5), niedrige Abdeckung, Plattform-Schieflagen"]
}

Antworte ausschliesslich mit dem JSON-Objekt, ohne Markdown-Codefences."""
    return header + "\n" + blocks + schema


def validate_baustein_citations(
    llm_report: PatternBriefingLLMReport,
    evidence: PatternBriefingEvidence,
) -> tuple[PatternBriefingLLMReport, int]:
    """Citation-Pflicht (Prompt-Regel 2), im Code durchgesetzt: jeder
    Baustein, dessen ``cited_post_ids`` nicht vollstaendig aus den
    mitgegebenen Beispiel-URLs stammen, fliegt raus.

    Verworfen wird der ganze Baustein, nicht nur die falsche ID — eine
    Empfehlung, deren Beleg nicht existiert, ist keine Empfehlung mit
    einem Schoenheitsfehler. Rueckgabe: (bereinigter Report, Anzahl
    verworfener Bausteine).
    """
    allow: set[str] = {
        ex.post_url for cell in evidence.patterns for ex in cell.examples
    }
    kept: list[PatternTextBaustein] = []
    dropped = 0
    for baustein in llm_report.bausteine:
        unknown = [pid for pid in baustein.cited_post_ids if pid not in allow]
        if unknown:
            dropped += 1
            logger.warning(
                "pattern-briefing-citation-dropped",
                extra={
                    "muster": baustein.muster[:120],
                    "unknown_ids": unknown[:5],
                    "allow_set_size": len(allow),
                },
            )
            continue
        kept.append(baustein)
    if dropped == 0:
        return llm_report, 0
    return (
        PatternBriefingLLMReport(
            bausteine=kept, data_caveats=list(llm_report.data_caveats)
        ),
        dropped,
    )


def generate_pattern_briefing(
    session: Session,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    model: str = OPUS_MODEL_ALIAS,
    max_tokens: int = PATTERN_BRIEFING_MAX_TOKENS,
    now: Optional[datetime] = None,
) -> PatternBriefingReport:
    """Evidence bauen → (falls Muster da) LLM-Call mit JSON-Retry →
    Schema-Validierung → Citation-Pruefung. Persistenz separat via
    ``generate_and_persist_pattern_briefing``.

    Leerlauf (keine belastbaren Genre-Muster): KEIN LLM-Call,
    ``model="none"``, deterministischer ``data_caveat`` — das ist bis
    zum ersten Title-Sync mit Genres der Normalfall und kostet nichts.
    """
    evidence = build_pattern_evidence(session, window_days=window_days, now=now)
    generated_at = datetime.now(timezone.utc)

    if not evidence.patterns:
        caveat = (
            f"Kein belastbares Genre-Muster im Fenster "
            f"(Genre-Abdeckung {evidence.genre_coverage * 100:.0f} % von "
            f"{evidence.posts_with_baseline} Posts mit Baseline). Genres "
            f"kommen aus TMDb ueber die Titel-Zuordnung und fuellen sich "
            f"mit jedem Title-Sync-Lauf."
        )
        logger.info(
            "pattern_briefing.idle",
            extra={
                "iso_year": evidence.iso_year,
                "iso_week": evidence.iso_week,
                "genre_coverage": evidence.genre_coverage,
            },
        )
        return PatternBriefingReport(
            mode=evidence.mode,
            iso_year=evidence.iso_year,
            iso_week=evidence.iso_week,
            window_days=window_days,
            generated_at=generated_at,
            model="none",
            evidence=evidence,
            llm_output=PatternBriefingLLMReport(
                bausteine=[], data_caveats=[caveat]
            ),
        )

    if not is_anthropic_configured():
        raise AnthropicAuthError(
            "ANTHROPIC_API_KEY ist nicht gesetzt — Pattern-Briefing kann nicht generieren."
        )

    user_prompt = _build_user_prompt(evidence)
    logger.info(
        "pattern_briefing_anthropic_call_start",
        extra={
            "iso_year": evidence.iso_year,
            "iso_week": evidence.iso_week,
            "patterns": len(evidence.patterns),
            "prompt_chars": len(user_prompt),
        },
    )

    retry_result = call_with_json_retry(
        model=model,
        system=PATTERN_BRIEFING_SYSTEM_PROMPT,
        user_message=user_prompt,
        max_tokens=max_tokens,
        max_recalls=2,
        log_prefix="pattern_briefing",
        log_extra={
            "iso_year": evidence.iso_year,
            "iso_week": evidence.iso_week,
        },
    )

    last_raw_text = (
        retry_result.call_attempts[-1][1] if retry_result.call_attempts else ""
    )

    llm_output: Optional[PatternBriefingLLMReport] = None
    citation_dropped = 0
    raw_for_response: Optional[str] = None
    if retry_result.parsed is not None:
        candidate = _unwrap_single_key(
            retry_result.parsed, expected_field="bausteine"
        )
        try:
            validated = PatternBriefingLLMReport.model_validate(candidate)
        except ValueError as exc:
            logger.error(
                "pattern-briefing-schema-validation-failed",
                extra={
                    "error_message": str(exc)[:500],
                    "raw_response_first_500": last_raw_text[:500],
                },
            )
            raw_for_response = last_raw_text
        else:
            llm_output, citation_dropped = validate_baustein_citations(
                validated, evidence
            )
            logger.info(
                "pattern_briefing_llm_call_ok",
                extra={
                    "bausteine": len(llm_output.bausteine),
                    "citation_dropped": citation_dropped,
                    "parse_path": retry_result.parse_path,
                    "anthropic_calls": len(retry_result.call_attempts),
                },
            )
    else:
        pos = (
            retry_result.parse_error.pos
            if retry_result.parse_error and retry_result.parse_error.pos is not None
            else 0
        )
        logger.error(
            "pattern-briefing-json-parse-failed",
            extra={
                "char_position": pos,
                "raw_response_length": len(last_raw_text),
                "raw_response_first_500": last_raw_text[:500],
                "anthropic_calls": len(retry_result.call_attempts),
            },
        )
        raw_for_response = last_raw_text

    input_tokens_total = 0
    output_tokens_total = 0
    for msg_attempt, _ in retry_result.call_attempts:
        usage = getattr(msg_attempt, "usage", None)
        if usage is None:
            continue
        in_t = int(getattr(usage, "input_tokens", 0) or 0)
        out_t = int(getattr(usage, "output_tokens", 0) or 0)
        if not (in_t or out_t):
            continue
        input_tokens_total += in_t
        output_tokens_total += out_t
        record_anthropic_call(
            usage,
            model=model,
            operation="pattern_briefing",
            meta={
                "mode": evidence.mode,
                "iso_year": evidence.iso_year,
                "iso_week": evidence.iso_week,
            },
        )
    cost = (
        _estimate_cost_usd(input_tokens_total, output_tokens_total)
        if (input_tokens_total or output_tokens_total)
        else None
    )

    return PatternBriefingReport(
        mode=evidence.mode,
        iso_year=evidence.iso_year,
        iso_week=evidence.iso_week,
        window_days=window_days,
        generated_at=generated_at,
        model=model,
        evidence=evidence,
        llm_output=llm_output,
        cost_usd_estimate=cost,
        input_tokens=input_tokens_total or None,
        output_tokens=output_tokens_total or None,
        citation_dropped=citation_dropped,
        raw_llm_text=raw_for_response,
    )


def _persist_briefing(session: Session, report: PatternBriefingReport) -> None:
    """Upsert keyed auf ``(mode, iso_year, iso_week)``, Last-Write-Wins
    (delete-then-insert, Konvention der Brief-Pfade).

    Persistiert IMMER — auch bei ``llm_output=None`` (Parse-/Schema-Fail)
    und im Leerlauf: die Evidence ist das Audit-Produkt, und
    ``raw_llm_text`` haelt die verworfene Antwort fuer die Diagnose
    (Cutter-Weekly-Konvention, bewusste Abweichung vom Roundup-Skip).
    """
    cost_cents: Optional[int] = (
        int(round(report.cost_usd_estimate * 100))
        if report.cost_usd_estimate
        else None
    )
    existing = session.get(
        PatternBriefingRow, (report.mode, report.iso_year, report.iso_week)
    )
    if existing is not None:
        session.delete(existing)
        session.flush()

    session.add(
        PatternBriefingRow(
            mode=report.mode,
            iso_year=report.iso_year,
            iso_week=report.iso_week,
            window_days=report.window_days,
            evidence=report.evidence.model_dump(mode="json"),
            llm_output=(
                report.llm_output.model_dump(mode="json")
                if report.llm_output is not None
                else None
            ),
            raw_llm_text=report.raw_llm_text,
            generated_at=report.generated_at,
            model=report.model,
            cost_usd_cents=cost_cents,
            input_tokens=report.input_tokens,
            output_tokens=report.output_tokens,
            citation_dropped=report.citation_dropped,
        )
    )
    session.commit()


def generate_and_persist_pattern_briefing(
    session: Session,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    model: str = OPUS_MODEL_ALIAS,
    now: Optional[datetime] = None,
) -> PatternBriefingReport:
    """End-to-End: Evidence → LLM (falls Muster da) → Citation-Pruefung →
    persistieren. Cron-Block und Admin-Trigger rufen das hier auf."""
    report = generate_pattern_briefing(
        session, window_days=window_days, model=model, now=now
    )
    _persist_briefing(session, report)
    return report


__all__ = [
    "BRIEFING_MODE_GENRE",
    "EXAMPLES_PER_PATTERN",
    "MAX_PATTERNS_PER_BRIEFING",
    "PATTERN_BRIEFING_SYSTEM_PROMPT",
    "build_pattern_evidence",
    "generate_and_persist_pattern_briefing",
    "generate_pattern_briefing",
    "validate_baustein_citations",
]
