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

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.admin_session import (
    ADMIN_SESSION_COOKIE,
    create_session_token,
    require_admin_session,
    user_session_is_admin,
    verify_admin_password,
    verify_session_token,
)
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models.entities import (
    Channel,
    CostLog,
    CutterWeeklyBriefing,
    DesignerWeeklyBriefing,
    Post,
    Title,
)
from app.services.anthropic_client import (
    AnthropicAPIError,
    AnthropicAuthError,
    AnthropicRateLimitError,
)
from app.services.budget_check import (
    compute_anthropic_monthly_spend,
    compute_apify_monthly_spend,
    compute_openai_monthly_spend,
)
from app.core.feature_flags import is_segment_roundups_enabled
from app.models.entities import ChannelSegment
from app.services.insight_engine import (
    PAIRS,
    compute_breakout_feed,
    generate_and_persist_report,
    run_prompt_eval,
)
from app.services.forecast import generate_er_forecast
from app.schemas.insights import ForecastResponse, MarketForecast, TimelineWeek
from app.services.title_brief import generate_and_persist_title_brief
from app.services.title_aggregation import AmbiguousTitleError
from app.services.rate_limit import rate_limit
from app.services.segment_roundup import (
    ROUNDUP_DEFAULT_TOP_POSTS_N,
    ROUNDUP_DEFAULT_WINDOW_DAYS,
    generate_and_persist_roundup,
)

logger = logging.getLogger(__name__)

# Sprint 28.05.2026 (Admin-Login): zwei Router unter demselben
# ``/api/admin``-Prefix.
#
# ``router`` haengt an einer Router-Level-Dependency, die jeden
# Aufruf gegen ``require_admin_session`` prueft — alle Admin-Werkzeuge
# (cost-summary, youtube/sync, roundups/generate, insights/regenerate
# etc.) brauchen ein gueltiges Session-Cookie. Bei
# ``admin_auth_enabled=False`` ist die Dependency ein No-Op, sodass
# bestehende Tests + dev-Setups ohne Login weiterlaufen.
#
# ``login_router`` haengt am gleichen Prefix, aber OHNE Dependency —
# Login/Logout/Me muessen ohne Session erreichbar sein, sonst kommt
# kein User je in den Admin-Bereich. Bearer-Token-Schutz greift weiter
# global (auth.py), der Frontend-Bundle schickt ihn mit.
router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_session)],
)
login_router = APIRouter(prefix="/api/admin", tags=["admin-auth"])


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

    ``cost_usd_millicents`` (Kosten-Audit 2026-08-01) ist die Zahl, die
    Anzeigen benutzen sollen: ein einzelner gpt-5.4-mini-Call kostet
    ~0,13 ct, rundet in ``cost_usd_cents`` also auf 0 — im Juli 2026
    verschwanden so 3.781 OpenAI-Calls (5,02 USD) komplett aus der
    Tabelle, waehrend die Budget-Karten daneben den korrekten Wert aus
    ``compute_openai_monthly_spend`` zogen. ``cost_usd_cents`` bleibt
    unveraendert im Response (Back-Compat fuer bestehende Konsumenten).
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
        lambda: {
            "count": 0,
            "cost_usd_cents": 0,
            "cost_usd_millicents": 0,
            "cost_eur_cents": 0,
        }
    )
    total_usd = 0
    total_usd_milli = 0
    total_eur = 0

    for row in rows:
        if group_by == "day":
            key = row.timestamp.date().isoformat()
        elif group_by == "operation":
            key = f"{row.provider}:{row.operation}"
        else:  # provider
            key = row.provider
        # Zeilen von vor der Millicent-Spalte (Migration-Default 0) haben
        # nur den Cent-Wert — ``or`` faellt genau fuer die zurueck, ohne
        # neuere Sub-Cent-Zeilen (Cent 0, Millicent >0) zu ueberschreiben.
        millicents = row.cost_usd_millicents or row.cost_usd_cents * 1000
        buckets[key]["count"] += 1
        buckets[key]["cost_usd_cents"] += row.cost_usd_cents
        buckets[key]["cost_usd_millicents"] += millicents
        buckets[key]["cost_eur_cents"] += row.cost_eur_cents
        total_usd += row.cost_usd_cents
        total_usd_milli += millicents
        total_eur += row.cost_eur_cents

    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "group_by": group_by,
        "total_count": len(rows),
        "total_cost_usd_cents": total_usd,
        "total_cost_usd_millicents": total_usd_milli,
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


