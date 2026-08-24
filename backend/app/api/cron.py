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
import functools
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

import hmac

import httpx
from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from app.admin_session import user_session_is_admin, verify_session_token
from app.config import settings
from app.database import engine, get_session
from app.models.entities import (
    Asset,
    CronRun,
    InsightReport as InsightReportRow,
    Post,
    TitleSyncRun,
)
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
    compute_openai_monthly_spend,
)
from app.services.cron_channel_selection import compute_run_index, select_channels_for_cron
from app.core.feature_flags import (
    is_cutter_weekly_enabled,
    is_designer_weekly_enabled,
    is_segment_roundups_enabled,
    is_trailer_intelligence_enabled,
)
from app.models.entities import SegmentRoundup as SegmentRoundupRow
from app.services.forecast import generate_er_forecast
from app.services.insight_engine import PAIRS, generate_and_persist_report
from app.services.segment_roundup import (
    generate_and_persist_roundup,
    parse_cron_roundup_segments,
)
from app.services.candidate_autopilot import run_candidate_autopilot
from app.services.candidate_llm_assist import run_candidate_llm_assist
from app.services.recommendation_snapshot import persist_recommendation_snapshot
from app.services.asset_screenshot_persistence import backfill_missing_evidence
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

# Naeherungswert der Vision-Call-Kosten fuer die Cron-Zusammenfassung.
# Die echte Abrechnung steht pro Call im costlog (record_openai_call);
# dieser Wert dient nur der Groessenordnung im Summary.
#
# Wartung 20.08.2026: von 0.015 auf 0.0027 korrigiert. Der alte Wert war
# ausdruecklich die "gpt-4o-mini Vision pricing ballpark" aus dem
# Sprint-Beta-Plan und ist seit dem Modellwechsel (gpt-4o-mini →
# gpt-5.4-nano → gpt-5.4-mini) um Faktor 5,6 zu hoch. Gemessen am
# costlog: 1.409 vision_calls auf gpt-5.4-mini kosteten 379,73 Cent,
# also ~$0,0027 pro Call.
#
# Die Zahl war nicht nur Kosmetik: sie steht in den Kommentaren, mit
# denen die beiden Vision-Deckel unten begruendet wurden — und hat sie
# damit fuenfmal zu eng gerechnet.
_VISION_COST_USD_PER_CALL = 0.0027

# Zeitbudget beider Vision-Stages zusammen, in Sekunden.
#
# Wartung 20.08.2026. Bis dahin begrenzte AUSSCHLIESSLICH die Stueckzahl
# (50 frisch + 200 Backlog), was zwei Probleme hatte: der Deckel war an
# einem 5,6-fach zu hohen Preis kalibriert, und er sagt nichts ueber die
# Laufzeit — die eigentliche Gefahr beim Hochsetzen, denn ein zu langer
# Vision-Block frisst das Gesamtbudget (CRON_TOTAL_RUN_TIMEOUT_SECONDS,
# produktiv 12.000s bei gemessenen ~7.200s Gesamtlaufzeit).
#
# Mit einem Zeitbudget begrenzt die Zeit den Lauf und nicht mehr eine
# geratene Stueckzahl. Die Deckel duerfen dadurch grosszuegig werden:
# was nicht mehr in die Zeit passt, bleibt liegen und wird im naechsten
# Lauf nachgezogen — sichtbar als ``skipped_budget`` im Summary, nicht
# still.
#
# 1800s wie title_sync, rematch und post_analysis. Bei den bisher
# beobachteten Laufzeiten (Summary-Feld ``duration_seconds`` beider
# Stages) ist das ein Vielfaches des heutigen Verbrauchs und passt auch
# dann noch, wenn eine Umgebung den Gesamtwert nicht angehoben hat.
_VISION_STAGE_BUDGET_DEFAULT_SECONDS = 1800


def _vision_stage_budget_seconds() -> int:
    raw = os.environ.get(
        "VISION_STAGE_TIMEOUT_SECONDS", str(_VISION_STAGE_BUDGET_DEFAULT_SECONDS)
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "cron-vision-budget-unparsable; falling back to default",
            extra={"raw": raw, "default": _VISION_STAGE_BUDGET_DEFAULT_SECONDS},
        )
        return _VISION_STAGE_BUDGET_DEFAULT_SECONDS
    # 0 oder negativ = kein Budget. Bewusst erlaubt: so laesst sich das
    # alte Verhalten (nur Stueckzahl begrenzt) ohne Code-Aenderung
    # wiederherstellen, falls das Budget je im Weg steht.
    return value

_VISION_SUCCESS_STATUSES = frozenset({"analyzed", "done"})
_VISION_FETCH_FAIL_STATUSES = frozenset({"fetch_failed", "no_source", "image_unreachable", "image_invalid"})

# Approximate per-post cost of the text-only post-analysis path (one Haiku
# call for format+tone, one Sonnet call for purpose+lifecycle_stage), used
# for the cron summary line only. The authoritative per-call figures are
# logged by record_anthropic_call from the real usage objects. Full pipeline
# with the Sonnet vision call is ~$0.0101; see cron_post_analysis_skip_vision.
_POST_ANALYSIS_COST_USD_PER_POST_TEXT = 0.0029
_POST_ANALYSIS_COST_USD_PER_POST_FULL = 0.0101

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
    *,
    deadline: float | None = None,
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
    counters = _vision_process_ids(session, chosen, deadline=deadline)

    duration_seconds = round(time.monotonic() - started, 2)
    estimated_cost_usd = round(counters["attempted"] * _VISION_COST_USD_PER_CALL, 4)

    return {
        **counters,
        "skipped_cap": skipped_cap,
        "duration_seconds": duration_seconds,
        "estimated_cost_usd": estimated_cost_usd,
    }


def _vision_process_ids(
    session: Session,
    asset_ids: list[UUID],
    *,
    deadline: float | None = None,
) -> dict:
    """Run ``analyze_asset_visual`` over the given asset IDs, isolating
    per-asset failures, and return the status counters. Shared by the
    fresh-asset path (``_run_vision_after_sync``) and the backlog-drain
    path (``_run_vision_backlog``) so the counting/error-isolation rules
    stay identical.

    ``deadline`` ist ein ``time.monotonic()``-Zeitpunkt (Wartung
    20.08.2026). Ist er erreicht, bricht die Schleife ab und der Rest
    landet als ``skipped_budget`` im Summary — nicht still, sondern
    zaehlbar. Beide Stages teilen sich DENSELBEN Zeitpunkt, damit die
    Summe begrenzt ist und nicht jede Stage ihr eigenes Budget
    ausschoepft.

    Geprueft wird VOR jedem Asset, nicht danach: ein einzelner Call kann
    das Budget ueberziehen (er laeuft zu Ende), aber es startet keiner
    mehr, dessen Beginn schon jenseits der Grenze liegt.
    """
    counters = {
        "attempted": 0,
        "succeeded": 0,
        "text_fallback": 0,
        "fetch_failed": 0,
        "vision_error": 0,
        "skipped_budget": 0,
    }
    for index, asset_id in enumerate(asset_ids):
        if deadline is not None and time.monotonic() >= deadline:
            counters["skipped_budget"] = len(asset_ids) - index
            logger.warning(
                "cron-vision-budget-exhausted",
                extra={
                    "processed": index,
                    "skipped_budget": counters["skipped_budget"],
                },
            )
            break
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
    deadline: float | None = None,
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

    counters = _vision_process_ids(session, candidates, deadline=deadline)
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


def _post_analysis_stage_timeout_seconds() -> int:
    """Zeitbudget der Post-Analyse-Stage in Sekunden.

    Default 1800s, wie ``title_sync`` und ``rematch`` — und aus demselben
    Grund sicher: der Gesamtlauf braucht gemessen ~7.200s, der
    Code-Default von ``CRON_TOTAL_RUN_TIMEOUT_SECONDS`` liegt bei 9.000s.
    1800s passen also auch dann noch, wenn die Umgebung den Gesamtwert
    nicht angehoben hat.

    Steht ``CRON_TOTAL_RUN_TIMEOUT_SECONDS`` hoeher (produktiv seit
    10.08.2026: 12.000s), darf dieser Wert mitwachsen — 2700s entsprechen
    bei gemessenen 3,7s/Post rund 730 Posts pro Lauf.
    """
    raw = os.environ.get("CRON_POST_ANALYSIS_STAGE_TIMEOUT_SECONDS", "1800")
    try:
        return max(1, int(raw))
    except ValueError:
        return 1800


