"""Tests for the cost-logging service (Phase 4 W4 Task 4.4 / F0.6).

Coverage:
- record_apify_run persists a row with the right USD/EUR conversion
  and metadata
- record_openai_call accepts both dataclass-style usage (SDK) and dict
- record_*-failures swallowed (DB error doesn't break the caller)
- _to_eur_cents uses the snapshot rate at logging time
- /api/admin/cost-summary aggregates correctly per group_by
- Cost-summary endpoint requires Bearer auth (via global middleware)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.main import app
from app.models.entities import CostLog
from app.services import cost_log as cost_log_module


def _shared_test_engine():
    """SQLite :memory: + StaticPool so all sessions in this process see the
    same in-memory DB. Without StaticPool, every new Session call opens a
    brand-new isolated DB and reads return 'no such table'."""
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


# ---------- _to_eur_cents ----------


def test_to_eur_cents_uses_settings_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "usd_to_eur_rate", 0.92, raising=False)
    assert cost_log_module._to_eur_cents(100) == 92  # 100 cents @ 0.92
    monkeypatch.setattr(settings, "usd_to_eur_rate", 1.0, raising=False)
    assert cost_log_module._to_eur_cents(100) == 100


def test_to_eur_cents_handles_zero_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defensive: if the rate is somehow 0/None, _to_eur_cents falls back
    to 0.92 (avoids dividing-by-zero downstream and matches W4 default)."""
    monkeypatch.setattr(settings, "usd_to_eur_rate", 0, raising=False)
    assert cost_log_module._to_eur_cents(100) == 92


# ---------- record_apify_run ----------