@router.get("/anthropic-budget-status")
def anthropic_budget_status(session: Session = Depends(get_session)) -> dict:
    """Sprint F0.7 — current Anthropic monthly budget snapshot.

    Mirror of ``/budget-status`` for the Anthropic side, separate endpoint
    to avoid breaking the existing single-flat-dict contract. Aggregates
    across all five ``anthropic_*``-provider buckets so Opus brief calls,
    Haiku/Sonnet post-analyzer calls, and the Sonnet vision pathway all
    show in the same monthly figure that the cron pre-flight evaluates.
    """
    return compute_anthropic_monthly_spend(session).to_dict()


@router.get("/openai-budget-status")
def openai_budget_status(session: Session = Depends(get_session)) -> dict:
    """Incident 2026-07-13 — current OpenAI monthly budget snapshot.

    Mirror of ``/budget-status``/``/anthropic-budget-status`` for the
    OpenAI side, same reasoning: separate endpoint per provider so each
    stays a flat, stable-shape dict for dashboards. Aggregates the single
    ``openai``-provider bucket (Vision-Analyse + Caption-Analyse).
    """
    return compute_openai_monthly_spend(session).to_dict()


@router.get("/breakouts")
def breakouts(
    window_days: int = Query(30, ge=7, le=90),
    limit: int = Query(20, ge=1, le=100),
    min_multiplier: float = Query(2.0, ge=1.0, le=20.0),
    session: Session = Depends(get_session),
) -> dict:
    """Platin 4 — Breakout-Feed über alle aktivierten Pairs.

    Rein lesend, kein LLM-Call: wiederverwendet ``aggregate_pair`` (DB-only)
    und die darin bereits berechneten ``ChannelStats.breakouts``, gefiltert
    auf ``multiplier >= min_multiplier`` (Default 2x Kanal-Schnitt) und
    sortiert nach ``weighted_score`` (Z-Score × Recency-Decay) absteigend.
    Kann beliebig oft aufgerufen werden — keine Budget-Auswirkung.
    """
    entries = compute_breakout_feed(
        session, window_days=window_days, limit=limit, min_multiplier=min_multiplier,
    )
    return {"count": len(entries), "min_multiplier": min_multiplier, "entries": entries}


