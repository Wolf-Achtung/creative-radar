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

Sprint F0.7 (2026-05-25) — ``compute_anthropic_monthly_spend`` adds the
Anthropic-Monthly-Cap with the same shape and semantics. Settings split
between the two providers so they can be raised/lowered/disabled
independently; the cron pre-flight runs both checks sequentially and
aborts on whichever fires first.
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


# Sprint F0.7 — single source of truth for "every CostLog row that
# counts toward Anthropic spend". ``record_anthropic_call`` routes into
# one of these five buckets based on model family / operation; the
# Monthly-Cap query must filter on the full set so haiku, opus, sonnet
# (text), sonnet (vision), and the generic-fallback bucket all roll up.
# Kept here next to the cap query so future bucket additions land in one
# obvious place — ``aggregate_anthropic_costs_since`` below references
# the same tuple.
ANTHROPIC_PROVIDER_BUCKETS: tuple[str, ...] = (
    "anthropic",
    "anthropic_haiku",
    "anthropic_sonnet",
    "anthropic_sonnet_vision",
    "anthropic_opus",
)


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


def compute_anthropic_monthly_spend(
    session: Session,
    *,
    now: datetime | None = None,
) -> BudgetStatus:
    """Aggregate Anthropic USD spend over the current calendar month (UTC).

    Sprint F0.7 (2026-05-25). Same shape and semantics as
    ``compute_apify_monthly_spend`` — calendar-month window, half-open
    interval, permissive failure policy — but bucketed across the five
    Anthropic provider strings (see ``ANTHROPIC_PROVIDER_BUCKETS``) so
    Opus brief generation, Haiku/Sonnet post-analyzer calls, and the
    Sonnet vision pathway all roll into one monthly figure.

    Sub-cent precision: the brief path's M2-Retry-Logic (PR #157) can
    fire up to three Opus calls per pair-week, but Haiku post-analyzer
    calls land at fractions of a cent each. We sum
    ``cost_usd_millicents`` (1 cent = 1000 millicents — the precision-
    safe column added in the 2026-05-12 cost-tracking-fix) and floor-
    divide to cents at the boundary. The ``BudgetStatus`` shape stays
    identical to F0.6 so the cron summary block and admin endpoint can
    handle both providers without branching.

    Wolf-spec baseline (two data points pre-launch): 17.05 force run of
    9 pairs = $17.27; 25.05 regular run of 8 pairs incl. one M2-retry
    warnerbros = $17.90. Default cap $100/month gives ~5× cushion over
    the observed weekly cost — enough for prompt expansions, more pairs,
    or a bad week of intermittent JSON-parse retries.
    """
    window_start, window_end = _month_window_utc(now)

    budget_usd = float(settings.anthropic_monthly_budget_usd or 0.0)
    budget_usd_cents = int(round(budget_usd * 100))

    try:
        spent_millicents = int(session.exec(
            select(func.coalesce(func.sum(CostLog.cost_usd_millicents), 0))
            .where(CostLog.provider.in_(ANTHROPIC_PROVIDER_BUCKETS))
            .where(CostLog.timestamp >= window_start)
            .where(CostLog.timestamp < window_end)
        ).one())
    except Exception as exc:  # noqa: BLE001
        # Permissive fallback — same reasoning as the Apify path.
        # A cap query that hiccups must not silently block the cron.
        logger.warning("anthropic-budget-read-failed: %s", exc)
        spent_millicents = 0

    # Floor-divide millicents → cents. At $100 budget scale, sub-cent
    # rounding never crosses the boundary in either direction — a $99.9999
    # spend reads as 9999 cents (under cap), a $100.001 reads as 10000
    # cents (at cap). Reporting in cents keeps the BudgetStatus shape
    # interchangeable with the Apify path.
    spent_usd_cents = spent_millicents // 1000

    pct_used = (spent_usd_cents / budget_usd_cents) if budget_usd_cents > 0 else 0.0
    soft_pct = float(settings.anthropic_soft_warn_pct or 0.0)
    hard_pct = float(settings.anthropic_hard_cap_pct or 0.0)

    return BudgetStatus(
        window_start=window_start,
        window_end=window_end,
        spent_usd_cents=spent_usd_cents,
        budget_usd_cents=budget_usd_cents,
        pct_used=pct_used,
        soft_warn_exceeded=pct_used >= soft_pct,
        hard_cap_exceeded=pct_used >= hard_pct,
        enforced=bool(settings.anthropic_budget_enforced),
    )


