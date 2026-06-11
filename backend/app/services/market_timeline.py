"""V3 Sprint 6/7 — shared per-market weekly timeline computation.

Variante A: metrics are computed FRESH per discrete ISO week from the Post
tables (NOT from the 30-day-rolling persisted brief aggregates). The
``insight_report`` table only supplies the week axis (which ISO weeks the pair
has a brief for). Extracted from the ``GET /api/insights/timeline`` endpoint so
the timeline endpoint AND the Sprint-7 forecast endpoint share one source of
truth — the timeline display behaviour is unchanged by the extraction.

The ER definition mirrors the FilmMarketSummaryBar / title_posts endpoint:
``ER = sum(likes+comments) / sum(views)`` over posts with ``views > 0`` only,
else ``None``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import sqlalchemy as sa
from sqlmodel import Session, select

from app.models.entities import Channel, InsightReport as InsightReportRow, Market, Post
from app.services.insight_engine import _platforms_dict_for

# Feste Spalten-Reihenfolge (wie der title_posts-Endpoint). INT/MIXED/UNKNOWN
# gehören nicht in die DE/US/UK-Sicht.
TIMELINE_MARKET_VALUES: list[str] = [Market.DE.value, Market.US.value, Market.UK.value]


def iso_week_monday(iso_year: int, iso_week: int) -> datetime:
    """Montag 00:00 UTC der gegebenen ISO-Woche."""
    return datetime.fromisocalendar(iso_year, iso_week, 1).replace(tzinfo=timezone.utc)


def gapless_weeks(
    first: tuple[int, int], last: tuple[int, int]
) -> list[tuple[int, int]]:
    """Alle ISO-Wochen von ``first`` bis ``last`` einschließlich, lückenlos —
    fehlende KW (ohne Brief) bleiben als Achsen-Punkt erhalten, werden NICHT
    zusammengeschoben. Iteriert über Montags-Daten, damit Jahresgrenzen
    (KW 52/53 → KW 1) korrekt sind."""
    cur = iso_week_monday(*first)
    end = iso_week_monday(*last)
    weeks: list[tuple[int, int]] = []
    while cur <= end:
        iso = cur.isocalendar()
        weeks.append((iso.year, iso.week))
        cur += timedelta(days=7)
    return weeks


def pair_handles(pair_def: dict) -> list[str]:
    """Alle Channel-Handles eines Pairs über alle Plattformen, lowercased
    für den case-insensitive DB-Lookup. Single source — ``insights._handles_for_pair``
    delegiert hierher."""
    handles: list[str] = []
    for specs in _platforms_dict_for(pair_def).values():
        for spec in specs:
            handle = spec.get("handle")
            if handle:
                handles.append(handle.lower())
    return handles


def compute_market_timeline(
    session: Session,
    pair_key: str,
    pair_def: dict,
    *,
    weeks: Optional[int] = None,
) -> dict:
    """Deskriptive Zeitreihe pro Markt (DE/US/UK). Rückgabe als plain dict, das
    beide Endpoints (Timeline-Anzeige, Forecast) in ihre jeweilige Form gießen::

        {
          "weeks": [(iso_year, iso_week), ...],          # lückenlose Achse
          "markets": {
            "DE": [{"iso_year","iso_week","views","er","posts"}, ...],
            "US": [...], "UK": [...],
          },
        }

    ``er`` ist ``None``, wenn in der Woche kein Post mit ``views > 0`` liegt.
    """
    empty_markets: dict[str, list[dict]] = {m: [] for m in TIMELINE_MARKET_VALUES}

    # 1) Brief-KWs des Pairs aus insight_report (NUR als Wochen-Achsen-Quelle).
    brief_week_rows = session.exec(
        select(InsightReportRow.iso_year, InsightReportRow.iso_week)
        .where(InsightReportRow.pair_key == pair_key)
        .distinct()
    ).all()
    brief_weeks = sorted({(y, w) for y, w in brief_week_rows})
    if not brief_weeks:
        return {"weeks": [], "markets": empty_markets}

    # 2) Lückenlose Achse min..max; optional auf die letzten N Wochen kürzen.
    axis = gapless_weeks(brief_weeks[0], brief_weeks[-1])
    if weeks is not None and weeks < len(axis):
        axis = axis[-weeks:]
    axis_set = set(axis)

    # 3) Channel-Pool des Pairs (alle Plattformen), handle→channel_id→market.
    handles = sorted(set(pair_handles(pair_def)))
    channel_market: dict[UUID, str] = {}
    if handles:
        rows = session.exec(
            select(Channel.id, Channel.market, Channel.handle).where(
                sa.func.lower(Channel.handle).in_(handles)
            )
        ).all()
        for cid, cmarket, _chandle in rows:
            mv = getattr(cmarket, "value", cmarket)
            if mv in TIMELINE_MARKET_VALUES:
                channel_market[cid] = mv

    # 4) Buckets initialisieren: (market, (year, week)) → Akkumulator.
    buckets: dict[tuple[str, tuple[int, int]], dict[str, int]] = {
        (m, wk): {"views": 0, "eng_num": 0, "eng_den": 0, "posts": 0}
        for m in TIMELINE_MARKET_VALUES
        for wk in axis
    }

    # 5) Posts des Pools im Achsen-Zeitraum laden und in Python bucketen —
    #    KW-Zuordnung über published_at (Fallback detected_at), exakt wie
    #    ``title_aggregation`` die Wochen-Buckets baut.
    if channel_market:
        span_start = iso_week_monday(*axis[0])
        span_end = iso_week_monday(*axis[-1]) + timedelta(days=7)  # exklusiv
        post_rows = session.exec(
            select(
                Post.channel_id,
                Post.published_at,
                Post.detected_at,
                Post.visible_views,
                Post.visible_likes,
                Post.visible_comments,
            )
            .where(Post.channel_id.in_(list(channel_market.keys())))
            .where(
                sa.or_(
                    sa.and_(
                        Post.published_at.is_not(None),
                        Post.published_at >= span_start,
                        Post.published_at < span_end,
                    ),
                    sa.and_(
                        Post.published_at.is_(None),
                        Post.detected_at >= span_start,
                        Post.detected_at < span_end,
                    ),
                )
            )
        ).all()
        for cid, published_at, detected_at, views, likes, comments in post_rows:
            market = channel_market.get(cid)
            if market is None:
                continue
            ref_dt = published_at or detected_at
            if ref_dt is None:
                continue
            iso = ref_dt.isocalendar()
            wk = (iso.year, iso.week)
            if wk not in axis_set:
                continue
            acc = buckets[(market, wk)]
            v = views or 0
            acc["views"] += v
            acc["posts"] += 1
            if v > 0:
                # max(0, …): Sentinel-Guard — Apify likesCount=-1 ("Likes
                # verborgen") darf die ER-Summe nicht drücken.
                acc["eng_num"] += max(0, likes or 0) + max(0, comments or 0)
                acc["eng_den"] += v

    # 6) Plain-dict-Ausgabe positionsgleich zur Achse.
    markets_out: dict[str, list[dict]] = {}
    for m in TIMELINE_MARKET_VALUES:
        points: list[dict] = []
        for (y, w) in axis:
            acc = buckets[(m, (y, w))]
            er = acc["eng_num"] / acc["eng_den"] if acc["eng_den"] > 0 else None
            points.append({
                "iso_year": y, "iso_week": w,
                "views": acc["views"], "er": er, "posts": acc["posts"],
            })
        markets_out[m] = points

    return {"weeks": list(axis), "markets": markets_out}