@router.get("/trailer-patterns")
def trailer_patterns(
    window_days: int = Query(90, ge=7, le=365),
    market: str | None = Query(None),
    min_sample: int = Query(5, ge=2, le=100),
    min_channels: int = Query(3, ge=1, le=50),
    session: Session = Depends(get_session),
) -> dict:
    """Trailer-Intelligence Stufe 1, Schritt 2 — korpusweite Muster.

    Rein lesend, kein Modell-Call, kein Budget-Effekt: beliebig oft
    aufrufbar. Aggregiert ueber alle Kanaele (optional je ``market``),
    welche Merkmale mit ueberdurchschnittlicher Reichweite einhergehen —
    ``format``, ``tone``, ``lifecycle_stage``, ``duration_bucket`` und
    ``music_kind``.

    Anders als ``/breakouts`` (einzelne Ausreisser-Posts) und anders als
    der Empfehlungs-Baustein im Wochen-Brief (ein Pair, 7 Tage) sucht
    dieser Endpunkt nach dem *stabilen Strukturmuster* ueber ein langes
    Fenster.

    Jeder Post wird gegen den Median seines eigenen Kanals normiert, damit
    Kanalgroesse das Ergebnis nicht diktiert. Zellen, die die
    Ehrlichkeits-Schwellen reissen, werden mit ``verdict="insufficient"``
    und einer Begruendung ausgegeben statt weggefiltert — eine duenne
    Datenlage ist ein Befund, kein Grund zum Schweigen.

    Beim Lesen beide Richtungen einer Dimension zusammen betrachten: ein
    Format, das den Output eines Kanals dominiert, bestimmt dessen Median
    mit und erscheint deshalb als ``neutral``, waehrend sich das Signal im
    ``under`` der uebrigen Werte zeigt. Details im Modul-Docstring von
    ``app/services/trailer_patterns.py``.

    **Zwei Kennzahlen pro Zelle, die verschiedene Fragen beantworten:**

    - ``median_lift`` / ``verdict`` — laeuft der typische Post besser?
      Bei Korpusgroesse regrediert jeder Teilmengen-Median Richtung 1,0,
      dieses Verdikt spricht deshalb selten an.
    - ``breakout_rate`` / ``breakout_verdict`` — produziert das Merkmal
      mehr Ausreisser? Anteil Posts mit Lift >= 2,0 gegen
      ``baseline_breakout_rate`` (Korpusquote), bewertet per z-Test, der
      die Stichprobengroesse beruecksichtigt. In der ersten echten
      Auswertung war das die einzige der beiden Kennzahlen mit Signal.

    Beide koennen gegenlaeufig sein — ein Merkmal mit schwachem Median
    und hoher Trefferquote ist meist Blindgaenger, aber ueberdurch-
    schnittlich oft Volltreffer. Das ist die eigentliche Information,
    kein Widerspruch. Sortiert wird nach ``breakout_z`` absteigend.
    """
    from app.services.trailer_patterns import compute_trailer_patterns

    report = compute_trailer_patterns(
        session,
        window_days=window_days,
        market=market,
        min_sample=min_sample,
        min_channels=min_channels,
    )
    return report.to_dict()


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

    # Sprint 27.05.2026 — Cron-Konsistenz-Fix. Der Admin-Endpoint hat den
    # ``platform_channel_id``-Hint bisher nicht weitergereicht; nur cron.py:260
    # las ihn. Folge: ein Channel wie WarnerBrosUK mit korrekt gesetzter
    # UC-ID konnte den Hint hier nicht nutzen, der Resolver routete weiter
    # ueber ``@<handle>`` und landete auf einem leeren Squatter-Account →
    # 404 playlistNotFound. Mit dem Hint geht der Resolver direkt auf die
    # UC-ID, kriegt die echte Uploads-Playlist, sync laeuft.
    # ``getattr``-Defensiv-Lesart spiegelt den Cron-Pfad.
    channel_id_hint = getattr(channel, "platform_channel_id", None)
    try:
        _, raw_videos = fetch_channel_videos(lookup, channel_id_hint=channel_id_hint)
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
    replace: bool = Query(
        False,
        description=(
            "PR #150: bypassed den Sprint-3c-Composite-PK-Check und triggert "
            "eine echte Neugenerierung mit UPSERT. Ohne ``replace=true`` "
            "respektiert der Endpoint den Cache-Hit-Vertrag und liefert den "
            "existierenden Brief unverändert zurück. Operator-only — pro Pair "
            "~$0.15 Opus-Cost pro Aufruf."
        ),
    ),
    target_week: str = Query(
        "completed",
        pattern="^(completed|current)$",
        description=(
            "Incident 2026-07-13 (Folgefund): ohne diesen Parameter griff der "
            "Endpoint stets ``now=None`` -> ``datetime.now()`` intern, also "
            "immer die LAUFENDE KW -- ein gezielter Reparatur-Aufruf fuer "
            "einen einzelnen zuvor fehlgeschlagenen Pair-Brief haette so "
            "versehentlich die naechste KW mit einem Bruchteil ihrer Daten "
            "vorzeitig befuellt (derselbe Cache-Poisoning-Mechanismus wie "
            "beim Montags-Cron-Bug). ``completed`` (Default): gerade "
            "abgeschlossene KW (``utcnow - 1 Tag``, identisch zum "
            "woechentlichen Cron). ``current``: laufende KW, fuer den "
            "seltenen Fall, dass wirklich ein Zwischenstand gebraucht wird."
        ),
    ),
    session: Session = Depends(get_session),
):
    """Sprint 1 (Persistenz) — manueller Regenerate-Trigger.

    Kostet pro generiertem Brief ~$0.15 (Opus 4.8, Preis korrigiert
    2026-07-01 — zuvor stand hier veraltetes Opus-4/4.1-Pricing). Ein
    ``pair=all``-Lauf über die sechs aktiven Tier-A-Pairs liegt bei ~$0.90.
    Auto-Trigger im
    Cron-Lauf folgt im Cadence-Sprint; bis dahin füllt Wolf den Cache
    manuell über diesen Endpoint.

    Pro Pair wird ``generate_and_persist_report(force=True, replace=replace)``
    aufgerufen — Last-Write-Wins auf der Composite-PK. Per-Pair-Fehler
    werden im ``results``-Array isoliert reportet, der Loop läuft weiter
    (ein einzelner Anthropic-401 für einen Pair stoppt die anderen fünf
    nicht).

    PR #150 (Force-Regenerate Bug-Fix): ``force=True`` allein behält den
    Sprint-3c-Vertrag (Cache-Hit-Schutz gegen Sprint-3b-Race). Für eine
    echte Neugenerierung muss zusätzlich ``replace=true`` gesetzt werden.
    Der ``replace``-Param wird per Pair durchgereicht — bei ``pair=all``
    wirkt er auf alle iterierten Pairs gleichermassen.
    """
    regen_now = (
        datetime.now(timezone.utc)
        if target_week == "current"
        else datetime.now(timezone.utc) - timedelta(days=1)
    )
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
                replace=replace,
                now=regen_now,
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
            # Diagnose-Surface (additiv): ein no_llm_output-Report (Parse-/
            # Schema-/Citation-Fail) kommt mit ``llm_output=None`` zurueck und
            # wurde bisher als ``status="ok"`` mit verworfenem Rohtext
            # maskiert. ``raw_llm_text`` inline surfacen, damit
            # ``/insights/regenerate?pair=X`` den verworfenen Opus-Output
            # zeigt — spiegelt ``/insights/title/regenerate`` (admin.py:706),
            # kein Logik-Change am Schema/Generierungs-Pfad.
            "status": "ok" if report.llm_output is not None else "generation_failed",
            "iso_year": report.iso_year,
            "iso_week": report.iso_week,
            "cost_cents": cost_cents,
            "raw_llm_text": report.raw_llm_text if report.llm_output is None else None,
        })

    return {"results": results, "total_cost_cents": total_cost_cents}


