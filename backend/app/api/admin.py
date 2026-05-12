"""Admin endpoints.

Permanent endpoints after Task 4.5 cleanup + Sprint 5.2.3:

- ``GET /api/admin/cost-summary``: aggregate read over creative_radar.costlog
  for daily monitoring (W4 Task 4.4 / F0.6).
- ``POST /api/admin/youtube/sync/{channel_id}``: pull recent videos for a
  YouTube channel via Data API v3 and persist them as Posts (Sprint
  5.2.3). Lazy-imports the connector inside the handler so a load-time
  bug in ``app.services.youtube_connector`` only fails this one route
  rather than crashing the entire admin router on boot.

The W4 throwaway endpoints — run-schema-migration, run-schema-rollback,
run-alembic-upgrade — were removed in Task 4.5 once the F0.2/F2.18/F0.6
migrations were confirmed stable in production. The underlying scripts
under backend/scripts/ are retained as maintenance tooling: they can be
invoked manually from a Railway shell or replicated via a fresh
short-lived endpoint if a future migration ever needs orchestrating.

Auth: every endpoint here is gated by the global Bearer-auth middleware
(W4 Task 4.3) and reads ``settings.api_token``. There is no separate
admin token; the historical layer-drift between an earlier per-endpoint
token check and the global middleware is described in
PHASE_4_DONE.md Lesson 6.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models.entities import Channel, CostLog, Post
from app.services.anthropic_client import (
    AnthropicAPIError,
    AnthropicAuthError,
    AnthropicRateLimitError,
)
from app.services.budget_check import compute_apify_monthly_spend
from app.services.insight_engine import PAIRS, generate_and_persist_report

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------- Cost summary (Task 4.4 / F0.6, permanent) -----------------


def _default_window() -> tuple[datetime, datetime]:
    """Default range: from start of the current calendar month (UTC) up to now.
    Wolf can override via ?from_date=&to_date= query params."""
    now = datetime.now(timezone.utc)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    return month_start, now


def _parse_iso_date(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        # Accept YYYY-MM-DD (treated as UTC midnight) or full ISO timestamp.
        if len(value) == 10:
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date '{value}': expected ISO 8601 (YYYY-MM-DD or full timestamp)",
        ) from exc


@router.get("/cost-summary")
def cost_summary(
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
    group_by: Literal["day", "provider", "operation"] = Query("provider"),
    session: Session = Depends(get_session),
) -> dict:
    """Aggregate the cost_log table into a small summary suitable for daily
    monitoring. Auth runs through the global Bearer-auth middleware — no
    separate ADMIN token here.

    Buckets are grouped by ``group_by`` (default 'provider'). Each bucket
    carries cost in EUR and USD cents plus the row count. EUR cents are
    snapshot-rate values from logging time, so adjusting
    ``settings.usd_to_eur_rate`` later does NOT retroactively change them.
    """
    default_from, default_to = _default_window()
    start = _parse_iso_date(from_date, default_from)
    end = _parse_iso_date(to_date, default_to)
    if start > end:
        raise HTTPException(status_code=400, detail="from_date must be <= to_date")

    statement = (
        select(CostLog)
        .where(CostLog.timestamp >= start)
        .where(CostLog.timestamp <= end)
        .order_by(CostLog.timestamp.asc())
    )
    rows = list(session.exec(statement).all())

    buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"count": 0, "cost_usd_cents": 0, "cost_eur_cents": 0}
    )
    total_usd = 0
    total_eur = 0

    for row in rows:
        if group_by == "day":
            key = row.timestamp.date().isoformat()
        elif group_by == "operation":
            key = f"{row.provider}:{row.operation}"
        else:  # provider
            key = row.provider
        buckets[key]["count"] += 1
        buckets[key]["cost_usd_cents"] += row.cost_usd_cents
        buckets[key]["cost_eur_cents"] += row.cost_eur_cents
        total_usd += row.cost_usd_cents
        total_eur += row.cost_eur_cents

    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "group_by": group_by,
        "total_count": len(rows),
        "total_cost_usd_cents": total_usd,
        "total_cost_eur_cents": total_eur,
        "buckets": [
            {"key": key, **values}
            for key, values in sorted(buckets.items())
        ],
    }


@router.get("/budget-status")
def budget_status(session: Session = Depends(get_session)) -> dict:
    """Sprint F0.6 — current Apify monthly budget snapshot.

    Returns the same ``BudgetStatus`` payload that ``_run_cron_sync_background``
    consults for its pre-flight check, so Wolf can verify mid-month whether
    the next weekend's cron run will be allowed through. Bearer-auth runs
    through the global middleware — no separate ADMIN token here.
    """
    return compute_apify_monthly_spend(session).to_dict()


# ---------- YouTube sync (Sprint 5.2.3) -------------------------------


def _channel_handle_or_id(channel: Channel) -> str:
    """Pick the best identifier YouTube Data API v3 will accept. Channels
    seeded with a UCxxx id should use that; otherwise fall back to the
    handle (with the @ that the connector strips internally) or the URL's
    last path segment as a final attempt."""
    handle = (channel.handle or "").strip()
    if handle:
        return handle if handle.startswith(("@", "UC")) else f"@{handle}"
    url = (channel.url or "").rstrip("/")
    if "/@" in url:
        tail = url.split("/@", 1)[1].split("/", 1)[0]
        if tail:
            return f"@{tail}"
    if "/channel/" in url:
        tail = url.split("/channel/", 1)[1].split("/", 1)[0]
        if tail:
            return tail
    return ""


@router.post("/youtube/sync/{channel_id}")
def youtube_sync(
    channel_id: UUID,
    session: Session = Depends(get_session),
) -> dict:
    """Pull recent videos for ``channel_id`` from the YouTube Data API
    and persist them as ``Post`` rows. Idempotent: the unique constraint
    on ``post.post_url`` plus an explicit pre-check skip already-stored
    videos. Asset rows / AI analysis are deliberately NOT created here —
    that pipeline lands uniformly across all platforms in Sprint 5.3.

    Errors map to:
    - 401: API key missing/invalid
    - 404: channel UUID unknown OR YouTube has no such handle
    - 429: YouTube quota exhausted
    - 503: connector module unavailable (lazy-import failure)
    """
    try:
        from app.services.youtube_connector import (
            YouTubeAuthError,
            YouTubeNotFoundError,
            YouTubeQuotaExceededError,
            fetch_channel_videos,
            is_youtube_configured,
            normalize_youtube_video,
        )
    except ImportError as exc:  # noqa: BLE001
        logger.exception("youtube-connector-import-failed")
        raise HTTPException(
            status_code=503,
            detail=f"YouTube connector unavailable: {exc}",
        ) from exc

    if not is_youtube_configured():
        raise HTTPException(
            status_code=401,
            detail="YOUTUBE_API_KEY ist nicht gesetzt. Bitte in Railway konfigurieren.",
        )

    channel = session.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail=f"Channel {channel_id} nicht gefunden.")
    if channel.platform != "youtube":
        raise HTTPException(
            status_code=400,
            detail=f"Channel {channel_id} hat platform='{channel.platform}', erwartet 'youtube'.",
        )

    lookup = _channel_handle_or_id(channel)
    if not lookup:
        raise HTTPException(
            status_code=400,
            detail="Channel hat weder handle noch auswertbare URL — kein YouTube-Lookup möglich.",
        )

    try:
        _, raw_videos = fetch_channel_videos(lookup)
    except YouTubeAuthError as exc:
        raise HTTPException(status_code=401, detail=f"YouTube auth failed: {exc}") from exc
    except YouTubeQuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=f"YouTube quota exhausted: {exc}") from exc
    except YouTubeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"YouTube channel not found: {exc}") from exc

    synced = 0
    skipped = 0
    errors: list[str] = []

    for raw in raw_videos:
        try:
            normalized = normalize_youtube_video(raw)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"normalize:{type(exc).__name__}:{exc}")
            continue
        post_url = normalized.get("post_url")
        if not post_url:
            errors.append("missing-post-url")
            continue
        existing = session.exec(select(Post).where(Post.post_url == post_url)).first()
        if existing:
            skipped += 1
            continue
        post = Post(
            channel_id=channel.id,
            platform="youtube",
            post_url=post_url,
            external_id=normalized.get("external_id"),
            published_at=normalized.get("published_at"),
            caption=normalized.get("caption") or "",
            raw_payload=normalized.get("raw") or {},
            visible_likes=normalized.get("visible_likes"),
            visible_comments=normalized.get("visible_comments"),
            visible_views=normalized.get("visible_views"),
            visible_shares=normalized.get("visible_shares"),
            visible_bookmarks=normalized.get("visible_bookmarks"),
            duration_seconds=normalized.get("duration_seconds"),
            media_type="youtube",
        )
        try:
            session.add(post)
            session.commit()
            synced += 1
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            errors.append(f"persist:{type(exc).__name__}:{exc}")

    # Three quota units per sync (channels.list + playlistItems.list + videos.list).
    # If the channel had zero uploads, only the first two ran — keep the
    # number honest.
    quota_units_used = 3 if raw_videos else 2

    return {
        "channel_id": str(channel.id),
        "platform": "youtube",
        "synced_videos": synced,
        "skipped_videos": skipped,
        "errors": errors,
        "quota_units_used": quota_units_used,
    }


# ---------- Cross-platform AI analysis (Sprint 5.3.1) -----------------


@router.post("/analyze/{channel_id}")
def analyze_channel(
    channel_id: UUID,
    force: bool = Query(False),
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict:
    """Run the cross-platform AI analyzer for the most recent posts of
    ``channel_id``. Per post: vision-describe the asset (Sonnet), then
    classify format+tone (Haiku) + purpose+lifecycle_stage (Sonnet).

    Idempotent by default: posts whose ``last_analyzed_at`` is already
    set are skipped. ``?force=true`` re-analyzes everything in the
    selected window. ``?limit=N`` (default 50) caps the batch as a
    cost guardrail — typical channel-sync drops 5-10 new posts, so 50
    leaves headroom for backfill on first runs without going wild.

    The analyzer commits per post — a crash mid-batch keeps the
    already-completed posts intact and returns the partial result with
    an ``errors`` array describing what went wrong.

    Errors map to:
    - 401: ANTHROPIC_API_KEY missing/invalid (auth failures from any
      Anthropic call short-circuit the whole batch — non-recoverable)
    - 404: channel UUID unknown
    - 503: analyzer module unavailable (lazy-import failure of the
      anthropic SDK or app.services.post_analyzer)

    Per-post failures (rate limit after the wrapper's retries, vision
    URL unreachable, classifier returns invalid JSON twice) are
    swallowed and surface in the response's ``errors`` array — the
    batch keeps going so a single bad post doesn't strand 49 healthy
    ones. The HTTP status stays 200 in that case; callers should
    inspect ``errors`` and ``analyzed_posts`` for the real outcome.
    """
    try:
        from app.services.anthropic_client import (
            AnthropicAuthError,
            is_anthropic_configured,
        )
        from app.services.post_analyzer import analyze_post
    except ImportError as exc:  # noqa: BLE001
        logger.exception("analyzer-import-failed")
        raise HTTPException(
            status_code=503,
            detail=f"Analyzer unavailable: {exc}",
        ) from exc

    if not is_anthropic_configured():
        raise HTTPException(
            status_code=401,
            detail="ANTHROPIC_API_KEY ist nicht gesetzt. Bitte in Railway konfigurieren.",
        )

    channel = session.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail=f"Channel {channel_id} nicht gefunden.")

    # Pick posts: newest first, capped at ``limit``. Default skip-pre-check
    # filters posts we've already analyzed; ?force=true re-runs them.
    post_q = (
        select(Post)
        .where(Post.channel_id == channel_id)
        .order_by(Post.detected_at.desc())
        .limit(limit)
    )
    if not force:
        post_q = post_q.where(Post.last_analyzed_at.is_(None))
    posts_to_analyze = list(session.exec(post_q).all())

    # Skipped = posts in the channel we *would* have analyzed but for
    # the last_analyzed_at filter. Counted in a second query so the
    # response is honest even when the limit cap excludes some new posts.
    if not force:
        skipped_q = (
            select(Post)
            .where(Post.channel_id == channel_id)
            .where(Post.last_analyzed_at.is_not(None))
        )
        skipped_count = len(list(session.exec(skipped_q).all()))
    else:
        skipped_count = 0

    analyzed = 0
    asset_rows_created = 0
    errors: list[str] = []
    calls_total = {"haiku": 0, "sonnet": 0, "sonnet_vision": 0}

    for post in posts_to_analyze:
        try:
            result = analyze_post(session, post)
        except AnthropicAuthError as exc:
            # Auth is non-recoverable — short-circuit the whole batch.
            session.rollback()
            raise HTTPException(status_code=401, detail=f"Anthropic auth failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            # Per-post failure outside the analyzer's own try/except
            # — log + continue. Rate-limit is already absorbed inside
            # analyze_post (skip-and-log per Wolf's Sprint-5.3.1 spec),
            # so anything that reaches here is genuinely unexpected.
            session.rollback()
            logger.exception("analyze-post-failed", extra={"post_id": str(post.id)})
            errors.append(f"{post.id}:{type(exc).__name__}:{exc}")
            continue

        for key, n in result.calls.items():
            calls_total[key] = calls_total.get(key, 0) + n

        if result.status == "analyzed":
            try:
                session.commit()
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                errors.append(f"{post.id}:persist:{type(exc).__name__}:{exc}")
                continue
            analyzed += 1
            if result.asset_created:
                asset_rows_created += 1
        else:
            session.rollback()
            errors.append(f"{post.id}:{','.join(result.errors) or result.status}")

    return {
        "channel_id": str(channel.id),
        "platform": channel.platform,
        "analyzed_posts": analyzed,
        "skipped_posts": skipped_count,
        "asset_rows_created": asset_rows_created,
        "errors": errors,
        "anthropic_calls": calls_total,
    }


@router.post("/insights/regenerate")
def regenerate_insights(
    pair: str = Query(
        "all",
        description=(
            "Pair-Key (z.B. 'netflix') oder 'all' für alle aktivierten Pairs. "
            "Disabled Pairs werden mit status='skipped' übersprungen."
        ),
    ),
    window_days: int = Query(30, ge=7, le=90),
    session: Session = Depends(get_session),
):
    """Sprint 1 (Persistenz) — manueller Regenerate-Trigger.

    Kostet pro generiertem Brief ~$0.40 (Opus 4.7). Ein ``pair=all``-Lauf
    über die sechs aktiven Tier-A-Pairs liegt bei ~$2.40. Auto-Trigger im
    Cron-Lauf folgt im Cadence-Sprint; bis dahin füllt Wolf den Cache
    manuell über diesen Endpoint.

    Pro Pair wird ``generate_and_persist_report(force=True)`` aufgerufen
    — Last-Write-Wins auf der Composite-PK. Per-Pair-Fehler werden im
    ``results``-Array isoliert reportet, der Loop läuft weiter (ein
    einzelner Anthropic-401 für einen Pair stoppt die anderen fünf
    nicht).
    """
    if pair == "all":
        pairs_to_run = [k for k, v in PAIRS.items() if v.get("enabled", False)]
    elif pair in PAIRS:
        if not PAIRS[pair].get("enabled", False):
            return {
                "results": [
                    {
                        "pair": pair,
                        "status": "skipped",
                        "reason": PAIRS[pair].get("reason") or "disabled",
                    }
                ],
                "total_cost_cents": 0,
            }
        pairs_to_run = [pair]
    else:
        raise HTTPException(
            status_code=404,
            detail=f"Unbekannter Pair-Key: {pair!r}",
        )

    results: list[dict] = []
    total_cost_cents = 0
    for p in pairs_to_run:
        try:
            report = generate_and_persist_report(
                session,
                p,
                window_days=window_days,
                force=True,
            )
        except (AnthropicAuthError, AnthropicRateLimitError, AnthropicAPIError) as exc:
            logger.warning("regenerate-insight failed for pair=%s: %s", p, exc)
            results.append({"pair": p, "status": "error", "message": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 — keep the loop alive
            logger.exception("regenerate-insight unexpected failure for pair=%s", p)
            results.append({
                "pair": p,
                "status": "error",
                "message": f"{type(exc).__name__}: {exc}",
            })
            continue

        cost_cents = (
            int(round(report.cost_usd_estimate * 100))
            if report.cost_usd_estimate else 0
        )
        total_cost_cents += cost_cents
        results.append({
            "pair": p,
            "status": "ok",
            "iso_year": report.iso_year,
            "iso_week": report.iso_week,
            "cost_cents": cost_cents,
        })

    return {"results": results, "total_cost_cents": total_cost_cents}
