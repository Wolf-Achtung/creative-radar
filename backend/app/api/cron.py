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
from app.models.entities import Asset, CronRun, InsightReport as InsightReportRow
from app.services.apify_connector import (
    is_apify_configured,
    is_tiktok_configured,
    normalize_public_item,
    normalize_tiktok_item,
    run_public_channel_monitor,
    run_tiktok_profile_monitor,
)
from app.services.budget_check import (
    aggregate_anthropic_costs_since,
    aggregate_apify_costs_since,
    aggregate_openai_costs_since,
    compute_anthropic_monthly_spend,
    compute_apify_monthly_spend,
)
from app.services.cron_channel_selection import compute_run_index, select_channels_for_cron
from app.core.feature_flags import is_segment_roundups_enabled
from app.models.entities import SegmentRoundup as SegmentRoundupRow
from app.services.insight_engine import PAIRS, generate_and_persist_report
from app.services.segment_roundup import (
    generate_and_persist_roundup,
    parse_cron_roundup_segments,
)
from app.services.title_rematch import rematch_unassigned_assets
from app.services.title_sync import sync_titles_from_tmdb
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
    Block-2 ``failed_channels`` plus Sprint-FU-1 ``zero_yield_channels``
    blocks are kept for the persisted log."""
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
            # Sprint FU-1: ``zero_yield_channels`` + ``zero_yield_count`` flow
            # through ``_summarize`` automatically — surfaces the silent
            # 0-Yield-Channels per the B2-α diagnose.
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
                # Sprint FU-1: see IG-block comment — ``zero_yield_channels``
                # surfaces via the same ``_summarize`` spread.
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

        # Sprint 4.5 — bug 1 fix. Forward the stored UCxxx-ID (when
        # populated) so the connector resolves legacy custom-URL channels
        # in one quota unit instead of failing 404 on ``forHandle``.
        channel_id_hint = getattr(channel, "platform_channel_id", None)
        try:
            # Sync function from app.services.youtube_connector — wrapped in
            # asyncio.to_thread so the cron event loop isn't blocked during
            # the three sequential GET requests (channels.list +
            # playlistItems.list + videos.list).
            _channel_meta, raw_videos = await asyncio.to_thread(
                fetch_channel_videos,
                handle,
                CRON_RESULTS_LIMIT_PER_CHANNEL,
                channel_id_hint=channel_id_hint,
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
    counters = _vision_process_ids(session, chosen)

    duration_seconds = round(time.monotonic() - started, 2)
    estimated_cost_usd = round(counters["attempted"] * _VISION_COST_USD_PER_CALL, 4)

    return {
        **counters,
        "skipped_cap": skipped_cap,
        "duration_seconds": duration_seconds,
        "estimated_cost_usd": estimated_cost_usd,
    }


def _vision_process_ids(session: Session, asset_ids: list[UUID]) -> dict:
    """Run ``analyze_asset_visual`` over the given asset IDs, isolating
    per-asset failures, and return the status counters. Shared by the
    fresh-asset path (``_run_vision_after_sync``) and the backlog-drain
    path (``_run_vision_backlog``) so the counting/error-isolation rules
    stay identical."""
    counters = {
        "attempted": 0,
        "succeeded": 0,
        "text_fallback": 0,
        "fetch_failed": 0,
        "vision_error": 0,
    }
    for asset_id in asset_ids:
        asset = session.get(Asset, asset_id)
        if asset is None:
            # Asset disappeared between selection and vision. Should not
            # happen in production (same session, same task), but treat as
            # a soft skip rather than a hard failure.
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
    return counters


def _run_vision_backlog(
    session: Session,
    backlog_cap: int,
    *,
    exclude_ids: list[UUID],
) -> dict:
    """Drain up to ``backlog_cap`` oldest ``pending`` assets that the
    feed-forward vision path never reached (historical backlog + per-run
    ``skipped_cap`` overflow). Runs AFTER the fresh-asset vision step; the
    ``created_asset_ids`` selection/cap is untouched.

    ``exclude_ids`` are the asset IDs the fresh-asset step already handled
    this run — they may still read as ``pending`` mid-transaction, so we
    skip them to avoid double processing. ``backlog_cap`` bounds the cost
    (N * ~$0.015 per run); 0 disables the drain entirely.
    """
    if backlog_cap <= 0:
        return {"enabled": False, "backlog_cap": backlog_cap}

    started = time.monotonic()
    exclude = set(exclude_ids)
    stmt = (
        select(Asset)
        .where(Asset.visual_analysis_status == "pending")
        .order_by(Asset.created_at.asc())
        .limit(backlog_cap + len(exclude))
    )
    candidates = [a.id for a in session.exec(stmt).all() if a.id not in exclude][:backlog_cap]

    counters = _vision_process_ids(session, candidates)
    duration_seconds = round(time.monotonic() - started, 2)
    estimated_cost_usd = round(counters["attempted"] * _VISION_COST_USD_PER_CALL, 4)

    return {
        "enabled": True,
        "backlog_cap": backlog_cap,
        "selected": len(candidates),
        **counters,
        "duration_seconds": duration_seconds,
        "estimated_cost_usd": estimated_cost_usd,
    }


async def _run_title_sync_after_scrape(session: Session) -> dict:
    """Pull the TMDb title catalogue (movies + TV series) as a cron stage,
    BEFORE the rematch step so freshly synced titles are available to match
    against in the same run. Mirrors the brief-gen contract: per-stage
    try/except (a TMDb outage must not fail the whole cron) and a kill-switch.

    Kill-Switch: ENV ``ENABLE_TITLE_SYNC_IN_CRON`` (Default ``true``). Off →
    stage skipped, scrape/rematch/briefs run normally; no code deploy needed.
    ``sync_titles_from_tmdb`` writes its own ``TitleSyncRun`` audit row, so
    idempotency and logging are already handled there.
    """
    enabled = os.getenv("ENABLE_TITLE_SYNC_IN_CRON", "true").lower() == "true"
    if not enabled:
        logger.info("title_sync.skipped reason=env_disabled")
        return {"enabled": False}
    try:
        result = await sync_titles_from_tmdb(session)
    except Exception as exc:  # noqa: BLE001 — top-level guard, best-effort stage
        logger.exception("cron title-sync failed")
        return {"enabled": True, "error": str(exc)[:500]}
    return {"enabled": True, **result}


def _run_rematch_after_sync(session: Session) -> dict:
    """Sprint 10e — auto re-match unassigned assets after every cron sync.

    New TMDb-title rows arrive continuously between cron runs (Sprint 10a's
    popularity-sorted discover pulls them in), and a title that wasn't in
    the DB at initial-ingest time silently drifts as ``Asset.title_id =
    NULL`` until someone hits ``POST /api/titles/rematch-assets`` by hand.
    Wiring rematch into the cron tail removes that manual step.

    Runs unconditionally per cron tick (also when no new assets were
    created in this run): the new-title path is the actual driver, not
    the new-asset path. Failures are absorbed into the summary instead of
    failing the whole cron — re-match is a best-effort enrichment, the
    sync itself has already succeeded by this point.
    """
    try:
        summary = rematch_unassigned_assets(session)
    except Exception as exc:  # noqa: BLE001 — top-level guard, see PC-4
        logger.exception("auto-rematch after cron sync failed")
        return {"error": str(exc)[:500]}
    return summary.to_dict()


def _run_brief_generation_after_sync(session: Session) -> dict:
    """Cadence-Sprint 2026-05-17 — Brief-Generation als Cron-Stage.

    Iteriert über alle ``enabled=True``-Pairs aus ``PAIRS`` und ruft je Pair
    ``generate_and_persist_report`` für die *gerade abgeschlossene* ISO-
    Woche auf (``now - 1 day`` als ISO-Anker, siehe Aufrufer-Kommentar).
    Per-Pair-Try/Except, damit ein fehlschlagender Pair die anderen nicht
    killt — der gleiche Robustness-Vertrag wie bei
    ``_run_rematch_after_sync``.

    Kill-Switch: ENV ``ENABLE_BRIEF_GEN_IN_CRON`` (Default ``true``). Bei
    Incidents (Cost-Explosion, Mock-Leak, LLM-Outage) setzt Wolf den Wert im
    Railway-UI auf ``false``; Brief-Gen wird dann übersprungen, Scrape und
    Rematch laufen normal weiter. Kein Code-Deploy nötig.

    Cache-Hit-Detection: vor jedem Aufruf wird die PK
    ``(pair, iso_year, iso_week)`` direkt geprüft. Bei Treffer wird
    ``skipped_cache_hit`` inkrementiert und ``generate_and_persist_report``
    gar nicht aufgerufen — die Funktion würde dasselbe Ergebnis liefern,
    aber die Pre-Check-Variante erlaubt uns einen sauberen Counter ohne
    Return-Value-Diskriminanz (die Funktion liefert in beiden Pfaden ein
    ``InsightReport``-Objekt zurück).

    Cost-Counter: nur frisch generierte Briefs zählen in
    ``cost_usd_cents`` ein (Cache-Hits verursachen keinen LLM-Call).
    """
    brief_gen_enabled = os.getenv("ENABLE_BRIEF_GEN_IN_CRON", "true").lower() == "true"
    briefs_summary: dict = {
        "enabled": brief_gen_enabled,
        "generated": 0,
        "skipped_cache_hit": 0,
        "failed": 0,
        "cost_usd_cents": 0,
        "errors": [],
    }
    if not brief_gen_enabled:
        logger.info("brief_gen.skipped reason=env_disabled")
        return briefs_summary

    brief_now = datetime.now(timezone.utc) - timedelta(days=1)
    iso_cal = brief_now.isocalendar()
    target_iso_year, target_iso_week = iso_cal.year, iso_cal.week
    enabled_pairs = [k for k, v in PAIRS.items() if v.get("enabled", False)]

    logger.info(
        "brief_gen.start",
        extra={
            "pairs": len(enabled_pairs),
            "target_iso_year": target_iso_year,
            "target_iso_week": target_iso_week,
        },
    )

    for pair_key in enabled_pairs:
        existing_row = session.get(
            InsightReportRow,
            (pair_key, target_iso_year, target_iso_week),
        )
        if existing_row is not None:
            briefs_summary["skipped_cache_hit"] += 1
            continue
        try:
            report = generate_and_persist_report(
                session,
                pair_key,
                window_days=30,
                now=brief_now,
            )
            # generate_and_persist_report returns normally even when the
            # brief failed JSON-parse / schema-validation: the report then
            # carries ``llm_output is None`` and ``_persist_report`` skipped
            # the write (no row persisted). Counting that as "generated"
            # reported phantom successes — 2026-06-01 logged generated=8
            # while all 8 schema-failed and nothing persisted. Treat a
            # missing llm_output as a failure so the summary reflects what
            # actually landed in insight_report. This only makes the failure
            # visible; the root-cause schema mismatch is a separate fix.
            if report is None or report.llm_output is None:
                briefs_summary["failed"] += 1
                briefs_summary["errors"].append({
                    "pair": pair_key,
                    "error_class": "no_llm_output",
                    "error_message": "llm_output is None (JSON-parse/schema/citation failure, brief not persisted)",
                })
            else:
                briefs_summary["generated"] += 1
            if report is not None and report.cost_usd_estimate:
                briefs_summary["cost_usd_cents"] += int(round(report.cost_usd_estimate * 100))
        except Exception as exc:  # noqa: BLE001 — per-pair isolation, see docstring
            logger.exception("brief_gen.failed pair=%s", pair_key)
            briefs_summary["failed"] += 1
            briefs_summary["errors"].append({
                "pair": pair_key,
                "error_class": type(exc).__name__,
                "error_message": str(exc)[:200],
            })

    logger.info(
        "brief_gen.complete",
        extra={
            "generated": briefs_summary["generated"],
            "skipped_cache_hit": briefs_summary["skipped_cache_hit"],
            "failed": briefs_summary["failed"],
            "cost_usd_cents": briefs_summary["cost_usd_cents"],
        },
    )
    return briefs_summary


def _run_segment_roundups_after_briefs(session: Session) -> dict:
    """Master-Plan-Schritt-4 — Segment-Roundup-Block additiv NACH dem
    Pair-Brief-Block. Reihenfolge ist Pflicht (Wolf-Konzept §6): Pair-
    Briefs zuerst, Roundups danach, damit bei mid-Run-Cap-Abbruch
    zuerst die Roundups entfallen, nie ein Pair-Brief.

    Mechanik (analog ``_run_brief_generation_after_sync``):
    1. Feature-Flag-Gate ``FEATURE_SEGMENT_ROUNDUPS_ENABLED``. Off →
       skipped block mit ``enabled=False``.
    2. Segment-Liste aus ``settings.cron_roundup_segments`` parsen.
       Leerer/unparsebarer Gesamtwert → skipped block mit
       ``reason="no_parseable_segments"`` (Wolf-Festlegung Ping 1:
       nicht still in keine Roundups kippen).
    3. **Zweiter F0.7-Cap-Check** (PFLICHT laut Wolf-Festlegung Ping 1):
       ``compute_anthropic_monthly_spend`` re-compute — der Run-Start-
       Pre-Flight sah die Pair-Brief-Kosten noch nicht. Wenn jetzt
       ``hard_cap_exceeded and enforced``, ueberspringt der ganze
       Roundup-Block mit ``reason="anthropic_budget_exceeded"`` und
       Budget-Snapshot im Summary. Pair-Briefs sind dann sicher schon
       persistiert.
    4. ``brief_now = utcnow - 1 day`` analog Pair-Pfad — Roundups gehen
       fuer die *gerade abgeschlossene* ISO-Woche.
    5. Pro Segment in CSV-Reihenfolge:
       - Cache-Hit-Check auf PK ``(segment, iso_year, iso_week)``. Wenn
         existing-Row → ``skipped_cache_hit++``, kein LLM-Call.
       - Sonst ``generate_and_persist_roundup`` mit ``now=brief_now``,
         ``window_days=14``, ``top_posts_n=5``.
       - Per-Segment-Try/Except: ein scheiternder Segment-Lauf killt
         nicht die anderen (gleicher Robustness-Vertrag wie Pair-Brief-
         Block).

    Returns: dict fuer ``summary["roundups"]``.
    """
    roundup_enabled = is_segment_roundups_enabled()
    roundups_summary: dict = {
        "enabled": roundup_enabled,
        "skipped": False,
        "generated": 0,
        "skipped_cache_hit": 0,
        "failed": 0,
        "cost_usd_cents": 0,
        "results": [],
        "errors": [],
    }
    if not roundup_enabled:
        roundups_summary["skipped"] = True
        roundups_summary["reason"] = "feature_flag_off"
        logger.info("roundups.skipped reason=feature_flag_off")
        return roundups_summary

    segments = parse_cron_roundup_segments(settings.cron_roundup_segments)
    roundups_summary["segments_configured"] = len(segments)
    if not segments:
        # Parser hat schon einen ERROR-Log gefahren (cron_roundup_segments_empty);
        # hier setzen wir den Skip-Marker, der das im summary_json sichtbar
        # macht.
        roundups_summary["skipped"] = True
        roundups_summary["reason"] = "no_parseable_segments"
        return roundups_summary

    # Zweiter F0.7-Cap-Check — PFLICHT-Anforderung Ping 1.
    cap_check = compute_anthropic_monthly_spend(session)
    if cap_check.hard_cap_exceeded and cap_check.enforced:
        roundups_summary["skipped"] = True
        roundups_summary["reason"] = "anthropic_budget_exceeded"
        roundups_summary["anthropic_budget"] = cap_check.to_dict()
        logger.warning(
            "roundups.skipped reason=anthropic_budget_exceeded spent=%d/%d cents (%.1f%%)",
            cap_check.spent_usd_cents, cap_check.budget_usd_cents,
            cap_check.pct_used * 100,
        )
        return roundups_summary

    brief_now = datetime.now(timezone.utc) - timedelta(days=1)
    iso_cal = brief_now.isocalendar()
    target_iso_year, target_iso_week = iso_cal.year, iso_cal.week

    logger.info(
        "roundups.start",
        extra={
            "segments": [s.value for s in segments],
            "target_iso_year": target_iso_year,
            "target_iso_week": target_iso_week,
        },
    )

    for segment in segments:
        existing_row = session.get(
            SegmentRoundupRow,
            (segment, target_iso_year, target_iso_week),
        )
        if existing_row is not None:
            roundups_summary["skipped_cache_hit"] += 1
            roundups_summary["results"].append({
                "segment": segment.value,
                "status": "cache_hit",
                "iso_year": target_iso_year,
                "iso_week": target_iso_week,
            })
            continue
        try:
            report = generate_and_persist_roundup(
                session,
                segment,
                window_days=14,
                top_posts_n=5,
                now=brief_now,
            )
            # generate_and_persist_roundup returns normally even when the
            # roundup failed JSON-parse / schema-validation: the report then
            # carries ``llm_output is None`` and ``_persist_roundup`` skipped
            # the write (no row persisted). Count that as a failure, not a
            # generation — same contract as the brief counter (PR #210) — so
            # the cron summary reflects what actually landed in
            # segment_roundup. Cost + the results entry are still recorded
            # for both outcomes so other summary fields stay unchanged.
            if report is None or report.llm_output is None:
                roundups_summary["failed"] += 1
                roundups_summary["errors"].append({
                    "segment": segment.value,
                    "error_class": "no_llm_output",
                    "error_message": "llm_output is None (JSON-parse/schema failure, roundup not persisted)",
                })
            else:
                roundups_summary["generated"] += 1
            if report is not None:
                cost_cents = (
                    int(round(report.cost_usd_estimate * 100))
                    if report.cost_usd_estimate else 0
                )
                roundups_summary["cost_usd_cents"] += cost_cents
                roundups_summary["results"].append({
                    "segment": segment.value,
                    "status": "ok" if report.llm_output is not None else "persist_skipped",
                    "iso_year": report.iso_year,
                    "iso_week": report.iso_week,
                    "cost_cents": cost_cents,
                    "channels_evaluated": report.aggregation.channels_evaluated,
                    "channels_with_posts": report.aggregation.channels_with_posts,
                    "total_posts": report.aggregation.total_posts,
                })
        except Exception as exc:  # noqa: BLE001 — per-segment isolation
            logger.exception("roundups.failed segment=%s", segment.value)
            roundups_summary["failed"] += 1
            roundups_summary["errors"].append({
                "segment": segment.value,
                "error_class": type(exc).__name__,
                "error_message": str(exc)[:200],
            })

    logger.info(
        "roundups.complete",
        extra={
            "generated": roundups_summary["generated"],
            "skipped_cache_hit": roundups_summary["skipped_cache_hit"],
            "failed": roundups_summary["failed"],
            "cost_usd_cents": roundups_summary["cost_usd_cents"],
        },
    )
    return roundups_summary


async def _run_cron_sync_background(run_id: UUID, run_index: int) -> None:
    """Background task body. Owns its own Session — the request session is
    closed by the time this runs."""
    with Session(engine) as session:
        run = session.get(CronRun, run_id)
        if not run:
            logger.error("cron run %s not found in background task", run_id)
            return
        try:
            # Sprint F0.6 — Apify-Monatsbudget-Pre-Flight. Wenn der Hard-Cap
            # erreicht ist und der Kill-Switch nicht aus ist, bricht der
            # Run hier vor jeder API-Aktion ab. Der CronRun bleibt als
            # Audit-Trail erhalten mit Status ``budget_exceeded`` und
            # vollständigem BudgetStatus im summary_json. Soft-Warn (>=80%)
            # läuft weiter und wird nur via ``budget_warning=True``
            # markiert — die ~$80 Cushion zwischen Soft und Hard ist die
            # bewusste Sicherheitsmarge.
            budget = compute_apify_monthly_spend(session)
            if budget.hard_cap_exceeded and budget.enforced:
                summary = {
                    "skipped": True,
                    "reason": "apify_budget_exceeded",
                    "budget": budget.to_dict(),
                }
                run.summary_json = summary
                run.status = "budget_exceeded"
                run.completed_at = datetime.now(timezone.utc)
                session.add(run)
                session.commit()
                logger.warning(
                    "cron run %s aborted: apify budget %d/%d cents (%.1f%%)",
                    run_id, budget.spent_usd_cents, budget.budget_usd_cents,
                    budget.pct_used * 100,
                )
                return

            # Sprint F0.7 — Anthropic-Monatsbudget-Pre-Flight. Exakt analog
            # zur Apify-Logik darüber: separater Hard-Cap-Wert ($100 default)
            # mit eigenem Kill-Switch (``anthropic_budget_enforced``), gleiche
            # Abort-Semantik (``status='budget_exceeded'``, CronRun bleibt
            # als Audit-Trail). Reason-Feld unterscheidet die zwei Caps
            # (``apify_budget_exceeded`` vs ``anthropic_budget_exceeded``)
            # damit Postmortems sofort sehen welcher Provider die Bremse
            # war. Beide Budgets landen im Summary, damit Wolf in der
            # Admin-UI auch nach Apify-Abort sieht, wo Anthropic gerade
            # steht (und umgekehrt).
            anthropic_budget = compute_anthropic_monthly_spend(session)
            if anthropic_budget.hard_cap_exceeded and anthropic_budget.enforced:
                summary = {
                    "skipped": True,
                    "reason": "anthropic_budget_exceeded",
                    "budget": budget.to_dict(),
                    "anthropic_budget": anthropic_budget.to_dict(),
                }
                run.summary_json = summary
                run.status = "budget_exceeded"
                run.completed_at = datetime.now(timezone.utc)
                session.add(run)
                session.commit()
                logger.warning(
                    "cron run %s aborted: anthropic budget %d/%d cents (%.1f%%)",
                    run_id, anthropic_budget.spent_usd_cents,
                    anthropic_budget.budget_usd_cents,
                    anthropic_budget.pct_used * 100,
                )
                return

            summary, created_asset_ids = await _execute_platform_sync(session, run_index)
            cap = settings.cron_vision_max_assets_per_run
            if created_asset_ids:
                summary["vision"] = _run_vision_after_sync(session, created_asset_ids, cap)
            # Backlog-Drain (Dauerfix gegen feed-forward-Lücke): nach den
            # frisch erzeugten Assets bis zu N älteste ``pending``-Assets
            # nachziehen. created_asset_ids-Selektion/Cap oben bleibt
            # unberührt; backlog_cap=0 deaktiviert den Pfad.
            backlog_cap = settings.cron_vision_backlog_max_assets_per_run
            if backlog_cap > 0:
                summary["vision_backlog"] = _run_vision_backlog(
                    session, backlog_cap, exclude_ids=created_asset_ids
                )
            # Title-Katalog-Sync (Movies + TV) VOR dem Rematch, damit frisch
            # gezogene Titel im selben Lauf gematcht werden. Hinter
            # ENABLE_TITLE_SYNC_IN_CRON (Default true); eigener try/except.
            summary["title_sync"] = await _run_title_sync_after_scrape(session)
            summary["rematch"] = _run_rematch_after_sync(session)
            # Cadence-Sprint 2026-05-17 — Brief-Generation für die gerade
            # abgeschlossene ISO-Woche. Vor diesem Sprint hat der Sonntag-Cron
            # nur Scrape gemacht; Briefs entstanden ausschließlich lazy beim
            # ersten UI-Aufruf (oder manuell via admin.py). Cutter sahen am
            # Montag den Brief vom Mittwoch (Lazy-Generate-Burst). Jetzt:
            # Schedule auf Mo 06:00 UTC verschoben (workflow.yml), Brief-Gen
            # hier eingebaut.
            #
            # H4-Mitigation: ``brief_now = utcnow - 1 day``. Ein Lauf am Montag
            # 06:00 UTC will Briefs für die *gerade abgeschlossene* KW
            # generieren. ``now.isocalendar()`` am Montag 00-23:59 UTC liefert
            # bereits die neue KW; ``now - 1 day`` zieht uns garantiert in
            # den Sonntag der Vorwoche zurück, also die KW, deren Daten-
            # Aggregation tatsächlich vollständig vorliegt. Selbe Logik wie
            # ``aggregate_pair`` (insight_engine.py:1880), nur explizit
            # ein Tag zurück.
            summary["briefs"] = _run_brief_generation_after_sync(session)
            # Master-Plan-Schritt-4 — Segment-Roundup-Block additiv NACH
            # den Pair-Briefs (Konzept §6, Wolf-Festlegung 25.05.). Der
            # zweite F0.7-Cap-Check im Roundup-Block stellt sicher, dass
            # bei mid-Run-Cap-Triggern ausschliesslich die Roundups
            # entfallen — Pair-Briefs sind hier bereits persistiert.
            # Hinter ``FEATURE_SEGMENT_ROUNDUPS_ENABLED``: Flag off =
            # Cron-Verhalten exakt wie vor Schritt 4.
            summary["roundups"] = _run_segment_roundups_after_briefs(session)
            # Tech-Debt A5 — Apify-Cost dieses Runs ins summary_json.
            # ``record_apify_run`` läuft synchron im ``_run_actor``-Pfad ab
            # und stempelt UTC-now-Timestamps, also liegen alle Rows des
            # aktuellen Runs strikt nach ``run.started_at`` und keine
            # Concurrency-Race (parallele Cron-Runs werden vorne via
            # 409-Lock geblockt). Block wird auch bei null Calls emittiert.
            summary["apify"] = aggregate_apify_costs_since(session, run.started_at)
            # Cost-Tracking-Fix 2026-05-12 — Anthropic + OpenAI Surface.
            # Diese beiden Aggregat-Blöcke kamen vor dem Fix gar nicht im
            # Cron-Summary an: ``record_anthropic_call`` wurde aus dem
            # Brief-Pfad nie aufgerufen, und OpenAI rundete jeden Call
            # auf 0 cents ab. Jetzt liefern beide reale Sub-Cent-Werte
            # über die ``cost_usd_millicents``-Spalte.
            summary["anthropic"] = aggregate_anthropic_costs_since(session, run.started_at)
            summary["openai"] = aggregate_openai_costs_since(session, run.started_at)
            summary["budget"] = budget.to_dict()
            if budget.soft_warn_exceeded:
                summary["budget_warning"] = True
            # F0.7 surface: same shape as ``budget``, separate key so the
            # admin dashboard (and any operator-side grep) can tell at a
            # glance which provider is approaching its cap. Soft-warn
            # ($80) goes via a separate flag so the Apify-only legacy
            # ``budget_warning`` semantics keep their meaning.
            summary["anthropic_budget"] = anthropic_budget.to_dict()
            if anthropic_budget.soft_warn_exceeded:
                summary["anthropic_budget_warning"] = True
            # Cadence-Sprint 2026-05-17 — Frühwarnsignal #2 aus dem Premortem
            # (PR #147, Failure-Mode #2 "Bug regrediert nach Refactor"). Ein
            # Cron-Run mit aktivierter Brief-Gen, aber 0 generierten Briefs UND
            # <$5 Anthropic-Cost ist ein klares Signal: irgendetwas hat den
            # Pfad lautlos blockiert (Mock-Leak, ENV-Toggle-Race, Code-Pfad-
            # Regression). Logger.critical landet rot in Railway-Logs.
            briefs = summary.get("briefs", {})
            anthropic_cost_usd = summary.get("anthropic", {}).get("estimated_cost_usd", 0.0)
            if (
                briefs.get("enabled")
                and briefs.get("generated", 0) == 0
                and anthropic_cost_usd < 5.0
            ):
                logger.critical(
                    "cron_brief_gen.silent_failure",
                    extra={
                        "run_id": str(run.id),
                        "briefs_enabled": briefs.get("enabled"),
                        "briefs_generated": briefs.get("generated", 0),
                        "briefs_failed": briefs.get("failed", 0),
                        "anthropic_cost_usd": anthropic_cost_usd,
                    },
                )
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