class PromptEvalRequest(BaseModel):
    variant_b_system_prompt: str = Field(min_length=50, max_length=40000)


@router.post("/insights/eval-prompt")
def eval_prompt(
    body: PromptEvalRequest,
    pair: str = Query(..., description="Pair-Key (z.B. 'netflix'). Kein 'all' — ein Eval-Lauf ist pro Pair."),
    window_days: int = Query(30, ge=7, le=90),
    target_week: str = Query(
        "completed",
        pattern="^(completed|current)$",
        description="Wie bei /insights/regenerate: 'completed' (Default) nimmt die zuletzt abgeschlossene KW.",
    ),
    session: Session = Depends(get_session),
):
    """Platin 3 — Eval-Harness für Brief-Prompts.

    Vergleicht den aktuellen Produktions-``SYSTEM_PROMPT`` ("variant_a")
    gegen einen im Request-Body übergebenen Kandidaten-Prompt-Text
    ("variant_b") auf DENSELBEN echten, bereits gesammelten Daten für ein
    Pair/Woche. Beide Varianten laufen auf identischem Aggregations-/
    User-Prompt-Input, damit der Vergleich fair ist. Kein Ergebnis wird im
    ``InsightReport``-Cache gespeichert — beliebig oft wiederholbar ohne
    das Cache-Poisoning-Risiko von ``/insights/regenerate``.

    Kosten: ~2x ein normaler Brief (~$0.30 bei aktuellen Opus-Preisen) pro
    Aufruf, taucht unter ``operation=prompt_eval`` in
    ``/admin/cost-summary?group_by=operation`` separat auf. Rein
    Operator-getriggert, nie Teil des automatischen Wochen-Crons.
    """
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Unbekannter Pair-Key: {pair!r}")
    if not PAIRS[pair].get("enabled", False):
        raise HTTPException(status_code=409, detail=f"Pair {pair!r} ist aktuell deaktiviert.")

    eval_now = (
        datetime.now(timezone.utc)
        if target_week == "current"
        else datetime.now(timezone.utc) - timedelta(days=1)
    )
    try:
        result = run_prompt_eval(
            session,
            pair,
            variant_b_system_prompt=body.variant_b_system_prompt,
            window_days=window_days,
            now=eval_now,
        )
    except (AnthropicAuthError, AnthropicRateLimitError, AnthropicAPIError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return result


def _looks_like_uuid(value: str) -> bool:
    try:
        UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


@router.post("/insights/title/regenerate")
def regenerate_title_insight(
    title: str | None = Query(
        None,
        description="Title-ID (UUID) ODER Titel-Name (exakt, sonst eindeutiger Substring).",
    ),
    tmdb_id: int | None = Query(
        None,
        description="TMDb-ID — eindeutiger Schlüssel, bevorzugt für gleichnamige Sequels.",
    ),
    window_days: int = Query(30, ge=7, le=365),
    replace: bool = Query(
        False,
        description=(
            "Ohne ``replace=true`` liefert der Endpoint einen bereits "
            "persistierten Titel-Brief der laufenden ISO-Woche aus dem Cache. "
            "Mit ``replace=true`` echte Neugenerierung + UPSERT "
            "(Last-Write-Wins). Operator-only — ~$0.40 Opus-Cost pro Lauf."
        ),
    ),
    session: Session = Depends(get_session),
):
    """Manueller Titel-Brief-Trigger (C5) — generiert/cached EINEN Titel-Brief
    über alle Channels/Plattformen/Märkte. Auth wie ``/insights/regenerate``
    (Bearer + Admin-Session via Router-Dependency).

    Auflösungs-Priorität (Defekt-1-Fix): (1) ``title`` als UUID = title_id,
    (2) ``tmdb_id`` exakt, (3) ``title`` als Name (gehärteter Resolver:
    exakt -> eindeutiger Substring; mehrdeutig -> 409 + Kandidaten). Mindestens
    einer von ``title``/``tmdb_id`` ist Pflicht (sonst 400). 404 bei
    unbekanntem Titel. Keine Cron-Integration.
    """
    # Resolve the reference deterministically by priority.
    ref: str | UUID
    if title and _looks_like_uuid(title):
        ref = title  # (1) title_id
    elif tmdb_id is not None:
        # (2) tmdb_id — exact. tmdb_id is indexed but NOT unique, so guard >1.
        matches = list(session.exec(select(Title).where(Title.tmdb_id == tmdb_id)).all())
        if not matches:
            raise HTTPException(status_code=404, detail=f"Kein Titel mit tmdb_id={tmdb_id}")
        if len(matches) > 1:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "ambiguous_tmdb_id",
                    "candidates": [
                        {"title_id": str(t.id), "title_original": t.title_original, "tmdb_id": t.tmdb_id}
                        for t in matches
                    ],
                },
            )
        ref = matches[0].id  # UUID
    elif title:
        ref = title  # (3) name -> hardened resolver (may raise AmbiguousTitleError)
    else:
        raise HTTPException(
            status_code=400,
            detail="Mindestens 'title' (UUID oder Name) oder 'tmdb_id' erforderlich.",
        )

    try:
        report = generate_and_persist_title_brief(
            session, ref, window_days=window_days, force=True, replace=replace,
        )
    except AmbiguousTitleError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "ambiguous_title", "candidates": exc.candidates},
        ) from exc
    except (AnthropicAuthError, AnthropicRateLimitError, AnthropicAPIError) as exc:
        logger.warning("regenerate-title-insight failed for ref=%s: %s", ref, exc)
        raise HTTPException(status_code=502, detail=f"Anthropic-Fehler: {exc}") from exc

    if report is None:
        raise HTTPException(status_code=404, detail=f"Unbekannter Titel: {title or tmdb_id!r}")

    cost_cents = (
        int(round(report.cost_usd_estimate * 100)) if report.cost_usd_estimate else 0
    )
    return {
        "title_id": report.title_id,
        "title_original": report.title_original,
        "iso_year": report.iso_year,
        "iso_week": report.iso_week,
        "window_days": report.window_days,
        "status": "ok" if report.llm_output is not None else "generation_failed",
        "cost_cents": cost_cents,
        "llm_output": report.llm_output.model_dump(mode="json") if report.llm_output else None,
        "raw_llm_text": report.raw_llm_text if report.llm_output is None else None,
    }


