from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.config import settings
from app.database import DB_POOL_TOTAL_BUDGET, get_session
from app.models.entities import Asset, Channel, Post, Title
from app.schemas.dto import ApifyMonitorRequest, TikTokMonitorRequest
from app.services.apify_connector import (
    is_apify_configured,
    is_tiktok_configured,
    normalize_public_item,
    normalize_tiktok_item,
    run_public_channel_monitor,
    run_tiktok_profile_monitor,
)
from app.services.asset_screenshot_persistence import persist_asset_screenshot_async
from app.services.creative_ai import analyze_creative_text_async
from app.services.title_candidates import create_candidate_from_asset, resolve_open_candidates_for_asset
from app.services.whitelist_matcher import find_best_title_match, is_safe_auto_match

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Block 2.5 — async-refactor concurrency budget.
#
# PR #79 first shipped Block-2 with 5 OpenAI / 10 httpx and a 4-phase
# per-task DB pattern. Production load on 2026-05-06 09:20 UTC blew up
# the Postgres pool: 261 IG items × ~4 short Sessions ≈ ~1k connection
# open/close cycles in a few minutes against the SQLAlchemy default pool
# (size=5, overflow=10). Postgres logged "Connection reset by peer" and
# "unexpected EOF on client connection"; the run hung 2h+ in
# status=running. PR #79 was reverted at 11:38 UTC.
#
# Block 2.5 dials these down so the in-flight DB work stays well under
# the new ``DB_POOL_TOTAL_BUDGET`` (size + overflow). The pool ceiling
# is enforced by a startup-time test — see
# ``test_concurrency_constants_within_pool_budget``. Two channels of
# in-flight DB work (Phase A + Phase C) per task, so the worst case is
# roughly OpenAI_concurrency + httpx_concurrency tasks holding a
# connection at any instant.
#
# - ASSET_CREATION_OPENAI_CONCURRENCY=3 (was 5):
#       conservative for OpenAI rate limit and pool budget; OpenAI is
#       still the slow leg, so 3 in flight gives us a real concurrency
#       win without the pool storm.
# - ASSET_CREATION_HTTPX_CONCURRENCY=5 (was 10):
#       image downloads are fast; 5 in flight is enough to amortise the
#       per-source latency, leaves pool headroom for the request handlers.
ASSET_CREATION_OPENAI_CONCURRENCY = 3
ASSET_CREATION_HTTPX_CONCURRENCY = 5

# Backwards-compat aliases — the PR79 tests imported these names.
DEFAULT_OPENAI_CONCURRENCY = ASSET_CREATION_OPENAI_CONCURRENCY
DEFAULT_HTTPX_CONCURRENCY = ASSET_CREATION_HTTPX_CONCURRENCY


router = APIRouter(prefix="/api/monitor", tags=["monitor"])


def _handle_from_url_or_value(value: str | None) -> str:
    clean = (value or "").strip().rstrip("/")
    if not clean:
        return ""
    if "tiktok.com/@" in clean:
        return clean.split("tiktok.com/@", 1)[1].split("/", 1)[0].lstrip("@")
    if "instagram.com/" in clean:
        return clean.split("instagram.com/", 1)[1].split("/", 1)[0].lstrip("@")
    return clean.lstrip("@")


def _match_channel(channels: list[Channel], owner: str | None, fallback_index: int = 0) -> Channel:
    owner_clean = _handle_from_url_or_value(owner).lower()
    for channel in channels:
        handle = _handle_from_url_or_value(channel.handle or channel.url).lower()
        if handle and owner_clean and handle == owner_clean:
            return channel
    return channels[min(fallback_index, len(channels) - 1)]


