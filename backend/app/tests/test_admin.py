"""Tests für temporäre Admin-Endpoints (Phase 4 W3 follow-up: backfill).
Auth-Logik wird isoliert getestet plus die Summary-Phrase. Kein DB-Backfill-Test
hier — der existiert in test_backfill.py gegen die echte run()-Funktion."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.admin import _summary_line
from app.config import settings
from app.main import app

client = TestClient(app)


def test_run_backfill_returns_503_when_token_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_backfill_token", None, raising=False)
    response = client.post(
        "/api/admin/run-backfill",
        headers={"Authorization": "Bearer anything"},
    )
    assert response.status_code == 503
    assert "ADMIN_BACKFILL_TOKEN" in response.json()["detail"]


def test_run_backfill_requires_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_backfill_token", "secret-w3-backfill", raising=False)
    response = client.post("/api/admin/run-backfill")
    assert response.status_code == 401


def test_run_backfill_rejects_wrong_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_backfill_token", "secret-w3-backfill", raising=False)
    response = client.post(
        "/api/admin/run-backfill",
        headers={"Authorization": "secret-w3-backfill"},  # missing 'Bearer '
    )
    assert response.status_code == 401


def test_run_backfill_rejects_wrong_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_backfill_token", "secret-w3-backfill", raising=False)
    response = client.post(
        "/api/admin/run-backfill",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 403


def test_run_backfill_calls_backfill_run_when_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_backfill_token", "secret-w3-backfill", raising=False)

    fake_stats = {
        "total": 20,
        "migrated": 12,
        "skipped": 0,
        "failed": 8,
        "failed_ids": ["asset-1", "asset-2"],
        "failed_reasons": {"asset-1": "fetch_failed", "asset-2": "fetch_failed"},
    }
    with patch("app.api.admin.backfill_evidence.run", return_value=fake_stats) as mocked:
        response = client.post(
            "/api/admin/run-backfill",
            headers={"Authorization": "Bearer secret-w3-backfill"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["migrated"] == 12
    assert body["failed"] == 8
    assert body["failed_ids"] == ["asset-1", "asset-2"]
    assert "12 of 20" in body["summary"]
    assert mocked.called


def test_summary_line_formats_full_run() -> None:
    line = _summary_line({"total": 20, "migrated": 12, "skipped": 0, "failed": 8,
                          "failed_ids": [], "failed_reasons": {}})
    assert line.startswith("12 of 20 assets migrated successfully.")
    assert "8 failed" in line
    assert "TikTok/Instagram CDN" in line


def test_summary_line_handles_empty_run() -> None:
    line = _summary_line({"total": 0, "migrated": 0, "skipped": 0, "failed": 0,
                          "failed_ids": [], "failed_reasons": {}})
    assert "No backfill candidates" in line


def test_summary_line_handles_all_skipped() -> None:
    line = _summary_line({"total": 5, "migrated": 0, "skipped": 5, "failed": 0,
                          "failed_ids": [], "failed_reasons": {}})
    assert "0 of 5" in line
    assert "5 skipped" in line
