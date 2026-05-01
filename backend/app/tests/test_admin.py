"""Tests für temporäre Admin-Endpoints (Phase 4 W3 Task 3.4 sampling helper).
Auth-Logik wird isoliert getestet — kein DB-Inhalt-Test, kein echter Sample-Lauf."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)


def test_sample_vision_outputs_returns_503_when_token_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_sample_token", None, raising=False)
    response = client.get(
        "/api/admin/sample-vision-outputs",
        headers={"Authorization": "Bearer anything"},
    )
    assert response.status_code == 503
    assert "ADMIN_SAMPLE_TOKEN" in response.json()["detail"]


def test_sample_vision_outputs_requires_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_sample_token", "secret-w3-sample", raising=False)
    response = client.get("/api/admin/sample-vision-outputs")
    assert response.status_code == 401


def test_sample_vision_outputs_rejects_wrong_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_sample_token", "secret-w3-sample", raising=False)
    response = client.get(
        "/api/admin/sample-vision-outputs",
        headers={"Authorization": "secret-w3-sample"},  # missing 'Bearer ' prefix
    )
    assert response.status_code == 401


def test_sample_vision_outputs_rejects_wrong_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_sample_token", "secret-w3-sample", raising=False)
    response = client.get(
        "/api/admin/sample-vision-outputs",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 403