# --------------------------------------------------------------------------
# Block 2.5 — async asset-creation pipeline (consolidated to 2 DB sessions).
#
# Per-asset shape after the Block-2.5 consolidation (one task per item):
#
#   Phase A (sync DB via asyncio.to_thread, ONE Session):
#     - existing-check (URL dedupe)
#     - first whitelist title-match
#     - Post insert + commit
#     - title_name read (Title.title_original) for the OpenAI prompt
#       — done in the SAME session that loaded the title row, no
#         extra connection.
#
#   Phase B (async OpenAI, openai_semaphore=3):
#     - analyze_creative_text_async (gpt-4o-mini)
#       — runs OUTSIDE any DB session so the pool is free during the
#         1-3s OpenAI roundtrip.
#
#   Phase B' (async screenshot, httpx_semaphore=5):
#     - persist_asset_screenshot_async (httpx.AsyncClient)
#       — also OUTSIDE any DB session.
#
#   Phase C (sync DB via asyncio.to_thread, ONE Session):
#     - optional enriched whitelist re-match (only when no title yet)
#     - Asset insert + commit
#     - resolve / create title-candidate
#
# vs PR #79: the 4-phase shape opened up to 4 short Sessions per task
# (Phase 1, the inline title-name read, plus Phase 4, plus the candidate
# resolve which sometimes opened a fresh session under the hood). With
# 261 items × concurrency=5 that compounded into the connection storm.
# Block 2.5 lands at TWO sessions per task — both short, both released
# back to the pool while the slow IO legs (OpenAI + httpx) run.
# --------------------------------------------------------------------------


def _phase_a_create_post(
    engine: Engine,
    *,
    item: dict,
    channel_id: UUID,
    platform: str,
    only_whitelist_matches: bool,
) -> tuple[UUID | None, UUID | None, str | None, float, str, str]:
    """Synchronous Phase A. Returns
    ``(post_id, title_id, title_name, match_confidence, status, caption)``.

    ``status`` is one of: ok, no_url, existing, no_match. Opens ONE
    short-lived Session — concurrent tasks must NEVER share a Session.

    Block-2.5 consolidation: the title-name read for the OpenAI prompt
    happens here too (same session, same connection), saving a separate
    ``Session(engine)`` round-trip per task vs PR #79.
    """
    post_url = item.get("post_url")
    if not post_url:
        return None, None, None, 0.0, "no_url", ""

    with Session(engine) as session:
        existing = session.exec(select(Post).where(Post.post_url == post_url)).first()
        if existing:
            return None, None, None, 0.0, "existing", ""

        caption = item.get("caption") or ""
        match_fields = {
            "caption": caption,
            "ocr_text": item.get("ocr_text"),
            "detected_keywords": item.get("detected_keywords") or [],
            "suggested_title": item.get("suggested_title"),
        }
        match = find_best_title_match(session, caption, fields=match_fields)
        title = match.title if is_safe_auto_match(match) else None
        if only_whitelist_matches and not title:
            return None, None, None, float(match.confidence), "no_match", caption

        post = Post(
            channel_id=channel_id,
            platform=platform,
            post_url=post_url,
            external_id=item.get("external_id"),
            caption=caption,
            published_at=item.get("published_at"),
            media_type=platform,
            raw_payload=item.get("raw") or {},
            visible_likes=item.get("visible_likes"),
            visible_comments=item.get("visible_comments"),
            visible_views=item.get("visible_views"),
            visible_shares=item.get("visible_shares"),
            visible_bookmarks=item.get("visible_bookmarks"),
            duration_seconds=item.get("duration_seconds"),
        )
        session.add(post)
        session.commit()
        session.refresh(post)

        # Title.title_original is needed for the OpenAI prompt. The title
        # row is already attached to ``match.title`` from the matcher, so
        # we read it via the same session (no extra checkout).
        title_name = title.title_original if title else None

        return (
            post.id,
            (title.id if title else None),
            title_name,
            float(match.confidence),
            "ok",
            caption,
        )