# ---------- ER-Prognose pro Markt (V3 Sprint 7, Admin-only) -----------


@router.post("/insights/forecast", response_model=ForecastResponse)
def forecast_insights(
    pair: str = Query(..., description="Pair-Key, z.B. 'warnerbros'"),
    weeks: int | None = Query(
        None, ge=1,
        description="Optionale Begrenzung der Zeitreihe auf die letzten N Wochen.",
    ),
    session: Session = Depends(get_session),
) -> ForecastResponse:
    """Admin-only ER-Prognose pro Markt (DE/US/UK) für die nächste ISO-Woche.

    Auth über den Router-Gate ``require_admin_session`` (Bearer + Admin-Session)
    — kein eigener Auth-Pfad. Lineare Regression über die ER-Zeitreihe (Sprint 6,
    geteilter ``compute_market_timeline``-Kern) + eine sachliche LLM-Einordnung,
    die NUR die Regressions-Ausgabe interpretiert. NUR ER, kein Views-Forecast.

    POST, weil ein (kostenpflichtiger) LLM-Call ausgelöst wird — analog
    ``/insights/regenerate``.
    """
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Unbekannter Pair-Key: {pair!r}")

    # #252: bewusst UNGEGATET (apply_gate=False) — der Admin sieht alle
    # Märkte inkl. R²/Prognosewert derer, die das Ehrlichkeits-Gate der
    # öffentlichen Sicht (POST /api/insights/forecast) ausblendet. Die
    # Einordnung kommt aus demselben (pair, ziel_woche)-Cache und ist die
    # public-safe Fassung.
    result = generate_er_forecast(session, pair, PAIRS[pair], weeks=weeks, apply_gate=False)
    nxt = result.get("next_week")
    return ForecastResponse(
        pair_key=result["pair_key"],
        n_axis_weeks=result["n_axis_weeks"],
        next_week=TimelineWeek(**nxt) if nxt else None,
        markets={m: MarketForecast(**r) for m, r in result["markets"].items()},
        einordnung=result.get("einordnung"),
    )


# ---------- Segment-Roundup-Trigger (Master-Plan-Schritt-3 Pilot) -----


