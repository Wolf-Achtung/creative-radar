"""Title-centric aggregation — the counter-perspective to the channel/pair
brief in ``insight_engine``.

``aggregate_title`` rolls ONE title up across all channels, platforms, markets
and pairs it appeared in, using the *same* Kennzahl logic as the channel path
(``_engagement_sum`` = likes+comments+shares+saves, ``compute_activation_rate``
= (likes+comments+saves)/views, YT without saves). It is the data block a later
title-brief would hand the LLM.

Read-only: pure aggregation, no persistence, no LLM, no schema change. The
channel-aggregation code in ``insight_engine`` is reused, not modified.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.entities import Asset, Channel, Post, Title
from app.services.insight_engine import (
    PAIRS,
    _engagement_sum,
    compute_activation_rate,
)


def _ref_date(post: Post) -> Optional[datetime]:
    """Same recency fallback the channel window-filter uses: published_at
    if present, else detected_at. Naive datetimes (SQLite test rows) are
    treated as UTC so window/min/max comparisons never mix naive+aware."""
    raw = post.published_at or post.detected_at
    if raw is not None and raw.tzinfo is None:
        return raw.replace(tzinfo=timezone.utc)
    return raw


def _market_str(channel: Channel) -> Optional[str]:
    """``channel.market`` is a Market ENUM — surface its plain string value
    (the SQL equivalent casts ``market::text``)."""
    m = getattr(channel, "market", None)
    if m is None:
        return None
    return str(getattr(m, "value", m))


def _excerpt(text: Optional[str], limit: int = 140) -> Optional[str]:
    if not text:
        return None
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _handle_to_pairs() -> dict[str, set[str]]:
    """Reverse-index ``PAIRS``: lowercased channel handle -> set of pair_keys.
    Drives the cross-pair-visibility field (the core value of the title view:
    which studio pairs a title surfaced under)."""
    idx: dict[str, set[str]] = {}
    for pair_key, cfg in PAIRS.items():
        specs: list[dict] = []
        for platform_specs in (cfg.get("platforms") or {}).values():
            specs.extend(platform_specs or [])
        specs.extend(cfg.get("channels") or [])
        for spec in specs:
            handle = (spec.get("handle") or "").strip().lower()
            if handle:
                idx.setdefault(handle, set()).add(pair_key)
    return idx


# --------------------------------------------------------------------------
# Output dataclasses (plain data — no Text, no persistence).
# --------------------------------------------------------------------------


@dataclass
class TitlePostRef:
    post_url: Optional[str]
    platform: Optional[str]
    market: Optional[str]
    channel_handle: Optional[str]
    channel_name: Optional[str]
    pair_keys: list[str]
    engagement_sum: int
    likes: Optional[int]
    comments: Optional[int]
    shares: Optional[int]
    saves: Optional[int]
    views: Optional[int]
    activation_rate: float
    duration_seconds: Optional[int]
    published_at: Optional[datetime]
    detected_at: Optional[datetime]
    caption_excerpt: Optional[str]


@dataclass
class TitlePlatformStats:
    platform: str
    post_count: int
    engagement_sum: int
    engagement_avg: float
    views_sum: int
    views_avg: float
    activation_rate_avg: float
    top_post: Optional[TitlePostRef]


@dataclass
class TitleMarketStats:
    market: str
    post_count: int
    engagement_sum: int
    engagement_avg: float
    views_sum: int
    views_avg: float
    activation_rate_avg: float


@dataclass
class TitleChannelRef:
    channel_handle: Optional[str]
    channel_name: Optional[str]
    platform: Optional[str]
    market: Optional[str]
    pair_keys: list[str]
    post_count: int
    engagement_sum: int


@dataclass
class TitleWeekBucket:
    iso_year: int
    iso_week: int
    post_count: int
    engagement_sum: int


@dataclass
class TitleAggregation:
    # Stammdaten
    title_id: UUID
    title_original: str
    title_local: Optional[str]
    content_type: Optional[str]
    franchise: Optional[str]
    tmdb_id: Optional[int]
    aliases: list[str]
    release_date_de: Optional[Any]
    release_date_us: Optional[Any]
    # Window
    window_days: int
    window_start: datetime
    window_end: datetime
    # Full data span (ALL posts of the title, ignoring window) — lets the
    # caller see whether the 30-day default is too short for the campaign.
    first_post_at: Optional[datetime]
    last_post_at: Optional[datetime]
    total_posts_all_time: int
    # Windowed totals
    total_posts: int
    total_engagement: int
    total_views: int
    activation_rate_avg: float
    # Breakdowns
    platforms: list[TitlePlatformStats]
    markets: list[TitleMarketStats]
    channels: list[TitleChannelRef]
    pair_keys: list[str]
    top_posts: list[TitlePostRef]
    weekly: list[TitleWeekBucket]


class AmbiguousTitleError(Exception):
    """Raised when a title NAME resolves to more than one Title (e.g. a film
    and its same-named context, or a sequel sharing a substring). Carries the
    candidate list so the caller can disambiguate by title_id or tmdb_id —
    never a silent ``.first()`` guess."""
    def __init__(self, candidates: list[dict]):
        self.candidates = candidates
        super().__init__(f"ambiguous title name — {len(candidates)} candidates")


def _title_candidates(titles) -> list[dict]:
    return [
        {"title_id": str(t.id), "title_original": t.title_original, "tmdb_id": t.tmdb_id}
        for t in titles
    ]


def _resolve_title(session: Session, title_ref: Union[str, UUID, Title]) -> Optional[Title]:
    """Resolve a title reference to exactly ONE Title — deterministically.

    Order: Title passthrough -> UUID (id) -> UUID-string (id) -> exact
    case-insensitive name -> single substring match. A name that matches
    multiple titles (multiple exact, or no-exact-but-multiple-substring) raises
    ``AmbiguousTitleError`` with the candidates instead of guessing the
    alphabetical first. No match -> None (unchanged).
    """
    if isinstance(title_ref, Title):
        return title_ref
    if isinstance(title_ref, UUID):
        return session.get(Title, title_ref)
    # string: try UUID first
    try:
        return session.get(Title, UUID(str(title_ref)))
    except (ValueError, AttributeError):
        pass
    name = str(title_ref).strip()
    # (a) exact, case-insensitive
    exact = list(session.exec(
        select(Title).where(func.lower(Title.title_original) == name.lower())
    ).all())
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        # (b) multiple exact -> ambiguous, never guess
        raise AmbiguousTitleError(_title_candidates(exact))
    # (c) no exact: substring
    subs = list(session.exec(
        select(Title).where(Title.title_original.ilike(f"%{name}%")).order_by(Title.title_original)
    ).all())
    if len(subs) == 1:
        return subs[0]
    if len(subs) > 1:
        # (b) multiple substring, no exact -> ambiguous
        raise AmbiguousTitleError(_title_candidates(subs))
    # (d) no match
    return None


def aggregate_title(
    session: Session,
    title_ref: Union[str, UUID, Title],
    *,
    window_days: int = 30,
    now: Optional[datetime] = None,
    top_n: int = 10,
) -> Optional[TitleAggregation]:
    """Aggregate one title across all channels/platforms/markets/pairs.

    ``title_ref`` may be a ``Title``, a UUID, a UUID-string, or a title name
    (case-insensitive exact, then substring). Returns ``None`` if no title
    matches. Read-only.
    """
    title = _resolve_title(session, title_ref)
    if title is None:
        return None

    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(days=window_days)

    # One title can sit on several assets of the same post — dedupe by post.id.
    rows = session.exec(
        select(Post, Channel)
        .join(Asset, Asset.post_id == Post.id)
        .join(Channel, Channel.id == Post.channel_id)
        .where(Asset.title_id == title.id)
    ).all()
    post_channel: dict[Any, tuple[Post, Channel]] = {}
    for post, channel in rows:
        post_channel.setdefault(post.id, (post, channel))

    all_pairs = _handle_to_pairs()

    # Full span over ALL posts (no window) — the window-default sanity check.
    all_refs = [r for r in (_ref_date(p) for p, _ in post_channel.values()) if r is not None]
    first_post_at = min(all_refs) if all_refs else None
    last_post_at = max(all_refs) if all_refs else None

    def _pairs_for(channel: Channel) -> list[str]:
        handle = (getattr(channel, "handle", None) or "").strip().lower()
        return sorted(all_pairs.get(handle, set()))

    # Windowed working set.
    windowed: list[tuple[Post, Channel]] = []
    for post, channel in post_channel.values():
        ref = _ref_date(post)
        if ref is not None and ref >= window_start:
            windowed.append((post, channel))

    def _ref(post: Post, channel: Channel) -> TitlePostRef:
        eng = _engagement_sum(post)
        return TitlePostRef(
            post_url=post.post_url,
            platform=channel.platform,
            market=_market_str(channel),
            channel_handle=getattr(channel, "handle", None),
            channel_name=getattr(channel, "name", None),
            pair_keys=_pairs_for(channel),
            engagement_sum=eng,
            likes=post.visible_likes,
            comments=post.visible_comments,
            shares=post.visible_shares,
            saves=post.visible_bookmarks,
            views=post.visible_views,
            activation_rate=round(compute_activation_rate(post, channel.platform), 4),
            duration_seconds=post.duration_seconds,
            published_at=post.published_at,
            detected_at=post.detected_at,
            caption_excerpt=_excerpt(post.caption),
        )

    refs = [_ref(p, c) for p, c in windowed]

    # ---- per platform ----
    platforms: list[TitlePlatformStats] = []
    by_platform: dict[str, list[TitlePostRef]] = {}
    for r in refs:
        by_platform.setdefault(r.platform or "unknown", []).append(r)
    for platform, group in sorted(by_platform.items(), key=lambda kv: -sum(x.engagement_sum for x in kv[1])):
        eng_sum = sum(x.engagement_sum for x in group)
        views_sum = sum(int(x.views or 0) for x in group)
        n = len(group)
        platforms.append(TitlePlatformStats(
            platform=platform,
            post_count=n,
            engagement_sum=eng_sum,
            engagement_avg=round(eng_sum / n, 1) if n else 0.0,
            views_sum=views_sum,
            views_avg=round(views_sum / n, 1) if n else 0.0,
            activation_rate_avg=round(sum(x.activation_rate for x in group) / n, 4) if n else 0.0,
            top_post=max(group, key=lambda x: x.engagement_sum) if group else None,
        ))

    # ---- per market ----
    markets: list[TitleMarketStats] = []
    by_market: dict[str, list[TitlePostRef]] = {}
    for r in refs:
        by_market.setdefault(r.market or "UNKNOWN", []).append(r)
    for market, group in sorted(by_market.items(), key=lambda kv: -sum(x.engagement_sum for x in kv[1])):
        eng_sum = sum(x.engagement_sum for x in group)
        views_sum = sum(int(x.views or 0) for x in group)
        n = len(group)
        markets.append(TitleMarketStats(
            market=market,
            post_count=n,
            engagement_sum=eng_sum,
            engagement_avg=round(eng_sum / n, 1) if n else 0.0,
            views_sum=views_sum,
            views_avg=round(views_sum / n, 1) if n else 0.0,
            activation_rate_avg=round(sum(x.activation_rate for x in group) / n, 4) if n else 0.0,
        ))

    # ---- per channel ----
    channels: list[TitleChannelRef] = []
    by_channel: dict[Any, list[tuple[Post, Channel]]] = {}
    for post, channel in windowed:
        by_channel.setdefault(channel.id, []).append((post, channel))
    for _, group in by_channel.items():
        ch = group[0][1]
        eng_sum = sum(_engagement_sum(p) for p, _ in group)
        channels.append(TitleChannelRef(
            channel_handle=getattr(ch, "handle", None),
            channel_name=getattr(ch, "name", None),
            platform=ch.platform,
            market=_market_str(ch),
            pair_keys=_pairs_for(ch),
            post_count=len(group),
            engagement_sum=eng_sum,
        ))
    channels.sort(key=lambda c: -c.engagement_sum)

    # ---- weekly buckets ----
    weekly_map: dict[tuple[int, int], list[TitlePostRef]] = {}
    for r in refs:
        ref_dt = r.published_at or r.detected_at
        if ref_dt is None:
            continue
        iso = ref_dt.isocalendar()
        weekly_map.setdefault((iso.year, iso.week), []).append(r)
    weekly = [
        TitleWeekBucket(
            iso_year=y, iso_week=w,
            post_count=len(g),
            engagement_sum=sum(x.engagement_sum for x in g),
        )
        for (y, w), g in sorted(weekly_map.items())
    ]

    # ---- top posts overall ----
    top_posts = sorted(refs, key=lambda x: x.engagement_sum, reverse=True)[: max(0, top_n)]

    total_engagement = sum(r.engagement_sum for r in refs)
    total_views = sum(int(r.views or 0) for r in refs)
    activation_avg = round(sum(r.activation_rate for r in refs) / len(refs), 4) if refs else 0.0
    pair_keys = sorted({pk for c in channels for pk in c.pair_keys})

    return TitleAggregation(
        title_id=title.id,
        title_original=title.title_original,
        title_local=title.title_local,
        content_type=getattr(title, "content_type", None),
        franchise=title.franchise,
        tmdb_id=title.tmdb_id,
        aliases=list(title.aliases or []),
        release_date_de=title.release_date_de,
        release_date_us=title.release_date_us,
        window_days=window_days,
        window_start=window_start,
        window_end=now,
        first_post_at=first_post_at,
        last_post_at=last_post_at,
        total_posts_all_time=len(post_channel),
        total_posts=len(refs),
        total_engagement=total_engagement,
        total_views=total_views,
        activation_rate_avg=activation_avg,
        platforms=platforms,
        markets=markets,
        channels=channels,
        pair_keys=pair_keys,
        top_posts=top_posts,
        weekly=weekly,
    )