def _run_post_analysis_backlog(
    session: Session,
    cap: int,
    *,
    skip_vision: bool,
    budget_seconds: float | None = None,
) -> dict:
    """Trailer-Intelligence Stufe 1 — classify up to ``cap`` posts that have
    no ``last_analyzed_at`` yet (format / tone / purpose / lifecycle_stage).

    Selection is **newest first**, deliberately. The user-facing consumer is
    the insight-engine recommendation builder, which aggregates over a 7-day
    window inside a 30-day frame — it only ever reads recent posts. Draining
    oldest-first would spend the whole per-run cap on 90-day-old rows while
    the current week stays unclassified, so the feature would keep starving.
    Newest-first classifies the current week in the first run; the historical
    backlog drains with whatever cap is left over on subsequent runs.

    Cost is bounded three ways: the per-run ``cap``, the text-only default
    (``skip_vision``), and the pre-existing Anthropic monthly budget
    pre-flight that aborts the whole cron run before this stage is reached.

    Per-post failures are isolated exactly like the vision stages — a bad
    post bumps a counter and the loop continues. An Anthropic auth error is
    the one exception: it is non-recoverable and would repeat for every
    remaining post, so the stage stops early and reports it.

    Zeitbudget (``budget_seconds``, Vorfall 10.08.2026)
    --------------------------------------------------
    Am 10.08. hat diese Stage mit ``cap=2500`` den gesamten Cron-Lauf ins
    Gesamt-Timeout laufen lassen: ~3,7s pro Post ergeben bei 2500 Posts
    rund 2,5 Stunden, die zur Grundlaufzeit von ~7.200s dazukamen. Der
    Lauf wurde bei 9.000s abgeschnitten — Scrape war fertig, aber Briefs,
    Roundups und Wochenbriefings fehlten komplett, weil sie hinter dieser
    Stage stehen.

    Die Lehre: ein Cap in *Posts* ist kein Schutz, weil er nichts ueber
    Zeit sagt. ``budget_seconds`` deckelt stattdessen die Wall-Clock. Die
    Pruefung sitzt bewusst **zwischen** den Posts und nicht in einem
    ``asyncio.wait_for`` um den ``to_thread``-Aufruf: ein Python-Thread
    laesst sich nicht abbrechen, ``wait_for`` wuerde nur die wartende
    Coroutine aufgeben und den Thread im Hintergrund weiterlaufen lassen.
    Kooperativ abbrechen ist hier sauber moeglich, weil die Schleife nach
    jedem Post committet — es geht nichts verloren, der Rest kommt im
    naechsten Lauf dran.
    """
    if cap <= 0:
        return {"enabled": False, "cap": cap}

    try:
        from app.services.anthropic_client import (
            AnthropicAuthError,
            is_anthropic_configured,
        )
        from app.services.post_analyzer import analyze_post
    except ImportError as exc:  # noqa: BLE001 — SDK optional, mirror admin.py
        logger.exception("post-analysis-import-failed")
        return {"enabled": False, "reason": "analyzer_unavailable", "error": str(exc)[:200]}

    if not is_anthropic_configured():
        # Staging runs without an Anthropic key on purpose (MOCK_EXTERNAL_APIS);
        # this is a normal skip, not an error.
        return {"enabled": False, "reason": "anthropic_not_configured"}

    started = time.monotonic()
    stmt = (
        select(Post)
        .where(Post.last_analyzed_at.is_(None))
        .order_by(Post.detected_at.desc())
        .limit(cap)
    )
    posts = list(session.exec(stmt).all())

    counters = {
        "attempted": 0,
        "analyzed": 0,
        "errors": 0,
        "assets_created": 0,
    }
    auth_failed = False
    timed_out = False
    error_samples: list[str] = []

    for post in posts:
        if budget_seconds is not None and time.monotonic() - started >= budget_seconds:
            # Zeitbudget aufgebraucht: geordnet aussteigen, damit die
            # nachfolgenden Stages (title_sync, rematch, Briefs, Roundups)
            # noch laufen. Alles bis hierher ist committed.
            timed_out = True
            logger.warning(
                "cron-post-analysis stage budget of %ss exhausted after %s/%s "
                "posts — stopping early so the rest of the chain can run",
                budget_seconds, counters["attempted"], len(posts),
            )
            break
        counters["attempted"] += 1
        try:
            result = analyze_post(session, post, skip_vision=skip_vision)
        except AnthropicAuthError as exc:
            # Non-recoverable and would repeat for every remaining post —
            # stop the stage instead of burning the cap on certain failures.
            session.rollback()
            logger.error("cron-post-analysis auth failed, stopping stage: %s", exc)
            auth_failed = True
            counters["attempted"] -= 1
            break
        except Exception as exc:  # noqa: BLE001 — per-post guard, mirrors vision
            session.rollback()
            logger.exception("cron-post-analysis failed for post %s", post.id)
            counters["errors"] += 1
            if len(error_samples) < 5:
                error_samples.append(f"{post.id}:{type(exc).__name__}")
            continue

        if result.status == "analyzed":
            counters["analyzed"] += 1
            if result.asset_created:
                counters["assets_created"] += 1
            session.commit()
        else:
            # analyze_post returns status='error' without writing when a
            # classifier failed twice — nothing staged, nothing to commit.
            session.rollback()
            counters["errors"] += 1
            if len(error_samples) < 5 and result.errors:
                error_samples.append(f"{post.id}:{result.errors[0][:80]}")

    per_post = (
        _POST_ANALYSIS_COST_USD_PER_POST_TEXT
        if skip_vision
        else _POST_ANALYSIS_COST_USD_PER_POST_FULL
    )
    return {
        "enabled": True,
        "cap": cap,
        "skip_vision": skip_vision,
        "selected": len(posts),
        **counters,
        "auth_failed": auth_failed,
        "timed_out": timed_out,
        # Was das Budget nicht mehr geschafft hat. Nicht verloren — die
        # Posts haben weiterhin kein ``last_analyzed_at`` und werden im
        # naechsten Lauf erneut ausgewaehlt.
        "remaining": len(posts) - counters["attempted"],
        "budget_seconds": budget_seconds,
        "error_samples": error_samples,
        "duration_seconds": round(time.monotonic() - started, 2),
        "estimated_cost_usd": round(counters["attempted"] * per_post, 4),
    }


def _title_sync_stage_timeout_seconds() -> int:
    # Default 1800s (Diagnose-Folge 2026-07-06): der ursprüngliche 3600s-Schätzwert
    # (siehe Docstring unten) ist durch die Batch-Commit-Fix jetzt weit über dem
    # gemessenen Bedarf — zwei saubere Läufe post-Fix brauchten 516s/523s. 1800s
    # gibt ~3,5x Puffer über dem Messwert (Katalog wächst pro Woche weiter, da
    # title_sync bewusst ohne Seiten-Cap läuft) und deckt sich mit dem
    # REMATCH_STAGE_TIMEOUT_SECONDS-Default für konsistente Erwartungen.
    raw = os.environ.get("TITLE_SYNC_STAGE_TIMEOUT_SECONDS", "1800")
    try:
        return max(1, int(raw))
    except ValueError:
        return 1800


def _mark_stuck_title_sync_run_error(session: Session, timeout_s: int) -> None:
    """Best-effort audit-cleanup nach einem Stage-Timeout.

    ``asyncio.wait_for`` cancelt die Coroutine mit ``CancelledError`` — die
    leitet sich von ``BaseException`` ab und wird vom ``except Exception`` in
    ``sync_titles_from_tmdb`` NICHT gefangen, also bleibt die dort angelegte
    ``TitleSyncRun``-Row auf ``running`` haengen (genau das Zombie-Symptom der
    17.06.-Rows). Hier die juengste laufende tmdb-Row auf ``error`` setzen.
    Defensiv: die Session kann nach dem Cancel mitten in einer Transaktion
    stehen, daher erst ``rollback``; ein Fehlschlag hier darf die Stage nicht
    kippen (sie ist ohnehin schon als Timeout verbucht)."""
    try:
        session.rollback()
        stuck = session.exec(
            select(TitleSyncRun)
            .where(TitleSyncRun.source == "tmdb", TitleSyncRun.status == "running")
            .order_by(TitleSyncRun.created_at.desc())
        ).first()
        if stuck is not None:
            stuck.status = "error"
            stuck.error_message = f"stage_timeout after {timeout_s}s"
            session.add(stuck)
            session.commit()
    except Exception:  # noqa: BLE001 — best-effort audit cleanup
        logger.exception("failed to mark timed-out title_sync run as error")


async def _run_evidence_backfill_stage(session: Session) -> dict:
    """Evidence-Backfill als Cron-Stage (22.08.2026).

    Der Scrape captured jedes neue Asset sofort — transiente Fehler
    (CDN-Timeout, Storage-Schluckauf) liessen Assets aber dauerhaft
    ohne gespeichertes Bild zurueck. Diese Stage holt Captures fuer
    junge Assets nach, solange die Quelle noch lebt. Kill-Switch +
    Deckel + Zeit-Budget aus den Settings; best-effort.
    """
    if not settings.evidence_backfill_in_cron:
        return {"skipped": True, "reason": "disabled"}
    try:
        return await backfill_missing_evidence(
            session,
            max_assets=settings.evidence_backfill_max_assets,
            budget_seconds=settings.evidence_backfill_budget_seconds,
            max_age_days=settings.evidence_backfill_max_age_days,
        )
    except Exception as exc:  # noqa: BLE001 — Stage-Guard, Muster Autopilot
        logger.exception("evidence-backfill stage failed")
        return {"error": str(exc)[:500]}


async def _run_candidate_llm_assist_stage(session: Session) -> dict:
    """KI-Pruefung der Rest-Vorschlaege als Cron-Stage (22.08.2026).

    Der Autopilot schliesst nur Exakt-Treffer; alles Uebrige blieb bis
    heute liegen, bis Wolf im Admin den Button klickte. Diese Stage
    laesst denselben Service (identischer Code-Pfad wie der Klick,
    inkl. Fortschritts-Marker und Kosten-Log) einmal pro Lauf mit
    groesserem Batch laufen.

    Kill-Switch: ``settings.candidate_llm_assist_in_cron`` (Default an).
    Batch-Deckel: ``settings.candidate_llm_assist_cron_max`` — bei
    Haiku-Preisen kostet ein voller 60er-Batch um die 2 Cent; der
    Anthropic-Monatsdeckel greift zusaetzlich ueber das Kosten-Log.
    Best-effort: ein Fehler hier kippt den Lauf nicht.
    """
    if not settings.candidate_llm_assist_in_cron:
        return {"skipped": True, "reason": "disabled"}
    try:
        ergebnis = await asyncio.to_thread(
            run_candidate_llm_assist,
            session,
            max_candidates=settings.candidate_llm_assist_cron_max,
        )
        return ergebnis.to_dict()
    except Exception as exc:  # noqa: BLE001 — Stage-Guard, Muster Autopilot
        logger.exception("candidate-llm-assist stage failed")
        return {"error": str(exc)[:500]}


def _run_recommendation_snapshot_stage(session: Session) -> dict:
    """Empfehlungs-Snapshot als Cron-Stage (22.08.2026).

    Friert die MACHEN-Empfehlungen der Woche ein (Tabelle
    ``recommendation_snapshot``) — die Grundlage fuer das
    Vorher/Nachher-Design der Wir-Schleife. Jede Woche ohne Snapshot
    ist eine verlorene Messwoche, deshalb laeuft die Stage in JEDEM
    Lauf (Re-Run derselben Woche ueberschreibt, Last-Write-Wins).

    Deterministisch und LLM-frei (nur DB-Lesen + eine JSON-Row);
    ``settings.recommendation_snapshot_in_cron`` ist ein reiner
    Not-Aus. Best-effort: ein Fehler hier kippt den Lauf nicht.
    """
    if not settings.recommendation_snapshot_in_cron:
        return {"skipped": True, "reason": "disabled"}
    try:
        return persist_recommendation_snapshot(session)
    except Exception as exc:  # noqa: BLE001 — Stage-Guard, Muster Autopilot
        logger.exception("recommendation-snapshot stage failed")
        return {"error": str(exc)[:500]}


