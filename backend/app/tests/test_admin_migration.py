"""Tests for the F0.2 schema-migration endpoints.

Auth is the global Bearer middleware (W4 Task 4.3); the endpoints
themselves no longer carry a separate ADMIN_MIGRATION_TOKEN check
(removed in W4-Hotfix-4 — see PHASE_4_DONE.md Lesson 6). Tests here
verify the happy path with auth disabled — auth-layer behaviour is
covered by test_auth_middleware.py.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture
def auth_off_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Disable auth so the test focuses on the endpoint's own behaviour
    (delegating to the migration script). Bearer-auth coverage lives in
    test_auth_middleware.py."""
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    return TestClient(app)


# ---------- forward ----------


def test_run_schema_migration_calls_forward_run(
    auth_off_client: TestClient,
) -> None:
    fake_stats = {
        "schema_created": True,
        "target_schema": "creative_radar",
        "tables_moved": ["channel", "post", "asset"],
        "tables_skipped": ["alembic_version (absent from public)"],
        "errors": {},
        "summary": "3 tables moved to creative_radar, 1 skipped.",
    }
    with patch(
        "scripts.migrate_to_creative_radar_schema.run", return_value=fake_stats
    ) as mocked:
        response = auth_off_client.post("/api/admin/run-schema-migration")

    assert response.status_code == 200
    body = response.json()
    assert body["tables_moved"] == ["channel", "post", "asset"]
    assert body["summary"].startswith("3 tables moved")
    assert mocked.called


# ---------- rollback ----------


def test_run_schema_rollback_calls_rollback_run(
    auth_off_client: TestClient,
) -> None:
    fake_stats = {
        "source_schema": "creative_radar",
        "target_schema": "public",
        "tables_moved": ["channel", "post"],
        "tables_skipped": [],
        "errors": {},
        "summary": "2 tables rolled back to public, 0 skipped.",
    }
    with patch(
        "scripts.rollback_creative_radar_schema.run", return_value=fake_stats
    ) as mocked:
        response = auth_off_client.post("/api/admin/run-schema-rollback")

    assert response.status_code == 200
    body = response.json()
    assert "rolled back" in body["summary"]
    assert mocked.called


# ---------- alembic upgrade ----------


def test_run_alembic_upgrade_calls_apply_run(
    auth_off_client: TestClient,
) -> None:
    fake_stats = {
        "before_revision": None,
        "after_revision": "857d9777a8d0",
        "baseline_stamped": True,
        "actions": ["stamped baseline cf842bbfaeb5", "upgraded to head"],
        "errors": {},
        "summary": (
            "Alembic: none -> 857d9777a8d0. "
            "Actions: stamped baseline cf842bbfaeb5, upgraded to head"
        ),
    }
    with patch(
        "scripts.apply_alembic_upgrade.run", return_value=fake_stats
    ) as mocked:
        response = auth_off_client.post("/api/admin/run-alembic-upgrade")

    assert response.status_code == 200
    body = response.json()
    assert body["after_revision"] == "857d9777a8d0"
    assert body["baseline_stamped"] is True
    assert mocked.called


# ---------- regression guard: no double-auth ----------


def test_endpoints_have_no_local_token_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W4-Hotfix-4 regression guard: with auth disabled the endpoints must
    pass without any Authorization header. If anyone re-introduces a
    separate ADMIN_MIGRATION_TOKEN check inside the route function this test
    fails — the request would 401/403/503 even with global auth off.
    """
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    client = TestClient(app)

    fake_stats = {
        "schema_created": False,
        "target_schema": "creative_radar",
        "tables_moved": [],
        "tables_skipped": [],
        "errors": {},
        "summary": "noop",
    }
    with patch(
        "scripts.migrate_to_creative_radar_schema.run", return_value=fake_stats
    ):
        response = client.post("/api/admin/run-schema-migration")

    assert response.status_code == 200, (
        f"endpoint added a non-global auth check (status={response.status_code}, "
        f"body={response.text[:200]})"
    )
