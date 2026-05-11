"""Apify monthly budget check (Sprint F0.6 Hard-Cap-Vollausbau).

Reads ``CostLog`` rows for ``provider='apify'`` in the current calendar
month (UTC) and compares the aggregated USD spend against the configured
``apify_monthly_budget_usd``. Two thresholds:

- ``apify_soft_warn_pct`` (default 80%) — non-blocking. The cron tail
  documents ``summary["budget_warning"]=True`` so it surfaces in admin
  dashboards without aborting the run.
- ``apify_hard_cap_pct`` (default 100%) — blocking when
  ``apify_budget_enforced`` is True. The cron background task short-
  circuits, the ``CronRun`` row is committed with status
  ``budget_exceeded`` instead of ``completed``/``failed``.

Reset logic: calendar month, UTC. The window opens on the first of the
month at 00:00 UTC and the cap auto-resets when the calendar rolls over.
Wolf can override the cap mid-month via Railway ENV (raise the budget,
flip the kill-switch); no deploy needed.

Failure policy: a DB error during the read returns a "permissive"
status — better to let a cron run rather than block on a stat-query
hiccup. The audit log surfaces the failure for follow-up.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import Session, select

from app.config import settings
from app.models.entities import CostLog

logger = logging.getLogger(__name__)


@dataclass
class BudgetStatus:
    window_start: datetime
    window_end: datetime
    spent_usd_cents: int
    budget_usd_cents: int
    pct_used: float
    soft_warn_exceeded: bool
    hard_cap_exceeded: bool
    enforced: bool

    def to_dict(self) -> dict:
        # ``cost_eur_cents`` deliberately omitted — the cap is denominated
        # in USD per Wolf-spec, EUR is a presentation concern for the
        # admin dashboard.
        return {
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "spent_usd_cents": self.spent_usd_cents,
            "budget_usd_cents": self.budget_usd_cents,
            "pct_used": round(self.pct_used, 4),
            "soft_warn_exceeded": self.soft_warn_exceeded,
            "hard_cap_exceeded": self.hard_cap_exceeded,
            "enforced": self.enforced,
        }


def _month_window_utc(now: datetime | None = None) -> tuple[datetime, datetime]:
    """First-of-month 00:00 UTC to first-of-next-month 00:00 UTC.

    Half-open: ``[window_start, window_end)``. The exclusive upper bound
    matches the convention used by ``CostLog.timestamp >= start AND
    timestamp < end`` filters elsewhere in the codebase.
    """
    base = now or datetime.now(timezone.utc)
    start = base.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def compute_apify_monthly_spend(
    session: Session,
    *,
    now: datetime | None = None,
) -> BudgetStatus:
    """Aggregate Apify USD spend over the current calendar month (UTC).

    ``now`` is injectable for tests; production callers pass nothing.
    Returns a ``BudgetStatus`` with both thresholds resolved. The caller
    decides whether to act on ``hard_cap_exceeded`` (cron pre-flight) or
    just surface ``soft_warn_exceeded`` (read endpoint).
    """
    window_start, window_end = _month_window_utc(now)

    budget_usd = float(settings.apify_monthly_budget_usd or 0.0)
    budget_usd_cents = int(round(budget_usd * 100))

    try:
        spent_usd_cents = int(session.exec(
            select(func.coalesce(func.sum(CostLog.cost_usd_cents), 0))
            .where(CostLog.provider == "apify")
            .where(CostLog.timestamp >= window_start)
            .where(CostLog.timestamp < window_end)
        ).one())
    except Exception as exc:  # noqa: BLE001
        # Permissive fallback — see module docstring failure policy.
        logger.warning("apify-budget-read-failed: %s", exc)
        spent_usd_cents = 0

    pct_used = (spent_usd_cents / budget_usd_cents) if budget_usd_cents > 0 else 0.0
    soft_pct = float(settings.apify_soft_warn_pct or 0.0)
    hard_pct = float(settings.apify_hard_cap_pct or 0.0)

    return BudgetStatus(
        window_start=window_start,
        window_end=window_end,
        spent_usd_cents=spent_usd_cents,
        budget_usd_cents=budget_usd_cents,
        pct_used=pct_used,
        soft_warn_exceeded=pct_used >= soft_pct,
        hard_cap_exceeded=pct_used >= hard_pct,
        enforced=bool(settings.apify_budget_enforced),
    )


def aggregate_apify_costs_since(session: Session, since: datetime) -> dict:
    """Tech-Debt A5 — Apify-Cost-Aggregat für den laufenden Cron-Run.

    Bündelt alle ``provider='apify'``-Rows mit ``timestamp >= since`` zu
    einem Surface-Block für ``cron_run.summary_json``. Format analog zum
    Vision-Block (``estimated_cost_usd`` als USD-float gerundet auf 4
    Stellen). Cents bleiben Backend-intern.

    Bei null Calls (Apify nicht konfiguriert, alle Channels geskippt,
    Run vor `record_apify_run` abgebrochen) emittiert die Funktion
    trotzdem den Block mit Nullen — ein sichtbares „diesem Run wurden
    keine Apify-Kosten zugeordnet" ist im Dashboard mehr wert als ein
    fehlendes Feld.

    Failure policy: DB-Fehler werden geloggt und produzieren einen
    Null-Block. Die Logging-Pipeline ist nicht der richtige Ort, einen
    Cron-Run wegen einer Stat-Query scheitern zu lassen.
    """
    try:
        rows = list(session.exec(
            select(CostLog.operation, CostLog.cost_usd_cents)
            .where(CostLog.provider == "apify")
            .where(CostLog.timestamp >= since)
        ).all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("apify-cost-aggregate-failed: %s", exc)
        rows = []

    total_cents = 0
    calls_by_operation: dict[str, int] = {}
    for operation, cost_usd_cents in rows:
        total_cents += int(cost_usd_cents or 0)
        calls_by_operation[operation] = calls_by_operation.get(operation, 0) + 1

    return {
        "estimated_cost_usd": round(total_cents / 100.0, 4),
        "calls_total": len(rows),
        "calls_by_operation": calls_by_operation,
    }
