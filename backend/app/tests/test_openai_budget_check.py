"""Incident 2026-07-13 (Re-Audit-Folgefund) — OpenAI monthly budget tests.

Mirrors the Apify/Anthropic-cap test layout (test_budget_check.py /
test_anthropic_budget_check.py) so all three providers stay in lockstep.
OpenAI was the only one of the three cost-incurring providers with no
hard cap at all despite real, ongoing spend (Vision-Analyse + Caption-
Analyse, ~500-700 Calls/Woche).
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
    BudgetStatus,
    _month_window_utc,
    compute_openai_monthly_spend,
)


def _engine_for_path(path: str):
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_openai_budget_", suffix=".db")
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
    # Audit 2026-08-17: sync-all verlangt den dedizierten Cron-Token oder
    # eine Admin-Session (require_cron_trigger_auth) — Haupt-Token reicht
    # nicht mehr; diese Tests fahren den GitHub-Action-Pfad.
    monkeypatch.setattr(settings, "cron_api_token", "TESTTOKEN", raising=False)
    # Apify + Anthropic: locked to generous defaults so their pre-flight
    # checks (which run BEFORE the OpenAI one) don't accidentally fire
    # and mask the OpenAI behaviour under test.
    monkeypatch.setattr(settings, "apify_api_token", "TEST", raising=False)
    monkeypatch.setattr(settings, "apify_instagram_actor_id", "test/ig", raising=False)
    monkeypatch.setattr(settings, "apify_tiktok_actor_id", "test/tt", raising=False)
    monkeypatch.setattr(settings, "apify_monthly_budget_usd", 200.0, raising=False)
    monkeypatch.setattr(settings, "apify_budget_enforced", True, raising=False)
    monkeypatch.setattr(settings, "anthropic_monthly_budget_usd", 200.0, raising=False)
    monkeypatch.setattr(settings, "anthropic_budget_enforced", True, raising=False)
    # OpenAI-cap deterministic baseline.
    monkeypatch.setattr(settings, "openai_monthly_budget_usd", 50.0, raising=False)
    monkeypatch.setattr(settings, "openai_soft_warn_pct", 0.80, raising=False)
    monkeypatch.setattr(settings, "openai_hard_cap_pct", 1.00, raising=False)
    monkeypatch.setattr(settings, "openai_budget_enforced", True, raising=False)

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
    operation: str = "vision_call",
) -> None:
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


def test_aggregates_openai_bucket_in_current_month(db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "openai_monthly_budget_usd", 50.0, raising=False)
    monkeypatch.setattr(settings, "openai_soft_warn_pct", 0.80, raising=False)
    monkeypatch.setattr(settings, "openai_hard_cap_pct", 1.00, raising=False)
    monkeypatch.setattr(settings, "openai_budget_enforced", True, raising=False)

    now = datetime.now(timezone.utc)
    window_start, _ = _month_window_utc(now)
    last_month = window_start - timedelta(days=2)

    _seed_costlog(db, provider="openai", usd_millicents=500_000,
                  timestamp=now - timedelta(hours=1))
    # Cross-provider noise — must be ignored.
    _seed_costlog(db, provider="apify", usd_millicents=999_000_000, timestamp=now)
    _seed_costlog(db, provider="anthropic_opus", usd_millicents=999_000_000, timestamp=now)
    # Last-month OpenAI — must be ignored.
    _seed_costlog(db, provider="openai", usd_millicents=999_000_000, timestamp=last_month)

    with Session(db) as session:
        status = compute_openai_monthly_spend(session, now=now)

    assert isinstance(status, BudgetStatus)
    assert status.spent_usd_cents == 500  # $5.00
    assert status.budget_usd_cents == 5_000  # $50
    assert status.pct_used == pytest.approx(0.10)
    assert status.soft_warn_exceeded is False
    assert status.hard_cap_exceeded is False
    assert status.enforced is True
    assert status.window_start == window_start


def test_soft_warn_fires_without_hard_cap(db, monkeypatch: pytest.MonkeyPatch):
    """85% of $50 = $42.50 spent. Above soft (80%) and below hard (100%)."""
    monkeypatch.setattr(settings, "openai_monthly_budget_usd", 50.0, raising=False)
    monkeypatch.setattr(settings, "openai_soft_warn_pct", 0.80, raising=False)
    monkeypatch.setattr(settings, "openai_hard_cap_pct", 1.00, raising=False)

    _seed_costlog(db, provider="openai", usd_millicents=4_250_000)  # $42.50

    with Session(db) as session:
        status = compute_openai_monthly_spend(session)

    assert status.soft_warn_exceeded is True
    assert status.hard_cap_exceeded is False
    assert status.pct_used == pytest.approx(0.85)


def test_sub_cent_vision_calls_aggregate_via_millicents(db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "openai_monthly_budget_usd", 50.0, raising=False)

    # 10 calls at 300 millicents each = 3000 millicents = 3 cents — each
    # individual call would round to 0 cents under a cents-only aggregate.
    for _ in range(10):
        _seed_costlog(db, provider="openai", usd_millicents=300, operation="chat_completion")

    with Session(db) as session:
        status = compute_openai_monthly_spend(session)

    assert status.spent_usd_cents == 3
    assert status.pct_used == pytest.approx(0.0006)


def test_kill_switch_disables_enforcement_but_keeps_visibility(
    db, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "openai_monthly_budget_usd", 50.0, raising=False)
    monkeypatch.setattr(settings, "openai_hard_cap_pct", 1.00, raising=False)
    monkeypatch.setattr(settings, "openai_budget_enforced", False, raising=False)

    # 110% spend.
    _seed_costlog(db, provider="openai", usd_millicents=5_500_000)

    with Session(db) as session:
        status = compute_openai_monthly_spend(session)

    assert status.hard_cap_exceeded is True
    assert status.enforced is False


# ---------- Integration: cron pre-flight abort -----------------------------


def test_cron_aborts_when_openai_budget_hard_cap_exceeded(client_with_auth, db):
    """Hard-cap fires → CronRun lands at status ``budget_exceeded``, reason
    ``openai_budget_exceeded`` (distinct from Apify/Anthropic), all three
    provider budgets surfaced in summary_json. Apify scrape mocks must NOT
    execute — pre-flight short-circuits the entire pipeline."""
    _seed_ig_channel(db, handle="netflixde")
    # 105% of $50 cap.
    _seed_costlog(db, provider="openai", usd_millicents=5_250_000)

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
        assert run.summary_json["reason"] == "openai_budget_exceeded"
        assert run.summary_json["openai_budget"]["hard_cap_exceeded"] is True
        assert run.summary_json["openai_budget"]["spent_usd_cents"] == 5_250
        # Other two providers surfaced too, even though not the cause.
        assert "budget" in run.summary_json
        assert "anthropic_budget" in run.summary_json
        assert run.summary_json["budget"]["hard_cap_exceeded"] is False
        assert run.summary_json["anthropic_budget"]["hard_cap_exceeded"] is False

    mock_ig.assert_not_called()
    mock_tt.assert_not_called()


def test_cron_does_not_abort_when_kill_switch_off_even_if_over_cap(
    client_with_auth, db, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "openai_budget_enforced", False, raising=False)
    _seed_ig_channel(db, handle="netflixde")
    _seed_costlog(db, provider="openai", usd_millicents=5_250_000)

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
        if run.status == "budget_exceeded":
            assert (run.summary_json or {}).get("reason") != "openai_budget_exceeded"


# ---------- Integration: admin endpoint ------------------------------------


def test_admin_openai_budget_status_endpoint_returns_current_state(client_with_auth, db):
    _seed_costlog(db, provider="openai", usd_millicents=1_250_000)  # $12.50 = 25%
    _seed_costlog(db, provider="apify", usd_millicents=999_000_000)  # ignored
    _seed_costlog(db, provider="anthropic_opus", usd_millicents=999_000_000)  # ignored

    response = client_with_auth.get(
        "/api/admin/openai-budget-status",
        headers={"Authorization": "Bearer TESTTOKEN"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["spent_usd_cents"] == 1_250
    assert body["budget_usd_cents"] == 5_000
    assert body["pct_used"] == pytest.approx(0.25)
    assert body["soft_warn_exceeded"] is False
    assert body["hard_cap_exceeded"] is False
    assert body["enforced"] is True
