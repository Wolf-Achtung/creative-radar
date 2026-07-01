"""Tests for the in-memory rate limiter (Sicherheits-Audit 2026-07-01)."""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.services.rate_limit import rate_limit


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/limited", dependencies=[Depends(rate_limit("test-bucket", max_calls=2, window_seconds=60))])
    def limited():
        return {"ok": True}

    return app


def test_allows_up_to_max_calls():
    client = TestClient(_make_app())
    assert client.get("/limited").status_code == 200
    assert client.get("/limited").status_code == 200


def test_blocks_after_max_calls():
    client = TestClient(_make_app())
    client.get("/limited")
    client.get("/limited")
    response = client.get("/limited")
    assert response.status_code == 429
    assert "Retry-After" in response.headers


def test_disabled_via_settings(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False, raising=False)
    client = TestClient(_make_app())
    for _ in range(5):
        assert client.get("/limited").status_code == 200


def test_different_ips_have_independent_buckets():
    client = TestClient(_make_app())
    headers_a = {"X-Real-Ip": "1.1.1.1"}
    headers_b = {"X-Real-Ip": "2.2.2.2"}
    assert client.get("/limited", headers=headers_a).status_code == 200
    assert client.get("/limited", headers=headers_a).status_code == 200
    assert client.get("/limited", headers=headers_a).status_code == 429
    # Different client IP, fresh bucket.
    assert client.get("/limited", headers=headers_b).status_code == 200
