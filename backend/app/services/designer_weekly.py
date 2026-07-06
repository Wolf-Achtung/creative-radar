"""Designer-Wochenbriefing — LLM-Synthese + Persistenz.

Sprint 2026-07-06: mirror von ``cutter_weekly.py`` 1:1, nur die LLM-Lens
unterscheidet sich — Motion-/Grafik-Beobachtung (Caption-Style, Text-
Overlay, Branding-Einsatz, analog ``FuerMotionDesigner`` in
``schemas/insights.py``) statt Schnitt-Beobachtung.

Hintergrund: die 9 Pair-Briefs tragen bereits eine ``FuerMotionDesigner``-
Sektion, aber die ist in 9 separaten Wochen-Briefs vergraben und deckt
nur Majors ab. Cutter-Weekly hat gezeigt, dass ein EIGENSTAENDIGES
Wochen-Synthese-Artefakt — quer ueber alle 9 Pair-Briefs UND die 6
Segment-Roundups (Majors, Independents, Verleiher) — praktisch genutzt
wird, weil es die Beobachtung buendelt statt sie zu verstreuen. Designer-
Weekly schliesst dieselbe Luecke fuer die Design-/Motion-Rolle.

**Die Code-Pruefung (``services/weekly_briefing_evidence.py``) entscheidet,
was ein Muster ist** — das LLM formuliert ausschliesslich, was diese
Pruefung freigegeben hat, und darf nichts dazuerfinden. Dieselbe
Evidenz-Disziplin wie Cutter-Weekly, dieselbe Schwelle (siehe Docstring
von ``weekly_briefing_evidence.py`` fuer die bewusste Begruendung, warum
KEIN eigenes Caption-/Overlay-Signal existiert und die ER-p75-Schwelle
wiederverwendet wird — es gibt schlicht kein strukturiertes Caption-
Style-Feld auf ``Post``, das eine eigene deterministische Pruefung
tragen koennte).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session

from app.schemas.insights import (
    DesignerPlatformBlock,
    DesignerWeeklyLLMReport,
    DesignerWeeklyReport,
    WeeklyBriefingEvidence,
    WeeklyEvidencePost,
    WeeklyForecastSignal,
    WeeklyPlatformEvidence,
)
from app.services.weekly_briefing_evidence import (
    build_weekly_evidence,
    collect_forecast_signals,
)

logger = logging.getLogger(__name__)


# ===========================================================================
# LLM-Synthese mit Beleg-Validierung (Citation strict) — mirror Cutter-Weekly
# ===========================================================================
#
# Arbeitsteilung (identisch zu Cutter-Weekly): ``build_weekly_evidence``
# hat entschieden, WAS ein Muster ist. Das LLM bekommt ausschliesslich die
# freigegebenen Muster-Kandidaten (kompakt, nicht die vollen Blobs) und
# formuliert pro freigegebener Plattform einen Block. Leerlauf-Plattformen
# erhalten deterministische Code-Bloecke — das LLM sieht sie nicht und
# kann fuer sie nichts erfinden. Citation strict: jede zitierte ID muss im
# Allow-Set der stuetzenden Posts liegen, sonst wird die komplette Antwort
# verworfen und einmal neu angefragt; danach ``llm_output=None`` (Evidence
# bleibt).

_PLATFORM_LABELS: dict[str, str] = {
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "youtube": "YouTube",
}

# Wie viele Beleg-Posts pro freigegebener Plattform in den Prompt gehen.
# Der Evidence-Blob behaelt IMMER alle — das Cap haelt nur den Prompt
# kompakt (ein LLM-Call/Woche, kleines Token-Budget).
_PROMPT_POSTS_CAP = 10

# Maximal zwei volle Anlaeufe (Schema-/Citation-Fail loest genau einen
# frischen Versuch aus); innerhalb jedes Anlaufs faengt
# ``call_with_json_retry`` mit max_recalls=1 reine JSON-Parse-Fehler ab.
# Worst case 4 Anthropic-Calls — bewusst unter dem Pair-Brief-Niveau.
_MAX_LLM_ATTEMPTS = 2

DESIGNER_WEEKLY_SYSTEM_PROMPT = """Du schreibst das woechentliche Designer-Briefing fuer ein Trailerhaus: eine plattformweise Mustersicht quer ueber alle beobachteten Studios, Verleiher und Independents — aus der Motion-Design-/Grafik-Perspektive eines Motion-Designers/Grafikers.