def compute_openai_monthly_spend(
    session: Session,
    *,
    now: datetime | None = None,
) -> BudgetStatus:
    """Aggregate OpenAI USD spend over the current calendar month (UTC).

    Incident 2026-07-13 (Re-Audit-Folgefund): unlike Apify (F0.6) and
    Anthropic (F0.7), OpenAI never had a monthly hard-cap despite being a
    real, uncapped cost line (Vision-Analyse + Caption-Analyse, ~500-700
    Calls/Woche). Same shape and semantics as the other two: calendar-
    month window, half-open interval, permissive failure policy. Sums
    ``cost_usd_millicents`` for the single ``openai`` provider bucket
    (matches ``aggregate_openai_costs_since`` above).
    """
    window_start, window_end = _month_window_utc(now)

    budget_usd = float(settings.openai_monthly_budget_usd or 0.0)
    budget_usd_cents = int(round(budget_usd * 100))

    try:
        spent_millicents = int(session.exec(
            select(func.coalesce(func.sum(CostLog.cost_usd_millicents), 0))
            .where(CostLog.provider == "openai")
            .where(CostLog.timestamp >= window_start)
            .where(CostLog.timestamp < window_end)
        ).one())
    except Exception as exc:  # noqa: BLE001
        # Permissive fallback — same reasoning as the Apify/Anthropic paths.
        logger.warning("openai-budget-read-failed: %s", exc)
        spent_millicents = 0

    spent_usd_cents = spent_millicents // 1000

    pct_used = (spent_usd_cents / budget_usd_cents) if budget_usd_cents > 0 else 0.0
    soft_pct = float(settings.openai_soft_warn_pct or 0.0)
    hard_pct = float(settings.openai_hard_cap_pct or 0.0)

    return BudgetStatus(
        window_start=window_start,
        window_end=window_end,
        spent_usd_cents=spent_usd_cents,
        budget_usd_cents=budget_usd_cents,
        pct_used=pct_used,
        soft_warn_exceeded=pct_used >= soft_pct,
        hard_cap_exceeded=pct_used >= hard_pct,
        enforced=bool(settings.openai_budget_enforced),
    )


def _aggregate_costs_by_provider_prefix(
    session: Session,
    *,
    providers: tuple[str, ...],
    since: datetime,
    log_tag: str,
) -> dict:
    """Shared body for the anthropic/openai cost aggregators below.

    The two callers differ only in their provider-bucket filter:
    Anthropic has four buckets (``anthropic``, ``anthropic_haiku``,
    ``anthropic_sonnet``, ``anthropic_sonnet_vision``, ``anthropic_opus``)
    after the 2026-05-12 fix; OpenAI is a single bucket. Both sum the
    new ``cost_usd_millicents`` column to preserve sub-cent calls, and
    both bucket-count per ``operation`` for dashboard drill-down.
    """
    try:
        rows = list(session.exec(
            select(
                CostLog.provider,
                CostLog.operation,
                CostLog.cost_usd_millicents,
            )
            .where(CostLog.provider.in_(providers))
            .where(CostLog.timestamp >= since)
        ).all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s-cost-aggregate-failed: %s", log_tag, exc)
        rows = []

    total_millicents = 0
    calls_by_operation: dict[str, int] = {}
    calls_by_provider: dict[str, int] = {}
    for provider, operation, cost_millicents in rows:
        total_millicents += int(cost_millicents or 0)
        calls_by_operation[operation] = calls_by_operation.get(operation, 0) + 1
        calls_by_provider[provider] = calls_by_provider.get(provider, 0) + 1

    return {
        # USD is the source of truth; rounding to 6 decimals keeps sub-cent
        # precision visible in JSON while staying readable in dashboards.
        "estimated_cost_usd": round(total_millicents / 100_000.0, 6),
        "calls_total": len(rows),
        "calls_by_operation": calls_by_operation,
        "calls_by_provider": calls_by_provider,
    }


def aggregate_anthropic_costs_since(session: Session, since: datetime) -> dict:
    """Cost-Tracking-Fix 2026-05-12 — Anthropic-Cost-Aggregat für einen
    laufenden Cron-Run.

    Bündelt alle Anthropic-Provider-Buckets (``anthropic``,
    ``anthropic_haiku``, ``anthropic_sonnet``, ``anthropic_sonnet_vision``,
    ``anthropic_opus``) zu einem Block für ``cron_run.summary_json``. Vor
    diesem Sprint waren Anthropic-Buckets gar nicht im Cron-Summary
    sichtbar — der Brief-Pfad und post_analyzer-Vision-Pfad hatten je
    Cost in der DB, das Dashboard sah nichts davon.

    Aggregiert ``cost_usd_millicents`` statt ``cost_usd_cents`` — der
    Sub-Cent-Pfad ist nach dem Precision-Loss-Fix die einzige verlässliche
    Quelle. Bei null Anthropic-Calls bleibt der Block mit Nullen sichtbar
    (gleiche Konvention wie Apify-Aggregator).
    """
    return _aggregate_costs_by_provider_prefix(
        session,
        providers=ANTHROPIC_PROVIDER_BUCKETS,
        since=since,
        log_tag="anthropic",
    )


def aggregate_openai_costs_since(session: Session, since: datetime) -> dict:
    """Cost-Tracking-Fix 2026-05-12 — OpenAI-Cost-Aggregat für einen
    laufenden Cron-Run.

    Vor dem Precision-Loss-Fix lieferte ``cost_usd_cents`` für 1118
    chat_completions + 448 vision_calls über 7 Tage konstant 0. Nach dem
    Fix summieren wir ``cost_usd_millicents`` und zeigen den realen
    Sub-Cent-Verbrauch (~$0.80/7d Schätzung).
    """
    return _aggregate_costs_by_provider_prefix(
        session,
        providers=("openai",),
        since=since,
        log_tag="openai",
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