@router.get("/cutter-weekly/latest")
def cutter_weekly_latest(
    iso_year: int | None = Query(
        default=None,
        description="ISO-Jahr einer konkreten Woche — nur zusammen mit iso_week.",
    ),
    iso_week: int | None = Query(
        default=None, ge=1, le=53,
        description="ISO-Woche — nur zusammen mit iso_year.",
    ),
    session: Session = Depends(get_session),
) -> dict:
    """Trockenlauf-Lesezugriff auf das Cutter-Wochenbriefing (Master-Plan-
    Sprint 2026-06-12, Commit E).

    Ohne Parameter: die juengste Woche (PK-Ordnung iso_year/iso_week
    absteigend). Mit ``iso_year`` + ``iso_week``: exakte Row. Die Antwort
    ist die rohe Row inklusive ``evidence``-Blob — der Blob ist das
    Kalibrierungs-Produkt (p75-Schwellen, freigegebene UND verworfene
    Muster mit Grund, ``title_key_share``), den Wolf in der Trockenlauf-
    Phase pro Woche sichtet. Mehr braucht der Trockenlauf nicht; ein
    Frontend-Pfad ist bewusst NICHT Teil dieses Sprints (erst nach
    Kalibrierung).
    """
    if (iso_year is None) != (iso_week is None):
        raise HTTPException(
            status_code=422,
            detail="iso_year und iso_week nur zusammen angeben (oder beide weglassen).",
        )

    if iso_year is not None:
        row = session.get(CutterWeeklyBriefing, (iso_year, iso_week))
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"Kein Cutter-Wochenbriefing fuer KW {iso_week}/{iso_year}.",
            )
    else:
        row = session.exec(
            select(CutterWeeklyBriefing)
            .order_by(
                CutterWeeklyBriefing.iso_year.desc(),
                CutterWeeklyBriefing.iso_week.desc(),
            )
            .limit(1)
        ).first()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Noch kein Cutter-Wochenbriefing persistiert.",
            )

    return {
        "iso_year": row.iso_year,
        "iso_week": row.iso_week,
        "generated_at": row.generated_at,
        "model": row.model,
        "cost_usd_cents": row.cost_usd_cents,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "llm_output": row.llm_output,
        "evidence": row.evidence,
        "raw_llm_text": row.raw_llm_text,
    }


@router.get("/designer-weekly/latest")
def designer_weekly_latest(
    iso_year: int | None = Query(
        default=None,
        description="ISO-Jahr einer konkreten Woche — nur zusammen mit iso_week.",
    ),
    iso_week: int | None = Query(
        default=None, ge=1, le=53,
        description="ISO-Woche — nur zusammen mit iso_year.",
    ),
    session: Session = Depends(get_session),
) -> dict:
    """Trockenlauf-Lesezugriff auf das Designer-Wochenbriefing — mirror
    ``cutter_weekly_latest`` (Diagnose-Folge 2026-07-06, Designer-Weekly
    Kalibrierungsphase). Ohne Parameter: die juengste Woche. Mit
    ``iso_year`` + ``iso_week``: exakte Row. Ein Frontend-Pfad ist bewusst
    NICHT Teil dieses Sprints (erst nach Kalibrierung, analog Cutter)."""
    if (iso_year is None) != (iso_week is None):
        raise HTTPException(
            status_code=422,
            detail="iso_year und iso_week nur zusammen angeben (oder beide weglassen).",
        )

    if iso_year is not None:
        row = session.get(DesignerWeeklyBriefing, (iso_year, iso_week))
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"Kein Designer-Wochenbriefing fuer KW {iso_week}/{iso_year}.",
            )
    else:
        row = session.exec(
            select(DesignerWeeklyBriefing)
            .order_by(
                DesignerWeeklyBriefing.iso_year.desc(),
                DesignerWeeklyBriefing.iso_week.desc(),
            )
            .limit(1)
        ).first()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Noch kein Designer-Wochenbriefing persistiert.",
            )

    return {
        "iso_year": row.iso_year,
        "iso_week": row.iso_week,
        "generated_at": row.generated_at,
        "model": row.model,
        "cost_usd_cents": row.cost_usd_cents,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "llm_output": row.llm_output,
        "evidence": row.evidence,
        "raw_llm_text": row.raw_llm_text,
    }