DEINE ROLLE — UND IHRE GRENZE:
Eine Code-Pruefung hat bereits entschieden, welche Plattformen diese Woche ein belegtes Muster haben (Evidenzschwelle: mehrere ueberdurchschnittliche Posts ueber mehrere Titel verteilt). Du bekommst NUR die freigegebenen Plattformen mit ihren Beleg-Posts. Du formulierst, was diese Belege gemeinsam zeigen — du entscheidest NICHT, ob ein Muster existiert, und du erfindest keine Muster fuer Plattformen, die dir nicht vorgelegt wurden.

TON UND HALTUNG:
- Sachlich, beobachtend, in ganzen Saetzen. Schreibe Zahlen aus (33.000, nicht 33k).
- Beschreibe, WAS die Posts gemeinsam haben aus Design-Sicht (Caption-Style, Text-Overlay-Einsatz, Branding-Platzierung, visueller Aufbau, Titel-Mix) — behaupte NIE, WARUM es funktioniert hat. Keine kausalen Diagnosen ("das Caption-Overlay hat funktioniert, weil..." ist verboten; "die starken Posts dieser Woche setzen alle grossflaechige Text-Overlays im ersten Drittel ein" ist erlaubt).
- Der optionale design_impuls ist ein vorsichtiger Hinweis zum Hinschauen, keine Anweisung und keine Erfolgsgarantie. Wenn die Belege keinen Impuls decken: null.
- Kein Berater-Vokabular, keine Wertungsformeln, kein Szene-Jargon.

EVIDENZ-PFLICHT (hart):
Jeder Block MUSS in cited_post_ids die exakten post_url-Strings der Belege nennen, auf die sich die Beobachtung stuetzt (mindestens zwei, nur aus der Beleg-Liste derselben Plattform). Eine Antwort mit IDs ausserhalb der Beleg-Listen wird vollstaendig verworfen.

QUER-MUSTER (optional):
quer_muster nur ausfuellen, wenn dieselbe Beobachtung sichtbar auf mindestens zwei Plattformen traegt — dann quer_cited_post_ids mit Belegen aus mindestens zwei Plattformen. Im Zweifel: null. Ein erzwungener Quer-Block ist schlechter als keiner.

MARKT-SIGNAL (optional, beobachtend):
Wenn dir Markt-Signale aus dem ER-Forecast vorgelegt werden (nur Majors, nur Markt-Ebene), fasse sie in markt_signal_notiz als Hinschauen-Hinweis zusammen — ohne Ursache, ohne Plattform-Zuordnung, ohne Prognosezahl. Ohne vorgelegte Signale: null.

