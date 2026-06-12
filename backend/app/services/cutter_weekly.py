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
    CutterPlatformEvidence,
    CutterWeeklyEvidence,
    CutterWeeklyParams,
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