def _phase_c_commit_asset(
    engine: Engine,
    *,
    asset: Asset,
    initial_title_id: UUID | None,
    initial_match_confidence: float,
    caption: str,
) -> UUID:
    """Synchronous Phase C. Optional whitelist re-match, then Asset
    insert and candidate resolve/create. ONE short-lived Session per
    task. Block-2.5 consolidation: the candidate resolve/create runs
    in the SAME session as the asset insert — keeps everything to one
    pool checkout for this phase."""
    with Session(engine) as session:
        title_id = initial_title_id
        match_confidence = initial_match_confidence
        if title_id is None:
            enriched_fields = {
                "caption": caption,
                "ocr_text": asset.ocr_text,
                "detected_keywords": asset.detected_keywords,
                "ai_summary_de": asset.ai_summary_de,
                "ai_summary_en": asset.ai_summary_en,
                "suggested_title": asset.placement_title_text,
                "visual_notes": asset.visual_notes,
            }
            enriched_match = find_best_title_match(session, caption, fields=enriched_fields)
            if is_safe_auto_match(enriched_match):
                title_id = enriched_match.title.id if enriched_match.title else None
                asset.title_id = title_id
                match_confidence = float(enriched_match.confidence)

        session.add(asset)
        session.commit()
        session.refresh(asset)

        if title_id:
            resolve_open_candidates_for_asset(session, asset.id)
        elif match_confidence < 0.95:
            create_candidate_from_asset(session, asset.id)
        return asset.id


async def _create_asset_from_item_async(
    *,
    engine: Engine,
    item: dict,
    channel_id: UUID,
    channel_name: str,
    channel_market: str,
    platform: str,
    only_whitelist_matches: bool,
    openai_semaphore: asyncio.Semaphore,
    httpx_semaphore: asyncio.Semaphore,
) -> tuple[UUID | None, str]:
    """Async asset creation for a single normalized item. Returns
    (asset_id, status). Status whitelist: created, no_url, existing,
    no_match, skipped_other.

    Per-item exceptions bubble up — callers wrap with
    ``asyncio.gather(return_exceptions=True)``. The function itself never
    catches its own logic errors, only the screenshot path is best-effort
    (handled inside ``persist_asset_screenshot_async``).

    Block-2.5: TWO DB sessions per task max (Phase A + Phase C). Both
    short-lived; the slow legs (OpenAI + httpx) run between them, off
    any DB connection.
    """
    post_id, title_id, title_name, match_confidence, status, caption = await asyncio.to_thread(
        _phase_a_create_post,
        engine,
        item=item,
        channel_id=channel_id,
        platform=platform,
        only_whitelist_matches=only_whitelist_matches,
    )
    if status != "ok":
        return None, status
    assert post_id is not None  # invariant from status=="ok"

    async with openai_semaphore:
        ai = await analyze_creative_text_async(
            post_url=item.get("post_url") or "",
            channel_name=channel_name,
            market=channel_market,
            title_name=title_name,
            caption=caption,
            ocr_text=None,
        )

    asset = Asset(
        post_id=post_id,
        title_id=title_id,
        asset_type=ai.get("asset_type"),
        screenshot_url=item.get("image_url"),
        thumbnail_url=item.get("image_url"),
    )
    for key, value in ai.items():
        if hasattr(asset, key):
            setattr(asset, key, value)

    async with httpx_semaphore:
        await persist_asset_screenshot_async(asset)

    asset_id = await asyncio.to_thread(
        _phase_c_commit_asset,
        engine,
        asset=asset,
        initial_title_id=title_id,
        initial_match_confidence=match_confidence,
        caption=caption,
    )
    return asset_id, "created"


def _group_items_by_channel(
    raw_items: list[dict],
    channels: list[Channel],
    normalize: Callable[[dict], dict],
) -> list[tuple[Channel, list[dict]]]:
    """Bucket normalized items by their inferred channel.

    Apify returns one batched dataset per platform; each item carries an
    ``owner_username`` we map back to a Channel via ``_match_channel``.
    The fallback-by-index path mirrors the previous sync loop so behaviour
    stays identical when Apify omits owner attribution.
    """
    by_channel: dict[UUID, list[dict]] = defaultdict(list)
    channel_lookup: dict[UUID, Channel] = {}
    for index, raw_item in enumerate(raw_items):
        item = normalize(raw_item)
        channel = _match_channel(channels, item.get("owner_username"), index)
        by_channel[channel.id].append(item)
        channel_lookup[channel.id] = channel
    return [(channel_lookup[cid], items) for cid, items in by_channel.items()]


