"""Sprint 5.3.5 — Cron-Auto-Trigger.

Endpoint that the Railway cron service calls every ``CRON_SYNC_INTERVAL_DAYS``
days (default 3). Bearer-auth is provided by the global ``auth_middleware``;
this router just lives outside the public-paths whitelist so any caller has
to present a valid token.

Scope (Sprint 5.3.5): Apify-driven IG + TikTok only. YouTube cron lands in a
separate sprint (different shape: youtube_sync + analyze_post per channel,
plus Anthropic Sonnet vision cost cap).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.config import settings
from app.database import get_session
from app.services.apify_connector import (
    is_apify_configured,
    is_tiktok_configured,
    normalize_public_item,
    normalize_tiktok_item,
    run_public_channel_monitor,
    run_tiktok_profile_monitor,
)
from app.services.cron_channel_selection import compute_run_index, select_channels_for_cron

from app.api.monitor import _handle_from_url_or_value, _run_apify_sync_for_platform

router = APIRouter(prefix="/api/admin/cron", tags=["cron"])
logger = logging.getLogger(__name__)

CRON_RESULTS_LIMIT_PER_CHANNEL = 5


def _summarize(summary: dict) -> dict:
    """Strip the heavy ``assets`` list from a sync summary for the cron response.

    Cron callers only need counters; the per-asset payload would balloon the
    log line for every Railway invocation."""
    return {k: v for k, v in summary.items() if k != "assets"}


@router.post("/sync-all")
async def cron_sync_all(session: Session = Depends(get_session)) -> dict:
    run_index = compute_run_index()
    logger.info("cron-sync started: run_index=%d", run_index)

    response: dict = {"run_index": run_index, "platforms": {}}

    # --- Instagram ---------------------------------------------------------
    if not is_apify_configured():
        response["platforms"]["instagram"] = {"skipped": True, "reason": "apify_not_configured"}
    else:
        ig_channels = select_channels_for_cron(session, "instagram", run_index)
        if not ig_channels:
            response["platforms"]["instagram"] = {"skipped": True, "reason": "no_channels", "channels_checked": 0}
        else:
            channel_urls = [c.url for c in ig_channels if c.url]
            raw_items = await run_public_channel_monitor(channel_urls, CRON_RESULTS_LIMIT_PER_CHANNEL)
            summary = _run_apify_sync_for_platform(
                session=session,
                channels=ig_channels,
                raw_items=raw_items,
                platform="instagram",
                normalize=normalize_public_item,
                only_whitelist_matches=False,
            )
            response["platforms"]["instagram"] = {
                "channels_checked": len(ig_channels),
                "raw_items": len(raw_items),
                **_summarize(summary),
                "apify_actor_id": settings.apify_instagram_actor_id,
            }

    # --- TikTok ------------------------------------------------------------
    if not is_tiktok_configured():
        response["platforms"]["tiktok"] = {"skipped": True, "reason": "tiktok_not_configured"}
    else:
        tt_channels = select_channels_for_cron(session, "tiktok", run_index)
        if not tt_channels:
            response["platforms"]["tiktok"] = {"skipped": True, "reason": "no_channels", "channels_checked": 0}
        else:
            usernames = [u for u in (_handle_from_url_or_value(c.handle or c.url) for c in tt_channels) if u]
            if not usernames:
                response["platforms"]["tiktok"] = {"skipped": True, "reason": "no_usernames", "channels_checked": len(tt_channels)}
            else:
                raw_items = await run_tiktok_profile_monitor(usernames, CRON_RESULTS_LIMIT_PER_CHANNEL)
                summary = _run_apify_sync_for_platform(
                    session=session,
                    channels=tt_channels,
                    raw_items=raw_items,
                    platform="tiktok",
                    normalize=normalize_tiktok_item,
                    only_whitelist_matches=False,
                )
                response["platforms"]["tiktok"] = {
                    "channels_checked": len(tt_channels),
                    "raw_items": len(raw_items),
                    **_summarize(summary),
                    "apify_actor_id": settings.apify_tiktok_actor_id,
                }

    logger.info("cron-sync done: %s", response)
    return response
