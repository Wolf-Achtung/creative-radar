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
from app.database import get_session
from app.models.entities import Asset, Channel, Post
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
from app.services.whitelist_matcher import find_best_title_match, is_safe_auto_match
from app.services.title_candidates import create_candidate_from_asset, resolve_open_candidates_for_asset

logger = logging.getLogger(__name__)


# Block 2 — async refactor: concurrency budgets per platform-batch.
#
# OPENAI_CONCURRENCY=5: empirically the OpenAI rate-limit headroom for
# gpt-4o-mini at our org tier; 5 parallel completions stay well under the
# 500 RPM tier-1 cap even when summed across IG+TT batches.
#
# HTTPX_CONCURRENCY=10: image downloads are fast and IO-bound; 10 parallel
# requests don't strain memory and finish well before any sane CDN throttle.
# These are tuned for the Railway single-worker uvicorn deployment;
# override per-call via ``run_apify_sync`` kwargs if a different topology
# wants a different ceiling.
DEFAULT_OPENAI_CONCURRENCY = 5
DEFAULT_HTTPX_CONCURRENCY = 10

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
# Block 2 — async asset-creation pipeline.
#
# Per-asset shape after the refactor (one task per item):
#
#   Phase 1 (sync DB, asyncio.to_thread):
#     - existing-check (URL dedupe)
#     - first whitelist title-match
#     - Post insert + commit
#
#   Phase 2 (async OpenAI, openai_semaphore <=5):
#     - analyze_creative_text_async (gpt-4o-mini)
#
#   Phase 3 (async screenshot, httpx_semaphore <=10):
#     - persist_asset_screenshot_async (httpx.AsyncClient)
#
#   Phase 4 (sync DB, asyncio.to_thread):
#     - optional enriched whitelist re-match
#     - Asset insert + commit
#     - resolve / create title-candidate
#
# Phases 1 + 4 each open their OWN ``Session(engine)`` so concurrent tasks
# never share a SQLAlchemy Session (which is not thread-safe). Earlier
# fixtures used the request-scoped session; that pattern is incompatible
# with ``asyncio.gather`` of mutating tasks.
# --------------------------------------------------------------------------


def _phase1_create_post(
    engine: Engine,
    *,
    item: dict,
    channel_id: UUID,
    platform: str,
    only_whitelist_matches: bool,
) -> tuple[UUID | None, UUID | None, float, str, str]:
    """Synchronous Phase 1. Returns (post_id, title_id, match_confidence,
    status, caption). ``status`` is one of: ok, no_url, existing, no_match.
    Opens a short-lived Session — must NOT be shared with concurrent tasks."""
    post_url = item.get("post_url")
    if not post_url:
        return None, None, 0.0, "no_url", ""

    with Session(engine) as session:
        existing = session.exec(select(Post).where(Post.post_url == post_url)).first()
        if existing:
            return None, None, 0.0, "existing", ""

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
            return None, None, float(match.confidence), "no_match", caption

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
        return (
            post.id,
            (title.id if title else None),
            float(match.confidence),
            "ok",
            caption,
        )


def _phase4_commit_asset(
    engine: Engine,
    *,
    asset: Asset,
    initial_title_id: UUID | None,
    initial_match_confidence: float,
    caption: str,
) -> UUID:
    """Synchronous Phase 4. Optional whitelist re-match, then Asset insert
    and candidate resolve/create. Opens a short-lived Session per task."""
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
    (handled inside ``persist_asset_screenshot_async``)."""
    post_id, title_id, match_confidence, status, caption = await asyncio.to_thread(
        _phase1_create_post,
        engine,
        item=item,
        channel_id=channel_id,
        platform=platform,
        only_whitelist_matches=only_whitelist_matches,
    )
    if status != "ok":
        return None, status
    assert post_id is not None  # invariant from status=="ok"

    title_name: str | None = None
    if title_id is not None:
        # title_original lookup — cheap point-read, do it in a thread
        def _read_title_name() -> str | None:
            from app.models.entities import Title  # local to avoid cycle
            with Session(engine) as s:
                t = s.get(Title, title_id)
                return t.title_original if t else None
        title_name = await asyncio.to_thread(_read_title_name)

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
        _phase4_commit_asset,
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

    Returns an ordered list of (channel, items) so the iteration below is
    deterministic — the same Channel may appear twice only when Apify
    returns mixed-channel items in non-contiguous order; we collapse those
    by Channel.id."""
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
    openai_concurrency: int = DEFAULT_OPENAI_CONCURRENCY,
    httpx_concurrency: int = DEFAULT_HTTPX_CONCURRENCY,
) -> dict:
    """Async per-platform sync with per-channel error isolation.

    Behaviour vs the pre-Block-2 sync loop:
    - Items grouped by inferred channel (``_group_items_by_channel``).
    - Each channel's items run via ``asyncio.gather(..., return_exceptions=True)``
      — one bad item bumps ``skipped_other`` and lands in ``failed_items``
      under that channel's bucket but does NOT crash the channel batch.
    - Each channel batch is wrapped in try/except — a transport-level
      failure (DB outage, etc.) marks the channel in ``failed_channels``
      with class+message and continues to the next channel.
    - Concurrency: per-batch Semaphore(5) for OpenAI, Semaphore(10) for
      httpx. These ceilings stay constant across IG+TT — the cron driver
      runs the platforms sequentially today (see ``_execute_platform_sync``).

    Schema-compatible additions to the return dict (vs Sprint-5.3.5):
    - ``failed_channels``: list[{handle, market, error_class, error_message}]
    - ``processed_channels``: int — total channels for which we actually
      ran the items-gather (i.e. had at least one item).

    The ``assets`` key now carries Asset IDs (UUIDs), not ORM instances —
    the only consumer (cron's vision step) already only reads ``a.id``.
    """
    counters = {"created": 0, "skipped_existing": 0, "skipped_no_match": 0, "skipped_other": 0}
    asset_ids: list[UUID] = []
    failed_channels: list[dict[str, Any]] = []

    openai_sem = asyncio.Semaphore(openai_concurrency)
    httpx_sem = asyncio.Semaphore(httpx_concurrency)

    grouped = _group_items_by_channel(raw_items, channels, normalize)
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
        # failed in addition to the per-item bookkeeping. Mirrors the
        # blast-radius rule from the briefing: "ein defekter Channel".
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