@router.post("/roundups/generate")
def trigger_segment_roundup(
    segment: str = Query(
        ...,
        description=(
            "Segment-Key — einer der sechs Werte aus ``creative_radar.channel_segment``: "
            "``us_major``, ``us_independent``, ``uk_major``, ``uk_independent``, "
            "``de_verleih``, ``de_independent``. Pilot in Master-Plan-Schritt-3 laeuft "
            "ausschliesslich gegen ``us_major``."
        ),
    ),
    window_days: int = Query(
        ROUNDUP_DEFAULT_WINDOW_DAYS,
        ge=1, le=90,
        description=(
            "Zeitfenster in Tagen. Default 14 (Wolf 25.05., bewusste Abweichung vom "
            "30d-Pair-Default). Audit-Wert wird pro Row in ``segment_roundup.window_days`` "
            "persistiert."
        ),
    ),
    top_posts_n: int = Query(
        ROUNDUP_DEFAULT_TOP_POSTS_N,
        ge=1, le=20,
        description=(
            "Top-N Posts pro Channel im LLM-Prompt. Default 5 — entspricht der "
            "Sprint-6-Konvention im Pair-Prompt. Hoehere Werte erhoehen Input-Tokens "
            "und damit Lauf-Kosten direkt proportional."
        ),
    ),
    session: Session = Depends(get_session),
):
    """Master-Plan-Schritt-3 Pilot — manueller Roundup-Trigger.

    Eigene Code-Bahn, beruehrt keinen Pair-Pipeline-Code. Eigene Persistenz
    (``segment_roundup``-Tabelle), eigenes LLM-Schema (deskriptive
    Synthese, kein Vergleich). Last-Write-Wins auf
    ``(segment, iso_year, iso_week)``.

    Gate: ``FEATURE_SEGMENT_ROUNDUPS_ENABLED``-Env-Var (PR #155, default off).
    Off → 503. Wolf kann den Pilot in Production via Railway-ENV-Toggle
    ein-/ausschalten ohne Code-Deploy. **Hinweis Wolf 25.05.: Die Flag-
    Zuordnung ist vorlaeufig; die endgueltige Benennung faellt in
    Schritt 4 (Konzept §8), das Gate wird dann ggf. umgehaengt.**

    Kosten: pro Lauf eine Opus-Call-Erfassung in costlog mit
    ``operation='segment_roundup'``. Faellt automatisch in die F0.7-
    Anthropic-Cap-Aggregation. Pilot-Hochrechnung erfolgt nach dem
    ersten Real-Lauf gegen Production-Daten.
    """
    if not is_segment_roundups_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Segment-Roundup-Pilot ist deaktiviert. "
                "FEATURE_SEGMENT_ROUNDUPS_ENABLED muss in Railway-ENV auf 'true' gesetzt sein."
            ),
        )

    try:
        segment_enum = ChannelSegment(segment)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unbekanntes Segment: {segment!r}. "
                f"Erlaubt: {[s.value for s in ChannelSegment]}."
            ),
        )

    try:
        report = generate_and_persist_roundup(
            session,
            segment_enum,
            window_days=window_days,
            top_posts_n=top_posts_n,
        )
    except (AnthropicAuthError, AnthropicRateLimitError, AnthropicAPIError) as exc:
        logger.warning("segment-roundup failed for segment=%s: %s", segment, exc)
        raise HTTPException(status_code=502, detail=f"Anthropic-API: {exc}")

    cost_cents = (
        int(round(report.cost_usd_estimate * 100))
        if report.cost_usd_estimate
        else 0
    )
    return {
        "segment": report.segment,
        "iso_year": report.iso_year,
        "iso_week": report.iso_week,
        "window_days": report.window_days,
        "channels_evaluated": report.aggregation.channels_evaluated,
        "channels_with_posts": report.aggregation.channels_with_posts,
        "total_posts": report.aggregation.total_posts,
        "llm_output_present": report.llm_output is not None,
        "cost_usd_cents": cost_cents,
        "input_tokens": report.input_tokens,
        "output_tokens": report.output_tokens,
    }


# ---------- Admin-Session-Login (Sprint 28.05.2026) -------------------
#
# Login-Endpoint nimmt das Passwort, prueft konstant-zeitig gegen
# ``settings.admin_password`` und gibt ein HMAC-signiertes Session-
# Cookie zurueck. Die Endpoints unten ueberschneiden sich NICHT mit der
# Bearer-Token-Middleware-Logik aus app/auth.py — die laeuft global vor
# diesem Router. ``/api/admin/login`` ist Bearer-geschuetzt wie alles
# andere unter ``/api/admin/*`` (Frontend sendet Bearer aus dem Bundle).
#
# Cookie-Setup: HttpOnly + SameSite=Lax + Secure (HTTPS-only) in
# Production, Secure=False im dev/test wenn der Browser HTTP spricht.
# ``settings.app_env`` steuert den Secure-Flag — Default "production"
# in Settings, Local-Setups setzen typischerweise APP_ENV=dev.


class AdminLoginRequest(BaseModel):
    password: str


class AdminLoginResponse(BaseModel):
    ok: bool
    expires_unix: int


def _cookie_kwargs() -> dict:
    """Cookie-Attribute pro Umgebung. ``secure=True`` blockiert das
    Cookie ueber HTTP — in lokalen dev-Setups muss das aus, sonst
    funktioniert das Login lokal nicht."""
    secure = settings.app_env not in ("dev", "test", "local")
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": secure,
        "path": "/",
    }


