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


def _persist(
    provider: str,
    operation: str,
    usd_cents: int,
    meta: dict | None,
    *,
    usd_millicents: int | None = None,
) -> None:
    """Open a fresh session, write the row, never raise.

    ``usd_millicents`` is the sub-cent-precise integer (1 cent = 1000
    millicents) and survives the rounding loss that flattens many LLM
    per-call costs to ``cost_usd_cents = 0`` for gpt-4o-mini (~0.03-0.06
    cents/call). When omitted we backfill ``usd_cents * 1000`` so legacy
    callers (Apify, YouTube quota logging) keep working without churn.
    """
    if usd_millicents is None:
        usd_millicents = usd_cents * 1000
    try:
        with Session(engine) as session:
            session.add(
                CostLog(
                    provider=provider,
                    operation=operation,
                    cost_usd_cents=usd_cents,
                    cost_usd_millicents=usd_millicents,
                    cost_eur_cents=_to_eur_cents(usd_cents),
                    cost_meta=meta or {},
                )
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001
        # ERROR statt WARNING: ein verlorener costlog-Row ist
        # audit-relevant — Wolf hat im 2026-05-12-Smoke-Test gesehen,
        # dass eine silent-swallow-Anomalie genau das Doppel-Call-
        # Tracking unbrauchbar gemacht hat. Log-Extras decken den
        # vollen Recovery-Datensatz ab (provider, operation, USD-Cents
        # + Millicents, plus ``meta`` mit Tokens/Modell), damit eine
        # verlorene Row aus dem Log-Aggregator rekonstruierbar ist.
        logger.error(
            "cost-log-write-failed",
            extra={
                "provider": provider,
                "operation": operation,
                "usd_cents": usd_cents,
                "usd_millicents": usd_millicents,
                "meta": meta or {},
                "error_class": type(exc).__name__,
                "error_message": str(exc),
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
    key). The authoritative USD figure lives in ``usageTotalUsd``, which
    Apify computes server-side over the run's active pricing model — Pay-
    Per-Usage (compute units), Pay-Per-Event, Pay-Per-Dataset-Item, or
    any combination Apify ships next. Reading this single field replaces
    the older ``compute_units * settings.apify_compute_unit_usd`` math
    that broke when Apify migrated ``apify~instagram-scraper`` and
    ``clockworks~tiktok-scraper`` to Pay-Per-Event in 2025: the CU figure
    is now $0 for those actors, all real cost lives in event buckets.

    Fallback policy: if ``usageTotalUsd`` is missing (anonymous run,
    free-tier owner, or a malformed response), we log 0 cents with a
    WARN log and preserve the legacy ``compute_units`` + ``items_count``
    in ``cost_meta`` so the audit trail keeps something we can drill on
    later.
    """
    if not isinstance(run_data, dict):
        run_data = {}

    usage_total_usd_raw = run_data.get("usageTotalUsd")
    usage_total_usd: float | None
    try:
        usage_total_usd = (
            float(usage_total_usd_raw) if usage_total_usd_raw is not None else None
        )
    except (TypeError, ValueError):
        usage_total_usd = None

    if usage_total_usd is None:
        logger.warning(
            "apify-usage-total-usd-missing",
            extra={
                "operation": operation,
                "run_id": run_data.get("id"),
                "actor_id": run_data.get("actId"),
            },
        )
        usd_cents = 0
    else:
        usd_cents = int(round(usage_total_usd * 100))

    # Backward-compat audit fields: keep ``compute_units`` derived from
    # the legacy ``usage`` dict so a historical drill-down still works,
    # even though we no longer base ``cost_usd_cents`` on it.
    compute_units = 0.0
    usage_dict = run_data.get("usage")
    if isinstance(usage_dict, dict):
        cu = (
            usage_dict.get("ACTOR_COMPUTE_UNITS")
            or usage_dict.get("COMPUTE_UNITS")
            or usage_dict.get("computeUnits")
        )
        try:
            compute_units = float(cu) if cu is not None else 0.0
        except (TypeError, ValueError):
            compute_units = 0.0

    pricing_model = None
    pricing_info = run_data.get("pricingInfo")
    if isinstance(pricing_info, dict):
        pricing_model = pricing_info.get("pricingModel")

    full_meta = {
        "usage_total_usd": usage_total_usd,
        "charged_event_counts": run_data.get("chargedEventCounts") or {},
        "usage_usd": run_data.get("usageUsd") or {},
        "pricing_model": pricing_model,
        "compute_units": compute_units,
        "items_count": items_count,
        "actor_id": run_data.get("actId"),
        "run_id": run_data.get("id"),
        **(meta or {}),
    }
    _persist("apify", operation, usd_cents, full_meta)


def record_youtube_api_call(
    quota_units: int,
    operation: str,
    meta: dict | None = None,
) -> None:
    """Persist one cost log row for a YouTube Data API v3 call.

    YouTube's free tier is 10k quota units per day; we don't pay USD until
    that's exhausted. We log ``cost_usd_cents=0`` and stash ``quota_units``
    in ``cost_meta`` so cost-summary can surface the daily quota burn even
    though the EUR/USD totals stay at zero. Provider name matches the
    ``AcquisitionStrategy.YOUTUBE_API`` enum value from Sprint 5.2.1.
    """
    full_meta = {
        "quota_units": int(quota_units or 0),
        **(meta or {}),
    }
    _persist("youtube_api", operation, 0, full_meta)


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
    total_usd = input_usd + output_usd
    # Precision-loss-fix: gpt-4o-mini calls cost ~0.03-0.06 cents each, so
    # int(round(total_usd*100)) flattens every single call to 0 and the
    # per-call audit trail becomes useless for aggregation. Persist the
    # millicent-precise integer in addition to the cents column so the
    # cost-summary endpoint can sum sub-cent calls without losing them.
    usd_millicents = int(round(total_usd * 100_000))
    usd_cents = int(round(total_usd * 100))

    full_meta = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd_millicents": usd_millicents,
        **(meta or {}),
    }
    _persist("openai", operation, usd_cents, full_meta, usd_millicents=usd_millicents)


def record_anthropic_call(
    usage: Any,
    model: str,
    operation: str,
    meta: dict | None = None,
) -> None:
    """Persist one cost log row for an Anthropic Messages call (Sprint 5.3.1).

    ``usage`` is the ``message.usage`` object from the SDK (or a dict
    in tests). The Anthropic SDK has reported the *final*
    input_tokens (already including image-conversion tokens for vision
    calls) since mid-2024, so there is no separate image-token
    pricing.

    ``input_tokens`` allein ist aber NICHT der volle Input: bei aktivem
    Prompt-Caching zaehlt es nur die Token nach dem letzten Cache-
    Breakpoint. Die Input-Kosten werden deshalb aus drei Toepfen gebildet
    (regulaer 1.00x, 5m-Cache-Write 1.25x, 1h-Cache-Write 2.00x,
    Cache-Read 0.10x auf ``in_rate``); ohne verlaesslichen 5m/1h-Split
    zaehlt der gesamte Write-Anteil konservativ zum 1h-Satz.

    Provider routing into three cost-summary buckets (Wolf-spec):
    - operation starting with ``vision_`` -> ``anthropic_sonnet_vision``
    - model starting with ``claude-haiku``  -> ``anthropic_haiku``
    - model starting with ``claude-sonnet`` -> ``anthropic_sonnet``
    Anything else falls through to the generic ``anthropic`` provider
    so the row is still auditable.

    The pricing-per-1k-token lookup keys off the same model-prefix
    check; cost_meta records the resolved provider + model for
    post-hoc auditing without re-deriving the bucket logic.
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

    input_tokens = _get("input_tokens", "prompt_tokens")
    output_tokens = _get("output_tokens", "completion_tokens")

    # Prompt-Caching-Diagnose (reines Logging, keine Kosten-/Bucket-Logik):
    # ``record_anthropic_call`` laeuft an JEDER Anthropic-Call-Site, also ist
    # das die eine Stelle, an der sich Cache-Wirksamkeit ohne Signatur-
    # Aenderung beobachten laesst. ``operation`` ist das Call-Site-Label.
    # cache_read_input_tokens=0 ueber wiederholte Calls mit identischem
    # Prefix => Cache greift nicht (heute: kein cache_control gesetzt, also
    # erwartungsgemaess durchgaengig 0).
    cache_creation_input_tokens = _get(
        "cache_creation_input_tokens", "cache_creation_tokens"
    )
    cache_read_input_tokens = _get("cache_read_input_tokens", "cache_read_tokens")
    logger.info(
        "anthropic-cache-usage",
        extra={
            "call_site": operation,
            "model": model,
            "input_tokens": input_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
            "cache_read_input_tokens": cache_read_input_tokens,
            "prompt_tokens_total": (
                input_tokens + cache_creation_input_tokens + cache_read_input_tokens
            ),
        },
    )

    model_lc = (model or "").lower()
    if model_lc.startswith("claude-haiku"):
        in_rate = settings.anthropic_haiku_input_per_1k_usd or 0.0
        out_rate = settings.anthropic_haiku_output_per_1k_usd or 0.0
        bucket_family = "anthropic_haiku"
    elif model_lc.startswith("claude-sonnet"):
        in_rate = settings.anthropic_sonnet_input_per_1k_usd or 0.0
        out_rate = settings.anthropic_sonnet_output_per_1k_usd or 0.0
        bucket_family = "anthropic_sonnet"
    elif model_lc.startswith("claude-opus"):
        in_rate = settings.anthropic_opus_input_per_1k_usd or 0.0
        out_rate = settings.anthropic_opus_output_per_1k_usd or 0.0
        bucket_family = "anthropic_opus"
    else:
        in_rate = 0.0
        out_rate = 0.0
        bucket_family = "anthropic"

    # Vision attribution rides on the operation name, not the model —
    # Wolf's design: vision and text Sonnet calls share the same
    # token-cost math, but report into separate buckets so daily
    # cost-summary can split text vs vision spend at a glance.
    if bucket_family == "anthropic_sonnet" and (operation or "").startswith("vision_"):
        provider = "anthropic_sonnet_vision"
    else:
        provider = bucket_family

    # --- Cache-aware Input-Kosten ----------------------------------------
    # Bei aktivem Prompt-Caching zaehlt ``input_tokens`` nur noch die Token
    # NACH dem letzten Cache-Breakpoint; der gecachte Anteil steckt in
    # ``cache_creation_input_tokens`` (Write) bzw. ``cache_read_input_tokens``
    # (Read). Wer nur ``input_tokens`` abrechnet, unterschaetzt die Kosten —
    # und ANTHROPIC_MONTHLY_BUDGET_USD greift entsprechend zu spaet.
    #
    # Multiplikatoren auf ``in_rate`` (Anthropic-Preisdoku), bewusst aus der
    # vorhandenen Rate abgeleitet statt als neue settings-Keys:
    #   regulaerer Input 1.00 | 5m-Write 1.25 | 1h-Write 2.00 | Read 0.10
    def _cache_creation_split() -> tuple[int, int] | None:
        """``(5m, 1h)``-Split aus ``usage.cache_creation``.

        ``None``, wenn der Split fehlt, nicht lesbar ist oder seine Summe
        nicht zu ``cache_creation_input_tokens`` passt — der Aufrufer faellt
        dann auf den teureren 1h-Satz zurueck.
        """
        container: Any = None
        if usage is not None:
            if hasattr(usage, "cache_creation"):
                container = getattr(usage, "cache_creation")
            elif isinstance(usage, dict):
                container = usage.get("cache_creation")
        if container is None:
            return None

        def _field(name: str) -> int | None:
            value = None
            if hasattr(container, name):
                value = getattr(container, name)
            elif isinstance(container, dict) and name in container:
                value = container[name]
            if value is None:
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        five_m = _field("ephemeral_5m_input_tokens")
        one_h = _field("ephemeral_1h_input_tokens")
        if five_m is None and one_h is None:
            return None
        five_m, one_h = five_m or 0, one_h or 0
        if five_m + one_h != cache_creation_input_tokens:
            # Split passt nicht zur Gesamtsumme → nicht vertrauenswuerdig.
            return None
        return five_m, one_h

    # ``cache_split_source`` macht im Audit-Trail unterscheidbar, ob
    # ``cache_creation_5m/1h`` gemeldete Werte sind oder nur die Preis-Basis
    # des konservativen Pfads — sobald Caching live ist, waere das sonst nicht
    # mehr rekonstruierbar. Zweitnutzen: greift ``fallback`` im Betrieb
    # regelmaessig, ist das ein Signal (geaenderte Response-Form der API oder
    # unbeabsichtigtes 1h-TTL).
    _split = _cache_creation_split()
    if cache_creation_input_tokens == 0:
        # Kein Write — es gibt schlicht nichts zu splitten.
        cache_creation_5m, cache_creation_1h = 0, 0
        cache_split_source = "none"
    elif _split is not None:
        cache_creation_5m, cache_creation_1h = _split
        cache_split_source = "reported"
    else:
        # Konservativ: ohne verlaesslichen Split den GESAMTEN Write-Anteil zum
        # teureren 1h-Satz rechnen, damit der Budget-Cap eher zu frueh als zu
        # spaet greift. Die beiden Werte in ``full_meta`` geben deshalb die
        # Preis-Basis wieder, nicht zwingend eine von der API gemeldete TTL.
        cache_creation_5m, cache_creation_1h = 0, cache_creation_input_tokens
        cache_split_source = "fallback"

    billable_input_tokens = (
        input_tokens
        + cache_creation_5m * 1.25
        + cache_creation_1h * 2.00
        + cache_read_input_tokens * 0.10
    )
    input_usd = (billable_input_tokens / 1000.0) * in_rate
    output_usd = (output_tokens / 1000.0) * out_rate
    total_usd = input_usd + output_usd
    # Same precision-loss reasoning as record_openai_call: Haiku calls
    # ($1/$5 per Mtok) can land below 1 cent each; the millicent column
    # preserves the per-call signal so cost-summary can aggregate without
    # the floor-to-zero round-trip.
    usd_millicents = int(round(total_usd * 100_000))
    usd_cents = int(round(total_usd * 100))

    full_meta = {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "cache_creation_5m": cache_creation_5m,
        "cache_creation_1h": cache_creation_1h,
        "cache_split_source": cache_split_source,
        "prompt_tokens_total": (
            input_tokens + cache_creation_input_tokens + cache_read_input_tokens
        ),
        "cost_usd_millicents": usd_millicents,
        **(meta or {}),
    }
    _persist(provider, operation, usd_cents, full_meta, usd_millicents=usd_millicents)