async def _run_title_sync_after_scrape(session: Session) -> dict:
    """Pull the TMDb title catalogue (movies + TV series) as a cron stage,
    BEFORE the rematch step so freshly synced titles are available to match
    against in the same run. Mirrors the brief-gen contract: per-stage
    try/except (a TMDb outage must not fail the whole cron) and a kill-switch.

    Kill-Switch: ENV ``ENABLE_TITLE_SYNC_IN_CRON`` (Default ``true``). Off →
    stage skipped, scrape/rematch/briefs run normally; no code deploy needed.
    ``sync_titles_from_tmdb`` writes its own ``TitleSyncRun`` audit row, so
    idempotency and logging are already handled there.

    Stage-Timeout (Sprint 2026-06-29, Default datengetrieben nachjustiert
    2026-07-06): der Pass laeuft in ``asyncio.wait_for`` (ENV
    ``TITLE_SYNC_STAGE_TIMEOUT_SECONDS``, Default 1800s — siehe Begruendung
    in ``_title_sync_stage_timeout_seconds``). Bei Timeout wird die Stage als
    ``error`` verbucht, die haengende Run-Row auf ``error`` gesetzt und der
    Cron laeuft zu rematch/briefs weiter — der ewig-``running``-Zustand
    (5,4h-Hang 29.06., 5,8-Tage-Hang 16.06.) ist damit gedeckelt. Die
    Stage-Dauer (``duration_seconds``) landet im Summary.

    WICHTIG — der Timeout deckelt die ASYNC-Wall-Clock (httpx-Discover,
    kumulative Pagination). Ein rein SYNCHRON blockierender ``session.commit``
    (die bestaetigte 29.06.-Hangstelle in ``_upsert_normalized_title``) haelt
    den Event-Loop und kann hier NICHT unterbrochen werden — den faengt der
    ``statement_timeout`` auf Engine-Ebene (separater Commit). Die zwei Hebel
    sind komplementaer, nicht redundant.
    """
    enabled = os.getenv("ENABLE_TITLE_SYNC_IN_CRON", "true").lower() == "true"
    if not enabled:
        logger.info("title_sync.skipped reason=env_disabled")
        return {"enabled": False}
    timeout_s = _title_sync_stage_timeout_seconds()
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(sync_titles_from_tmdb(session), timeout=timeout_s)
    except asyncio.TimeoutError:
        elapsed = round(time.monotonic() - started, 1)
        logger.error(
            "cron title-sync stage timed out after %ss (limit %ss)", elapsed, timeout_s
        )
        _mark_stuck_title_sync_run_error(session, timeout_s)
        return {
            "enabled": True,
            "error": f"stage_timeout after {timeout_s}s",
            "timed_out": True,
            "duration_seconds": elapsed,
        }
    except Exception as exc:  # noqa: BLE001 — top-level guard, best-effort stage
        elapsed = round(time.monotonic() - started, 1)
        logger.exception("cron title-sync failed")
        return {"enabled": True, "error": str(exc)[:500], "duration_seconds": elapsed}
    elapsed = round(time.monotonic() - started, 1)
    logger.info("title_sync.complete duration_seconds=%s", elapsed)
    return {"enabled": True, "duration_seconds": elapsed, **result}


def _rematch_stage_timeout_seconds() -> int:
    raw = os.environ.get("REMATCH_STAGE_TIMEOUT_SECONDS", "1800")
    try:
        return max(1, int(raw))
    except ValueError:
        return 1800


