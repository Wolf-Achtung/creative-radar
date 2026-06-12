"""Cutter-Wochenbriefing — deterministische Evidenz-Prüfung (Commit A).

Master-Plan-Sprint 2026-06-12: Quer über alle persistierten Pair-Briefs
(``insight_report.aggregation``) und Segment-Roundups
(``segment_roundup.channels_aggregation``) einer ISO-Woche wird pro
Plattform (Instagram/TikTok/YouTube) geprüft, ob ein belegtes Muster
vorliegt. **Die Code-Prüfung hier entscheidet, was ein Muster ist** — das
LLM (Commit B) formuliert ausschließlich, was diese Prüfung freigegeben
hat, und darf nichts dazuerfinden.

Evidenzschwelle (Wolf-Freigabe 12.06.2026, alle Werte ENV-verstellbar):
ein Muster gilt als gedeckt, wenn in der ISO-Woche pro Plattform
``>= cutter_weekly_min_posts`` Posts mit ``ER >= rollender plattform-p75``
über ``>= cutter_weekly_min_distinct_keys`` Distinct-Keys liegen.
Distinct-Key = ``title_original`` wenn gesetzt, sonst Pair-/Segment-
Channel-Fallback (Entscheidung 2); der Anteil echter Titel-Keys wird als
``title_key_share`` pro Woche gemessen (Kalibrier-Frage aus Phase 0).

Bewusste Eigenschaften der Datenbasis (Phase-0-Befund):

- **Wochen-Filter (Entscheidung 1):** Die Blobs sind rollierende Fenster
  (Pair 30d, Roundup 14d) — jeder Kandidat wird über ``published_at`` hart
  auf die ISO-Woche gefiltert. Posts ohne ``published_at`` werden
  ausgeschlossen (Wochen-Zugehörigkeit nicht beweisbar — lieber ein Post
  weniger als ein falsch zugeordneter).
- **Top-N-Trunkierung:** Die Blobs tragen nur die Top-10 (Pair) bzw.
  Top-8 (Roundup) Posts pro Channel. Ein über-p75-Post auf Rang 11 ist
  unsichtbar — die Zählung ist also konservativ: sie kann ein echtes
  Muster verpassen, aber kein Scheinmuster erzeugen.
- **p75-Grundgesamtheit:** Die rollende p75 kommt aus der ``post``-Tabelle
  (volle Population, letzte N Wochen), NICHT aus den Top-N-Blobs — die
  sind auf Spitzen-Performance vorgefiltert und würden die Schwelle
  systematisch nach oben verzerren. ER-Definition identisch zur Timeline
  (``(likes+comments)/views`` für ``views > 0``, ``max(0, ...)``-Guard
  gegen den Apify-Sentinel ``likesCount=-1``, vgl. market_timeline).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from app.config import settings
from app.models.entities import (
    InsightReport as InsightReportRow,
    Post,
    SegmentRoundup as SegmentRoundupRow,
)
from app.schemas.insights import (
    CutterEvidencePost,
    CutterForecastSignal,
    CutterPlatformBlock,
    CutterPlatformEvidence,
    CutterWeeklyEvidence,
    CutterWeeklyLLMReport,
    CutterWeeklyParams,
    CutterWeeklyReport,
    CutterWeeklySources,
    PairAggregation,
    RankedPost,
    SegmentAggregation,
)
from app.services.market_timeline import iso_week_monday

logger = logging.getLogger(__name__)

# Feste Plattform-Reihenfolge der drei Briefing-Blöcke. Entspricht den
# ``Post.platform``-Werten; alles andere (z. B. künftige Plattformen)
# läuft in der Prüfung einfach leer mit.
CUTTER_PLATFORMS: list[str] = ["instagram", "tiktok", "youtube"]


def week_bounds(iso_year: int, iso_week: int) -> tuple[datetime, datetime]:
    """[Montag 00:00 UTC, Montag+7d) der ISO-Woche — Ende exklusiv."""
    start = iso_week_monday(iso_year, iso_week)
    return start, start + timedelta(days=7)


def post_er(views: int, likes: int, comments: int) -> Optional[float]:
    """ER eines Einzel-Posts nach Timeline-Definition. ``None`` bei
    ``views <= 0``; Sentinel-Guard gegen negative likes/comments."""
    if views is None or views <= 0:
        return None
    return (max(0, likes or 0) + max(0, comments or 0)) / views


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Perzentil mit linearer Interpolation zwischen Nachbar-Rängen —
    dependency-frei (kein numpy, analog der Regression in forecast.py).
    Erwartet eine aufsteigend sortierte, nicht-leere Liste."""
    rank = pct * (len(sorted_values) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_values[lo]
    frac = rank - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def compute_platform_p75(
    session: Session,
    *,
    iso_year: int,
    iso_week: int,
    window_weeks: Optional[int] = None,
    min_sample: Optional[int] = None,
) -> dict[str, dict]:
    """Rollende p75-ER pro Plattform über die letzten ``window_weeks``
    ISO-Wochen (einschließlich der Briefing-Woche selbst — die aktuelle
    Verteilung gehört in die Schwelle, sonst hinkt sie eine Woche nach).

    Rückgabe pro Plattform: ``{"p75": float|None, "sample_size": int}``.
    ``p75 = None`` wenn weniger als ``min_sample`` Posts mit ``views > 0``
    im Rollfenster liegen — die Schwelle ist dann undefiniert und die
    Plattform geht in den ehrlichen Leerlauf (kein geratener Wert).

    KW-Zuordnung über ``published_at`` mit Fallback ``detected_at``,
    exakt wie ``compute_market_timeline`` — gleiche Population, gleiche
    Wochen-Semantik.
    """
    window_weeks = window_weeks or settings.cutter_weekly_p75_window_weeks
    min_sample = min_sample or settings.cutter_weekly_p75_min_sample

    week_start, week_end = week_bounds(iso_year, iso_week)
    span_start = week_start - timedelta(weeks=window_weeks - 1)

    rows = session.exec(
        select(
            Post.platform,
            Post.published_at,
            Post.detected_at,
            Post.visible_views,
            Post.visible_likes,
            Post.visible_comments,
        ).where(
            # OR-Fenster analog market_timeline: published_at maßgeblich,
            # detected_at nur als Fallback für published_at IS NULL.
            (
                (Post.published_at.is_not(None))
                & (Post.published_at >= span_start)
                & (Post.published_at < week_end)
            )
            | (
                (Post.published_at.is_(None))
                & (Post.detected_at >= span_start)
                & (Post.detected_at < week_end)
            )
        )
    ).all()

    ers_by_platform: dict[str, list[float]] = {p: [] for p in CUTTER_PLATFORMS}
    for platform, _published_at, _detected_at, views, likes, comments in rows:
        bucket = ers_by_platform.get(platform)
        if bucket is None:
            continue
        er = post_er(views, likes, comments)
        if er is not None:
            bucket.append(er)

    result: dict[str, dict] = {}
    for platform in CUTTER_PLATFORMS:
        ers = sorted(ers_by_platform[platform])
        n = len(ers)
        if n < min_sample:
            result[platform] = {"p75": None, "sample_size": n}
        else:
            result[platform] = {"p75": _percentile(ers, 0.75), "sample_size": n}
    return result


def _candidate_from_ranked_post(
    rp: RankedPost,
    *,
    platform: str,
    source: str,
    fallback_key: str,
    week_start: datetime,
    week_end: datetime,
) -> Optional[CutterEvidencePost]:
    """RankedPost → CutterEvidencePost, oder ``None`` wenn der Post die
    Wochen-/Daten-Anforderungen nicht erfüllt (kein ``published_at``,
    nicht in der ISO-Woche, ``views <= 0``, keine ``post_url``)."""
    if not rp.post_url or rp.published_at is None:
        return None
    published = rp.published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    if not (week_start <= published < week_end):
        return None
    er = post_er(rp.views, rp.likes, rp.comments)
    if er is None:
        return None
    return CutterEvidencePost(
        post_url=rp.post_url,
        platform=platform,
        er=er,
        views=rp.views,
        likes=rp.likes,
        comments=rp.comments,
        engagement_sum=rp.engagement_sum,
        duration_seconds=rp.duration_seconds,
        caption_excerpt=rp.caption_excerpt,
        published_at=rp.published_at,
        distinct_key=rp.title_original or fallback_key,
        source=source,
        title_original=rp.title_original,
    )


def collect_week_posts(
    session: Session, iso_year: int, iso_week: int
) -> tuple[dict[str, list[CutterEvidencePost]], CutterWeeklySources]:
    """Alle Beleg-Kandidaten der ISO-Woche aus den persistierten Blobs,
    gebündelt pro Plattform. Dedup über ``post_url`` (Pair- und
    Segment-Pools sind per Disjunktheits-Vertrag getrennt — der Dedup ist
    defensiv). Nicht-validierende Blobs werden geskippt und in
    ``sources.unreadable_rows`` ausgewiesen, nie fatal.
    """
    week_start, week_end = week_bounds(iso_year, iso_week)
    by_platform: dict[str, list[CutterEvidencePost]] = {
        p: [] for p in CUTTER_PLATFORMS
    }
    sources = CutterWeeklySources()
    seen_urls: set[str] = set()

    def _add(candidate: Optional[CutterEvidencePost]) -> None:
        if candidate is None or candidate.platform not in by_platform:
            return
        if candidate.post_url in seen_urls:
            return
        seen_urls.add(candidate.post_url)
        by_platform[candidate.platform].append(candidate)

    # 1) Pair-Briefs: per_platform-Slices der PairAggregation. Die
    #    Legacy-Top-Level-Channels spiegeln per_platform[0] und werden
    #    bewusst NICHT zusätzlich gelesen (wäre Doppelzählung).
    pair_rows = session.exec(
        select(InsightReportRow)
        .where(InsightReportRow.iso_year == iso_year)
        .where(InsightReportRow.iso_week == iso_week)
    ).all()
    for row in sorted(pair_rows, key=lambda r: r.pair_key):
        try:
            agg = PairAggregation.model_validate(row.aggregation)
        except ValueError as exc:
            sources.unreadable_rows.append(f"pair:{row.pair_key}")
            logger.warning(
                "cutter-weekly-blob-unreadable",
                extra={
                    "source": f"pair:{row.pair_key}",
                    "iso_year": iso_year,
                    "iso_week": iso_week,
                    "error_message": str(exc)[:200],
                },
            )
            continue
        sources.pair_briefs.append(row.pair_key)
        for platform_agg in agg.per_platform or []:
            for channel in (
                platform_agg.de_channel,
                platform_agg.us_channel,
                platform_agg.uk_channel,
            ):
                if channel is None:
                    continue
                for rp in channel.ranked_posts:
                    _add(
                        _candidate_from_ranked_post(
                            rp,
                            platform=platform_agg.platform,
                            source=f"pair:{row.pair_key}",
                            fallback_key=f"pair:{row.pair_key}",
                            week_start=week_start,
                            week_end=week_end,
                        )
                    )

    # 2) Segment-Roundups: per-Channel-Stats tragen ``platform`` selbst.
    roundup_rows = session.exec(
        select(SegmentRoundupRow)
        .where(SegmentRoundupRow.iso_year == iso_year)
        .where(SegmentRoundupRow.iso_week == iso_week)
    ).all()
    for row in sorted(roundup_rows, key=lambda r: str(r.segment)):
        segment_value = getattr(row.segment, "value", str(row.segment))
        try:
            agg = SegmentAggregation.model_validate(row.channels_aggregation)
        except ValueError as exc:
            sources.unreadable_rows.append(f"segment:{segment_value}")
            logger.warning(
                "cutter-weekly-blob-unreadable",
                extra={
                    "source": f"segment:{segment_value}",
                    "iso_year": iso_year,
                    "iso_week": iso_week,
                    "error_message": str(exc)[:200],
                },
            )
            continue
        sources.segment_roundups.append(segment_value)
        for ch in agg.channels:
            for rp in ch.top_posts:
                _add(
                    _candidate_from_ranked_post(
                        rp,
                        platform=ch.platform,
                        source=f"segment:{segment_value}",
                        fallback_key=f"segment:{segment_value}:{ch.handle}",
                        week_start=week_start,
                        week_end=week_end,
                    )
                )

    return by_platform, sources


def check_platform_pattern(
    platform: str,
    candidates: list[CutterEvidencePost],
    threshold: dict,
    *,
    min_posts: int,
    min_distinct: int,
) -> CutterPlatformEvidence:
    """Die Evidenzschwelle — das Herz des Sprints. Gibt das vollständige
    Prüfergebnis zurück, auch im Verwerfungs-Fall mit Grund (der
    Evidence-Blob ist das Kalibrierungs-Produkt)."""
    p75 = threshold.get("p75")
    sample_size = int(threshold.get("sample_size", 0))

    if p75 is None:
        return CutterPlatformEvidence(
            platform=platform,
            status="no_threshold",
            reason=(
                f"p75 nicht definiert: nur {sample_size} Posts mit views>0 "
                f"im Rollfenster (Mindest-n {settings.cutter_weekly_p75_min_sample})."
            ),
            p75_sample_size=sample_size,
            week_posts_total=len(candidates),
        )

    above = sorted(
        (c for c in candidates if c.er >= p75),
        key=lambda c: (-c.er, c.post_url),
    )
    distinct_keys = sorted({c.distinct_key for c in above})

    if len(above) >= min_posts and len(distinct_keys) >= min_distinct:
        return CutterPlatformEvidence(
            platform=platform,
            status="pattern_released",
            p75_er=p75,
            p75_sample_size=sample_size,
            week_posts_total=len(candidates),
            candidates_above_p75=len(above),
            distinct_keys=distinct_keys,
            supporting_posts=above,
        )

    return CutterPlatformEvidence(
        platform=platform,
        status="no_pattern",
        reason=(
            f"{len(above)} Post(s) mit ER >= p75 ({p75:.4f}) über "
            f"{len(distinct_keys)} Distinct-Key(s) — Schwelle verlangt "
            f">= {min_posts} Posts über >= {min_distinct} Keys."
        ),
        p75_er=p75,
        p75_sample_size=sample_size,
        week_posts_total=len(candidates),
        candidates_above_p75=len(above),
        distinct_keys=distinct_keys,
        # Verworfene Kandidaten bleiben sichtbar — Kalibrierung will sehen,
        # WAS knapp unter der Schwelle lag, nicht nur dass es verworfen wurde.
        supporting_posts=above,
    )


def build_weekly_evidence(
    session: Session,
    *,
    now: Optional[datetime] = None,
    min_posts: Optional[int] = None,
    min_distinct: Optional[int] = None,
    p75_window_weeks: Optional[int] = None,
    p75_min_sample: Optional[int] = None,
) -> CutterWeeklyEvidence:
    """End-to-End der deterministischen Schicht: Blobs der ISO-Woche von
    ``now`` lesen, rollende p75 berechnen, pro Plattform prüfen. Läuft
    komplett ohne LLM — Commit B setzt auf dem Rückgabe-Objekt auf.

    ``now`` injectable wie in der Pair-/Roundup-Pipeline; der Cron
    übergibt seinen ``brief_now``-Anker (utcnow - 1 Tag), damit dieselbe
    gerade abgeschlossene KW gelesen wird, für die Briefs/Roundups im
    selben Lauf generiert wurden.
    """
    now = now or datetime.now(timezone.utc)
    iso = now.isocalendar()
    iso_year, iso_week = iso.year, iso.week

    params = CutterWeeklyParams(
        min_posts=min_posts or settings.cutter_weekly_min_posts,
        min_distinct_keys=min_distinct or settings.cutter_weekly_min_distinct_keys,
        p75_window_weeks=p75_window_weeks or settings.cutter_weekly_p75_window_weeks,
        p75_min_sample=p75_min_sample or settings.cutter_weekly_p75_min_sample,
    )
    week_start, week_end = week_bounds(iso_year, iso_week)

    by_platform, sources = collect_week_posts(session, iso_year, iso_week)
    thresholds = compute_platform_p75(
        session,
        iso_year=iso_year,
        iso_week=iso_week,
        window_weeks=params.p75_window_weeks,
        min_sample=params.p75_min_sample,
    )

    platforms = [
        check_platform_pattern(
            platform,
            by_platform[platform],
            thresholds[platform],
            min_posts=params.min_posts,
            min_distinct=params.min_distinct_keys,
        )
        for platform in CUTTER_PLATFORMS
    ]

    all_candidates = [c for posts in by_platform.values() for c in posts]
    title_key_share: Optional[float] = None
    if all_candidates:
        with_title = sum(1 for c in all_candidates if c.title_original)
        title_key_share = with_title / len(all_candidates)

    evidence = CutterWeeklyEvidence(
        iso_year=iso_year,
        iso_week=iso_week,
        week_start=week_start,
        week_end=week_end,
        params=params,
        sources=sources,
        platforms=platforms,
        week_posts_total=len(all_candidates),
        title_key_share=title_key_share,
    )
    logger.info(
        "cutter-weekly-evidence-built",
        extra={
            "iso_year": iso_year,
            "iso_week": iso_week,
            "week_posts_total": evidence.week_posts_total,
            "released": [p.platform for p in platforms if p.status == "pattern_released"],
            "pair_briefs": len(sources.pair_briefs),
            "segment_roundups": len(sources.segment_roundups),
            "title_key_share": title_key_share,
        },
    )
    return evidence


# ===========================================================================
# Commit B — LLM-Synthese mit Beleg-Validierung (Citation strict)
# ===========================================================================
#
# Arbeitsteilung (Kern-Disziplin des Sprints): ``build_weekly_evidence``
# oben hat entschieden, WAS ein Muster ist. Das LLM bekommt ausschliesslich
# die freigegebenen Muster-Kandidaten (kompakt, nicht die vollen Blobs)
# und formuliert pro freigegebener Plattform einen Block. Leerlauf-
# Plattformen erhalten deterministische Code-Bloecke — das LLM sieht sie
# nicht und kann fuer sie nichts erfinden. Citation strict von Tag 1
# (Wolf-Entscheidung 3): jede zitierte ID muss im Allow-Set der
# stuetzenden Posts liegen, sonst wird die komplette Antwort verworfen
# und einmal neu angefragt; danach ``llm_output=None`` (Evidence bleibt).

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

CUTTER_WEEKLY_SYSTEM_PROMPT = """Du schreibst das woechentliche Cutter-Briefing fuer ein Trailerhaus: eine plattformweise Mustersicht quer ueber alle beobachteten Studios, Verleiher und Independents.

DEINE ROLLE — UND IHRE GRENZE:
Eine Code-Pruefung hat bereits entschieden, welche Plattformen diese Woche ein belegtes Muster haben (Evidenzschwelle: mehrere ueberdurchschnittliche Posts ueber mehrere Titel verteilt). Du bekommst NUR die freigegebenen Plattformen mit ihren Beleg-Posts. Du formulierst, was diese Belege gemeinsam zeigen — du entscheidest NICHT, ob ein Muster existiert, und du erfindest keine Muster fuer Plattformen, die dir nicht vorgelegt wurden.

TON UND HALTUNG:
- Sachlich, beobachtend, in ganzen Saetzen. Schreibe Zahlen aus (33.000, nicht 33k).
- Beschreibe, WAS die Posts gemeinsam haben (Format, Laenge, Aufbau, Titel-Mix) — behaupte NIE, WARUM es funktioniert hat. Keine kausalen Schnitt-Diagnosen ("funktioniert, weil der Hook frueh kommt" ist verboten; "die starken Posts dieser Woche oeffnen alle in den ersten zwei Sekunden mit Footage" ist erlaubt).
- Der optionale schnitt_impuls ist ein vorsichtiger Hinweis zum Hinschauen, keine Anweisung und keine Erfolgsgarantie. Wenn die Belege keinen Impuls decken: null.
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
      "beobachtung": "2-4 Saetze: das verdichtete Muster dieser Woche mit 1-2 konkreten Belegen im Fliesstext (Titel + Kennzahl)",
      "schnitt_impuls": "1-2 Saetze vorsichtiger Impuls oder null",
      "cited_post_ids": ["exakte post_url-Strings aus der Beleg-Liste dieser Plattform"]
    }
  ],
  "quer_muster": "1-3 Saetze oder null",
  "quer_cited_post_ids": ["nur wenn quer_muster gesetzt; Belege aus mindestens zwei Plattformen"],
  "markt_signal_notiz": "1-2 Saetze oder null",
  "data_caveats": ["ehrliche Lautstaerke-Hinweise, z.B. duenne Wochen-Basis"]
}"""


def collect_forecast_signals(session: Session) -> list[CutterForecastSignal]:
    """Markt-Signale der Majors aus dem ER-Forecast (Variante 1):
    nur ``status='ok'``-Maerkte der gegateten Sicht, nur Richtung.
    Per-Pair-Isolation — ein scheiternder Forecast killt nicht das
    Briefing. Im Cron-Kontext laeuft das NACH dem Einordnungs-Warmup,
    d.h. ``generate_er_forecast`` trifft nur Cache (kostet nichts);
    die Regression selbst ist ohnehin LLM-frei.
    """
    # Lazy imports: PAIRS/generate_er_forecast nur hier gebraucht; haelt
    # den Modul-Load des Evidenz-Pfads (Commit A) frei von der
    # Forecast-/Engine-Kette.
    from app.services.forecast import generate_er_forecast
    from app.services.insight_engine import PAIRS

    signals: list[CutterForecastSignal] = []
    enabled_pairs = sorted(k for k, v in PAIRS.items() if v.get("enabled", False))
    for pair_key in enabled_pairs:
        try:
            result = generate_er_forecast(
                session, pair_key, PAIRS[pair_key], apply_gate=True
            )
        except Exception as exc:  # noqa: BLE001 — per-pair isolation
            logger.warning(
                "cutter-weekly-forecast-signal-failed pair=%s: %s", pair_key, exc
            )
            continue
        for market, market_result in (result.get("markets") or {}).items():
            if market_result.get("status") != "ok":
                continue
            direction = market_result.get("direction")
            if not direction:
                continue
            signals.append(
                CutterForecastSignal(
                    pair_key=pair_key,
                    market=market,
                    direction=direction,
                    n_points=int(market_result.get("n_points", 0)),
                )
            )
    return signals


def _released_platforms(evidence: CutterWeeklyEvidence) -> list[CutterPlatformEvidence]:
    return [p for p in evidence.platforms if p.status == "pattern_released"]


def _build_allow_sets(evidence: CutterWeeklyEvidence) -> dict[str, set[str]]:
    """Citation-Allow-Set pro FREIGEGEBENER Plattform — exakt die
    post_urls der stuetzenden Posts. Bewusst enger als das Pair-Brief-
    Allow-Set: auch ein real existierender, aber unter-schwelliger Post
    ist hier nicht zitierfaehig."""
    return {
        p.platform: {sp.post_url for sp in p.supporting_posts}
        for p in _released_platforms(evidence)
    }


def _format_evidence_post(idx: int, post: CutterEvidencePost) -> str:
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
    evidence: CutterWeeklyEvidence,
    signals: list[CutterForecastSignal],
) -> str:
    released = _released_platforms(evidence)
    sections: list[str] = [
        (
            f"# Cutter-Wochenbriefing KW {evidence.iso_week}/{evidence.iso_year}\n\n"
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


def _leerlauf_block(p: CutterPlatformEvidence) -> CutterPlatformBlock:
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
    return CutterPlatformBlock(
        platform=p.platform,
        beobachtung=beobachtung,
        schnitt_impuls=None,
        cited_post_ids=[],
        generated_by="code",
    )


def _validate_llm_report(
    report: CutterWeeklyLLMReport,
    evidence: CutterWeeklyEvidence,
    signals: list[CutterForecastSignal],
) -> list[str]:
    """Strict-Validierung der LLM-Antwort gegen die Code-Pruefung.
    Rueckgabe: Liste der Verstoesse (leer = belegt). Jeder Verstoss
    verwirft die GESAMTE Antwort — Citation strict von Tag 1."""
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
    evidence: CutterWeeklyEvidence,
    llm_report: Optional[CutterWeeklyLLMReport],
) -> CutterWeeklyLLMReport:
    """Finaler Report in fester Plattform-Reihenfolge: freigegebene
    Plattformen tragen den validierten LLM-Block (``generated_by='llm'``),
    Leerlauf-Plattformen den deterministischen Code-Block. Die
    Asymmetrie-Caveat zum Forecast-Signal stempelt der Code — sie haengt
    nicht von der Disziplin des Modells ab."""
    llm_blocks: dict[str, CutterPlatformBlock] = {}
    if llm_report is not None:
        for b in llm_report.bloecke:
            llm_blocks[b.platform] = b.model_copy(update={"generated_by": "llm"})

    blocks: list[CutterPlatformBlock] = []
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

    return CutterWeeklyLLMReport(
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


def generate_cutter_weekly(
    session: Session,
    *,
    now: Optional[datetime] = None,
    model: Optional[str] = None,
    max_tokens: int = 4000,
) -> CutterWeeklyReport:
    """End-to-End-Generierung des Cutter-Wochenbriefings (ohne Persistenz —
    die kommt in Commit C dazu).

    Ablauf:
    1. ``build_weekly_evidence`` — deterministische Pruefung (Commit A).
    2. Forecast-Signale der Majors einsammeln (beobachtend, gratis im
       Cron-Kontext nach dem Einordnungs-Warmup).
    3. Keine Plattform freigegeben → KEIN LLM-Call (``model='none'``),
       der Report besteht aus deterministischen Leerlauf-Bloecken.
       Ehrlicher Leerlauf kostet nichts.
    4. Sonst genau ein Opus-Call (Wolf-Entscheidung 4) mit kompaktem
       Kandidaten-Prompt; bis zu ein frischer Wiederholungs-Anlauf bei
       Schema-/Citation-Fail. Jeder bezahlte Call landet einzeln im
       costlog (``operation='cutter_weekly'``, F0.7-Cap).
    5. Total-Fail → ``llm_output=None`` + ``raw_llm_text`` — der
       Evidence-Blob bleibt vollstaendig (Kalibrierungs-Produkt).
    """
    # Lazy imports analog collect_forecast_signals (kein Engine-Load im
    # reinen Evidenz-Pfad, keine Import-Zyklen Richtung insight_engine).
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
            "cutter-weekly-no-pattern-week",
            extra={"iso_year": evidence.iso_year, "iso_week": evidence.iso_week},
        )
        return CutterWeeklyReport(
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
            "ANTHROPIC_API_KEY ist nicht gesetzt — Cutter-Wochenbriefing "
            "kann nicht generieren."
        )

    user_prompt = _build_user_prompt(evidence, signals)
    log_extra = {"iso_year": evidence.iso_year, "iso_week": evidence.iso_week}

    llm_output: Optional[CutterWeeklyLLMReport] = None
    raw_for_response: Optional[str] = None
    input_tokens_total = 0
    output_tokens_total = 0

    for attempt in range(_MAX_LLM_ATTEMPTS):
        retry_result = call_with_json_retry(
            model=model,
            system=CUTTER_WEEKLY_SYSTEM_PROMPT,
            user_message=user_prompt,
            max_tokens=max_tokens,
            max_recalls=1,
            log_prefix="cutter-weekly",
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
                operation="cutter_weekly",
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
                "cutter-weekly-json-parse-failed",
                extra={
                    **log_extra,
                    "outer_attempt": attempt,
                    "raw_response_first_500": last_raw_text[:500],
                },
            )
            continue

        candidate = _unwrap_single_key(retry_result.parsed, expected_field="bloecke")
        try:
            parsed_report = CutterWeeklyLLMReport.model_validate(candidate)
        except ValueError as exc:
            raw_for_response = last_raw_text
            logger.error(
                "cutter-weekly-schema-validation-failed",
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
                "cutter-weekly-citation-rejected",
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
            "cutter-weekly-llm-ok",
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

    return CutterWeeklyReport(
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
