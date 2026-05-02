"""Cost-logging helpers (Phase 4 W4 Task 4.4 / F0.6).

Two public entry points:

- ``record_apify_run(run_data, items_count, operation, meta=None)`` — call
  this right after every successful or failed Apify actor run with the
  ``run_data`` dict Apify returned. Pulls compute units out of run_data,
  converts via ``settings.apify_compute_unit_usd`` and ``usd_to_eur_rate``,
  persists one CostLog row.
- ``record_openai_call(usage, operation, meta=None)`` — call after every
  OpenAI chat or vision completion with the ``response.usage`` object.
  Token counts are converted via ``openai_{input,output}_per_1k_usd``.

Both helpers swallow their own DB-write errors (logged) so a failed
cost-log row never breaks the user-visible operation. Better to lose a
cost data point than to crash the analyze pipeline.

Read access is via ``GET /api/admin/cost-summary`` (Bearer-auth gated by
the W4 Task 4.3 middleware).
"""
from __future__ import annotations

import logging
from typing import Any

from sqlmodel import Session

from app.config import settings
from app.database import engine
from app.models.entities import CostLog

logger = logging.getLogger(__name__)


def _to_eur_cents(usd_cents: int) -> int:
    """Snapshot conversion at logging time so the rate-change isn't
    retroactive."""
    return int(round(usd_cents * (settings.usd_to_eur_rate or 0.92)))


def _persist(provider: str, operation: str, usd_cents: int, meta: dict | None) -> None:
    """Open a fresh session, write the row, never raise."""
    try:
        with Session(engine) as session:
            session.add(
                CostLog(
                    provider=provider,
                    operation=operation,
                    cost_usd_cents=usd_cents,
                    cost_eur_cents=_to_eur_cents(usd_cents),
                    cost_meta=meta or {},
                )
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "cost-log-write-failed",
            extra={
                "provider": provider,
                "operation": operation,
                "usd_cents": usd_cents,
                "error_class": type(exc).__name__,
            },
        )


def record_apify_run(
    run_data: dict[str, Any] | None,
    items_count: int,
    operation: str,
    meta: dict | None = None,
) -> None:
    """Persist one cost log row for an Apify actor run.

    ``run_data`` is the dict from Apify's run-response (under the ``data``
    key). We pull ``usage.COMPUTE_UNITS`` if present; otherwise estimate
    zero CU and log items_count anyway so the audit trail is intact.
    """
    compute_units = 0.0
    if isinstance(run_data, dict):
        usage = run_data.get("usage")
        if isinstance(usage, dict):
            cu = usage.get("COMPUTE_UNITS") or usage.get("computeUnits")
            try:
                compute_units = float(cu) if cu is not None else 0.0
            except (TypeError, ValueError):
                compute_units = 0.0

    usd = compute_units * (settings.apify_compute_unit_usd or 0.4)
    usd_cents = int(round(usd * 100))

    full_meta = {
        "compute_units": compute_units,
        "items_count": items_count,
        "actor_id": (run_data or {}).get("actId") if isinstance(run_data, dict) else None,
        "run_id": (run_data or {}).get("id") if isinstance(run_data, dict) else None,
        **(meta or {}),
    }
    _persist("apify", operation, usd_cents, full_meta)


def record_openai_call(
    usage: Any,
    operation: str,
    meta: dict | None = None,
) -> None:
    """Persist one cost log row for an OpenAI chat / vision completion.

    ``usage`` is the ``response.usage`` object from the SDK. We accept
    duck-typed input (the SDK returns a CompletionUsage dataclass; tests
    pass a plain dict). Either prompt_tokens/completion_tokens (modern SDK)
    or input_tokens/output_tokens (older naming) are tolerated.
    """
    def _get(name_a: str, name_b: str) -> int:
        if usage is None:
            return 0
        for name in (name_a, name_b):
            if hasattr(usage, name):
                value = getattr(usage, name)
                if value is not None:
                    return int(value)
            if isinstance(usage, dict) and name in usage:
                return int(usage[name] or 0)
        return 0

    input_tokens = _get("prompt_tokens", "input_tokens")
    output_tokens = _get("completion_tokens", "output_tokens")

    input_usd = (input_tokens / 1000.0) * (settings.openai_input_per_1k_usd or 0.0)
    output_usd = (output_tokens / 1000.0) * (settings.openai_output_per_1k_usd or 0.0)
    usd_cents = int(round((input_usd + output_usd) * 100))

    full_meta = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        **(meta or {}),
    }
    _persist("openai", operation, usd_cents, full_meta)