def test_record_apify_run_persists_row_with_compute_units(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "apify_compute_unit_usd", 0.4, raising=False)
    monkeypatch.setattr(settings, "usd_to_eur_rate", 0.92, raising=False)

    # Patch the module-level engine so _persist writes to our test session's DB
    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_apify_run(
            run_data={
                "id": "run-123",
                "actId": "apify~instagram-scraper",
                "usage": {"COMPUTE_UNITS": 0.5},
            },
            items_count=12,
            operation="actor:apify~instagram-scraper",
        )

    rows = session.exec(select(CostLog)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.provider == "apify"
    assert row.operation == "actor:apify~instagram-scraper"
    # 0.5 CU * 0.4 USD = 0.2 USD = 20 cents
    assert row.cost_usd_cents == 20
    # 20 cents * 0.92 = 18.4 -> rounded to 18
    assert row.cost_eur_cents == 18
    assert row.cost_meta["compute_units"] == 0.5
    assert row.cost_meta["items_count"] == 12
    assert row.cost_meta["run_id"] == "run-123"


def test_record_apify_run_handles_missing_usage(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Some Apify run responses omit the usage dict (failed runs, very old
    actors). Cost log still writes a 0-cost row for the audit trail."""
    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_apify_run(
            run_data={"id": "run-no-usage"},
            items_count=0,
            operation="actor:foo",
        )

    rows = session.exec(select(CostLog)).all()
    assert len(rows) == 1
    assert rows[0].cost_usd_cents == 0
    assert rows[0].cost_eur_cents == 0
    assert rows[0].cost_meta["compute_units"] == 0.0


def test_record_apify_run_swallows_db_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed cost-log write must NOT propagate — the actor run that
    triggered the log already returned data we shouldn't lose."""
    class FailingSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def add(self, *a): raise RuntimeError("DB down")
        def commit(self): pass

    with patch.object(cost_log_module, "Session", lambda _engine: FailingSession()):
        # Must not raise
        cost_log_module.record_apify_run(
            run_data={"usage": {"COMPUTE_UNITS": 1.0}},
            items_count=5,
            operation="actor:foo",
        )


# ---------- record_openai_call ----------


def test_record_openai_call_with_dict_usage(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openai_input_per_1k_usd", 0.000150, raising=False)
    monkeypatch.setattr(settings, "openai_output_per_1k_usd", 0.000600, raising=False)
    monkeypatch.setattr(settings, "usd_to_eur_rate", 0.92, raising=False)

    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_openai_call(
            usage={"prompt_tokens": 1000, "completion_tokens": 500},
            operation="vision_call",
            meta={"asset_id": "abc-123"},
        )

    rows = session.exec(select(CostLog)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.provider == "openai"
    assert row.operation == "vision_call"
    # 1k input @ 0.00015 + 0.5k output @ 0.0006 = 0.00015 + 0.0003 = 0.00045 USD
    # = 0.045 cents -> rounded to 0
    assert row.cost_usd_cents == 0
    assert row.cost_meta["input_tokens"] == 1000
    assert row.cost_meta["output_tokens"] == 500
    assert row.cost_meta["asset_id"] == "abc-123"


def test_record_openai_call_with_object_usage(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenAI SDK returns a CompletionUsage object with attribute access."""
    monkeypatch.setattr(settings, "openai_input_per_1k_usd", 0.000150, raising=False)
    monkeypatch.setattr(settings, "openai_output_per_1k_usd", 0.000600, raising=False)

    class FakeUsage:
        prompt_tokens = 100000
        completion_tokens = 50000

    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_openai_call(
            FakeUsage(), operation="chat_completion"
        )

    rows = session.exec(select(CostLog)).all()
    assert len(rows) == 1
    # 100k @ 0.00015 + 50k @ 0.0006 = 0.015 + 0.03 = 0.045 USD = 4.5 cents.
    # Python's int(round(4.5)) yields 4 (banker's rounding) — we accept that
    # since cost_log is best-effort accounting, not financial reporting.
    assert rows[0].cost_usd_cents in (4, 5)
    assert rows[0].cost_meta["input_tokens"] == 100000
    assert rows[0].cost_meta["output_tokens"] == 50000


def test_record_openai_call_handles_none_usage(
    session: Session,
) -> None:
    """If the SDK returned no usage info, log a 0-cost row anyway."""
    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_openai_call(None, operation="vision_call")

    rows = session.exec(select(CostLog)).all()
    assert len(rows) == 1
    assert rows[0].cost_usd_cents == 0
    assert rows[0].cost_meta["input_tokens"] == 0


# ---------- /api/admin/cost-summary endpoint ----------


@pytest.fixture
def auth_off_client(monkeypatch: pytest.MonkeyPatch):
    """Endpoint runs through the global Bearer-auth middleware. We test the
    cost-summary logic itself with auth off (it's tested separately in
    test_auth_middleware.py).

    SQLite ':memory:' is per-connection-isolated, so TestClient's request-
    scoped Session does not see tables created by an outer setup engine.
    Override get_session with a Session bound to a single shared in-memory
    engine — the Session yielded to the endpoint and the Session we seed
    rows from share the same in-memory DB.
    """
    from app.database import get_session  # noqa: PLC0415

    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)

    test_engine = _shared_test_engine()
    SQLModel.metadata.create_all(test_engine)

    def _override_session():
        with Session(test_engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app), test_engine
    finally:
        app.dependency_overrides.pop(get_session, None)


def _seed_cost_log(session: Session, *, provider: str, operation: str,
                   eur_cents: int, usd_cents: int, when: datetime) -> None:
    session.add(
        CostLog(
            id=uuid4(),
            timestamp=when,
            provider=provider,
            operation=operation,
            cost_usd_cents=usd_cents,
            cost_eur_cents=eur_cents,
            cost_meta={},
        )
    )
    session.commit()


def test_cost_summary_groups_by_provider_default(auth_off_client) -> None:
    """Smoke test: empty DB returns an empty buckets list with totals=0."""
    client, _engine = auth_off_client
    response = client.get("/api/admin/cost-summary")
    assert response.status_code == 200
    body = response.json()
    assert body["group_by"] == "provider"
    assert body["total_count"] == 0
    assert body["total_cost_eur_cents"] == 0
    assert body["buckets"] == []


def test_cost_summary_aggregates_by_provider(auth_off_client) -> None:
    """Seed two apify rows + one openai row in the same in-memory engine the
    endpoint will read from, then check the buckets sum correctly."""
    client, test_engine = auth_off_client
    now = datetime.now(timezone.utc)
    with Session(test_engine) as s:
        _seed_cost_log(s, provider="apify", operation="actor:foo",
                       eur_cents=20, usd_cents=22, when=now)
        _seed_cost_log(s, provider="apify", operation="actor:bar",
                       eur_cents=10, usd_cents=11, when=now)
        _seed_cost_log(s, provider="openai", operation="vision_call",
                       eur_cents=4, usd_cents=5, when=now)

    response = client.get("/api/admin/cost-summary")
    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 3
    assert body["total_cost_eur_cents"] == 34  # 20+10+4
    assert body["total_cost_usd_cents"] == 38  # 22+11+5

    buckets_by_key = {b["key"]: b for b in body["buckets"]}
    assert buckets_by_key["apify"]["count"] == 2
    assert buckets_by_key["apify"]["cost_eur_cents"] == 30
    assert buckets_by_key["openai"]["count"] == 1


def test_cost_summary_rejects_invalid_dates(auth_off_client) -> None:
    client, _engine = auth_off_client
    response = client.get("/api/admin/cost-summary?from_date=not-a-date")
    assert response.status_code == 400


def test_cost_summary_requires_auth_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Endpoint must be gated by the global Bearer middleware — no special
    cost-summary token. Without a header (and auth on) we get 401."""
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "valid-token", raising=False)

    client = TestClient(app)
    response = client.get("/api/admin/cost-summary")
    assert response.status_code == 401


def test_cost_summary_passes_with_correct_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auth-on smoke. Reuses the dependency-override pattern so we don't
    drag a real :memory: engine into this test — we only care that auth
    didn't reject the request, not the body content."""
    from app.database import get_session  # noqa: PLC0415

    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "valid-token", raising=False)

    test_engine = _shared_test_engine()
    SQLModel.metadata.create_all(test_engine)

    def _override_session():
        with Session(test_engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    try:
        client = TestClient(app)
        response = client.get(
            "/api/admin/cost-summary",
            headers={"Authorization": "Bearer valid-token"},
        )
        assert response.status_code == 200
    finally:
        app.dependency_overrides.pop(get_session, None)