async def _run_rematch_after_sync(session: Session) -> dict:
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

    Stage-Timeout (Diagnose-Folge 2026-07-06): der title_sync-Fix zieht pro
    Studio den kompletten TMDb-Backkatalog ohne Seiten-Cap (Absicht, wegen der
    frueheren 96%-Match-Luecke) — der Titel-Katalog ist dadurch stark
    gewachsen. Der 06.07.-Lauf hat dadurch beim Rematch das komplette
    2h-Gesamtbudget (``CRON_TOTAL_RUN_TIMEOUT_SECONDS``) verbraucht, OHNE dass
    Briefs/Roundups/Cutter-Weekly noch liefen. Eigener Timeout (ENV
    ``REMATCH_STAGE_TIMEOUT_SECONDS``, Default 1800s = 30min) deckelt das:
    bei Ueberschreitung wird die Stage als Timeout verbucht und der Cron
    laeuft zu Briefs/Roundups weiter — die fuer die woechentliche Cadence
    wichtiger sind als ein vollstaendiger Rematch-Durchlauf in derselben
    Woche (unmatched Assets werden beim naechsten Lauf erneut versucht).

    Soft-Deadline (Cron-Run 16421771, 20.07.2026): der 20.07.-Lauf traf das
    harte 1800s-Limit erneut (Katalog ~29k Titel, wachsender unmatched-
    Bestand). ``wait_for`` kann den ``asyncio.to_thread``-Worker nicht
    abbrechen — der Zombie-Thread lief frueher parallel zur Brief-Stage auf
    DERSELBEN Session weiter (Sessions sind nicht threadsafe). Deshalb
    bekommt ``rematch_unassigned_assets`` jetzt ein Zeitbudget 120s unter
    dem Stage-Limit und bricht SELBST sauber ab: Teilstand committet,
    ``partial``/``remaining`` in der Summary, Rest beim naechsten Lauf.
    Das harte ``wait_for`` bleibt als Havarie-Backstop bestehen (greift nur
    noch, wenn ein EINZELNER Asset-Durchlauf >120s haengt).
    """
    timeout_s = _rematch_stage_timeout_seconds()
    # 120s Marge: genug fuer den letzten Batch-Commit + Rueckkehr, bevor der
    # harte Backstop feuert. ``max(1, …)`` haelt Mini-Timeouts (Tests) sinnvoll.
    soft_budget_s = max(1.0, timeout_s - 120.0)
    started = time.monotonic()
    logger.info("rematch.start")
    try:
        summary = await asyncio.wait_for(
            asyncio.to_thread(
                rematch_unassigned_assets, session, time_budget_seconds=soft_budget_s
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        elapsed = round(time.monotonic() - started, 1)
        logger.error(
            "cron rematch stage timed out after %ss (limit %ss)", elapsed, timeout_s
        )
        return {
            "error": f"stage_timeout after {timeout_s}s",
            "timed_out": True,
            "duration_seconds": elapsed,
        }
    except Exception as exc:  # noqa: BLE001 — top-level guard, see PC-4
        elapsed = round(time.monotonic() - started, 1)
        logger.exception("auto-rematch after cron sync failed")
        return {"error": str(exc)[:500], "duration_seconds": elapsed}
    elapsed = round(time.monotonic() - started, 1)
    summary_dict = summary.to_dict()
    # Zeit-Aufteilung mitloggen (24.08.2026): Der Deckel wurde angehoben,
    # weil die Stage zwei Sekunden pro Asset brauchte — wohin die gingen,
    # war unbekannt. Die Aufteilung steht ab jetzt in derselben Zeile wie
    # die Gesamtdauer, damit die naechste Entscheidung gemessen ist.
    _zeiten = (
        "setup=%ss match=%ss kandidaten=%ss commit=%ss assets/s=%s"
        % (
            summary_dict.get("setup_seconds"),
            summary_dict.get("match_seconds"),
            summary_dict.get("candidate_seconds"),
            summary_dict.get("commit_seconds"),
            summary_dict.get("assets_pro_sekunde"),
        )
    )
    if summary_dict.get("partial"):
        logger.warning(
            "rematch.partial duration_seconds=%s checked=%s remaining=%s %s",
            elapsed, summary_dict.get("checked"), summary_dict.get("remaining"),
            _zeiten,
        )
    else:
        logger.info("rematch.complete duration_seconds=%s %s", elapsed, _zeiten)
    return {**summary_dict, "duration_seconds": elapsed}


def _truncate_head_tail(text: str, *, head: int = 2000, tail: int = 2000) -> str:
    """Diagnose-Instrumentierung (2026-06-22): kuerzt einen Roh-LLM-Output
    fuer den Cron-Diagnose-Block auf die ersten ``head`` + letzten ``tail``
    Zeichen, mit Marker dazwischen. So landet genug Kontext (Anfang + Ende
    des JSON, wo Truncation/Parse-Fehler typischerweise sichtbar werden) im
    Summary, ohne das Log mit einem 20k-Token-Blob zu fluten. Reiner Modell-
    JSON-Text — keine Secrets/Prompts/PII."""
    if len(text) <= head + tail:
        return text
    dropped = len(text) - head - tail
    return f"{text[:head]}…[TRUNCATED {dropped} chars]…{text[-tail:]}"


def _run_brief_generation_after_sync(
    session: Session,
    *,
    brief_now: datetime | None = None,
    force: bool = False,
    pairs: list[str] | None = None,
) -> dict:
    """Cadence-Sprint 2026-05-17 — Brief-Generation als Cron-Stage.

    Iteriert über alle ``enabled=True``-Pairs aus ``PAIRS`` und ruft je Pair
    ``generate_and_persist_report`` für die *gerade abgeschlossene* ISO-
    Woche auf (``now - 1 day`` als ISO-Anker, siehe Aufrufer-Kommentar).
    Per-Pair-Try/Except, damit ein fehlschlagender Pair die anderen nicht
    killt — der gleiche Robustness-Vertrag wie bei
    ``_run_rematch_after_sync``.

    Manueller On-Demand-Modus (Admin-Button "Jetzt komplett aktualisieren"):
    ``brief_now`` und ``force`` werden vom Aufrufer durchgereicht. Default
    ``brief_now=None`` → ``utcnow - 1 day`` (abgeschlossene KW, wöchentlicher
    GitHub-Action-Pfad byte-identisch). ``force=True`` (laufende KW + Force-
    Overwrite): der Cache-Hit-Pre-Check wird übersprungen und
    ``generate_and_persist_report`` mit ``force=True, replace=True`` aufgerufen
    (explizite UPSERT-Semantik, siehe insight_engine PR #150). Im Nicht-Force-
    Pfad bleibt alles wie zuvor — Pre-Check + ``skipped_cache_hit``-Counter.

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

    if brief_now is None:
        brief_now = datetime.now(timezone.utc) - timedelta(days=1)
    iso_cal = brief_now.isocalendar()
    target_iso_year, target_iso_week = iso_cal.year, iso_cal.week
    enabled_pairs = [k for k, v in PAIRS.items() if v.get("enabled", False)]
    # Pair-gescopter Selektiv-Lauf (Sprint 16.06.2026): ``pairs`` schneidet die
    # Brief-Stage auf genau die angeforderten Keys; alle anderen Stages
    # (Scrape/Rematch/Vision/Roundups/Cutter/Forecast) bleiben unberuehrt. Die
    # Gueltigkeit (bekannt + enabled) ist bereits im Endpoint geprueft (400),
    # daher hier reines Intersect — None = kein Filter (heutiges Verhalten).
    if pairs is not None:
        requested = set(pairs)
        enabled_pairs = [k for k in enabled_pairs if k in requested]

    logger.info(
        "brief_gen.start",
        extra={
            "pairs": len(enabled_pairs),
            "pairs_filter": sorted(pairs) if pairs is not None else None,
            "target_iso_year": target_iso_year,
            "target_iso_week": target_iso_week,
            "force": force,
        },
    )

    for pair_key in enabled_pairs:
        # Force-Pfad (manueller On-Demand-Lauf): Cache-Hit-Pre-Check
        # überspringen, damit ein bestehender Brief der laufenden KW
        # überschrieben wird. Nicht-Force-Pfad: unverändert — vorhandener
        # Brief zählt als skipped_cache_hit, kein LLM-Call.
        if not force:
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
                force=force,
                replace=force,
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
                # Diagnose-Instrumentierung (2026-06-22, additiv): den
                # konkreten Treiber aufschluesseln statt pauschal
                # ``no_llm_output``. ``failure_diagnostic`` traegt
                # ``{kind, detail}`` aus dem Kernel; ``raw_llm_text`` den
                # (gekuerzten) Roh-Output. Faellt auf das alte Sammel-Label
                # zurueck, falls die Diagnose fehlt (z.B. report is None).
                diag = (
                    getattr(report, "failure_diagnostic", None)
                    if report is not None
                    else None
                ) or {}
                error_class = diag.get("kind") or "no_llm_output"
                error_message = diag.get("detail") or (
                    "llm_output is None (JSON-parse/schema/citation/truncation "
                    "failure, brief not persisted)"
                )
                error_entry = {
                    "pair": pair_key,
                    "error_class": error_class,
                    "error_message": str(error_message)[:500],
                }
                raw_llm_text = (
                    getattr(report, "raw_llm_text", None)
                    if report is not None
                    else None
                )
                diagnostic: dict = {}
                if diag.get("kind"):
                    diagnostic["kind"] = diag["kind"]
                if diag.get("detail"):
                    diagnostic["detail"] = diag["detail"]
                if raw_llm_text:
                    diagnostic["raw_llm_output"] = _truncate_head_tail(raw_llm_text)
                if diagnostic:
                    error_entry["diagnostic"] = diagnostic
                briefs_summary["errors"].append(error_entry)
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


def _run_segment_roundups_after_briefs(
    session: Session,
    *,
    brief_now: datetime | None = None,
    force: bool = False,
) -> dict:
    """Master-Plan-Schritt-4 — Segment-Roundup-Block additiv NACH dem
    Pair-Brief-Block. Reihenfolge ist Pflicht (Wolf-Konzept §6): Pair-
    Briefs zuerst, Roundups danach, damit bei mid-Run-Cap-Abbruch
    zuerst die Roundups entfallen, nie ein Pair-Brief.

    Manueller On-Demand-Modus: ``brief_now``/``force`` analog zum Pair-Brief-
    Block. Default ``brief_now=None`` → ``utcnow - 1 day`` (abgeschlossene KW).
    ``force=True`` überspringt den Cache-Hit-Pre-Check; ``generate_and_persist_
    roundup`` persistiert ohnehin Last-Write-Wins (``_persist_roundup`` macht
    delete-then-insert), sodass der bestehende Roundup der laufenden KW
    überschrieben wird. "Komplett heißt komplett" — Roundups werden mitgeforct.

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

    if brief_now is None:
        brief_now = datetime.now(timezone.utc) - timedelta(days=1)
    iso_cal = brief_now.isocalendar()
    target_iso_year, target_iso_week = iso_cal.year, iso_cal.week

    logger.info(
        "roundups.start",
        extra={
            "segments": [s.value for s in segments],
            "target_iso_year": target_iso_year,
            "target_iso_week": target_iso_week,
            "force": force,
        },
    )

    for segment in segments:
        # Force-Pfad: Cache-Hit-Pre-Check überspringen → generate_and_persist_
        # roundup überschreibt den bestehenden Roundup (Last-Write-Wins).
        if not force:
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


def _run_er_forecast_warmup_after_roundups(session: Session) -> dict:
    """#252 — Einordnungs-Cache der ER-Prognose pro Pair vorwärmen.

    Läuft additiv NACH den Roundups (gleiche Stelle im Cron wie die
    anderen Anthropic-Stufen). Pro enabled-Pair ein
    ``generate_er_forecast(apply_gate=True)``: Cache-Miss erzeugt genau
    einen kleinen Opus-Call (max. 400 Output-Tokens, costlog-erfasst als
    ``operation='er_forecast'``) und persistiert die Einordnung in
    ``er_forecast_einordnung`` — die öffentlichen Aufrufe der Woche lesen
    dann nur noch. Cache-Hit (z.B. Re-Run derselben KW) kostet nichts.

    Budget-Pre-Flight exakt nach dem Roundup-Muster: F0.7-Cap-Re-Check
    direkt vor dem Block, damit ein mid-Run-Cap-Trigger nur die
    Forecast-Einordnungen entfallen lässt — Briefs/Roundups sind hier
    bereits persistiert. Die Regression selbst bleibt für User auch ohne
    Einordnung verfügbar (Live-Berechnung, gratis).
    """
    warmup_summary: dict = {
        "pairs_total": 0,
        "generated": 0,
        "cache_hits": 0,
        "no_einordnung": 0,
        "failed": 0,
        "errors": [],
    }

    cap_check = compute_anthropic_monthly_spend(session)
    if cap_check.hard_cap_exceeded and cap_check.enforced:
        warmup_summary["skipped"] = True
        warmup_summary["reason"] = "anthropic_budget_exceeded"
        logger.warning(
            "er_forecast_warmup.skipped reason=anthropic_budget_exceeded "
            "spent=%d/%d cents",
            cap_check.spent_usd_cents, cap_check.budget_usd_cents,
        )
        return warmup_summary

    enabled_pairs = [k for k, v in PAIRS.items() if v.get("enabled", False)]
    for pair_key in sorted(enabled_pairs):
        warmup_summary["pairs_total"] += 1
        try:
            result = generate_er_forecast(
                session, pair_key, PAIRS[pair_key], apply_gate=True
            )
        except Exception as exc:  # noqa: BLE001 — per-pair isolation
            logger.exception("er_forecast_warmup.failed pair=%s", pair_key)
            warmup_summary["failed"] += 1
            warmup_summary["errors"].append({
                "pair_key": pair_key,
                "error_class": type(exc).__name__,
                "error_message": str(exc)[:200],
            })
            continue
        source = result.get("einordnung_source")
        if source == "generated":
            warmup_summary["generated"] += 1
        elif source == "cache":
            warmup_summary["cache_hits"] += 1
        else:
            # keine Einordnung möglich (kein ok-Markt / LLM aus) — ok.
            warmup_summary["no_einordnung"] += 1

    logger.info(
        "er_forecast_warmup.complete generated=%d cache_hits=%d failed=%d",
        warmup_summary["generated"], warmup_summary["cache_hits"],
        warmup_summary["failed"],
    )
    return warmup_summary


def _run_cutter_weekly_after_forecasts(
    session: Session,
    *,
    brief_now: datetime | None = None,
    force: bool = False,
) -> dict:
    """Cutter-Wochenbriefing-Block (Master-Plan-Sprint 2026-06-12) —
    additiv NACH dem ER-Forecast-Warmup. Die Position ist Absicht:

    1. Alle Input-Blobs der Woche (Pair-Briefs, Roundups) sind zu diesem
       Zeitpunkt persistiert — das Briefing liest ausschliesslich daraus.
    2. Der Forecast-Einordnungs-Cache ist warm — der Signal-Read via
       ``generate_er_forecast(apply_gate=True)`` trifft nur Cache und
       kostet nichts extra (die Regression selbst ist ohnehin LLM-frei).

    Mechanik exakt nach dem Warmup-/Roundup-Muster:
    - Feature-Flag-Gate ``FEATURE_CUTTER_WEEKLY_ENABLED`` (Trockenlauf,
      Default off) — Flag off = Cron-Verhalten exakt wie vorher.
    - Eigener F0.7-Cap-Re-Check direkt vor dem Block: ein mid-Run-Cap-
      Trigger laesst nur das Cutter-Briefing entfallen, Briefs/Roundups/
      Einordnungen sind hier bereits durch.
    - Cache-Hit-Check auf PK ``(iso_year, iso_week)``: existing-Row →
      kein LLM-Call (Re-Run derselben KW kostet nichts). ``force=True``
      ueberspringt den Check; die Persistenz ist Last-Write-Wins.
    - Maximal EIN LLM-Call pro Woche (plus begrenzte Retry-Anlaeufe bei
      Schema-/Citation-Fail, siehe ``generate_cutter_weekly``); jeder
      bezahlte Call landet einzeln im costlog
      (``operation='cutter_weekly'``).
    """
    # Lazy import: haelt den Modul-Load von cron.py frei von der
    # cutter_weekly→insight_engine-Kette (analog Roundup-Pfad-Imports oben
    # waere top-level auch ok — der Block ist aber flag-gated Trockenlauf).
    from app.models.entities import CutterWeeklyBriefing
    from app.services.cutter_weekly import generate_and_persist_cutter_weekly

    enabled = is_cutter_weekly_enabled()
    summary: dict = {
        "enabled": enabled,
        "skipped": False,
        "generated": 0,
        "skipped_cache_hit": 0,
        "failed": 0,
    }
    if not enabled:
        summary["skipped"] = True
        summary["reason"] = "feature_flag_off"
        logger.info("cutter_weekly.skipped reason=feature_flag_off")
        return summary

    cap_check = compute_anthropic_monthly_spend(session)
    if cap_check.hard_cap_exceeded and cap_check.enforced:
        summary["skipped"] = True
        summary["reason"] = "anthropic_budget_exceeded"
        summary["anthropic_budget"] = cap_check.to_dict()
        logger.warning(
            "cutter_weekly.skipped reason=anthropic_budget_exceeded "
            "spent=%d/%d cents (%.1f%%)",
            cap_check.spent_usd_cents, cap_check.budget_usd_cents,
            cap_check.pct_used * 100,
        )
        return summary

    if brief_now is None:
        brief_now = datetime.now(timezone.utc) - timedelta(days=1)
    iso_cal = brief_now.isocalendar()
    target_iso_year, target_iso_week = iso_cal.year, iso_cal.week
    summary["iso_year"] = target_iso_year
    summary["iso_week"] = target_iso_week

    if not force:
        existing = session.get(
            CutterWeeklyBriefing, (target_iso_year, target_iso_week)
        )
        if existing is not None:
            summary["skipped_cache_hit"] = 1
            logger.info(
                "cutter_weekly.cache_hit iso_year=%d iso_week=%d",
                target_iso_year, target_iso_week,
            )
            return summary

    try:
        report = generate_and_persist_cutter_weekly(session, now=brief_now)
    except Exception as exc:  # noqa: BLE001 — Block-Isolation: ein Fehler
        # hier darf den Cron-Run-Status nicht kippen (Briefs/Roundups sind
        # bereits persistiert, der naechste Lauf holt das Briefing nach).
        logger.exception("cutter_weekly.failed")
        summary["failed"] = 1
        summary["error"] = {
            "error_class": type(exc).__name__,
            "error_message": str(exc)[:200],
        }
        return summary

    summary["generated"] = 1
    summary["model"] = report.model
    summary["llm_output_present"] = report.llm_output is not None
    summary["released_platforms"] = [
        p.platform
        for p in report.evidence.platforms
        if p.status == "pattern_released"
    ]
    if report.cost_usd_estimate:
        summary["cost_usd_cents"] = int(round(report.cost_usd_estimate * 100))
    logger.info(
        "cutter_weekly.complete",
        extra={
            "iso_year": target_iso_year,
            "iso_week": target_iso_week,
            "model": report.model,
            "released_platforms": summary["released_platforms"],
            "llm_output_present": summary["llm_output_present"],
        },
    )
    return summary


def _run_designer_weekly_after_cutter_weekly(
    session: Session,
    *,
    brief_now: datetime | None = None,
    force: bool = False,
) -> dict:
    """Designer-Wochenbriefing-Block (Sprint 2026-07-06) — additiv NACH dem
    Cutter-Wochenbriefing, mirror ``_run_cutter_weekly_after_forecasts``
    Feld-fuer-Feld. Die Position ist Absicht: beide Wochenbriefings teilen
    dieselbe Evidenz-Pipeline und lesen aus denselben bereits persistierten
    Blobs; die Reihenfolge zueinander ist nicht kausal, aber additiv-nach-
    Cutter haelt die Cron-Historie linear lesbar (ein weiterer Trockenlauf-
    Block, kein Um-Sortieren bestehender Bloecke).

    Mechanik exakt nach dem Cutter-Muster:
    - Feature-Flag-Gate ``FEATURE_DESIGNER_WEEKLY_ENABLED`` (Trockenlauf,
      Default off, UNABHAENGIG vom Cutter-Flag) — Flag off = Cron-Verhalten
      exakt wie vorher.
    - Eigener F0.7-Cap-Re-Check direkt vor dem Block: ein mid-Run-Cap-
      Trigger laesst nur das Designer-Briefing entfallen, alle vorherigen
      Bloecke (inkl. Cutter-Weekly) sind hier bereits durch.
    - Cache-Hit-Check auf PK ``(iso_year, iso_week)``: existing-Row → kein
      LLM-Call (Re-Run derselben KW kostet nichts). ``force=True``
      ueberspringt den Check; die Persistenz ist Last-Write-Wins.
    - Maximal EIN LLM-Call pro Woche (plus begrenzte Retry-Anlaeufe bei
      Schema-/Citation-Fail, siehe ``generate_designer_weekly``); jeder
      bezahlte Call landet einzeln im costlog
      (``operation='designer_weekly'``).
    """
    # Lazy import: haelt den Modul-Load von cron.py frei von der
    # designer_weekly→insight_engine-Kette (analog Cutter-Block-Import
    # oben) — der Block ist flag-gated Trockenlauf.
    from app.models.entities import DesignerWeeklyBriefing
    from app.services.designer_weekly import generate_and_persist_designer_weekly

    enabled = is_designer_weekly_enabled()
    summary: dict = {
        "enabled": enabled,
        "skipped": False,
        "generated": 0,
        "skipped_cache_hit": 0,
        "failed": 0,
    }
    if not enabled:
        summary["skipped"] = True
        summary["reason"] = "feature_flag_off"
        logger.info("designer_weekly.skipped reason=feature_flag_off")
        return summary

    cap_check = compute_anthropic_monthly_spend(session)
    if cap_check.hard_cap_exceeded and cap_check.enforced:
        summary["skipped"] = True
        summary["reason"] = "anthropic_budget_exceeded"
        summary["anthropic_budget"] = cap_check.to_dict()
        logger.warning(
            "designer_weekly.skipped reason=anthropic_budget_exceeded "
            "spent=%d/%d cents (%.1f%%)",
            cap_check.spent_usd_cents, cap_check.budget_usd_cents,
            cap_check.pct_used * 100,
        )
        return summary

    if brief_now is None:
        brief_now = datetime.now(timezone.utc) - timedelta(days=1)
    iso_cal = brief_now.isocalendar()
    target_iso_year, target_iso_week = iso_cal.year, iso_cal.week
    summary["iso_year"] = target_iso_year
    summary["iso_week"] = target_iso_week

    if not force:
        existing = session.get(
            DesignerWeeklyBriefing, (target_iso_year, target_iso_week)
        )
        if existing is not None:
            summary["skipped_cache_hit"] = 1
            logger.info(
                "designer_weekly.cache_hit iso_year=%d iso_week=%d",
                target_iso_year, target_iso_week,
            )
            return summary

    try:
        report = generate_and_persist_designer_weekly(session, now=brief_now)
    except Exception as exc:  # noqa: BLE001 — Block-Isolation: ein Fehler
        # hier darf den Cron-Run-Status nicht kippen (Briefs/Roundups/
        # Cutter-Weekly sind bereits persistiert, der naechste Lauf holt
        # das Briefing nach).
        logger.exception("designer_weekly.failed")
        summary["failed"] = 1
        summary["error"] = {
            "error_class": type(exc).__name__,
            "error_message": str(exc)[:200],
        }
        return summary

    summary["generated"] = 1
    summary["model"] = report.model
    summary["llm_output_present"] = report.llm_output is not None
    summary["released_platforms"] = [
        p.platform
        for p in report.evidence.platforms
        if p.status == "pattern_released"
    ]
    if report.cost_usd_estimate:
        summary["cost_usd_cents"] = int(round(report.cost_usd_estimate * 100))
    logger.info(
        "designer_weekly.complete",
        extra={
            "iso_year": target_iso_year,
            "iso_week": target_iso_week,
            "model": report.model,
            "released_platforms": summary["released_platforms"],
            "llm_output_present": summary["llm_output_present"],
        },
    )
    return summary


def _run_pattern_briefing_after_designer_weekly(
    session: Session,
    *,
    brief_now: datetime | None = None,
    force: bool = False,
) -> dict:
    """Pattern-Briefing-Block (Trailer-Intelligence Stufe 1, Schritt 3,
    20.08.2026) — additiv NACH dem Designer-Wochenbriefing, Mechanik
    Feld-fuer-Feld nach dem Cutter-/Designer-Muster:

    - Feature-Flag-Gate ``FEATURE_TRAILER_INTELLIGENCE_ENABLED``
      (Wolf-Entscheidung 20.08.: Generierung automatisch im Montags-Cron,
      hinter dem TI-Flag — in Production laeuft der Block erst, wenn Wolf
      das Flag nach der Abnahme setzt; Flag off = Cron exakt wie vorher).
    - Eigener F0.7-Cap-Re-Check direkt vor dem Block.
    - Beide Modi (``genre`` und ``title`` — Wolf-Entscheidung "Beides,
      Genre zuerst") mit je eigenem Cache-Hit-Check auf den PK
      ``(mode, iso_year, iso_week)`` und je eigener Fehler-Isolation:
      ein Fehler im Genre-Lauf laesst den Titel-Lauf nicht sterben.
      ``force=True`` ueberspringt die Cache-Checks (Last-Write-Wins).
    - Maximal EIN LLM-Call je Modus pro Woche (plus JSON-Retry-
      Anlaeufe); im Leerlauf (keine belastbaren Muster) faellt gar
      KEIN Call an, die Row wird mit ``model='none'`` persistiert.
    """
    # Lazy import analog Cutter-/Designer-Block: haelt den Modul-Load von
    # cron.py frei von der pattern_briefing→insight_engine-Kette.
    from app.models.entities import PatternBriefing
    from app.services.pattern_briefing import (
        BRIEFING_MODES,
        generate_and_persist_pattern_briefing,
    )

    enabled = is_trailer_intelligence_enabled()
    summary: dict = {
        "enabled": enabled,
        "skipped": False,
        "generated": 0,
        "skipped_cache_hit": 0,
        "failed": 0,
    }
    if not enabled:
        summary["skipped"] = True
        summary["reason"] = "feature_flag_off"
        logger.info("pattern_briefing.skipped reason=feature_flag_off")
        return summary

    cap_check = compute_anthropic_monthly_spend(session)
    if cap_check.hard_cap_exceeded and cap_check.enforced:
        summary["skipped"] = True
        summary["reason"] = "anthropic_budget_exceeded"
        summary["anthropic_budget"] = cap_check.to_dict()
        logger.warning(
            "pattern_briefing.skipped reason=anthropic_budget_exceeded "
            "spent=%d/%d cents (%.1f%%)",
            cap_check.spent_usd_cents, cap_check.budget_usd_cents,
            cap_check.pct_used * 100,
        )
        return summary

    if brief_now is None:
        brief_now = datetime.now(timezone.utc) - timedelta(days=1)
    iso_cal = brief_now.isocalendar()
    target_iso_year, target_iso_week = iso_cal.year, iso_cal.week
    summary["iso_year"] = target_iso_year
    summary["iso_week"] = target_iso_week

    summary["modes"] = {}
    cost_cents_total = 0
    for mode in BRIEFING_MODES:
        mode_summary: dict = {}
        summary["modes"][mode] = mode_summary
        if not force:
            existing = session.get(
                PatternBriefing, (mode, target_iso_year, target_iso_week)
            )
            if existing is not None:
                summary["skipped_cache_hit"] += 1
                mode_summary["skipped_cache_hit"] = True
                logger.info(
                    "pattern_briefing.cache_hit mode=%s iso_year=%d iso_week=%d",
                    mode, target_iso_year, target_iso_week,
                )
                continue

        try:
            report = generate_and_persist_pattern_briefing(
                session, mode=mode, now=brief_now
            )
        except Exception as exc:  # noqa: BLE001 — Block-Isolation wie bei
            # Cutter/Designer, hier je Modus: ein Fehler darf weder den
            # Cron-Run-Status kippen noch den anderen Modus verhindern;
            # der naechste Lauf holt das fehlende Briefing nach.
            logger.exception("pattern_briefing.failed mode=%s", mode)
            summary["failed"] += 1
            mode_summary["error"] = {
                "error_class": type(exc).__name__,
                "error_message": str(exc)[:200],
            }
            continue

        summary["generated"] += 1
        mode_summary["model"] = report.model
        mode_summary["llm_output_present"] = report.llm_output is not None
        mode_summary["bausteine"] = (
            len(report.llm_output.bausteine) if report.llm_output else 0
        )
        mode_summary["citation_dropped"] = report.citation_dropped
        if report.cost_usd_estimate:
            cost_cents_total += int(round(report.cost_usd_estimate * 100))
        logger.info(
            "pattern_briefing.complete",
            extra={
                "mode": mode,
                "iso_year": target_iso_year,
                "iso_week": target_iso_week,
                "model": report.model,
                "bausteine": mode_summary["bausteine"],
                "citation_dropped": report.citation_dropped,
                "llm_output_present": mode_summary["llm_output_present"],
            },
        )
    if cost_cents_total:
        summary["cost_usd_cents"] = cost_cents_total
    return summary


def _cron_total_timeout_seconds() -> int:
    # Re-Audit 2026-07-06: mit title_sync (1800s) + rematch (1800s) beide auf
    # ihrem eigenen Timeout UND den weiterhin ungedeckelten Stages (Scrape/
    # Vision/Briefs/Roundups/Forecasts/Cutter- und Designer-Weekly, beobachtet
    # ~3200-3500s zusammen) blieben beim alten 7200s-Default nur noch ~2-7min
    # Puffer — der neue Designer-Weekly-Block hat die Marge weiter verengt.
    # 9000s (2,5h) stellt wieder eine komfortable Marge her, ohne das normale
    # Laufzeitverhalten zu aendern (dieser Wert greift ohnehin nur im
    # Worst-Case-Havariefall, siehe Docstring von
    # ``_run_cron_sync_background``).
    raw = os.environ.get("CRON_TOTAL_RUN_TIMEOUT_SECONDS", "9000")
    try:
        return max(1, int(raw))
    except ValueError:
        return 9000


async def _ping_cron_heartbeat(success: bool) -> None:
    """Optionaler Healthchecks.io-artiger Dead-Man's-Switch-Ping nach jedem
    Cron-Lauf (Diagnose-Folge 2026-07-06 — es gab bisher kein Alerting; ein
    haengender/fehlgeschlagener Lauf fiel nur auf, wenn zufaellig jemand aufs
    Dashboard schaute). ``settings.cron_heartbeat_url`` leer = No-Op.

    Bei Erfolg wird die Basis-URL gepingt, bei Fehlschlag ``/fail`` angehaengt
    (healthchecks.io-Konvention) — der Dienst alarmiert dann sofort statt erst
    nach Ablauf des Erwartungsfensters. Best-effort: ein Fehler beim Ping-Call
    selbst (Netzwerk, DNS, ...) darf den Cron-Lauf nicht kippen — nur geloggt.
    """
    url = settings.cron_heartbeat_url
    if not url:
        return
    target = url if success else f"{url.rstrip('/')}/fail"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.get(target)
    except httpx.HTTPError:
        logger.warning("cron-heartbeat-ping-failed", extra={"success": success, "url": target})


async def _run_cron_sync_background(
    run_id: UUID,
    run_index: int,
    target_week: str = "completed",
    force: bool = False,
    brief_pairs: list[str] | None = None,
) -> None:
    """Global safety-net wrapper around ``_run_cron_sync_background_impl``.

    Diagnose 2026-07-06: der 06.07.-Lauf brauchte allein 27,9 Min fuer die
    Title-Sync-Stage (``title_sync.complete duration_seconds=1671.6``) —
    dieselbe Company-Axis-Discover-Schleife, die am 16.06./29.06. 5,8 Tage
    bzw. 5,4h haengen blieb (PR #287). Die dortigen Fixes (Stage-``wait_for``
    + Postgres-``statement_timeout``) decken NUR die Title-Sync-Stage; jede
    andere Stage (Vision/Rematch/Briefs/Roundups/Cutter-Weekly, alle via
    ``asyncio.to_thread``) hat weiterhin keine Wall-Clock-Decke. Dieser
    ENV-``CRON_TOTAL_RUN_TIMEOUT_SECONDS``-Timeout (Default 7200s = 2h, weit
    ueber der bisher beobachteten Worst-Case-Laufzeit) ist der letzte
    Ausweg: egal WELCHE Stage in Zukunft haengt, der komplette Lauf terminiert
    garantiert und der ``CronRun`` wird als ``error`` verbucht statt fuer
    Tage/Stunden auf ``running`` zu bleiben und den naechsten Montag zu
    blockieren.

    Bekannte Grenze (analog zum Stage-Timeout-Kommentar unten): ``wait_for``
    kann einen ``asyncio.to_thread``-Aufruf nicht wirklich abbrechen — der
    zugrunde liegende OS-Thread laeuft im Hintergrund weiter, bis die
    synchrone Funktion selbst zurueckkehrt (der Thread haelt dann noch
    dieselbe Session, ein spaeter Commit ist moeglich). Bei einem Timeout
    von 2h gegen eine Worst-Case-Beobachtung von <30 Min ist das ein
    akzeptabler Trade-off fuer einen Wert, der ausschliesslich als
    Last-Resort-Schutz gegen einen echten, sonst unbegrenzten Haenger dient.
    """
    timeout_s = _cron_total_timeout_seconds()
    try:
        await asyncio.wait_for(
            _run_cron_sync_background_impl(run_id, run_index, target_week, force, brief_pairs),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.error(
            "cron run %s exceeded total timeout of %ss - marking as error",
            run_id, timeout_s,
        )
        with Session(engine) as session:
            run = session.get(CronRun, run_id)
            if run is not None and run.status == "running":
                run.status = "error"
                run.error_message = f"total_run_timeout after {timeout_s}s"
                run.completed_at = datetime.now(timezone.utc)
                session.add(run)
                session.commit()
        await _ping_cron_heartbeat(success=False)


async def _run_cron_sync_background_impl(
    run_id: UUID,
    run_index: int,
    target_week: str = "completed",
    force: bool = False,
    brief_pairs: list[str] | None = None,
) -> None:
    """Background task body. Owns its own Session — the request session is
    closed by the time this runs.

    ``target_week``/``force`` steuern den manuellen On-Demand-Lauf (Admin-
    Button "Jetzt komplett aktualisieren"). Defaults (``completed``/``False``)
    = wöchentlicher GitHub-Action-Pfad, byte-identisch zum bisherigen
    Verhalten: ``target_week="completed"`` → ``brief_now = utcnow - 1 day``
    (gerade abgeschlossene KW), kein Force. ``target_week="current"`` +
    ``force=True`` → ``brief_now = utcnow`` (laufende KW) mit Force-Overwrite
    der Brief-/Roundup-Stages. Scrape/Vision/title_sync/rematch sind
    KW-agnostisch und laufen in beiden Modi identisch."""
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
                await _ping_cron_heartbeat(success=False)
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
                await _ping_cron_heartbeat(success=False)
                return

            # Incident 2026-07-13 (Re-Audit-Folgefund) — OpenAI-Monatsbudget-
            # Pre-Flight. Exakt analog zu Apify/Anthropic darueber: bislang
            # der einzige der drei kostenpflichtigen Provider ganz ohne
            # Deckel, obwohl Vision-Analyse + Caption-Analyse real und
            # ungebremst Kosten verursachen (~500-700 Calls/Woche).
            openai_budget = compute_openai_monthly_spend(session)
            if openai_budget.hard_cap_exceeded and openai_budget.enforced:
                summary = {
                    "skipped": True,
                    "reason": "openai_budget_exceeded",
                    "budget": budget.to_dict(),
                    "anthropic_budget": anthropic_budget.to_dict(),
                    "openai_budget": openai_budget.to_dict(),
                }
                run.summary_json = summary
                run.status = "budget_exceeded"
                run.completed_at = datetime.now(timezone.utc)
                session.add(run)
                session.commit()
                logger.warning(
                    "cron run %s aborted: openai budget %d/%d cents (%.1f%%)",
                    run_id, openai_budget.spent_usd_cents,
                    openai_budget.budget_usd_cents,
                    openai_budget.pct_used * 100,
                )
                await _ping_cron_heartbeat(success=False)
                return

            # Brief-/Roundup-Ziel-KW: laufende KW (manueller Force-Lauf) vs.
            # gerade abgeschlossene KW (wöchentlicher Default). Siehe
            # Docstring + H4-Mitigation-Kommentar am Brief-Stage-Aufruf.
            brief_now = (
                datetime.now(timezone.utc)
                if target_week == "current"
                else datetime.now(timezone.utc) - timedelta(days=1)
            )

            summary, created_asset_ids = await _execute_platform_sync(session, run_index)
            # Audit: Lauf-Modus ins summary_json, damit GET /runs den manuellen
            # Force-Lauf vom wöchentlichen Cron unterscheidbar macht.
            summary["run_mode"] = {"target_week": target_week, "force": force}
            cap = settings.cron_vision_max_assets_per_run
            # Ein Zeitpunkt fuer BEIDE Vision-Stages (Wartung 20.08.2026):
            # frisch und Backlog teilen sich das Budget, sonst koennte
            # jede Stage es einzeln ausschoepfen und die Summe waere
            # doppelt so gross wie gedacht.
            vision_budget = _vision_stage_budget_seconds()
            vision_deadline = (
                time.monotonic() + vision_budget if vision_budget > 0 else None
            )
            summary["vision_budget_seconds"] = vision_budget
            if created_asset_ids:
                # to_thread: die synchronen Langläufer-Stages (Vision/Backlog/
                # Rematch/Briefs/Roundups) laufen blockierende I/O (OpenAI/
                # Anthropic). Direkt im async-Loop aufgerufen würden sie den
                # einen uvicorn-Event-Loop für die volle (~50-100 Min) Lauf-
                # dauer starven → health/Admin/Frontend hängen. Auf einem
                # Worker-Thread bleibt der Loop frei. STRIKT sequenziell
                # einzeln awaiten (KEIN gather): alle Stages teilen die EINE
                # BG-Task-Session (Z. oben) — parallele Threads auf derselben
                # Session würden kollidieren. Die Reihenfolge der Kette bleibt.
                summary["vision"] = await asyncio.to_thread(
                    functools.partial(
                        _run_vision_after_sync,
                        session,
                        created_asset_ids,
                        cap,
                        deadline=vision_deadline,
                    )
                )
            # Backlog-Drain (Dauerfix gegen feed-forward-Lücke): nach den
            # frisch erzeugten Assets bis zu N älteste ``pending``-Assets
            # nachziehen. created_asset_ids-Selektion/Cap oben bleibt
            # unberührt; backlog_cap=0 deaktiviert den Pfad.
            backlog_cap = settings.cron_vision_backlog_max_assets_per_run
            if backlog_cap > 0:
                summary["vision_backlog"] = await asyncio.to_thread(
                    functools.partial(
                        _run_vision_backlog,
                        session,
                        backlog_cap,
                        exclude_ids=created_asset_ids,
                        deadline=vision_deadline,
                    )
                )
            # Trailer-Intelligence Stufe 1 — Post-Klassifikation (format /
            # tone / purpose / lifecycle_stage) fuer alle Posts ohne
            # ``last_analyzed_at``. Bis 08/2026 lief das ausschliesslich am
            # manuellen Admin-Endpunkt, entsprechend duenn war die Abdeckung
            # (12%). Gleiches to_thread-/Stage-Guard-Muster wie die
            # Vision-Stages darueber; Cap + text-only bremsen die Kosten.
            post_analysis_cap = settings.cron_post_analysis_max_posts_per_run
            if post_analysis_cap > 0:
                try:
                    summary["post_analysis"] = await asyncio.to_thread(
                        _run_post_analysis_backlog,
                        session,
                        post_analysis_cap,
                        skip_vision=settings.cron_post_analysis_skip_vision,
                        # Zeitbudget statt nur Post-Cap — siehe Docstring
                        # von ``_run_post_analysis_backlog``. Ohne das hat
                        # diese Stage am 10.08. den ganzen Lauf gerissen.
                        budget_seconds=_post_analysis_stage_timeout_seconds(),
                    )
                except Exception as exc:  # noqa: BLE001 — Stage-Guard, Muster rematch
                    logger.exception("post-analysis stage failed")
                    summary["post_analysis"] = {"error": str(exc)[:500]}
            # Evidence-Backfill (22.08.2026): direkt nach Scrape+Vision,
            # solange die CDN-Links der frischen Posts noch leben.
            summary["evidence_backfill"] = await _run_evidence_backfill_stage(session)
            # Title-Katalog-Sync (Movies + TV) VOR dem Rematch, damit frisch
            # gezogene Titel im selben Lauf gematcht werden. Hinter
            # ENABLE_TITLE_SYNC_IN_CRON (Default true); eigener try/except.
            summary["title_sync"] = await _run_title_sync_after_scrape(session)
            summary["rematch"] = await _run_rematch_after_sync(session)
            # Sprint Review-Automatisierung 2026-07-20 — Kandidaten-Autopilot
            # direkt NACH dem Rematch (frische Titel sind dann in der
            # Whitelist): bestaetigt Exakt-Treffer-Vorschlaege automatisch
            # und schliesst Karteileichen. Best-effort wie der Rematch —
            # ein Fehler hier kippt den Lauf nicht.
            if settings.candidate_autopilot_enabled:
                try:
                    autopilot = await asyncio.to_thread(
                        run_candidate_autopilot, session
                    )
                    summary["candidate_autopilot"] = autopilot.to_dict()
                except Exception as exc:  # noqa: BLE001 — Stage-Guard, Muster rematch
                    logger.exception("candidate-autopilot stage failed")
                    summary["candidate_autopilot"] = {"error": str(exc)[:500]}
            else:
                summary["candidate_autopilot"] = {"skipped": True, "reason": "disabled"}
            # KI-Pruefung der Rest-Vorschlaege (22.08.2026) — direkt NACH
            # dem Autopiloten: was der kostenlose Exakt-Treffer-Pfad nicht
            # schliesst, bekommt Haiku-Zuordnungen bzw. -Notizen. Die
            # Montags-Queue kommt damit VORGEPRUEFT bei Wolf an ("Titel
            # fehlt im Katalog: X" steht schon dran, statt erst nach
            # manuellen Klicks). Best-effort wie der Autopilot.
            summary["candidate_llm_assist"] = await _run_candidate_llm_assist_stage(session)
            # Empfehlungs-Snapshot (22.08.2026) — NACH Rematch/Autopilot,
            # damit die Zellen auf den frisch zugeordneten Daten der Woche
            # rechnen. Friert die MACHEN-Empfehlungen mit Zeitstempel ein;
            # ohne das gibt es kein Vorher/Nachher fuer die Wir-Schleife.
            summary["recommendation_snapshot"] = _run_recommendation_snapshot_stage(session)
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
            summary["briefs"] = await asyncio.to_thread(
                _run_brief_generation_after_sync,
                session, brief_now=brief_now, force=force, pairs=brief_pairs,
            )
            # Master-Plan-Schritt-4 — Segment-Roundup-Block additiv NACH
            # den Pair-Briefs (Konzept §6, Wolf-Festlegung 25.05.). Der
            # zweite F0.7-Cap-Check im Roundup-Block stellt sicher, dass
            # bei mid-Run-Cap-Triggern ausschliesslich die Roundups
            # entfallen — Pair-Briefs sind hier bereits persistiert.
            # Hinter ``FEATURE_SEGMENT_ROUNDUPS_ENABLED``: Flag off =
            # Cron-Verhalten exakt wie vor Schritt 4.
            summary["roundups"] = await asyncio.to_thread(
                _run_segment_roundups_after_briefs, session, brief_now=brief_now, force=force
            )
            # #252 — ER-Forecast-Einordnungs-Warmup additiv NACH den
            # Roundups; eigener F0.7-Re-Check im Block (s. Docstring).
            summary["er_forecasts"] = await asyncio.to_thread(
                _run_er_forecast_warmup_after_roundups, session
            )
            # Master-Plan-Sprint Cutter-Wochenbriefing — additiv NACH dem
            # Forecast-Warmup (Input-Blobs persistiert, Signal-Read trifft
            # nur Cache). Flag-gated Trockenlauf, eigener F0.7-Re-Check.
            summary["cutter_weekly"] = await asyncio.to_thread(
                _run_cutter_weekly_after_forecasts, session,
                brief_now=brief_now, force=force,
            )
            # Sprint 2026-07-06 — Designer-Wochenbriefing additiv NACH dem
            # Cutter-Wochenbriefing (mirror, eigenes Flag+F0.7-Re-Check,
            # siehe Docstring von ``_run_designer_weekly_after_cutter_weekly``).
            summary["designer_weekly"] = await asyncio.to_thread(
                _run_designer_weekly_after_cutter_weekly, session,
                brief_now=brief_now, force=force,
            )
            # Trailer-Intelligence Stufe 1, Schritt 3 (20.08.2026) —
            # Pattern-Briefing additiv NACH dem Designer-Wochenbriefing.
            # Hinter FEATURE_TRAILER_INTELLIGENCE_ENABLED (Wolf: Cron-
            # Ausloesung, aber Production erst nach Abnahme via Flag),
            # eigener F0.7-Re-Check im Block.
            summary["pattern_briefing"] = await asyncio.to_thread(
                _run_pattern_briefing_after_designer_weekly, session,
                brief_now=brief_now, force=force,
            )
            # Playbook-Montags-Mail (Radar, 20.08.2026) — additiv NACH dem
            # Pattern-Briefing, damit frische Bausteine mitgehen. Rein
            # lesend + Mailversand, kein LLM-Call; gated im Service
            # (TI-Flag, PLAYBOOK_MAIL_RECIPIENTS, nichts-zu-berichten).
            # Eigene Fehler-Isolation: eine kaputte Mail darf den
            # Cron-Status nicht kippen.
            try:
                from app.services.pattern_playbook import send_pattern_playbook

                summary["pattern_playbook"] = await send_pattern_playbook(
                    session, now=brief_now
                )
            except Exception:  # noqa: BLE001 — Block-Isolation wie oben
                logger.exception("pattern_playbook.failed")
                summary["pattern_playbook"] = {"failed": True}
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
            # Incident 2026-07-13 — dritter Provider-Cap-Block, gleiches
            # Schema wie ``budget``/``anthropic_budget`` oben.
            summary["openai_budget"] = openai_budget.to_dict()
            if openai_budget.soft_warn_exceeded:
                summary["openai_budget_warning"] = True
            # Cadence-Sprint 2026-05-17 — Frühwarnsignal #2 aus dem Premortem
            # (PR #147, Failure-Mode #2 "Bug regrediert nach Refactor").
            # Logger.critical landet rot in Railway-Logs.
            #
            # Variante B (PR #270): Der Trigger stützt sich NICHT mehr auf eine
            # Anthropic-Kostenschwelle. Kosten sind kein Erfolgssignal — sie
            # sind niedrig bei legitimem Cache-Hit (force=false-Re-Run auf eine
            # abgeschlossene Woche cached alle Pairs, $0) UND hoch bei teuren
            # Totalausfällen (jeder Pair ruft das LLM, schlägt aber nach dem
            # Call fehl). Die alte ``cost < $5``-Bedingung erzeugte deshalb
            # beides: Fehlalarme beim Cache und eine Blindstelle bei teuren
            # Komplettausfällen. Stattdessen prüfen wir zwei echte Ausfall-
            # muster gegen die Counter selbst:
            #   1. silent     — der Pfad hat nichts getan (nichts generiert,
            #      nichts gecacht, nichts versucht/gescheitert): Mock-Leak,
            #      ENV-Toggle-Race, Code-Pfad-Regression.
            #   2. all_failed — kein Brief frisch erzeugt UND mindestens die
            #      Hälfte aller enabled-Pairs gescheitert (Quote, Variante C,
            #      PR #270). Schema-Garantie: jeder enabled-Pair landet in genau
            #      einem Bucket, also ``generated + failed + cache_hit ==
            #      Anzahl enabled-Pairs`` — die Quote ``failed / total`` ist
            #      damit ohne ``PAIRS``-Import wohldefiniert. Die 0.5-Schwelle
            #      ist nicht willkürlich: bei ``generated == 0`` heißt sie
            #      "die Mehrheit der versuchten Pairs ist gescheitert und null
            #      frische Briefs liegen vor" — ein echter (Teil-)Totalausfall.
            #      Cache-Hits zählen dabei in ``total`` mit, maskieren aber
            #      nichts: ein einzelner Cache-Hit neben vielen Failures drückt
            #      die Quote nicht unter 0.5 (0/8/1 → 0.89 → Alarm), und ein
            #      einzelnes Failure neben vielen Cache-Hits löst keinen
            #      Fehlalarm aus (0/1/8 → 0.11 → still). ``total > 0`` ist
            #      zwingender Division-Guard: ein enabled-Lauf ohne Pairs hat
            #      total=0 und fällt sauber in ``silent`` (kein ZeroDivision).
            # Ein reiner Cache-Run (generated=0, failed=0, skipped_cache_hit>0)
            # ist KEIN Ausfall und löst keinen Alarm aus. ``anthropic_cost_usd``
            # bleibt im Payload als Diagnose-Info, ist aber kein Trigger mehr.
            briefs = summary.get("briefs", {})
            anthropic_cost_usd = summary.get("anthropic", {}).get("estimated_cost_usd", 0.0)
            generated = briefs.get("generated", 0)
            failed = briefs.get("failed", 0)
            cache_hit = briefs.get("skipped_cache_hit", 0)
            enabled = briefs.get("enabled")

            total = generated + failed + cache_hit
            silent = enabled and total == 0
            all_failed = (
                enabled
                and generated == 0
                and total > 0
                and (failed / total) >= 0.5
            )

            if silent or all_failed:
                logger.critical(
                    "cron_brief_gen.silent_failure",
                    extra={
                        "run_id": str(run.id),
                        "briefs_enabled": enabled,
                        "briefs_generated": generated,
                        "briefs_failed": failed,
                        "briefs_skipped_cache_hit": cache_hit,
                        "failure_mode": "all_failed" if all_failed else "silent",
                        "anthropic_cost_usd": anthropic_cost_usd,
                    },
                )
            run.summary_json = summary
            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)
            session.add(run)
            session.commit()
            logger.info("cron run %s completed: %s", run_id, summary)
            # Nur der Infrastruktur-Erfolg (Scrape/Vision/Rematch/Briefs-Stage
            # lief durch) zaehlt hier als "success" — ein stilles Brief-Gen-
            # Komplettversagen (silent/all_failed oben) hat sein eigenes
            # logger.critical-Signal und ist bewusst kein zweiter Heartbeat-
            # Kanal, um die Alarm-Semantik nicht zu vermischen.
            await _ping_cron_heartbeat(success=True)
        except Exception as exc:  # noqa: BLE001 — top-level guard, status persists
            logger.exception("cron run %s failed", run_id)
            run.status = "failed"
            run.error_message = str(exc)[:500]
            run.completed_at = datetime.now(timezone.utc)
            session.add(run)
            session.commit()
            await _ping_cron_heartbeat(success=False)


def require_cron_trigger_auth(
    request: Request,
    cr_admin_session: str | None = Cookie(default=None),
    cr_user_session: str | None = Cookie(default=None),
) -> None:
    """Audit 2026-08-17: sync-all nur noch per Admin-Session ODER dediziertem
    ``CRON_API_TOKEN``. Das allgemeine ``API_TOKEN`` liegt im oeffentlichen
    Frontend-Bundle (client.js, dort als "effectively public" dokumentiert)
    und berechtigt nicht mehr zum Ausloesen des teuersten Endpoints. Die
    Bearer-Middleware (app/auth.py) prueft weiterhin, DASS ein gueltiger
    Token vorliegt — hier wird geprueft, WELCHER.

    Akzeptierte Aufrufer:
    - Admin-UI: gueltige Admin-Session (Cookie) oder Admin-User-Session.
    - GitHub-Action-Fallback: ``Authorization: Bearer <CRON_API_TOKEN>``.
    - Dev/Tests ohne jede Auth-Schicht (beide Master-Schalter aus): offen —
      Production erzwingt beide Schalter per Boot-Check in main.py.
    """
    if not settings.auth_enabled and not settings.admin_auth_enabled:
        return
    if settings.admin_auth_enabled:
        if user_session_is_admin(cr_user_session):
            return
        secret = settings.admin_session_secret
        if secret and cr_admin_session and verify_session_token(cr_admin_session, secret):
            return
    cron_token = settings.cron_api_token
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    presented = auth_header.removeprefix("Bearer ").strip()
    if cron_token and presented and hmac.compare_digest(
        presented.encode("utf-8"), cron_token.encode("utf-8")
    ):
        return
    raise HTTPException(
        status_code=403,
        detail=(
            "sync-all verlangt eine Admin-Session oder den dedizierten "
            "CRON_API_TOKEN — das allgemeine API_TOKEN reicht nicht mehr."
        ),
    )


@router.post("/sync-all", dependencies=[Depends(require_cron_trigger_auth)])
async def cron_sync_all(
    background_tasks: BackgroundTasks,
    target_week: str = Query(
        "completed",
        pattern="^(completed|current)$",
        description=(
            "``completed`` (Default, wöchentlicher GitHub-Action-Cron): Briefs/"
            "Roundups für die gerade abgeschlossene KW (``utcnow - 1 day``). "
            "``current`` (manueller Admin-Button): laufende KW (``utcnow``)."
        ),
    ),
    force: bool = Query(
        False,
        description=(
            "Force-Overwrite: bestehende Briefs/Roundups der Ziel-KW werden "
            "neu generiert (UPSERT) statt per Cache-Hit übersprungen. Default "
            "``false`` hält den wöchentlichen Cron byte-identisch."
        ),
    ),
    pairs: str | None = Query(
        None,
        description=(
            "Kommagetrennte Pair-Keys — NUR diese Pairs werden in der "
            "Brief-Gen-Stage (neu) generiert; alle anderen Stages "
            "(Scrape/Rematch/Vision/Roundups/Cutter/Forecast) laufen "
            "unverändert voll. Ohne Wert: alle enabled Pairs (heutiges "
            "Verhalten). Spart NUR Brief-Kosten — Roundups, Cutter und Scrape "
            "kosten unverändert. Unbekannte oder disabled Pairs → 400."
        ),
    ),
    session: Session = Depends(get_session),
):
    # Query-Params (nicht Body): der GitHub-Action-``curl`` sendet einen leeren
    # POST ohne Body — ein required/optionaler Pydantic-Body würde dort
    # 422en. Ohne Query-Params greifen die Defaults completed/false →
    # wöchentlicher Lauf unverändert.

    # Pair-Filter (Sprint 16.06.2026): synchron im Handler validieren, damit ein
    # 400 den Aufrufer erreicht statt im Background-Task verloren zu gehen. Die
    # eigentliche Arbeit bleibt im BackgroundTask → Antwort weiterhin 202.
    brief_pairs: list[str] | None = None
    if pairs is not None:
        requested = [p.strip() for p in pairs.split(",") if p.strip()]
        if not requested:
            return JSONResponse(
                status_code=400,
                content={"detail": "pairs-Param gesetzt aber leer."},
            )
        unknown = [p for p in requested if p not in PAIRS]
        if unknown:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": (
                        f"Unbekannte Pairs: {unknown}. "
                        f"Verfügbar: {sorted(PAIRS.keys())}"
                    )
                },
            )
        disabled = [p for p in requested if not PAIRS[p].get("enabled", False)]
        if disabled:
            return JSONResponse(
                status_code=400,
                content={"detail": f"Pair(s) disabled: {disabled}."},
            )
        brief_pairs = requested

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

    background_tasks.add_task(
        _run_cron_sync_background, run.id, run_index, target_week, force, brief_pairs
    )

    logger.info(
        "cron-sync queued: run_id=%s run_index=%d target_week=%s force=%s pairs=%s",
        run.id, run_index, target_week, force, brief_pairs,
    )

    return JSONResponse(
        status_code=202,
        content={
            "run_id": str(run.id),
            "started_at": run.started_at.isoformat(),
            "status": "running",
            "run_index": run_index,
            "target_week": target_week,
            "force": force,
            "pairs": brief_pairs,
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