async def _run_apify_sync_for_platform_async(
    *,
    engine: Engine,
    channels: list[Channel],
    raw_items: list[dict],
    platform: str,
    normalize: Callable[[dict], dict],
    only_whitelist_matches: bool,
    openai_concurrency: int = ASSET_CREATION_OPENAI_CONCURRENCY,
    httpx_concurrency: int = ASSET_CREATION_HTTPX_CONCURRENCY,
) -> dict:
    """Async per-platform sync with per-channel error isolation.

    Block 2.5 — same wiring as PR #79 but with the dialed-down
    concurrency (3/5 instead of 5/10) and the consolidated 2-session
    per-task pipeline. Pool budget is enforced by
    ``test_concurrency_constants_within_pool_budget``.

    Schema-compatible additions vs Sprint-5.3.5:
    - ``failed_channels``: list[{handle, market, error_class, error_message, failed_items}]
    - ``processed_channels``: int

    Sprint FU-1 (B2-α follow-up) addition:
    - ``zero_yield_channels``: list[{channel_id, handle, platform, market, url}]
      — channels that were passed to the Apify actor run but had no items
      in the returned dataset. Closes the observability gap from the
      ``apify-uk-zombie-channels`` diagnose (May 2026): batched Apify runs
      return ONE dataset per platform, so channels with empty yields used
      to vanish silently. Now they're enumerated for audit. ``failed_channels``
      keeps its narrower contract (per-channel runtime errors / AllItemsFailed),
      so the two sets are disjoint by construction.

    The ``assets`` key now carries Asset IDs (UUIDs), not ORM instances —
    the only consumer (cron's vision step) already only reads ``a.id``.
    """
    counters = {"created": 0, "skipped_existing": 0, "skipped_no_match": 0, "skipped_other": 0}
    asset_ids: list[UUID] = []
    failed_channels: list[dict[str, Any]] = []

    openai_sem = asyncio.Semaphore(openai_concurrency)
    httpx_sem = asyncio.Semaphore(httpx_concurrency)

    grouped = _group_items_by_channel(raw_items, channels, normalize)
    channel_ids_with_items = {channel.id for channel, _ in grouped}
    zero_yield_channels: list[dict[str, Any]] = [
        {
            "channel_id": str(c.id),
            "handle": c.handle,
            "platform": c.platform,
            "market": str(c.market) if c.market else None,
            "url": c.url,
        }
        for c in channels
        if c.id not in channel_ids_with_items
    ]
    if zero_yield_channels:
        logger.info(
            "zero_yield_detected platform=%s count=%d handles=%s",
            platform,
            len(zero_yield_channels),
            [z["handle"] for z in zero_yield_channels],
        )
    processed_channels = 0

    for channel, channel_items in grouped:
        processed_channels += 1
        try:
            tasks = [
                _create_asset_from_item_async(
                    engine=engine,
                    item=item,
                    channel_id=channel.id,
                    channel_name=channel.name,
                    channel_market=str(channel.market),
                    platform=platform,
                    only_whitelist_matches=only_whitelist_matches,
                    openai_semaphore=openai_sem,
                    httpx_semaphore=httpx_sem,
                )
                for item in channel_items
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as exc:  # noqa: BLE001 — per-channel transport guard
            logger.warning(
                "channel-batch failed for %s/%s (%s): %s",
                platform, channel.handle, type(exc).__name__, exc,
            )
            failed_channels.append({
                "handle": channel.handle,
                "market": str(channel.market) if channel.market else None,
                "error_class": type(exc).__name__,
                "error_message": str(exc)[:200],
                "failed_items": len(channel_items),
            })
            continue

        per_item_exceptions = 0
        for result in results:
            if isinstance(result, BaseException):
                per_item_exceptions += 1
                counters["skipped_other"] += 1
                continue
            asset_id, status = result
            if status == "created" and asset_id is not None:
                counters["created"] += 1
                asset_ids.append(asset_id)
            elif status == "existing":
                counters["skipped_existing"] += 1
            elif status == "no_match":
                counters["skipped_no_match"] += 1
            else:
                counters["skipped_other"] += 1

        # If 100% of items for a channel raised, treat the channel as
        # failed in addition to the per-item bookkeeping.
        if channel_items and per_item_exceptions == len(channel_items):
            failed_channels.append({
                "handle": channel.handle,
                "market": str(channel.market) if channel.market else None,
                "error_class": "AllItemsFailed",
                "error_message": f"all {len(channel_items)} items raised",
                "failed_items": per_item_exceptions,
            })

    return {
        "created_assets": counters["created"],
        "skipped_existing": counters["skipped_existing"],
        "skipped_no_whitelist_match": counters["skipped_no_match"],
        "skipped_other": counters["skipped_other"],
        "asset_ids": asset_ids,
        "failed_channels": failed_channels,
        "processed_channels": processed_channels,
        "zero_yield_channels": zero_yield_channels,
        "zero_yield_count": len(zero_yield_channels),
    }


@router.post("/apify-instagram")
async def apify_instagram_monitor(payload: ApifyMonitorRequest, session: Session = Depends(get_session)):
    if not is_apify_configured():
        raise HTTPException(status_code=400, detail="Apify ist nicht konfiguriert. Bitte APIFY_API_TOKEN und APIFY_INSTAGRAM_ACTOR_ID in Railway setzen.")

    statement = select(Channel).where(Channel.active == True, Channel.mvp == True, Channel.platform == "instagram")  # noqa: E712
    if payload.channel_ids:
        statement = statement.where(Channel.id.in_(payload.channel_ids))
    channels = list(session.exec(statement).all())[: max(1, payload.max_channels)]
    if not channels:
        raise HTTPException(status_code=400, detail="Keine aktiven Instagram-Channels gefunden. Bitte erst Kanalliste importieren.")

    channel_urls = [channel.url for channel in channels]
    raw_items = await run_public_channel_monitor(channel_urls, payload.results_limit_per_channel)

    summary = await _run_apify_sync_for_platform_async(
        engine=session.get_bind(),
        channels=channels,
        raw_items=raw_items,
        platform="instagram",
        normalize=normalize_public_item,
        only_whitelist_matches=payload.only_whitelist_matches,
    )

    return {
        "platform": "instagram",
        "channels_checked": len(channels),
        "raw_items": len(raw_items),
        **summary,
        "apify_actor_id": settings.apify_instagram_actor_id,
    }


@router.post("/apify-tiktok")
async def apify_tiktok_monitor(payload: TikTokMonitorRequest, session: Session = Depends(get_session)):
    if not is_tiktok_configured():
        raise HTTPException(status_code=400, detail="TikTok-Apify ist nicht konfiguriert. Bitte APIFY_API_TOKEN und APIFY_TIKTOK_ACTOR_ID in Railway setzen.")

    statement = select(Channel).where(Channel.active == True, Channel.mvp == True, Channel.platform == "tiktok")  # noqa: E712
    if payload.channel_ids:
        statement = statement.where(Channel.id.in_(payload.channel_ids))
    channels = list(session.exec(statement).all())[: max(1, payload.max_channels)]

    usernames = [_handle_from_url_or_value(item) for item in payload.usernames if item]
    if not usernames:
        usernames = [_handle_from_url_or_value(channel.handle or channel.url) for channel in channels]
    usernames = [item for item in usernames if item]

    if not usernames:
        raise HTTPException(status_code=400, detail="Keine TikTok-Usernames gefunden. Bitte TikTok-Channels anlegen oder Usernames eingeben.")

    if not channels:
        # Temporary auto-channel for direct username test runs.
        first_username = usernames[0]
        channel = session.exec(select(Channel).where(Channel.platform == "tiktok", Channel.handle == first_username)).first()
        if not channel:
            channel = Channel(
                name=f"TikTok @{first_username}",
                platform="tiktok",
                url=f"https://www.tiktok.com/@{first_username}",
                handle=first_username,
                active=True,
                mvp=True,
            )
            session.add(channel)
            session.commit()
            session.refresh(channel)
        channels = [channel]

    raw_items = await run_tiktok_profile_monitor(usernames[: max(1, payload.max_channels)], payload.results_limit_per_channel)

    summary = await _run_apify_sync_for_platform_async(
        engine=session.get_bind(),
        channels=channels,
        raw_items=raw_items,
        platform="tiktok",
        normalize=normalize_tiktok_item,
        only_whitelist_matches=payload.only_whitelist_matches,
    )

    return {
        "platform": "tiktok",
        "channels_checked": len(usernames),
        "raw_items": len(raw_items),
        **summary,
        "apify_actor_id": settings.apify_tiktok_actor_id,
    }
