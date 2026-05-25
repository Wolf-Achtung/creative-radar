"""Sprint F0.7 Hard-Cap-Vollausbau — Anthropic monthly budget tests.

Mirrors the F0.6 Apify-cap test layout (test_budget_check.py) so the
two providers stay in lockstep. Five guarantees:

1. Unit: ``compute_anthropic_monthly_spend`` aggregates across all five
   ``anthropic_*`` provider buckets (opus/haiku/sonnet/sonnet_vision/
   generic) and ignores non-Anthropic + prior-month rows.
2. Unit: soft-warn (>=80%) fires without hard-cap (>=100%) — same
   ~$80 cushion semantics as Apify.
3. Unit: M2-Retry-Re-Calls (multiple ``weekly_brief`` rows for the same
   pair-week) sum correctly into the monthly figure. PR #157 fires
   ``record_anthropic_call`` per Anthropic call, so retries land as
   separate CostLog rows and the cap must see all of them.
4. Unit: sub-cent precision via ``cost_usd_millicents`` — Haiku calls
   can land below 1 cent each but must still aggregate into a non-zero
   monthly total when there are enough of them.
5. Integration: when the cap is exceeded and the kill-switch is on,
   ``_run_cron_sync_background`` aborts before any Apify call. CronRun
   row commits with status ``budget_exceeded`` and reason
   ``anthropic_budget_exceeded`` (distinct from the Apify reason).
6. Integration: ``GET /api/admin/anthropic-budget-status`` returns the
   same payload shape that the pre-flight consults.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import Channel, CostLog, CronRun
from app.services.budget_check import (
    ANTHROPIC_PROVIDER_BUCKETS,
    BudgetStatus,
    _month_window_utc,
    compute_anthropic_monthly_spend,
)


def _engine_for_path(path: str):
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_anthropic_budget_", suffix=".db")
    os.close(fd)
    engine = _engine_for_path(path)
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.fixture
def client_with_auth(db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "TESTTOKEN", raising=False)
    # Apify config: required for the cron pre-flight to even get past
    # its Apify-side check (which runs first). Lock to Wolf-spec defaults
    # so the Apify cap doesn't accidentally fire and mask the Anthropic
    # behaviour we're testing.
    monkeypatch.setattr(settings, "apify_api_token", "TEST", raising=False)
    monkeypatch.setattr(settings, "apify_instagram_actor_id", "test/ig", raising=False)
    monkeypatch.setattr(settings, "apify_tiktok_actor_id", "test/tt", raising=False)
    monkeypatch.setattr(settings, "apify_monthly_budget_usd", 200.0, raising=False)
    monkeypatch.setattr(settings, "apify_soft_warn_pct", 0.80, raising=False)
    monkeypatch.setattr(settings, "apify_hard_cap_pct", 1.00, raising=False)
    monkeypatch.setattr(settings, "apify_budget_enforced", True, raising=False)
    # Anthropic-cap deterministic baseline.
    monkeypatch.setattr(settings, "anthropic_monthly_budget_usd", 100.0, raising=False)
    monkeypatch.setattr(settings, "anthropic_soft_warn_pct", 0.80, raising=False)
    monkeypatch.setattr(settings, "anthropic_hard_cap_pct", 1.00, raising=False)
    monkeypatch.setattr(settings, "anthropic_budget_enforced", True, raising=False)

    def _override():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override
    monkeypatch.setattr("app.api.cron.engine", db)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed_costlog(
    db,
    *,
    provider: str,
    usd_millicents: int,
    timestamp: datetime | None = None,
    operation: str = "weekly_brief",
) -> None:
    """Insert one CostLog row. ``usd_millicents`` is the primary precision
    column for the Anthropic cap; ``cost_usd_cents`` is derived as
    floor(millicents / 1000) so the row is self-consistent if anyone
    inspects either column."""
    with Session(db) as session:
        session.add(
            CostLog(
                provider=provider,
                operation=operation,
                cost_usd_cents=usd_millicents // 1000,
                cost_usd_millicents=usd_millicents,
                cost_eur_cents=int(round((usd_millicents / 1000) * 0.92)),
                cost_meta={},
                timestamp=timestamp or datetime.now(timezone.utc),
            )
        )
        session.commit()


def _seed_ig_channel(db, *, handle: str = "test") -> None:
    with Session(db) as session:
        ch = Channel(
            id=uuid4(),
            name=handle,
            handle=handle,
            url=f"https://www.instagram.com/{handle}/",
            platform="instagram",
            active=True,
            mvp=True,
        )
        session.add(ch)
        session.commit()


# ---------- Unit: aggregation correctness ----------------------------------


def test_aggregates_all_anthropic_buckets_in_current_month(
    db, monkeypatch: pytest.MonkeyPatch,
):
    """Sum must cover opus + haiku + sonnet + sonnet_vision + generic.
    Non-Anthropic providers and prior-month rows are filtered out."""
    monkeypatch.setattr(settings, "anthropic_monthly_budget_usd", 100.0, raising=False)
    monkeypatch.setattr(settings, "anthropic_soft_warn_pct", 0.80, raising=False)
    monkeypatch.setattr(settings, "anthropic_hard_cap_pct", 1.00, raising=False)
    monkeypatch.setattr(settings, "anthropic_budget_enforced", True, raising=False)

    now = datetime.now(timezone.utc)
    window_start, _ = _month_window_utc(now)
    last_month = window_start - timedelta(days=2)

    # Each bucket contributes $1.00 = 100 cents = 100_000 millicents.
    for bucket in ANTHROPIC_PROVIDER_BUCKETS:
        _seed_costlog(
            db, provider=bucket, usd_millicents=100_000,
            timestamp=now - timedelta(hours=1),
        )
    # Cross-provider noise — must be ignored.
    _seed_costlog(db, provider="apify", usd_millicents=999_000_000,
                  timestamp=now, operation="instagram_actor")
    _seed_costlog(db, provider="openai", usd_millicents=999_000_000,
                  timestamp=now, operation="chat_completion")
    # Last-month Anthropic — must be ignored.
    _seed_costlog(db, provider="anthropic_opus", usd_millicents=999_000_000,
                  timestamp=last_month)

    with Session(db) as session:
        status = compute_anthropic_monthly_spend(session, now=now)

    assert isinstance(status, BudgetStatus)
    # 5 buckets × $1.00 = $5.00 = 500 cents.
    assert status.spent_usd_cents == 500
    assert status.budget_usd_cents == 10_000  # $100
    assert status.pct_used == pytest.approx(0.05)
    assert status.soft_warn_exceeded is False
    assert status.hard_cap_exceeded is False
    assert status.enforced is True
    assert status.window_start == window_start


def test_soft_warn_fires_without_hard_cap(
    db, monkeypatch: pytest.MonkeyPatch,
):
    """85% of $100 = $85 spent. Above soft (80) and below hard (100).
    Same cushion semantics as the Apify cap."""
    monkeypatch.setattr(settings, "anthropic_monthly_budget_usd", 100.0, raising=False)
    monkeypatch.setattr(settings, "anthropic_soft_warn_pct", 0.80, raising=False)
    monkeypatch.setattr(settings, "anthropic_hard_cap_pct", 1.00, raising=False)
    monkeypatch.setattr(settings, "anthropic_budget_enforced", True, raising=False)

    # 8500 cents = $85 = 85% of $100 cap.
    _seed_costlog(db, provider="anthropic_opus", usd_millicents=8_500_000)

    with Session(db) as session:
        status = compute_anthropic_monthly_spend(session)

    assert status.soft_warn_exceeded is True
    assert status.hard_cap_exceeded is False
    assert status.pct_used == pytest.approx(0.85)


def test_m2_retry_recall_rows_sum_into_cap(
    db, monkeypatch: pytest.MonkeyPatch,
):
    """PR #157 (M2 JSON-parse retry) fires ``record_anthropic_call`` for
    every Anthropic call — initial + each re-call. The cap must see all
    of them, otherwise a paid-leerlauf pair-week (3 Opus calls, all
    parse-failed) would silently slip past the budget."""
    monkeypatch.setattr(settings, "anthropic_monthly_budget_usd", 100.0, raising=False)
    monkeypatch.setattr(settings, "anthropic_soft_warn_pct", 0.80, raising=False)
    monkeypatch.setattr(settings, "anthropic_hard_cap_pct", 1.00, raising=False)

    # Three weekly_brief rows for the same pair-week (initial + 2 re-calls,
    # all 200 OK with bad JSON → all billed). $1.50 each → $4.50 total.
    for _ in range(3):
        _seed_costlog(
            db, provider="anthropic_opus", usd_millicents=150_000,
            operation="weekly_brief",
        )

    with Session(db) as session:
        status = compute_anthropic_monthly_spend(session)

    # 3 × 150_000 = 450_000 millicents = 450 cents = $4.50.
    assert status.spent_usd_cents == 450
    assert status.pct_used == pytest.approx(0.045)


def test_sub_cent_haiku_calls_aggregate_via_millicents(
    db, monkeypatch: pytest.MonkeyPatch,
):
    """Haiku calls can land at fractions of a cent each. The cap query
    must sum ``cost_usd_millicents`` (not ``cost_usd_cents``, which
    floor-rounds to 0 per call) so the monthly figure stays real."""
    monkeypatch.setattr(settings, "anthropic_monthly_budget_usd", 100.0, raising=False)

    # 10 Haiku calls at 500 millicents each = 5000 millicents = 5 cents.
    # Each individual call would round to 0 cents under the legacy
    # ``cost_usd_cents``-only aggregation, but millicents preserves the
    # signal and the sum lands at the honest 5-cent figure.
    for _ in range(10):
        _seed_costlog(
            db, provider="anthropic_haiku", usd_millicents=500,
            operation="post_analyze",
        )

    with Session(db) as session:
        status = compute_anthropic_monthly_spend(session)

    assert status.spent_usd_cents == 5
    assert status.pct_used == pytest.approx(0.0005)


def test_kill_switch_disables_enforcement_but_keeps_visibility(
    db, monkeypatch: pytest.MonkeyPatch,
):
    """``anthropic_budget_enforced=False`` keeps the snapshot honest
    (soft/hard flags still computed) but flips ``enforced`` so the cron
    pre-flight will not abort. Wolf-spec parity with Apify kill-switch."""
    monkeypatch.setattr(settings, "anthropic_monthly_budget_usd", 100.0, raising=False)
    monkeypatch.setattr(settings, "anthropic_soft_warn_pct", 0.80, raising=False)
    monkeypatch.setattr(settings, "anthropic_hard_cap_pct", 1.00, raising=False)
    monkeypatch.setattr(settings, "anthropic_budget_enforced", False, raising=False)

    # 110% spend.
    _seed_costlog(db, provider="anthropic_opus", usd_millicents=11_000_000)

    with Session(db) as session:
        status = compute_anthropic_monthly_spend(session)

    assert status.hard_cap_exceeded is True
    assert status.enforced is False


# ---------- Integration: cron pre-flight abort -----------------------------


def test_cron_aborts_when_anthropic_budget_hard_cap_exceeded(client_with_auth, db):
    """Hard-Cap fires → CronRun lands at status ``budget_exceeded``,
    summary_json carries the Anthropic BudgetStatus + the Apify status
    (so operators see both providers' position), reason field
    distinguishes from the Apify abort. Apify scrape mocks must NOT
    execute — pre-flight short-circuits the entire pipeline."""
    _seed_ig_channel(db, handle="netflixde")
    # 105% of $100 cap = 10_500 cents = 10_500_000 millicents.
    _seed_costlog(db, provider="anthropic_opus", usd_millicents=10_500_000)

    with patch("app.api.cron.run_public_channel_monitor",
               new_callable=AsyncMock, return_value=[]) as mock_ig, \
         patch("app.api.cron.run_tiktok_profile_monitor",
               new_callable=AsyncMock, return_value=[]) as mock_tt:
        response = client_with_auth.post(
            "/api/admin/cron/sync-all",
            headers={"Authorization": "Bearer TESTTOKEN"},
        )

    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]

    with Session(db) as session:
        run = session.get(CronRun, UUID(run_id))
        assert run is not None
        assert run.status == "budget_exceeded", run.status
        assert run.summary_json is not None
        assert run.summary_json["skipped"] is True
        assert run.summary_json["reason"] == "anthropic_budget_exceeded"
        assert run.summary_json["anthropic_budget"]["hard_cap_exceeded"] is True
        assert run.summary_json["anthropic_budget"]["spent_usd_cents"] == 10_500
        # Apify budget is also surfaced in the abort summary so operators
        # see the full provider picture, even though it wasn't the cause.
        assert "budget" in run.summary_json
        assert run.summary_json["budget"]["hard_cap_exceeded"] is False

    mock_ig.assert_not_called()
    mock_tt.assert_not_called()


def test_cron_does_not_abort_when_kill_switch_off_even_if_over_cap(
    client_with_auth, db, monkeypatch: pytest.MonkeyPatch,
):
    """Kill-switch off (``anthropic_budget_enforced=False``) keeps the
    cron running even when the cap is exceeded. The CronRun should NOT
    land at ``budget_exceeded`` for the Anthropic reason."""
    monkeypatch.setattr(settings, "anthropic_budget_enforced", False, raising=False)
    _seed_ig_channel(db, handle="netflixde")
    _seed_costlog(db, provider="anthropic_opus", usd_millicents=10_500_000)

    with patch("app.api.cron.run_public_channel_monitor",
               new_callable=AsyncMock, return_value=[]), \
         patch("app.api.cron.run_tiktok_profile_monitor",
               new_callable=AsyncMock, return_value=[]):
        response = client_with_auth.post(
            "/api/admin/cron/sync-all",
            headers={"Authorization": "Bearer TESTTOKEN"},
        )

    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]

    with Session(db) as session:
        run = session.get(CronRun, UUID(run_id))
        assert run is not None
        # The kill-switch path should NOT abort with the anthropic reason.
        if run.status == "budget_exceeded":
            assert (run.summary_json or {}).get("reason") != "anthropic_budget_exceeded"


# ---------- Integration: admin endpoint ------------------------------------


def test_admin_anthropic_budget_status_endpoint_returns_current_state(
    client_with_auth, db,
):
    """Admin endpoint mirrors ``/budget-status`` shape, separate URL so
    the two providers stay readable in dashboards without one payload
    growing two top-level keys."""
    _seed_costlog(db, provider="anthropic_opus", usd_millicents=2_500_000)  # $25 = 25%
    _seed_costlog(db, provider="apify", usd_millicents=999_000_000)  # ignored
    _seed_costlog(db, provider="openai", usd_millicents=999_000_000)  # ignored

    response = client_with_auth.get(
        "/api/admin/anthropic-budget-status",
        headers={"Authorization": "Bearer TESTTOKEN"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["spent_usd_cents"] == 2_500
    assert body["budget_usd_cents"] == 10_000
    assert body["pct_used"] == pytest.approx(0.25)
    assert body["soft_warn_exceeded"] is False
    assert body["hard_cap_exceeded"] is False
    assert body["enforced"] is True
