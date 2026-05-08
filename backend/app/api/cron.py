"""Sprint 5.3.5 — Cron-Auto-Trigger.

Endpoint that the Railway cron service calls every ``CRON_SYNC_INTERVAL_DAYS``
days (default 3). Bearer-auth is provided by the global ``auth_middleware``;
this router just lives outside the public-paths whitelist so any caller has
to present a valid token.

The endpoint returns 202 immediately and runs the actual Apify sync as a
FastAPI BackgroundTask; status, summary and error details are persisted in
``creative_radar.cron_run`` so Wolf can inspect each run via GET /runs.
A single-run lock prevents parallel triggers (returns 409); runs older than
``CRON_RUN_TIMEOUT_MINUTES`` (default 30) are reaped as ``failed`` on the
next trigger.

Scope (Sprint 4 — Multi-Plattform V2a): Apify-driven IG + TikTok plus
YouTube via the YouTube Data API. YouTube is gated by
``is_youtube_configured()`` — if the API key is missing the YT sub-block
skips with a structured ``reason``, the IG/TT path is unaffected.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from app.config import settings
from app.database import engine, get_session
from app.models.entities import Asset, CronRun
from app.services.apify_connector import (
    is_apify_configured,
    is_tiktok_configured,
    normalize_public_item,
    normalize_tiktok_item,
    run_public_channel_monitor,
    run_tiktok_profile_monitor,
)
from app.services.cron_channel_selection import compute_run_index, select_channels_for_cron
from app.services.visual_analysis import analyze_asset_visual
from app.services.youtube_connector import (
    YouTubeAPIError,
    YouTubeAuthError,
    YouTubeNotFoundError,
    YouTubeQuotaExceededError,
    fetch_channel_videos,
    is_youtube_configured,
    normalize_youtube_video,
)

from app.api.monitor import _handle_from_url_or_value, _run_apify_sync_for_platform_async

router = APIRouter(prefix="/api/admin/cron", tags=["cron"])
logger = logging.getLogger(__name__)

CRON_RESULTS_LIMIT_PER_CHANNEL = 5

# Approximate vision-call cost for cron summary reporting. Aligned with the
# gpt-4o-mini Vision pricing ballpark used in the Sprint Beta plan; not a
# precise per-call charge — token usage is logged separately by record_openai_call
# (services/cost_log.py). Override via env if a different pricing model lands.
_VISION_COST_USD_PER_CALL = 0.015

_VISION_SUCCESS_STATUSES = frozenset({"analyzed", "done"})
_VISION_FETCH_FAIL_STATUSES = frozenset({"fetch_failed", "no_source", "image_unreachable", "image_invalid"})

# Sprint 4.5 — bug 2: ``_run_apify_sync_for_platform_async`` returns its
# counters under historical (Sprint-5.3.5-era) keys that differ from the
# internal counter names. The cron YT aggregator must translate, otherwise
# ``created`` and ``skipped_no_match`` silently stay 0 even when items
# were persisted. ``skipped_existing``/``skipped_other`` happen to share
# names; they're listed for completeness so the mapping is the single
# source of truth.
_HELPER_COUNTER_KEY_MAP: dict[str, str] = {
    "created": "created_assets",
    "skipped_existing": "skipped_existing",
    "skipped_no_match": "skipped_no_whitelist_match",
    "skipped_other": "skipped_other",
}


def _run_timeout_minutes() -> int:
    raw = os.environ.get("CRON_RUN_TIMEOUT_MINUTES", "30")
    try:
        return max(1, int(raw))
    except ValueError:
        return 30


def _summarize(summary: dict) -> dict:
    """Strip the heavy in-memory artefacts (asset_ids stay sized; the
    grouped-channel ORM list, if present, is dropped) — counters and the
    Block-2 ``failed_channels`` block are kept for the persisted log."""
    return {k: v for k, v in summary.items() if k not in {"assets", "asset_ids"}}


def _reap_stale_runs(session: Session) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_run_timeout_minutes())
    stale = list(session.exec(
        select(CronRun).where(CronRun.status == "running", CronRun.started_at < cutoff)
    ).all())
    if not stale:
        return
    now = datetime.now(timezone.utc)
    for run in stale:
        run.status = "failed"
        run.error_message = "stale_run_timeout"
        run.completed_at = now
        session.add(run)
    session.commit()
    logger.warning("cron-sync reaped %d stale run(s)", len(stale))


async def _execute_platform_sync(session: Session, run_index: int) -> tuple[dict, list[UUID]]:
    """Run the per-platform Apify syncs and return both the summary block
    and the list of newly-created Asset IDs in chronological insertion
    order. Sprint Beta consumes that list to drive the auto-vision step.
    """
    summary: dict = {"platforms": {}}
    created_asset_ids: list[UUID] = []

    if not is_apify_configured():
        summary["platforms"]["instagram"] = {"skipped": True, "reason": "apify_not_configured"}
    else:
        ig_channels = select_channels_for_cron(session, "instagram", run_index)
        if not ig_channels:
            summary["platforms"]["instagram"] = {"skipped": True, "reason": "no_channels", "channels_checked": 0}
        else:
            channel_urls = [c.url for c in ig_channels if c.url]
            raw_items = await run_public_channel_monitor(channel_urls, CRON_RESULTS_LIMIT_PER_CHANNEL)
            sync = await _run_apify_sync_for_platform_async(
                engine=engine,
                channels=ig_channels,
                raw_items=raw_items,
                platform="instagram",
                normalize=normalize_public_item,
                only_whitelist_matches=False,
            )
            created_asset_ids.extend(aid for aid in sync.get("asset_ids", []) if aid is not None)
            summary["platforms"]["instagram"] = {
                "channels_checked": len(ig_channels),
                "raw_items": len(raw_items),
                **_summarize(sync),
                "apify_actor_id": settings.apify_instagram_actor_id,
            }

    if not is_tiktok_configured():
        summary["platforms"]["tiktok"] = {"skipped": True, "reason": "tiktok_not_configured"}
    else:
        tt_channels = select_channels_for_cron(session, "tiktok", run_index)
        if not tt_channels:
            summary["platforms"]["tiktok"] = {"skipped": True, "reason": "no_channels", "channels_checked": 0}
        else:
            usernames = [u for u in (_handle_from_url_or_value(c.handle or c.url) for c in tt_channels) if u]
            if not usernames:
                summary["platforms"]["tiktok"] = {"skipped": True, "reason": "no_usernames", "channels_checked": len(tt_channels)}
            else:
                raw_items = await run_tiktok_profile_monitor(usernames, CRON_RESULTS_LIMIT_PER_CHANNEL)
                sync = await _run_apify_sync_for_platform_async(
                    engine=engine,
                    channels=tt_channels,
                    raw_items=raw_items,
                    platform="tiktok",
                    normalize=normalize_tiktok_item,
                    only_whitelist_matches=False,
                )
                created_asset_ids.extend(aid for aid in sync.get("asset_ids", []) if aid is not None)
                summary["platforms"]["tiktok"] = {
                    "channels_checked": len(tt_channels),
                    "raw_items": len(raw_items),
                    **_summarize(sync),
                    "apify_actor_id": settings.apify_tiktok_actor_id,
                }

    # Sprint 4 — YouTube Data API leg. Structurally different from the
    # Apify platforms above: ``fetch_channel_videos`` is one
    # HTTP-call-bundle PER channel (3 quota units each), not one Apify
    # run for many usernames. We therefore loop per channel and call the
    # same ``_run_apify_sync_for_platform_async`` helper with a
    # single-channel list each time; the fallback-by-index path inside
    # ``_group_items_by_channel`` then maps every video back to that
    # channel cleanly even when the YT ``channelTitle`` differs from the
    # DB handle (e.g. ``NetflixDE`` vs. ``Netflix Deutschland``).
    yt_summary = await _execute_youtube_sync(session, run_index, created_asset_ids)
    summary["platforms"]["youtube"] = yt_summary

    return summary, created_asset_ids


async def _execute_youtube_sync(
    session: Session,
    run_index: int,
    created_asset_ids: list[UUID],
) -> dict:
    """Per-channel YouTube Data API sync. Mirrors the IG/TT paths
    structurally: select channels via the shared
    ``select_channels_for_cron`` rotation, then process each channel.
    Per-channel errors (404 channelNotFound, 429 quota, transient HTTP)
    are caught and logged as ``failed_channels`` entries so a single
    bad handle doesn't take down the whole YT leg.
    """
    if not is_youtube_configured():
        return {"skipped": True, "reason": "youtube_not_configured"}

    yt_channels = select_channels_for_cron(session, "youtube", run_index)
    if not yt_channels:
        return {"skipped": True, "reason": "no_channels", "channels_checked": 0}

    aggregated_counters = {
        "created": 0,
        "skipped_existing": 0,
        "skipped_no_match": 0,
        "skipped_other": 0,
    }
    failed_channels: list[dict] = []
    raw_items_total = 0
    quota_units_used = 0

    for channel in yt_channels:
        handle = channel.handle or _handle_from_url_or_value(channel.url)
        market_str = str(getattr(channel.market, "value", channel.market))
        if not handle:
            failed_channels.append({
                "handle": None, "market": market_str,
                "error_class": "no_handle",
                "error_message": "channel has no handle or url",
                "failed_items": 0,
            })
            continue

        try:
            # Sync function from app.services.youtube_connector — wrapped in
            # asyncio.to_thread so the cron event loop isn't blocked during
            # the three sequential GET requests (channels.list +
            # playlistItems.list + videos.list).
            _channel_meta, raw_videos = await asyncio.to_thread(
                fetch_channel_videos, handle, CRON_RESULTS_LIMIT_PER_CHANNEL,
            )
            quota_units_used += 3  # YT-Quota-Verbrauch (siehe youtube_connector docstring).
        except YouTubeAuthError as exc:
            logger.warning("youtube auth error for %s: %s", handle, exc)
            failed_channels.append({
                "handle": handle, "market": market_str,
                "error_class": "auth_error", "error_message": str(exc), "failed_items": 0,
            })
            continue
        except YouTubeQuotaExceededError as exc:
            logger.warning("youtube quota exceeded at %s — stopping YT leg: %s", handle, exc)
            failed_channels.append({
                "handle": handle, "market": market_str,
                "error_class": "quota_exceeded", "error_message": str(exc), "failed_items": 0,
            })
            # Quota is account-wide; further calls would burn the rest of
            # the day's allowance on the same error. Stop the YT loop and
            # let the next cron run pick up tomorrow.
            break
        except YouTubeNotFoundError as exc:
            logger.warning("youtube channel not found for %s: %s", handle, exc)
            failed_channels.append({
                "handle": handle, "market": market_str,
                "error_class": "not_found", "error_message": str(exc), "failed_items": 0,
            })
            continue
        except YouTubeAPIError as exc:
            logger.exception("youtube api error for %s", handle)
            failed_channels.append({
                "handle": handle, "market": market_str,
                "error_class": "api_error", "error_message": str(exc), "failed_items": 0,
            })
            continue

        raw_items_total += len(raw_videos)
        if not raw_videos:
            continue

        sync = await _run_apify_sync_for_platform_async(
            engine=engine,
            channels=[channel],
            raw_items=raw_videos,
            platform="youtube",
            normalize=normalize_youtube_video,
            only_whitelist_matches=False,
        )
        # Sprint 4.5 — bug 2: ``_run_apify_sync_for_platform_async`` returns
        # the counters under renamed keys (``created_assets`` and
        # ``skipped_no_whitelist_match``) compared to the internal counter
        # names. The Sprint-4 YT aggregator naively read ``sync.get(key)``
        # for the internal names and silently dropped the renamed counters
        # to 0 — so 75 actually-persisted videos appeared as ``created=0``
        # in the cron summary. Live-verified: 85 youtube posts in DB at
        # the time, so the Phase-A/Phase-C path was working all along.
        for our_key, helper_key in _HELPER_COUNTER_KEY_MAP.items():
            aggregated_counters[our_key] += int(sync.get(helper_key, 0) or 0)
        for failed in sync.get("failed_channels", []) or []:
            failed_channels.append(failed)
        created_asset_ids.extend(aid for aid in sync.get("asset_ids", []) if aid is not None)

    return {
        "channels_checked": len(yt_channels),
        "raw_items": raw_items_total,
        "quota_units_used": quota_units_used,
        "processed_channels": len(yt_channels) - len(failed_channels),
        "failed_channels": failed_channels,
        **aggregated_counters,
    }


def _run_vision_after_sync(
    session: Session,
    asset_ids: list[UUID],
    cap: int,
) -> dict:
    """Sprint Beta — run the OpenAI Vision pipeline for up to ``cap`` newly
    created Assets in FIFO order (caller passes IDs in insertion order).
    Per-asset failures are isolated; they bump the matching counter but do
    not abort the loop.

    Returns a Vision-Summary dict embedded under ``summary["vision"]`` in
    the CronRun. Counters mirror the visual_analysis_status whitelist:

    - ``succeeded``  -> status in {analyzed, done}
    - ``text_fallback`` -> status == "text_fallback"
    - ``fetch_failed`` -> status in {fetch_failed, no_source, image_unreachable, image_invalid}
    - ``vision_error`` -> any other terminal status (vision_empty / _timeout
      / _error) plus uncaught exceptions
    """
    total = len(asset_ids)
    skipped_cap = max(0, total - cap) if cap > 0 else total
    chosen = list(asset_ids[:cap]) if cap > 0 else []

    started = time.monotonic()
    counters = {
        "attempted": 0,
        "succeeded": 0,
        "text_fallback": 0,
        "fetch_failed": 0,
        "vision_error": 0,
    }

    for asset_id in chosen:
        asset = session.get(Asset, asset_id)
        if asset is None:
            # Asset disappeared between sync and vision. Should not happen
            # in production (same session, same task), but treat as a soft
            # skip rather than a hard failure.
            continue
        counters["attempted"] += 1
        try:
            updated = analyze_asset_visual(session, asset)
        except Exception:  # noqa: BLE001 — top-level guard, see PC-4
            logger.exception("cron-vision call failed for asset %s", asset_id)
            counters["vision_error"] += 1
            continue
        status = updated.visual_analysis_status
        if status in _VISION_SUCCESS_STATUSES:
            counters["succeeded"] += 1
        elif status == "text_fallback":
            counters["text_fallback"] += 1
        elif status in _VISION_FETCH_FAIL_STATUSES:
            counters["fetch_failed"] += 1
        else:
            counters["vision_error"] += 1

    duration_seconds = round(time.monotonic() - started, 2)
    estimated_cost_usd = round(counters["attempted"] * _VISION_COST_USD_PER_CALL, 4)

    return {
        **counters,
        "skipped_cap": skipped_cap,
        "duration_seconds": duration_seconds,
        "estimated_cost_usd": estimated_cost_usd,
    }


async def _run_cron_sync_background(run_id: UUID, run_index: int) -> None:
    """Background task body. Owns its own Session — the request session is
    closed by the time this runs."""
    with Session(engine) as session:
        run = session.get(CronRun, run_id)
        if not run:
            logger.error("cron run %s not found in background task", run_id)
            return
        try:
            summary, created_asset_ids = await _execute_platform_sync(session, run_index)
            cap = settings.cron_vision_max_assets_per_run
            if created_asset_ids:
                summary["vision"] = _run_vision_after_sync(session, created_asset_ids, cap)
            run.summary_json = summary
            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)
            session.add(run)
            session.commit()
            logger.info("cron run %s completed: %s", run_id, summary)
        except Exception as exc:  # noqa: BLE001 — top-level guard, status persists
            logger.exception("cron run %s failed", run_id)
            run.status = "failed"
            run.error_message = str(exc)[:500]
            run.completed_at = datetime.now(timezone.utc)
            session.add(run)
            session.commit()


@router.post("/sync-all")
async def cron_sync_all(
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    _reap_stale_runs(session)

    running = session.exec(select(CronRun).where(CronRun.status == "running")).first()
    if running:
        return JSONResponse(
            status_code=409,
            content={
                "error": "cron_run_already_running",
                "run_id": str(running.id),
                "started_at": running.started_at.isoformat(),
            },
        )

    run_index = compute_run_index()
    run = CronRun(run_index=run_index)
    session.add(run)
    session.commit()
    session.refresh(run)

    background_tasks.add_task(_run_cron_sync_background, run.id, run_index)

    logger.info("cron-sync queued: run_id=%s run_index=%d", run.id, run_index)

    return JSONResponse(
        status_code=202,
        content={
            "run_id": str(run.id),
            "started_at": run.started_at.isoformat(),
            "status": "running",
            "run_index": run_index,
            "message": "cron sync started in background",
        },
    )


@router.get("/runs")
async def list_cron_runs(
    limit: int = 10,
    session: Session = Depends(get_session),
):
    rows = session.exec(
        select(CronRun)
        .order_by(CronRun.started_at.desc())
        .limit(min(max(1, limit), 50))
    ).all()
    return [
        {
            "id": str(r.id),
            "started_at": r.started_at.isoformat(),
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "status": r.status,
            "run_index": r.run_index,
            "duration_seconds": (
                (r.completed_at - r.started_at).total_seconds()
                if r.completed_at else None
            ),
            "summary": r.summary_json,
            "error_message": r.error_message,
        }
        for r in rows
    ]
