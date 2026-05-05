"""Insight-Engine MVP — Sprint 1 (warnerbros DE+US TikTok).

Single-shot Opus 4.7 weekly briefing for the Trailerhaus creative team.
Deterministic aggregation lives in this module; the LLM call is one
``messages_create_text`` per report, no agent loop.

The pair definition is hardcoded by design (see ``PAIRS`` below) — generalising
to the other six Tier-A pairs is Sprint-2 work and explicitly out-of-scope for
this MVP. Adding a new pair before then is a config-only change in this file.

Cost expectation: ~5-10k input + ~1.5k output tokens per call. At the public
Opus 4.7 list price (~$15 / $75 per Mtok) that's roughly $0.20-0.40 per
report. The endpoint accepts ``dry_run=true`` to skip the LLM call entirely
when iterating on the aggregation or prompt.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import sqlalchemy as sa
from sqlmodel import Session, select

from app.models.entities import Asset, Channel, Post, Title
from app.schemas.insights import (
    Action,
    ChannelStats,
    CrossMarketInsight,
    CrossMarketMatch,
    HashtagFrequency,
    InsightReport,
    LLMReport,
    PairAggregation,
    TitleCoverage,
    TopPost,
    Trend,
)
from app.services.anthropic_client import (
    AnthropicAPIError,
    AnthropicAuthError,
    is_anthropic_configured,
    messages_create_text,
)

logger = logging.getLogger(__name__)


# ---------- Pair registry ---------------------------------------------------

# Hardcoded for the MVP. Sprint-2 promotes this to a DB-backed config table
# (see roadmap doc) once the 0d-Apify fix gives us reliable IG coverage for
# the other Tier-A pairs. The platform field is locked to TikTok because the
# Sprint-1 success criterion is specifically "TT-Coverage works for warnerbros".
PAIRS: dict[str, dict[str, Any]] = {
    "warnerbros": {
        "label": "warnerbros DE+US",
        "platform": "tiktok",
        "channels": [
            # Production-confirmed handle (channels_perplexity_2026_05_03.csv).
            {"handle": "warnerbros", "market": "US"},
            # DE handle per Wolf brief; aliasing handled by the case-insensitive
            # lookup. If the actual stored handle differs, ``aggregate_pair``
            # records that in ``notes`` rather than failing.
            {"handle": "warnerbrosdeutschland", "market": "DE"},
        ],
    },
}


# ---------- Model + cost ----------------------------------------------------

# Opus 4.7 alias — the briefing pins the engine to the latest Opus. Override
# via ENV (see config.Settings.anthropic_*_model pattern) if Wolf wants to
# force a datestamped pin, but no override is wired today: one Opus, one call.
OPUS_MODEL_ALIAS = "claude-opus-4-7"

# Public list price as of 2026-05 — Opus 4.7: $15/Mtok input, $75/Mtok output.
# Used only for the cost-estimate field in the response (informational, not
# enforced). Update when Anthropic publishes new pricing.
_OPUS_INPUT_PER_1K_USD = 0.015
_OPUS_OUTPUT_PER_1K_USD = 0.075


# ---------- System prompt ---------------------------------------------------

# The system prompt is the persona + output contract. The actual data is in
# the user message (``_build_user_prompt``) so the persona can be cached
# server-side once Anthropic's prompt-caching is wired in Sprint-2.
SYSTEM_PROMPT = """\
Du bist ein Senior-Trailer-Marketing-Stratege bei Trailerhaus, einem deutschen \
Kino-Trailer-Produktionsstudio. Du analysierst Social-Media-Daten von Filmverleihen, \
um konkrete kreative TODOs für Schneide- und Hook-Entscheidungen abzuleiten. Du \
sprichst die Sprache von Trailer-Producern: direkt, fachlich, ohne Marketing-\
Bullshit. Du gibst KEINE Allgemeinplätze ("Engagement ist wichtig") und KEINE \
Hashtag-Listen ohne Kontext, sondern handfeste Beobachtungen mit Daten-Anker \
(Zahl, Asset-URL oder konkretes Beispiel aus dem Datenpaket).

