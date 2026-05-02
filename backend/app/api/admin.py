"""Admin endpoints.

This module currently mixes two concerns:

- Throwaway migration endpoints (run-schema-migration, run-schema-rollback,
  run-alembic-upgrade). Will be removed in Task 4.5 once the F0.2/F2.18
  migrations are confirmed stable. Gated by the global Bearer-auth
  middleware (Task 4.3) — same token as the rest of the API. Earlier
  drafts of these endpoints carried a separate ADMIN_MIGRATION_TOKEN
  check; W4-Hotfix-4 removed that double-auth (see PHASE_4_DONE.md
  Lesson 6).
- Cost-summary read endpoint (Task 4.4 / F0.6). Permanent. Same global
  Bearer-auth, same API token.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models.entities import CostLog

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/run-schema-migration")
def run_schema_migration() -> dict:
    """Forward migration: move the eight CR tables from public to
    creative_radar. Idempotent (re-run is safe — already-moved tables are
    reported as skipped). The migration script handles its own transaction.
    Auth: global Bearer middleware (no separate token)."""
    # In-function import per the W3 hotfix lesson: keep app boot decoupled
    # from scripts/ being importable.
    from scripts import migrate_to_creative_radar_schema as forward  # noqa: PLC0415

    stats = forward.run()
    return stats


@router.post("/run-schema-rollback")
def run_schema_rollback() -> dict:
    """Symmetric rollback: move the eight CR tables back from
    creative_radar to public. Used only if the forward migration leaves
    production in a state Wolf cannot recover otherwise.
    Auth: global Bearer middleware (no separate token)."""
    from scripts import rollback_creative_radar_schema as backward  # noqa: PLC0415

    stats = backward.run()
    return stats


@router.post("/run-alembic-upgrade")
def run_alembic_upgrade() -> dict:
    """Apply pending Alembic migrations against the creative_radar schema.
    Idempotent: stamps baseline if alembic_version is empty, then upgrades
    to head. Re-running is a no-op once at head.
    Auth: global Bearer middleware (no separate token)."""
    from scripts import apply_alembic_upgrade as alembic_apply  # noqa: PLC0415

    stats = alembic_apply.run()
    return stats


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