@login_router.post(
    "/login",
    response_model=AdminLoginResponse,
    dependencies=[Depends(rate_limit("admin-login", max_calls=5, window_seconds=60))],
)
def admin_login(payload: AdminLoginRequest, response: Response) -> AdminLoginResponse:
    """Passwort-Login. Erfolg: setzt Session-Cookie + gibt
    ``expires_unix`` fuer den Frontend-Anzeige zurueck (kann z.B. einen
    "auto-logout in X Min"-Hinweis aus dem Timestamp ableiten).

    Fehler:
    - 401 wenn Passwort falsch oder Server kein ``admin_password`` hat
      (siehe ``verify_admin_password`` — bewusst nicht 503, um keinen
      Server-Misconfig-Hinweis nach aussen zu geben).
    - 503 wenn ``admin_session_secret`` fehlt (fail-closed: ohne Secret
      keine signierten Tokens).
    - 503 wenn ``admin_auth_enabled=False`` — der ganze Mechanismus
      ist deaktiviert, Login waere ein Toter Endpoint.
    """
    if not settings.admin_auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin auth disabled on server",
        )
    if not settings.admin_session_secret:
        logger.error("admin-login-session-secret-missing")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin session secret not configured on server",
        )
    if not verify_admin_password(payload.password):
        # Telemetrie OHNE Passwort-Wert (im Log nichts ueber den
        # presented-Wert ausgeben — auch nicht hashed).
        logger.warning("admin-login-failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    token, expires_unix = create_session_token(
        settings.admin_session_secret,
        settings.admin_session_ttl_seconds,
    )
    response.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=token,
        max_age=settings.admin_session_ttl_seconds,
        **_cookie_kwargs(),
    )
    logger.info("admin-login-success", extra={"expires_unix": expires_unix})
    return AdminLoginResponse(ok=True, expires_unix=expires_unix)


@login_router.post("/logout")
def admin_logout(response: Response) -> dict:
    """Logout = Cookie clearen. Stateless-Token bleibt zwar signiert
    gueltig bis zum Ablauf, aber der Browser sendet es nicht mehr —
    fuer einen geteilten-Geraet-Logout reicht das."""
    # delete_cookie respektiert path/samesite/secure aus _cookie_kwargs
    # nicht automatisch; explizit setzen.
    cookie_kwargs = _cookie_kwargs()
    response.delete_cookie(
        key=ADMIN_SESSION_COOKIE,
        path=cookie_kwargs["path"],
        samesite=cookie_kwargs["samesite"],
        secure=cookie_kwargs["secure"],
        httponly=cookie_kwargs["httponly"],
    )
    return {"ok": True}


@login_router.get("/me")
def admin_me(
    cr_admin_session: str | None = Cookie(default=None),
    cr_user_session: str | None = Cookie(default=None),
) -> dict:
    """Lightweight "bin ich noch eingeloggt?"-Check. Frontend ruft das
    beim Page-Load, um zu entscheiden, ob Login-Formular oder
    Tools-Panel gerendert wird.

    Antwortet IMMER mit 200 + ``{authenticated: bool}`` (nicht 401 wie
    die geschuetzten Routes) — der Frontend-Pfad soll keinen Error-Toast
    werfen, wenn der User schlicht noch nicht eingeloggt ist. Das ist
    die einzige Stelle, an der "keine Session" als legitimer Zustand
    durchgehen kann.

    Wenn ``admin_auth_enabled=False`` ist, antwortet der Endpoint mit
    ``authenticated=true`` — Konvention: deaktivierte Auth wird vom
    Frontend so behandelt, als sei der Nutzer immer eingeloggt (lokales
    dev-Setup).
    """
    if not settings.admin_auth_enabled:
        return {"authenticated": True, "auth_enabled": False}
    # Sprint 2026-07-21: E-Mail-Login-User aus ADMIN_USER_EMAILS gelten
    # als eingeloggt — /admin rendert fuer sie direkt die Werkzeuge,
    # ohne Passwort-Formular.
    if user_session_is_admin(cr_user_session):
        return {"authenticated": True, "auth_enabled": True, "via": "user"}
    secret = settings.admin_session_secret
    if not secret:
        return {"authenticated": False, "auth_enabled": True}
    authenticated = bool(
        cr_admin_session and verify_session_token(cr_admin_session, secret)
    )
    # "via" nur im User-Admin-Fall oben — der Passwort-Pfad behaelt die
    # bisherige Antwort-Form (bestehende Tests/Clients vergleichen exakt).
    return {"authenticated": authenticated, "auth_enabled": True}

