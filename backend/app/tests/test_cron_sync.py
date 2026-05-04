"""Tests for the Sprint-5.3.5 cron sync endpoint."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import Channel
from app.services import asset_screenshot_persistence as persistence_mod


def _engine():
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


@pytest.fixture
def db():
    engine = _engine()
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client_with_auth(db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "TESTTOKEN", raising=False)
    monkeypatch.setattr(settings, "apify_api_token", "TEST", raising=False)
    monkeypatch.setattr(settings, "apify_instagram_actor_id", "test/instagram", raising=False)
    monkeypatch.setattr(settings, "apify_tiktok_actor_id", "test/tiktok", raising=False)

    def _override():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed_ig_channel(db, *, handle: str = "netflixde") -> Channel:
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
        session.refresh(ch)
        return ch


def test_cron_sync_without_auth_returns_401(client_with_auth):
    response = client_with_auth.post("/api/admin/cron/sync-all")
    assert response.status_code == 401


def test_cron_sync_with_invalid_token_returns_403(client_with_auth):
    response = client_with_auth.post(
        "/api/admin/cron/sync-all",
        headers={"Authorization": "Bearer WRONG"},
    )
    assert response.status_code == 403


def test_cron_sync_with_valid_token_returns_summary(client_with_auth, db):
    _seed_ig_channel(db, handle="netflixde")

    fake_ig_item = {
        "url": "https://www.instagram.com/p/cron-test-1/",
        "ownerUsername": "netflixde",
        "displayUrl": "https://cdn.example/cron.jpg",
        "caption": "cron test",
        "timestamp": "2026-05-01T12:00:00Z",
    }

    def fake_capture(asset):
        from app.services.screenshot_capture import VisualEvidenceResult
        return VisualEvidenceResult(status="captured", evidence_url=f"evidence/{asset.id}.jpg")

    with patch("app.api.cron.run_public_channel_monitor",
               new_callable=AsyncMock, return_value=[fake_ig_item]), \
         patch("app.api.cron.run_tiktok_profile_monitor",
               new_callable=AsyncMock, return_value=[]), \
         patch.object(persistence_mod, "capture_asset_screenshot", side_effect=fake_capture):
        response = client_with_auth.post(
            "/api/admin/cron/sync-all",
            headers={"Authorization": "Bearer TESTTOKEN"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "run_index" in body and body["run_index"] in (0, 1, 2)
    assert "platforms" in body
    assert "instagram" in body["platforms"]
    assert body["platforms"]["instagram"]["created_assets"] == 1
    assert body["platforms"]["instagram"]["channels_checked"] == 1


def test_cron_sync_skips_platform_when_no_channels(client_with_auth):
    with patch("app.api.cron.run_public_channel_monitor",
               new_callable=AsyncMock, return_value=[]), \
         patch("app.api.cron.run_tiktok_profile_monitor",
               new_callable=AsyncMock, return_value=[]):
        response = client_with_auth.post(
            "/api/admin/cron/sync-all",
            headers={"Authorization": "Bearer TESTTOKEN"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["platforms"]["instagram"].get("skipped") is True
    assert body["platforms"]["instagram"]["reason"] == "no_channels"
    assert body["platforms"]["tiktok"].get("skipped") is True


def test_cron_sync_skips_platform_when_apify_not_configured(db, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "TESTTOKEN", raising=False)
    monkeypatch.setattr(settings, "apify_api_token", "", raising=False)
    monkeypatch.setattr(settings, "apify_instagram_actor_id", "", raising=False)
    monkeypatch.setattr(settings, "apify_tiktok_actor_id", "", raising=False)

    def _override():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override
    try:
        client = TestClient(app)
        response = client.post(
            "/api/admin/cron/sync-all",
            headers={"Authorization": "Bearer TESTTOKEN"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["platforms"]["instagram"]["skipped"] is True
    assert body["platforms"]["instagram"]["reason"] == "apify_not_configured"
    assert body["platforms"]["tiktok"]["skipped"] is True
    assert body["platforms"]["tiktok"]["reason"] == "tiktok_not_configured"
