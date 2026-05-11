"""Sprint F0.6 Hard-Cap-Vollausbau — Apify monthly budget tests.

Covers four guarantees:

1. Unit: ``compute_apify_monthly_spend`` aggregates only Apify rows in
   the current calendar month (UTC), ignoring non-Apify costs and
   prior-month rows.
2. Unit: soft-warn threshold (>=80%) fires while hard-cap (>=100%) does
   not — the cushion between them is the Wolf-spec safety margin.
3. Integration: when the hard cap is exceeded and the kill-switch is on,
   ``_run_cron_sync_background`` aborts before any Apify call. The
   CronRun row is committed with status ``budget_exceeded`` and a
   ``budget`` block in summary_json.
4. Integration: ``GET /api/admin/budget-status`` returns the same payload
   shape that the pre-flight consults.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import Channel, CostLog, CronRun
from app.services.budget_check import (
    BudgetStatus,
    _month_window_utc,
    aggregate_apify_costs_since,
    compute_apify_monthly_spend,
)


def _engine_for_path(path: str):
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_budget_", suffix=".db")
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
    monkeypatch.setattr(settings, "apify_api_token", "TEST", raising=False)
    monkeypatch.setattr(settings, "apify_instagram_actor_id", "test/ig", raising=False)
    monkeypatch.setattr(settings, "apify_tiktok_actor_id", "test/tt", raising=False)
    # Lock the budget to deterministic Wolf-spec defaults regardless of any
    # env-overridden values picked up in the test runner.
    monkeypatch.setattr(settings, "apify_monthly_budget_usd", 200.0, raising=False)
    monkeypatch.setattr(settings, "apify_soft_warn_pct", 0.80, raising=False)
    monkeypatch.setattr(settings, "apify_hard_cap_pct", 1.00, raising=False)
    monkeypatch.setattr(settings, "apify_budget_enforced", True, raising=False)

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
    usd_cents: int,
    timestamp: datetime | None = None,
    operation: str | None = None,
) -> None:
    with Session(db) as session:
        session.add(
            CostLog(
                provider=provider,
                operation=operation or ("instagram_actor" if provider == "apify" else "test"),
                cost_usd_cents=usd_cents,
                cost_eur_cents=int(round(usd_cents * 0.92)),
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


def test_compute_apify_monthly_spend_aggregates_only_apify_in_current_month(
    db, monkeypatch: pytest.MonkeyPatch,
):
    """Cross-provider + cross-month rows must NOT bleed into the Apify
    monthly aggregate. The query filter is the only thing standing
    between a clean cap and a noisy false-positive."""
    monkeypatch.setattr(settings, "apify_monthly_budget_usd", 200.0, raising=False)
    monkeypatch.setattr(settings, "apify_soft_warn_pct", 0.80, raising=False)
    monkeypatch.setattr(settings, "apify_hard_cap_pct", 1.00, raising=False)
    monkeypatch.setattr(settings, "apify_budget_enforced", True, raising=False)

    now = datetime.now(timezone.utc)
    window_start, _ = _month_window_utc(now)
    last_month = window_start - timedelta(days=2)

    # In-window apify rows — should sum.
    _seed_costlog(db, provider="apify", usd_cents=3_500, timestamp=now - timedelta(days=1))
    _seed_costlog(db, provider="apify", usd_cents=4_500, timestamp=now - timedelta(hours=2))
    # Non-apify in-window — must be ignored.
    _seed_costlog(db, provider="openai", usd_cents=10_000, timestamp=now - timedelta(days=1))
    # Apify but PRIOR month — must be ignored.
    _seed_costlog(db, provider="apify", usd_cents=99_999, timestamp=last_month)

    with Session(db) as session:
        status = compute_apify_monthly_spend(session, now=now)

    assert isinstance(status, BudgetStatus)
    assert status.spent_usd_cents == 8_000  # 3500 + 4500 only
    assert status.budget_usd_cents == 20_000
    assert status.pct_used == pytest.approx(0.40)
    assert status.soft_warn_exceeded is False
    assert status.hard_cap_exceeded is False
    assert status.enforced is True
    assert status.window_start == window_start


def test_compute_apify_monthly_spend_soft_warn_without_hard_cap(
    db, monkeypatch: pytest.MonkeyPatch,
):
    """80%-Marke greift (soft warn), aber 100% nicht — die ~$80 Cushion
    zwischen den Schwellen ist die Wolf-Spec-Sicherheitsmarge: nicht
    abbrechen, nur sichtbar markieren."""
    monkeypatch.setattr(settings, "apify_monthly_budget_usd", 200.0, raising=False)
    monkeypatch.setattr(settings, "apify_soft_warn_pct", 0.80, raising=False)
    monkeypatch.setattr(settings, "apify_hard_cap_pct", 1.00, raising=False)
    monkeypatch.setattr(settings, "apify_budget_enforced", True, raising=False)

    # 85% of 20_000 cents = 17_000 cents = $170. Above soft (160), below hard (200).
    _seed_costlog(db, provider="apify", usd_cents=17_000)

    with Session(db) as session:
        status = compute_apify_monthly_spend(session)

    assert status.soft_warn_exceeded is True
    assert status.hard_cap_exceeded is False
    assert status.pct_used == pytest.approx(0.85)


# ---------- Integration: cron pre-flight abort -----------------------------


def test_cron_aborts_when_apify_budget_hard_cap_exceeded(client_with_auth, db):
    """Hard-Cap fires → CronRun lands at status ``budget_exceeded``,
    summary_json carries the BudgetStatus, and the Apify run helpers are
    never invoked. Audit trail intact, zero downstream cost."""
    _seed_ig_channel(db, handle="netflixde")
    # 105% of $200 budget = 21000 cents
    _seed_costlog(db, provider="apify", usd_cents=21_000)

    # If the pre-flight is broken, these mocks will be called — we assert
    # the opposite below.
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
        assert run.summary_json["reason"] == "apify_budget_exceeded"
        assert run.summary_json["budget"]["hard_cap_exceeded"] is True
        assert run.summary_json["budget"]["spent_usd_cents"] == 21_000

    # Pre-flight must short-circuit BEFORE any Apify HTTP call.
    mock_ig.assert_not_called()
    mock_tt.assert_not_called()


# ---------- Integration: admin endpoint ------------------------------------


def test_admin_budget_status_endpoint_returns_current_state(client_with_auth, db):
    """Admin endpoint surface check: Bearer-auth, deterministic shape,
    same BudgetStatus serialisation as the cron summary_json carries."""
    _seed_costlog(db, provider="apify", usd_cents=5_000)  # 25%
    _seed_costlog(db, provider="openai", usd_cents=99_999)  # ignored

    response = client_with_auth.get(
        "/api/admin/budget-status",
        headers={"Authorization": "Bearer TESTTOKEN"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["spent_usd_cents"] == 5_000
    assert body["budget_usd_cents"] == 20_000
    assert body["pct_used"] == pytest.approx(0.25)
    assert body["soft_warn_exceeded"] is False
    assert body["hard_cap_exceeded"] is False
    assert body["enforced"] is True
    # Window fields are ISO-formatted UTC timestamps — sanity-check they
    # parse and span exactly one calendar month.
    start = datetime.fromisoformat(body["window_start"])
    end = datetime.fromisoformat(body["window_end"])
    assert start.day == 1 and start.hour == 0 and start.minute == 0
    assert end.day == 1
    assert end > start


# ---------- Tech-Debt A5: apify-cost in cron summary_json ------------------


def test_aggregate_apify_costs_since_buckets_per_operation_and_ignores_prior_rows(
    db, monkeypatch: pytest.MonkeyPatch,
):
    """Helper-Garantie: nur Apify-Rows ab dem ``since``-Cutoff fließen in
    den Cron-Summary-Block. Non-Apify-Provider (openai, anthropic_*) und
    Apify-Rows VOR ``since`` (z. B. vom vorigen Cron-Lauf am Samstag)
    bleiben strikt draußen. Bucket-Counts werden pro ``operation`` (=
    Actor-ID) hochgezählt — eine Eigenschaft, die das Dashboard braucht,
    um IG- vs TT-Anteil zu sehen."""
    since = datetime.now(timezone.utc) - timedelta(minutes=5)

    _seed_costlog(
        db, provider="apify", usd_cents=25,
        operation="actor:apify~instagram-scraper",
        timestamp=since + timedelta(seconds=30),
    )
    _seed_costlog(
        db, provider="apify", usd_cents=17,
        operation="actor:clockworks~tiktok-scraper",
        timestamp=since + timedelta(minutes=2),
    )
    # Apify row VOR dem Cutoff — vom vorigen Run, ignorieren.
    _seed_costlog(
        db, provider="apify", usd_cents=9_999,
        operation="actor:apify~instagram-scraper",
        timestamp=since - timedelta(minutes=10),
    )
    # Non-Apify im Window — ignorieren.
    _seed_costlog(
        db, provider="openai", usd_cents=5000,
        timestamp=since + timedelta(seconds=10),
    )

    with Session(db) as session:
        block = aggregate_apify_costs_since(session, since)

    assert block["calls_total"] == 2
    assert block["estimated_cost_usd"] == pytest.approx(0.42)  # (25 + 17) / 100
    assert block["calls_by_operation"] == {
        "actor:apify~instagram-scraper": 1,
        "actor:clockworks~tiktok-scraper": 1,
    }


def test_aggregate_apify_costs_since_emits_zero_block_when_no_rows(db):
    """Backward-Compat-Garantie: bei null Apify-Calls (Apify nicht
    konfiguriert, alle Channels geskippt, oder ``_run_actor`` failed
    before logging) emittiert der Aggregator trotzdem den vollständigen
    Block mit Nullen. Dashboards können nicht zwischen „Feld fehlt"
    und „kein Apify in diesem Run" unterscheiden — Nullen sind das
    explizitere Signal."""
    since = datetime.now(timezone.utc) - timedelta(minutes=1)

    with Session(db) as session:
        block = aggregate_apify_costs_since(session, since)

    assert block == {
        "estimated_cost_usd": 0.0,
        "calls_total": 0,
        "calls_by_operation": {},
    }


# ---------- F0.6 Budget-Default-Anchor (Regression-Guard) ------------------


def test_default_budget_is_50_usd():
    """Regression-Guard nach PR #116: Apify-Pay-per-Event-Cost-Tracking
    machte den realen Verbrauch erstmalig sichtbar — Wolfs Console zeigte
    ~$13.41/Monat. Pre-#116-Default $200 basierte auf einer 15×-falschen
    Verbrauchs-Schätzung. Neuer Default $50 hält ~4× Cushion auf $13 für
    saisonale Spikes, Channel-Ausbau und Apify-Pricing-Erhöhungen.

    Dieser Test fängt es, falls jemand den Default in einem späteren
    Refactor wieder auf einen pre-PR-#116-Wert dreht — Railway-ENV-
    Overrides bleiben unberührt, die testen wir nicht.
    """
    from app.config import Settings

    # Fresh Settings-Instanz ohne ENV-File-Read, damit der Test gegen den
    # Code-Default prüft (nicht gegen einen lokal gesetzten ENV-Wert).
    fresh = Settings(_env_file=None)
    assert fresh.apify_monthly_budget_usd == 50.0