OUTPUT:
Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, ohne Markdown-Zaeune, exakt in dieser Form:
{
  "bloecke": [
    {
      "platform": "instagram|tiktok|youtube — nur die dir vorgelegten Plattformen, jede genau einmal",
      "beobachtung": "2-4 Saetze: das verdichtete Design-Muster dieser Woche mit 1-2 konkreten Belegen im Fliesstext (Titel + Kennzahl)",
      "design_impuls": "1-2 Saetze vorsichtiger Impuls oder null",
      "cited_post_ids": ["exakte post_url-Strings aus der Beleg-Liste dieser Plattform"]
    }
  ],
  "quer_muster": "1-3 Saetze oder null",
  "quer_cited_post_ids": ["nur wenn quer_muster gesetzt; Belege aus mindestens zwei Plattformen"],
  "markt_signal_notiz": "1-2 Saetze oder null",
  "data_caveats": ["ehrliche Lautstaerke-Hinweise, z.B. duenne Wochen-Basis"]
}"""


def _released_platforms(evidence: WeeklyBriefingEvidence) -> list[WeeklyPlatformEvidence]:
    return [p for p in evidence.platforms if p.status == "pattern_released"]


def _build_allow_sets(evidence: WeeklyBriefingEvidence) -> dict[str, set[str]]:
    """Citation-Allow-Set pro FREIGEGEBENER Plattform — exakt die
    post_urls der stuetzenden Posts. Bewusst enger als das Pair-Brief-
    Allow-Set: auch ein real existierender, aber unter-schwelliger Post
    ist hier nicht zitierfaehig."""
    return {
        p.platform: {sp.post_url for sp in p.supporting_posts}
        for p in _released_platforms(evidence)
    }


def _format_evidence_post(idx: int, post: WeeklyEvidencePost) -> str:
    title = post.title_original or "(ohne Titel-Zuordnung)"
    duration = (
        f"{post.duration_seconds}s" if post.duration_seconds is not None else "k.A."
    )
    published = post.published_at.date().isoformat() if post.published_at else "k.A."
    return (
        f"{idx}. {post.post_url}\n"
        f"   Titel: {title} | Quelle: {post.source} | publiziert: {published}\n"
        f"   ER: {post.er:.4f} | Views: {post.views} | Likes: {post.likes} | "
        f"Kommentare: {post.comments} | Laenge: {duration}\n"
        f"   Caption: {post.caption_excerpt or '(leer)'}"
    )


def _build_user_prompt(
    evidence: WeeklyBriefingEvidence,
    signals: list[WeeklyForecastSignal],
) -> str:
    released = _released_platforms(evidence)
    sections: list[str] = [
        (
            f"# Designer-Wochenbriefing KW {evidence.iso_week}/{evidence.iso_year}\n\n"
            f"Die Code-Pruefung hat fuer {len(released)} Plattform(en) ein belegtes "
            f"Muster freigegeben. Schreibe fuer JEDE der folgenden Plattformen genau "
            f"einen Block — fuer keine andere."
        )
    ]

    for p in released:
        label = _PLATFORM_LABELS.get(p.platform, p.platform)
        posts = p.supporting_posts[:_PROMPT_POSTS_CAP]
        lines = "\n".join(
            _format_evidence_post(i + 1, post) for i, post in enumerate(posts)
        )
        cap_note = ""
        if len(p.supporting_posts) > len(posts):
            cap_note = (
                f"\n(Insgesamt {len(p.supporting_posts)} Belege ueber der Schwelle; "
                f"gezeigt sind die {len(posts)} staerksten.)"
            )
        sections.append(
            f"## {label}\n"
            f"Schwelle dieser Woche: ER >= {p.p75_er:.4f} (rollende p75 aus "
            f"{p.p75_sample_size} Posts). {p.candidates_above_p75} Beleg-Posts "
            f"ueber der Schwelle, verteilt ueber {len(p.distinct_keys)} Titel/Quellen: "
            f"{', '.join(p.distinct_keys)}.\n\n"
            f"Beleg-Posts (cited_post_ids MUESSEN aus diesen post_urls stammen):\n"
            f"{lines}{cap_note}"
        )

    if signals:
        signal_lines = "\n".join(
            f"- {s.pair_key} / Markt {s.market}: ER-Trend {s.direction} "
            f"(ueber {s.n_points} Wochen)"
            for s in signals
        )
        sections.append(
            "## Markt-Signale aus dem ER-Forecast (beobachtend)\n"
            "Nur Majors, nur Markt-Ebene — die Verleiher-/Independent-Segmente "
            "haben kein Forecast-Pendant (Asymmetrie bitte nicht verschweigen, "
            "gehoert in data_caveats). Keine Plattform-Zuordnung, keine Ursache, "
            "keine Prognosezahl.\n"
            f"{signal_lines}"
        )
    else:
        sections.append(
            "## Markt-Signale aus dem ER-Forecast\n"
            "Diese Woche liegen keine belastbaren ok-Signale vor — "
            "markt_signal_notiz MUSS null sein."
        )

    return "\n\n".join(sections)


def _leerlauf_block(p: WeeklyPlatformEvidence) -> DesignerPlatformBlock:
    """Deterministischer Leerlauf-Block — vom Code erzeugt, nicht vom LLM.
    Der ehrliche Kern des Briefings: lieber 'kein Muster' als ein
    erfundenes."""
    label = _PLATFORM_LABELS.get(p.platform, p.platform)
    if p.status == "no_threshold":
        beobachtung = (
            f"Keine belastbare Vergleichsbasis fuer {label} diese Woche: "
            f"{p.reason}"
        )
    else:
        beobachtung = (
            f"Kein klares Muster diese Woche auf {label}: {p.reason}"
        )
    return DesignerPlatformBlock(
        platform=p.platform,
        beobachtung=beobachtung,
        design_impuls=None,
        cited_post_ids=[],
        generated_by="code",
    )


def _validate_llm_report(
    report: DesignerWeeklyLLMReport,
    evidence: WeeklyBriefingEvidence,
    signals: list[WeeklyForecastSignal],
) -> list[str]:
    """Strict-Validierung der LLM-Antwort gegen die Code-Pruefung.
    Rueckgabe: Liste der Verstoesse (leer = belegt). Jeder Verstoss
    verwirft die GESAMTE Antwort — Citation strict, mirror Cutter-Weekly."""
    problems: list[str] = []
    allow_sets = _build_allow_sets(evidence)
    released = set(allow_sets)

    block_platforms = [b.platform for b in report.bloecke]
    if sorted(block_platforms) != sorted(released):
        problems.append(
            f"bloecke decken {sorted(block_platforms)} ab, freigegeben sind "
            f"exakt {sorted(released)}"
        )

    for b in report.bloecke:
        allow = allow_sets.get(b.platform)
        if allow is None:
            continue  # schon oben als Plattform-Mismatch erfasst
        if len(b.cited_post_ids) < 2:
            problems.append(
                f"bloecke[{b.platform}].cited_post_ids hat {len(b.cited_post_ids)} "
                f"Eintraege (mindestens 2 Belege gefordert)"
            )
        missing = [cid for cid in b.cited_post_ids if cid not in allow]
        if missing:
            problems.append(
                f"bloecke[{b.platform}] zitiert ausserhalb des Allow-Sets: "
                f"{missing[:3]}"
            )
        if b.generated_by != "llm":
            problems.append(
                f"bloecke[{b.platform}].generated_by={b.generated_by!r} — "
                f"das Feld setzt der Code, nicht das Modell"
            )

    if report.quer_muster is not None:
        union_allow = {url for s in allow_sets.values() for url in s}
        cited = report.quer_cited_post_ids
        missing = [cid for cid in cited if cid not in union_allow]
        if missing:
            problems.append(
                f"quer_cited_post_ids ausserhalb des Allow-Sets: {missing[:3]}"
            )
        cited_platforms = {
            platform
            for platform, allow in allow_sets.items()
            if any(cid in allow for cid in cited)
        }
        if len(cited_platforms) < 2:
            problems.append(
                "quer_muster gesetzt, aber Belege decken keine zwei Plattformen"
            )

    if report.markt_signal_notiz is not None and not signals:
        problems.append("markt_signal_notiz gesetzt, aber keine ok-Signale vorgelegt")

    return problems


def _assemble_report(
    evidence: WeeklyBriefingEvidence,
    llm_report: Optional[DesignerWeeklyLLMReport],
) -> DesignerWeeklyLLMReport:
    """Finaler Report in fester Plattform-Reihenfolge: freigegebene
    Plattformen tragen den validierten LLM-Block (``generated_by='llm'``),
    Leerlauf-Plattformen den deterministischen Code-Block. Die
    Asymmetrie-Caveat zum Forecast-Signal stempelt der Code — sie haengt
    nicht von der Disziplin des Modells ab."""
    llm_blocks: dict[str, DesignerPlatformBlock] = {}
    if llm_report is not None:
        for b in llm_report.bloecke:
            llm_blocks[b.platform] = b.model_copy(update={"generated_by": "llm"})

    blocks: list[DesignerPlatformBlock] = []
    for p in evidence.platforms:
        if p.status == "pattern_released" and p.platform in llm_blocks:
            blocks.append(llm_blocks[p.platform])
        else:
            blocks.append(_leerlauf_block(p))

    caveats: list[str] = list(llm_report.data_caveats) if llm_report else []
    if evidence.forecast_signals:
        caveats.append(
            "Markt-Signale decken nur die Majors (Pair-Briefs) ab — fuer "
            "Verleiher-/Independent-Segmente existiert kein Forecast-Pendant."
        )

    return DesignerWeeklyLLMReport(
        bloecke=blocks,
        quer_muster=llm_report.quer_muster if llm_report else None,
        quer_cited_post_ids=(
            list(llm_report.quer_cited_post_ids) if llm_report else []
        ),
        markt_signal_notiz=(
            llm_report.markt_signal_notiz if llm_report else None
        ),
        data_caveats=caveats,
    )


def generate_designer_weekly(
    session: Session,
    *,
    now: Optional[datetime] = None,
    model: Optional[str] = None,
    max_tokens: int = 4000,
) -> DesignerWeeklyReport:
    """End-to-End-Generierung des Designer-Wochenbriefings (ohne Persistenz).

    Ablauf (identisch zu ``generate_cutter_weekly``):
    1. ``build_weekly_evidence`` — deterministische Pruefung, geteilt mit
       Cutter-Weekly (``services/weekly_briefing_evidence.py``).
    2. Forecast-Signale der Majors einsammeln (beobachtend, gratis im
       Cron-Kontext nach dem Einordnungs-Warmup).
    3. Keine Plattform freigegeben → KEIN LLM-Call (``model='none'``),
       der Report besteht aus deterministischen Leerlauf-Bloecken.
       Ehrlicher Leerlauf kostet nichts.
    4. Sonst genau ein Opus-Call mit kompaktem Kandidaten-Prompt; bis zu
       ein frischer Wiederholungs-Anlauf bei Schema-/Citation-Fail. Jeder
       bezahlte Call landet einzeln im costlog
       (``operation='designer_weekly'``, F0.7-Cap).
    5. Total-Fail → ``llm_output=None`` + ``raw_llm_text`` — der
       Evidence-Blob bleibt vollstaendig (Kalibrierungs-Produkt).
    """
    # Lazy imports analog cutter_weekly.py (kein Engine-Load im reinen
    # Evidenz-Pfad, keine Import-Zyklen Richtung insight_engine).
    from app.services.anthropic_client import (
        call_with_json_retry,
        is_anthropic_configured,
        _unwrap_single_key,
    )
    from app.services.cost_log import record_anthropic_call
    from app.services.insight_engine import OPUS_MODEL_ALIAS, _estimate_cost_usd

    model = model or OPUS_MODEL_ALIAS
    now = now or datetime.now(timezone.utc)
    generated_at = datetime.now(timezone.utc)

    evidence = build_weekly_evidence(session, now=now)
    signals = collect_forecast_signals(session)
    evidence.forecast_signals = signals

    released = _released_platforms(evidence)
    if not released:
        logger.info(
            "designer-weekly-no-pattern-week",
            extra={"iso_year": evidence.iso_year, "iso_week": evidence.iso_week},
        )
        return DesignerWeeklyReport(
            iso_year=evidence.iso_year,
            iso_week=evidence.iso_week,
            generated_at=generated_at,
            model="none",
            evidence=evidence,
            llm_output=_assemble_report(evidence, None),
        )

    if not is_anthropic_configured():
        from app.services.anthropic_client import AnthropicAuthError

        raise AnthropicAuthError(
            "ANTHROPIC_API_KEY ist nicht gesetzt — Designer-Wochenbriefing "
            "kann nicht generieren."
        )

    user_prompt = _build_user_prompt(evidence, signals)
    log_extra = {"iso_year": evidence.iso_year, "iso_week": evidence.iso_week}

    llm_output: Optional[DesignerWeeklyLLMReport] = None
    raw_for_response: Optional[str] = None
    input_tokens_total = 0
    output_tokens_total = 0

    for attempt in range(_MAX_LLM_ATTEMPTS):
        retry_result = call_with_json_retry(
            model=model,
            system=DESIGNER_WEEKLY_SYSTEM_PROMPT,
            user_message=user_prompt,
            max_tokens=max_tokens,
            max_recalls=1,
            log_prefix="designer-weekly",
            log_extra={**log_extra, "outer_attempt": attempt},
        )

        # Jeden bezahlten Call einzeln erfassen — auch die einer spaeter
        # verworfenen Antwort (F0.7 sieht die wahre Spend-Summe).
        for msg_attempt, _raw in retry_result.call_attempts:
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
                operation="designer_weekly",
                meta={
                    "iso_year": evidence.iso_year,
                    "iso_week": evidence.iso_week,
                },
            )

        last_raw_text = (
            retry_result.call_attempts[-1][1] if retry_result.call_attempts else ""
        )

        if retry_result.parsed is None:
            raw_for_response = last_raw_text
            logger.error(
                "designer-weekly-json-parse-failed",
                extra={
                    **log_extra,
                    "outer_attempt": attempt,
                    "raw_response_first_500": last_raw_text[:500],
                },
            )
            continue

        candidate = _unwrap_single_key(retry_result.parsed, expected_field="bloecke")
        try:
            parsed_report = DesignerWeeklyLLMReport.model_validate(candidate)
        except ValueError as exc:
            raw_for_response = last_raw_text
            logger.error(
                "designer-weekly-schema-validation-failed",
                extra={
                    **log_extra,
                    "outer_attempt": attempt,
                    "error_message": str(exc)[:500],
                    "raw_response_first_500": last_raw_text[:500],
                },
            )
            continue

        problems = _validate_llm_report(parsed_report, evidence, signals)
        if problems:
            raw_for_response = last_raw_text
            logger.error(
                "designer-weekly-citation-rejected",
                extra={
                    **log_extra,
                    "outer_attempt": attempt,
                    "problems": problems[:5],
                },
            )
            continue

        llm_output = parsed_report
        raw_for_response = None
        logger.info(
            "designer-weekly-llm-ok",
            extra={
                **log_extra,
                "outer_attempt": attempt,
                "parse_path": retry_result.parse_path,
                "anthropic_calls": len(retry_result.call_attempts),
            },
        )
        break

    cost = (
        _estimate_cost_usd(input_tokens_total, output_tokens_total)
        if (input_tokens_total or output_tokens_total)
        else None
    )

    return DesignerWeeklyReport(
        iso_year=evidence.iso_year,
        iso_week=evidence.iso_week,
        generated_at=generated_at,
        model=model,
        evidence=evidence,
        llm_output=(
            _assemble_report(evidence, llm_output)
            if llm_output is not None
            else None
        ),
        cost_usd_estimate=cost,
        input_tokens=input_tokens_total or None,
        output_tokens=output_tokens_total or None,
        raw_llm_text=raw_for_response,
    )


# ===========================================================================
# Persistenz — mirror Cutter-Weekly Commit C
# ===========================================================================


def _persist_designer_weekly(session: Session, report: DesignerWeeklyReport) -> None:
    """Upsert einer ``designer_weekly_briefing``-Row keyed auf
    ``(iso_year, iso_week)``. Last-Write-Wins (delete-then-insert), mirror
    ``_persist_cutter_weekly``.

    BEWUSSTE Abweichung von der Roundup-Konvention (identisch zu Cutter):
    KEIN Persist-Skip bei ``llm_output=None``. Der Evidence-Blob ist das
    Kalibrierungs-Produkt der Trockenlauf-Phase — eine Woche, deren
    LLM-Synthese an der strikten Citation-Validierung scheitert, muss mit
    ihren freigegebenen/verworfenen Mustern trotzdem in der Tabelle
    landen. ``raw_llm_text`` traegt dann die letzte verworfene Antwort
    fuer die Diagnose.
    """
    from app.models.entities import DesignerWeeklyBriefing

    cost_cents: Optional[int] = (
        int(round(report.cost_usd_estimate * 100))
        if report.cost_usd_estimate
        else None
    )

    existing = session.get(
        DesignerWeeklyBriefing, (report.iso_year, report.iso_week)
    )
    if existing is not None:
        session.delete(existing)
        session.flush()

    row = DesignerWeeklyBriefing(
        iso_year=report.iso_year,
        iso_week=report.iso_week,
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
    )
    session.add(row)
    session.commit()
    logger.info(
        "designer-weekly-persisted",
        extra={
            "iso_year": report.iso_year,
            "iso_week": report.iso_week,
            "model": report.model,
            "llm_output_present": report.llm_output is not None,
            "cost_usd_cents": cost_cents,
        },
    )


def generate_and_persist_designer_weekly(
    session: Session,
    *,
    now: Optional[datetime] = None,
    model: Optional[str] = None,
) -> DesignerWeeklyReport:
    """End-to-End: Evidenz-Pruefung → LLM-Synthese → persistieren
    (idempotent Last-Write-Wins). Cron-Block und ein etwaiger manueller
    Admin-Trigger rufen das hier auf. Mirror ``generate_and_persist_cutter_weekly``."""
    report = generate_designer_weekly(session, now=now, model=model)
    _persist_designer_weekly(session, report)
    return report
