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

Scope (Sprint 5.3.5): Apify-driven IG + TikTok only. YouTube cron lands in
a separate sprint.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from app.config import settings
from app.database import engine, get_session
from app.models.entities import CronRun
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


def _run_timeout_minutes() -> int:
    raw = os.environ.get("CRON_RUN_TIMEOUT_MINUTES", "30")
    try:
        return max(1, int(raw))
    except ValueError:
        return 30


def _summarize(summary: dict) -> dict:
    """Strip the heavy ``assets`` list — counters only for the persisted log."""
    return {k: v for k, v in summary.items() if k != "assets"}


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


async def _execute_platform_sync(session: Session, run_index: int) -> dict:
    summary: dict = {"platforms": {}}

    if not is_apify_configured():
        summary["platforms"]["instagram"] = {"skipped": True, "reason": "apify_not_configured"}
    else:
        ig_channels = select_channels_for_cron(session, "instagram", run_index)
        if not ig_channels:
            summary["platforms"]["instagram"] = {"skipped": True, "reason": "no_channels", "channels_checked": 0}
        else:
            channel_urls = [c.url for c in ig_channels if c.url]
            raw_items = await run_public_channel_monitor(channel_urls, CRON_RESULTS_LIMIT_PER_CHANNEL)
            sync = _run_apify_sync_for_platform(
                session=session,
                channels=ig_channels,
                raw_items=raw_items,
                platform="instagram",
                normalize=normalize_public_item,
                only_whitelist_matches=False,
            )
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
                sync = _run_apify_sync_for_platform(
                    session=session,
                    channels=tt_channels,
                    raw_items=raw_items,
                    platform="tiktok",
                    normalize=normalize_tiktok_item,
                    only_whitelist_matches=False,
                )
                summary["platforms"]["tiktok"] = {
                    "channels_checked": len(tt_channels),
                    "raw_items": len(raw_items),
                    **_summarize(sync),
                    "apify_actor_id": settings.apify_tiktok_actor_id,
                }

    return summary


async def _run_cron_sync_background(run_id: UUID, run_index: int) -> None:
    """Background task body. Owns its own Session — the request session is
    closed by the time this runs."""
    with Session(engine) as session:
        run = session.get(CronRun, run_id)
        if not run:
            logger.error("cron run %s not found in background task", run_id)
            return
        try:
            summary = await _execute_platform_sync(session, run_index)
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
