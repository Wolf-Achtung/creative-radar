"""Tests for the temporary F0.2 schema-migration endpoints. Auth-Logik wird
isoliert getestet plus die Pass-through der scripts.migrate.run-Result-Maps."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)


# ---------- forward ----------


def test_run_schema_migration_returns_503_when_token_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_migration_token", None, raising=False)
    response = client.post(
        "/api/admin/run-schema-migration",
        headers={"Authorization": "Bearer anything"},
    )
    assert response.status_code == 503
    assert "ADMIN_MIGRATION_TOKEN" in response.json()["detail"]


def test_run_schema_migration_requires_auth_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_migration_token", "secret-w4-mig", raising=False)
    response = client.post("/api/admin/run-schema-migration")
    assert response.status_code == 401


def test_run_schema_migration_rejects_wrong_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_migration_token", "secret-w4-mig", raising=False)
    response = client.post(
        "/api/admin/run-schema-migration",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 403


def test_run_schema_migration_calls_forward_run_when_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_migration_token", "secret-w4-mig", raising=False)
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
        response = client.post(
            "/api/admin/run-schema-migration",
            headers={"Authorization": "Bearer secret-w4-mig"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["tables_moved"] == ["channel", "post", "asset"]
    assert body["summary"].startswith("3 tables moved")
    assert mocked.called


# ---------- rollback ----------


def test_run_schema_rollback_returns_503_when_token_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_migration_token", None, raising=False)
    response = client.post(
        "/api/admin/run-schema-rollback",
        headers={"Authorization": "Bearer anything"},
    )
    assert response.status_code == 503


def test_run_schema_rollback_calls_rollback_run_when_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_migration_token", "secret-w4-mig", raising=False)
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
        response = client.post(
            "/api/admin/run-schema-rollback",
            headers={"Authorization": "Bearer secret-w4-mig"},
        )

    assert response.status_code == 200
    body = response.json()
    assert "rolled back" in body["summary"]
    assert mocked.called


# ---------- alembic upgrade endpoint ----------


def test_run_alembic_upgrade_returns_503_when_token_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_migration_token", None, raising=False)
    response = client.post(
        "/api/admin/run-alembic-upgrade",
        headers={"Authorization": "Bearer anything"},
    )
    assert response.status_code == 503


def test_run_alembic_upgrade_calls_apply_run_when_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_migration_token", "secret-w4-mig", raising=False)
    fake_stats = {
        "before_revision": None,
        "after_revision": "857d9777a8d0",
        "baseline_stamped": True,
        "actions": ["stamped baseline cf842bbfaeb5", "upgraded to head"],
        "errors": {},
        "summary": "Alembic: none -> 857d9777a8d0. Actions: stamped baseline cf842bbfaeb5, upgraded to head",
    }
    with patch(
        "scripts.apply_alembic_upgrade.run", return_value=fake_stats
    ) as mocked:
        response = client.post(
            "/api/admin/run-alembic-upgrade",
            headers={"Authorization": "Bearer secret-w4-mig"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["after_revision"] == "857d9777a8d0"
    assert body["baseline_stamped"] is True
    assert mocked.called