Dein Output ist AUSSCHLIESSLICH ein JSON-Objekt nach folgendem Schema. \
Kein Vorspann, kein Markdown-Codefence, keine Erklärung — nur das JSON:

{
  "headline": "Eine Zeile, provokant, max. 90 Zeichen",
  "tldr": "3 Sätze: was ist diese Woche bei Warner anders, was sollte Trailerhaus daraus lernen, wo ist die Wette",
  "trends": [
    { "name": "...", "evidence": "konkrete Zahl oder Asset-Bezug aus den Daten", "implication_for_creation": "was Trailerhaus konkret in der Schnittarbeit ändern sollte" }
  ],
  "actions": [
    { "what": "konkrete Handlung", "why": "Beleg aus den Daten", "for_whom": "z.B. Cutter, Creative Producer, Hook-Designer" }
  ],
  "cross_market_insight": {
    "de_vs_us": "Was unterscheidet die Märkte diese Woche, mit Daten-Anker",
    "transfer_opportunity": "Was sollte aus US für DE adaptiert werden oder umgekehrt"
  },
  "risks": [ "..." ],
  "data_caveats": [ "..." ]
}

Wenn die Datengrundlage zu dünn ist (Coverage <30%, weniger als 5 Posts pro Markt, \
oder keine Cross-Market-Matches), sage das klar im Feld data_caveats und schlage \
NICHT vor, was du nicht aus den Daten ableiten kannst. Lieber 1 starker Trend mit \
Beleg als 5 Trends ohne Daten-Anker.\
"""


# ---------- Hashtag extraction ---------------------------------------------

# Unicode-aware so German Umlauts and emoji-adjacent tags work. We strip the
# leading hash and lowercase to make the frequency count case-insensitive.
_HASHTAG_RE = re.compile(r"#([\w\d_]+)", re.UNICODE)


def _extract_hashtags(caption: Optional[str], raw_payload: Optional[dict]) -> list[str]:
    """Hashtags first from ``raw_payload['hashtags']`` (TikTok actor field),
    fall back to a regex over the caption. Returns lowercase tags without
    the leading ``#``.

    The Apify TikTok actor stores hashtags as ``[{"name": "trailer", ...}]``
    or sometimes a flat string list, depending on the actor version. The
    caption-regex fallback handles both older posts and posts where the
    actor missed the hashtag-block extraction.
    """
    tags: list[str] = []
    if isinstance(raw_payload, dict):
        rh = raw_payload.get("hashtags")
        if isinstance(rh, list):
            for h in rh:
                if isinstance(h, dict):
                    name = h.get("name") or h.get("title")
                    if name:
                        tags.append(str(name).lstrip("#").lower())
                elif isinstance(h, str):
                    tags.append(h.lstrip("#").lower())
    if not tags and caption:
        tags = [m.lower() for m in _HASHTAG_RE.findall(caption)]
    return tags


# ---------- Engagement + bucketing -----------------------------------------


def _engagement_sum(post: Post) -> int:
    return (
        int(post.visible_likes or 0)
        + int(post.visible_comments or 0)
        + int(post.visible_shares or 0)
        + int(post.visible_bookmarks or 0)
    )


def _duration_bucket(d: Optional[int]) -> str:
    if d is None:
        return "unknown"
    if d < 15:
        return "<15s"
    if d < 30:
        return "15-30s"
    if d < 60:
        return "30-60s"
    return ">60s"


def _excerpt(text: Optional[str], max_len: int = 240) -> str:
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= max_len else text[:max_len].rstrip() + "…"


# ---------- Channel resolution ---------------------------------------------


def _find_channel(session: Session, handle: str, platform: str) -> Optional[Channel]:
    """Case-insensitive handle lookup scoped to the requested platform.

    The scraper sometimes stores handles with different casing depending on
    the source (Apify echoes the URL slug verbatim). Lowercase comparison
    keeps the lookup robust without a migration to normalise existing rows.
    """
    stmt = select(Channel).where(
        sa.func.lower(Channel.handle) == handle.lower(),
        Channel.platform == platform,
    )
    return session.exec(stmt).first()


# ---------- Aggregation -----------------------------------------------------


def _channel_stats(
    session: Session,
    channel: Optional[Channel],
    handle: str,
    market: str,
    window_start: datetime,
    window_end: datetime,
    *,
    top_posts_n: int = 3,
    top_hashtags_n: int = 5,
) -> ChannelStats:
    """Build the per-channel slice that goes into both the LLM prompt and
    the response payload.

    ``channel=None`` means the handle wasn't found in the DB — we still
    return a populated stats object (zeroed) so the Frontend can render
    a clear "channel not yet onboarded" caveat instead of crashing.
    """
    if channel is None:
        return ChannelStats(
            handle=handle,
            market=market,
            channel_id=None,
            channel_found=False,
            posts_count=0,
            assets_count=0,
            coverage_pct=0.0,
            top_hashtags=[],
            avg_caption_length=0.0,
            avg_duration_seconds=None,
            duration_buckets={},
            top_posts=[],
            avg_engagement=0.0,
        )

    posts_stmt = (
        select(Post)
        .where(Post.channel_id == channel.id)
        .where(
            sa.or_(
                # Prefer published_at (creator-supplied timestamp) but fall back to
                # detected_at when published_at is NULL — older Apify rows often
                # don't carry a ts.
                sa.and_(Post.published_at.is_not(None), Post.published_at >= window_start, Post.published_at <= window_end),
                sa.and_(Post.published_at.is_(None), Post.detected_at >= window_start, Post.detected_at <= window_end),
            )
        )
    )
    posts: list[Post] = list(session.exec(posts_stmt).all())

    if not posts:
        return ChannelStats(
            handle=handle,
            market=market,
            channel_id=str(channel.id),
            channel_found=True,
            posts_count=0,
            assets_count=0,
            coverage_pct=0.0,
            top_hashtags=[],
            avg_caption_length=0.0,
            avg_duration_seconds=None,
            duration_buckets={},
            top_posts=[],
            avg_engagement=0.0,
        )

    # Asset stats — pulled once, joined in Python (cheap at MVP scale; we're
    # talking dozens of posts per channel per 30d window, not thousands).
    post_ids = [p.id for p in posts]
    assets: list[Asset] = list(
        session.exec(select(Asset).where(Asset.post_id.in_(post_ids))).all()
    )
    assets_by_post: dict[Any, list[Asset]] = defaultdict(list)
    for a in assets:
        assets_by_post[a.post_id].append(a)

    # Hashtag frequency
    tag_counter: Counter[str] = Counter()
    caption_lens: list[int] = []
    for p in posts:
        caption_lens.append(len(p.caption or ""))
        for tag in _extract_hashtags(p.caption, p.raw_payload):
            tag_counter[tag] += 1

    # Duration distribution
    bucket_counter: Counter[str] = Counter()
    durations: list[int] = []
    for p in posts:
        bucket_counter[_duration_bucket(p.duration_seconds)] += 1
        if p.duration_seconds is not None:
            durations.append(int(p.duration_seconds))

    # Engagement + top-N posts
    engagements: list[tuple[Post, int]] = [(p, _engagement_sum(p)) for p in posts]
    engagements.sort(key=lambda item: item[1], reverse=True)
    top_posts: list[TopPost] = []
    for p, eng in engagements[:top_posts_n]:
        primary_asset = assets_by_post.get(p.id, [None])[0] if assets_by_post.get(p.id) else None
        title_text: Optional[str] = None
        asset_type: Optional[str] = None
        if primary_asset is not None:
            asset_type = (
                primary_asset.asset_type.value
                if hasattr(primary_asset.asset_type, "value")
                else str(primary_asset.asset_type)
            )
            if primary_asset.title_id:
                t = session.get(Title, primary_asset.title_id)
                if t:
                    title_text = t.title_original
            if not title_text and primary_asset.placement_title_text:
                title_text = primary_asset.placement_title_text
        top_posts.append(
            TopPost(
                post_url=p.post_url,
                caption_excerpt=_excerpt(p.caption),
                duration_seconds=p.duration_seconds,
                engagement_sum=eng,
                likes=p.visible_likes,
                comments=p.visible_comments,
                shares=p.visible_shares,
                saves=p.visible_bookmarks,
                views=p.visible_views,
                asset_type=asset_type,
                title=title_text,
                published_at=p.published_at,
            )
        )

    # Title-coverage on the asset level (an asset is "covered" if title_id is set).
    assets_with_title = sum(1 for a in assets if a.title_id is not None)
    coverage_pct = (assets_with_title / len(assets) * 100.0) if assets else 0.0

    avg_engagement = sum(eng for _, eng in engagements) / len(engagements) if engagements else 0.0
    avg_caption = sum(caption_lens) / len(caption_lens) if caption_lens else 0.0
    avg_duration = sum(durations) / len(durations) if durations else None

    top_hashtags = [
        HashtagFrequency(tag=tag, count=count)
        for tag, count in tag_counter.most_common(top_hashtags_n)
    ]

    return ChannelStats(
        handle=handle,
        market=market,
        channel_id=str(channel.id),
        channel_found=True,
        posts_count=len(posts),
        assets_count=len(assets),
        coverage_pct=round(coverage_pct, 1),
        top_hashtags=top_hashtags,
        avg_caption_length=round(avg_caption, 1),
        avg_duration_seconds=round(avg_duration, 1) if avg_duration is not None else None,
        duration_buckets=dict(bucket_counter),
        top_posts=top_posts,
        avg_engagement=round(avg_engagement, 1),
    )


def _cross_market_matches(
    session: Session,
    de_channel: Optional[Channel],
    us_channel: Optional[Channel],
    window_start: datetime,
    window_end: datetime,
) -> list[CrossMarketMatch]:
    """Group assets by ``de_us_match_key`` across the two channels.

    The match-key is set by ``services/match_key.py`` during ingest; an
    empty result here is itself a useful signal for the LLM ("no
    cross-market matches in this window").
    """
    if de_channel is None or us_channel is None:
        return []

    de_post_ids_stmt = (
        select(Post.id)
        .where(Post.channel_id == de_channel.id)
        .where(
            sa.or_(
                sa.and_(Post.published_at.is_not(None), Post.published_at >= window_start, Post.published_at <= window_end),
                sa.and_(Post.published_at.is_(None), Post.detected_at >= window_start, Post.detected_at <= window_end),
            )
        )
    )
    us_post_ids_stmt = (
        select(Post.id)
        .where(Post.channel_id == us_channel.id)
        .where(
            sa.or_(
                sa.and_(Post.published_at.is_not(None), Post.published_at >= window_start, Post.published_at <= window_end),
                sa.and_(Post.published_at.is_(None), Post.detected_at >= window_start, Post.detected_at <= window_end),
            )
        )
    )
    de_post_ids = list(session.exec(de_post_ids_stmt).all())
    us_post_ids = list(session.exec(us_post_ids_stmt).all())

    de_assets = list(
        session.exec(
            select(Asset)
            .where(Asset.post_id.in_(de_post_ids))
            .where(Asset.de_us_match_key.is_not(None))
        ).all()
    ) if de_post_ids else []
    us_assets = list(
        session.exec(
            select(Asset)
            .where(Asset.post_id.in_(us_post_ids))
            .where(Asset.de_us_match_key.is_not(None))
        ).all()
    ) if us_post_ids else []

    de_by_key: dict[str, Asset] = {a.de_us_match_key: a for a in de_assets if a.de_us_match_key}
    us_by_key: dict[str, Asset] = {a.de_us_match_key: a for a in us_assets if a.de_us_match_key}
    shared_keys = sorted(set(de_by_key.keys()) & set(us_by_key.keys()))

    matches: list[CrossMarketMatch] = []
    for key in shared_keys:
        de_asset = de_by_key[key]
        us_asset = us_by_key[key]
        de_post = session.get(Post, de_asset.post_id)
        us_post = session.get(Post, us_asset.post_id)
        title_text: Optional[str] = None
        # Prefer the title row, fall back to placement_title_text on either side
        for a in (de_asset, us_asset):
            if a.title_id:
                t = session.get(Title, a.title_id)
                if t:
                    title_text = t.title_original
                    break
        if not title_text:
            title_text = de_asset.placement_title_text or us_asset.placement_title_text

        matches.append(
            CrossMarketMatch(
                match_key=key,
                title=title_text,
                de_engagement=_engagement_sum(de_post) if de_post else 0,
                us_engagement=_engagement_sum(us_post) if us_post else 0,
                de_duration_seconds=de_post.duration_seconds if de_post else None,
                us_duration_seconds=us_post.duration_seconds if us_post else None,
                de_post_url=de_post.post_url if de_post else None,
                us_post_url=us_post.post_url if us_post else None,
                de_caption_excerpt=_excerpt(de_post.caption) if de_post else None,
                us_caption_excerpt=_excerpt(us_post.caption) if us_post else None,
            )
        )

    # Strongest cross-market signal first
    matches.sort(key=lambda m: m.de_engagement + m.us_engagement, reverse=True)
    return matches


def _title_coverage(
    de_stats: ChannelStats, us_stats: ChannelStats, session: Session,
    de_channel: Optional[Channel], us_channel: Optional[Channel],
    window_start: datetime, window_end: datetime,
) -> TitleCoverage:
    """Compute aggregate coverage + title-overlap across both channels."""
    de_titles: set[str] = set()
    us_titles: set[str] = set()
    de_with_title = 0
    de_total = 0
    us_with_title = 0
    us_total = 0

    for channel, market_titles_set, with_title_holder, total_holder in (
        (de_channel, de_titles, "de_with_title", "de_total"),
        (us_channel, us_titles, "us_with_title", "us_total"),
    ):
        if channel is None:
            continue
        post_ids = list(
            session.exec(
                select(Post.id)
                .where(Post.channel_id == channel.id)
                .where(
                    sa.or_(
                        sa.and_(Post.published_at.is_not(None), Post.published_at >= window_start, Post.published_at <= window_end),
                        sa.and_(Post.published_at.is_(None), Post.detected_at >= window_start, Post.detected_at <= window_end),
                    )
                )
            ).all()
        )
        if not post_ids:
            continue
        assets = list(session.exec(select(Asset).where(Asset.post_id.in_(post_ids))).all())
        for a in assets:
            if channel is de_channel:
                de_total += 1
            else:
                us_total += 1
            if a.title_id is not None:
                if channel is de_channel:
                    de_with_title += 1
                else:
                    us_with_title += 1
                t = session.get(Title, a.title_id)
                if t:
                    market_titles_set.add(t.title_original)

    both = sorted(de_titles & us_titles)
    de_only = sorted(de_titles - us_titles)
    us_only = sorted(us_titles - de_titles)
    total_assets = de_total + us_total
    overall = ((de_with_title + us_with_title) / total_assets * 100.0) if total_assets else 0.0

    return TitleCoverage(
        titles_in_both_markets=both,
        de_only_titles=de_only,
        us_only_titles=us_only,
        de_assets_with_title=de_with_title,
        de_assets_total=de_total,
        us_assets_with_title=us_with_title,
        us_assets_total=us_total,
        overall_coverage_pct=round(overall, 1),
    )


def aggregate_pair(
    session: Session, pair_key: str, window_days: int = 30, *, now: Optional[datetime] = None
) -> PairAggregation:
    """Build the deterministic pair aggregation for a window ending at ``now``.

    Raises ``ValueError`` for unknown pairs — the caller (the API endpoint)
    maps that to a 404.
    """
    if pair_key not in PAIRS:
        raise ValueError(f"Unknown pair: {pair_key!r}")

    pair_def = PAIRS[pair_key]
    now = now or datetime.now(timezone.utc)
    window_end = now
    window_start = now - timedelta(days=window_days)
    iso_year, iso_week, _ = now.isocalendar()

    notes: list[str] = []
    de_spec = next((c for c in pair_def["channels"] if c["market"] == "DE"), None)
    us_spec = next((c for c in pair_def["channels"] if c["market"] == "US"), None)
    de_channel = _find_channel(session, de_spec["handle"], pair_def["platform"]) if de_spec else None
    us_channel = _find_channel(session, us_spec["handle"], pair_def["platform"]) if us_spec else None

    if de_spec and de_channel is None:
        notes.append(
            f"DE-Channel @{de_spec['handle']} (TikTok) wurde nicht in der DB gefunden — "
            "Onboarding/Whitelist-Eintrag prüfen."
        )
    if us_spec and us_channel is None:
        notes.append(
            f"US-Channel @{us_spec['handle']} (TikTok) wurde nicht in der DB gefunden — "
            "Onboarding/Whitelist-Eintrag prüfen."
        )

    de_stats = _channel_stats(session, de_channel, de_spec["handle"] if de_spec else "", "DE", window_start, window_end) if de_spec else None
    us_stats = _channel_stats(session, us_channel, us_spec["handle"] if us_spec else "", "US", window_start, window_end) if us_spec else None
    matches = _cross_market_matches(session, de_channel, us_channel, window_start, window_end)
    coverage = _title_coverage(de_stats, us_stats, session, de_channel, us_channel, window_start, window_end)

    if de_stats and de_stats.posts_count < 5:
        notes.append(f"Datenbasis DE schwach: nur {de_stats.posts_count} Posts in den letzten {window_days} Tagen.")
    if us_stats and us_stats.posts_count < 5:
        notes.append(f"Datenbasis US schwach: nur {us_stats.posts_count} Posts in den letzten {window_days} Tagen.")
    if not matches:
        notes.append("Keine de_us_match_key-Treffer im Fenster — Cross-Market-Insight basiert auf indirekten Signalen.")

    return PairAggregation(
        pair_key=pair_key,
        pair_label=pair_def["label"],
        platform=pair_def["platform"],
        window_days=window_days,
        window_start=window_start,
        window_end=window_end,
        iso_week=iso_week,
        iso_year=iso_year,
        de_channel=de_stats,
        us_channel=us_stats,
        cross_market_matches=matches,
        title_coverage=coverage,
        notes=notes,
    )


# ---------- LLM call --------------------------------------------------------


def _build_user_prompt(agg: PairAggregation) -> str:
    """Compact data dump for the LLM. Keep it tabular so the model can
    reference exact numbers in evidence-fields without hallucinating
    counts. JSON is fine for the tabular parts, prose for the framing."""
    payload = agg.model_dump(mode="json")
    framing = (
        f"Generiere den Wochenreport für {agg.pair_label} (Plattform: {agg.platform.upper()}), "
        f"KW {agg.iso_week}/{agg.iso_year}, Datenfenster {agg.window_days} Tage "
        f"({agg.window_start.date().isoformat()} bis {agg.window_end.date().isoformat()}).\n\n"
        "Datenpaket (JSON):\n"
    )
    return framing + json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _strip_codefence(text: str) -> str:
    """Tolerate a ```json ... ``` wrap if the model adds one despite the
    "no Markdown" instruction. Strict mode is fine but defensive parsing
    avoids one easy way for a single retry to be wasted."""
    t = text.strip()
    if t.startswith("```"):
        # Remove first fence line
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1 :]
        if t.endswith("```"):
            t = t[: -3]
    return t.strip()


def _estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return round(
        (input_tokens / 1000.0) * _OPUS_INPUT_PER_1K_USD
        + (output_tokens / 1000.0) * _OPUS_OUTPUT_PER_1K_USD,
        4,
    )


def generate_weekly_report(
    session: Session,
    pair_key: str,
    *,
    window_days: int = 30,
    dry_run: bool = False,
    model: str = OPUS_MODEL_ALIAS,
    max_tokens: int = 2000,
    now: Optional[datetime] = None,
) -> InsightReport:
    """Build the aggregation, call Opus 4.7 once, return the merged report.

    ``dry_run=True`` returns the aggregation only — no LLM call, no cost.
    Use this for prompt-iteration and the inline-quality-gate before the
    Frontend goes live.
    """
    agg = aggregate_pair(session, pair_key, window_days=window_days, now=now)
    generated_at = datetime.now(timezone.utc)

    if dry_run:
        return InsightReport(
            pair_key=agg.pair_key,
            pair_label=agg.pair_label,
            iso_week=agg.iso_week,
            iso_year=agg.iso_year,
            window_days=window_days,
            coverage_pct=agg.title_coverage.overall_coverage_pct,
            generated_at=generated_at,
            model=model,
            dry_run=True,
            llm_output=None,
            aggregation=agg,
            cost_usd_estimate=0.0,
        )

    if not is_anthropic_configured():
        # Fail loud, not silent: an unconfigured deployment that returns
        # a stub report would cause the caveats banner to lie about
        # what's in front of the producer's eyes.
        raise AnthropicAuthError(
            "ANTHROPIC_API_KEY ist nicht gesetzt — Insight-Engine kann nicht generieren. "
            "Setze den Schlüssel in Railway oder ruf den Endpoint mit ?dry_run=true auf."
        )

    user_prompt = _build_user_prompt(agg)
    logger.info(
        "insight-engine-call",
        extra={
            "pair": pair_key,
            "window_days": window_days,
            "model": model,
            "prompt_chars": len(user_prompt),
        },
    )
    message = messages_create_text(
        model=model,
        system=SYSTEM_PROMPT,
        user_message=user_prompt,
        max_tokens=max_tokens,
    )

    raw_text = ""
    try:
        # Anthropic SDK Message objects expose ``content`` as a list of
        # content blocks; the text block has ``.text``. For a plain
        # text-only response there is exactly one block.
        for block in message.content or []:
            if getattr(block, "type", None) == "text":
                raw_text += getattr(block, "text", "")
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("insight-engine-content-extract-failed: %s", exc)

    cleaned = _strip_codefence(raw_text)
    llm_output: Optional[LLMReport] = None
    raw_for_response: Optional[str] = None
    try:
        parsed = json.loads(cleaned)
        llm_output = LLMReport.model_validate(parsed)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("insight-engine-json-parse-failed: %s", exc)
        raw_for_response = raw_text  # surface to caller, don't swallow

    usage = getattr(message, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cost = _estimate_cost_usd(input_tokens, output_tokens) if (input_tokens or output_tokens) else None

    return InsightReport(
        pair_key=agg.pair_key,
        pair_label=agg.pair_label,
        iso_week=agg.iso_week,
        iso_year=agg.iso_year,
        window_days=window_days,
        coverage_pct=agg.title_coverage.overall_coverage_pct,
        generated_at=generated_at,
        model=model,
        dry_run=False,
        llm_output=llm_output,
        aggregation=agg,
        cost_usd_estimate=cost,
        raw_llm_text=raw_for_response,
    )


__all__ = [
    "PAIRS",
    "OPUS_MODEL_ALIAS",
    "SYSTEM_PROMPT",
    "aggregate_pair",
    "generate_weekly_report",
]
