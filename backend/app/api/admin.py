"""Admin endpoints (Phase-4-Abschluss-Stand).

After Task 4.5 cleanup this module hosts exactly one permanent endpoint:

- ``GET /api/admin/cost-summary``: aggregate read over creative_radar.costlog
  for daily monitoring (W4 Task 4.4 / F0.6).

The W4 throwaway endpoints — run-schema-migration, run-schema-rollback,
run-alembic-upgrade — were removed in Task 4.5 once the F0.2/F2.18/F0.6
migrations were confirmed stable in production. The underlying scripts
under backend/scripts/ are retained as maintenance tooling: they can be
invoked manually from a Railway shell or replicated via a fresh
short-lived endpoint if a future migration ever needs orchestrating.

TEMPORARY (Sprint 5.2.1): ``POST /api/admin/run-alembic-upgrade`` is
re-added below to apply the channel-registry migration (revision
7e3b2c4a8f51). Will be removed again once Sprint-5.4 auto-migration-
on-startup is in place.

Auth: every endpoint here is gated by the global Bearer-auth middleware
(W4 Task 4.3) and reads ``settings.api_token``. There is no separate
admin token; the historical layer-drift between an earlier per-endpoint
token check and the global middleware is described in
PHASE_4_DONE.md Lesson 6.
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


# ---------- Throwaway: alembic upgrade (Sprint 5.2.1, will be removed) ----


@router.post("/run-alembic-upgrade")
def run_alembic_upgrade() -> dict:
    """Apply pending Alembic migrations against the live DB.

    TEMPORARY — re-added in Sprint 5.2.1 to ship revision 7e3b2c4a8f51
    (channel-registry fields). Auto-migration on container start is on
    the Sprint 5.4 backlog; once that lands this endpoint goes away
    again, matching the W4 throwaway lifecycle described above.

    Wraps scripts.apply_alembic_upgrade.run() — that script handles
    schema bootstrap, baseline-stamp, and the upgrade itself. Auth comes
    from the global Bearer middleware (W4 Task 4.3); no per-endpoint
    token check (Lesson 6).
    """
    # In-function import per Phase-4 Lesson #1: keep app boot decoupled
    # from scripts/ being importable inside the container.
    from scripts import apply_alembic_upgrade as apply_mod  # noqa: PLC0415

    stats = apply_mod.run()
    return {
        "status": "ok" if not stats["errors"] else "error",
        "previous": stats["before_revision"],
        "current": stats["after_revision"],
        "actions": stats["actions"],
        "errors": stats["errors"],
    }
