"""Tests for the cron sync endpoint (Sprint 5.3.5 + background-task hotfix)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import Channel, CronRun
from app.services import asset_screenshot_persistence as persistence_mod
from app.services.screenshot_capture import VisualEvidenceResult


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
    # Background tasks need engine pointing at the same in-memory DB.
    monkeypatch.setattr("app.api.cron.engine", db)
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


def test_cron_sync_returns_202_with_run_id_and_persists_running_row(client_with_auth, db):
    _seed_ig_channel(db, handle="netflixde")

    with patch("app.api.cron.run_public_channel_monitor",
               new_callable=AsyncMock, return_value=[]), \
         patch("app.api.cron.run_tiktok_profile_monitor",
               new_callable=AsyncMock, return_value=[]):
        response = client_with_auth.post(
            "/api/admin/cron/sync-all",
            headers={"Authorization": "Bearer TESTTOKEN"},
        )

    assert response.status_code == 202, response.text
    body = response.json()
    assert "run_id" in body
    assert body["status"] == "running"
    assert body["run_index"] in (0, 1, 2)
    assert "started_at" in body

    with Session(db) as session:
        runs = list(session.exec(select(CronRun)).all())
        assert len(runs) == 1
        assert str(runs[0].id) == body["run_id"]


def test_cron_sync_background_completes_run(client_with_auth, db):
    _seed_ig_channel(db, handle="netflixde")

    fake_ig_item = {
        "url": "https://www.instagram.com/p/cron-bg-1/",
        "ownerUsername": "netflixde",
        "displayUrl": "https://cdn.example/cron.jpg",
        "caption": "cron test",
        "timestamp": "2026-05-01T12:00:00Z",
    }

    def fake_capture(asset):
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

    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]

    # TestClient runs background tasks synchronously after response. Read the
    # final state from the DB.
    with Session(db) as session:
        run = session.get(CronRun, uuid4().__class__(run_id))
        assert run.status == "completed"
        assert run.completed_at is not None
        assert run.summary_json is not None
        assert run.summary_json["platforms"]["instagram"]["created_assets"] == 1


def test_cron_sync_blocks_parallel_trigger_with_409(client_with_auth, db):
    with Session(db) as session:
        existing = CronRun(run_index=0, status="running")
        session.add(existing)
        session.commit()
        session.refresh(existing)
        existing_id = str(existing.id)

    response = client_with_auth.post(
        "/api/admin/cron/sync-all",
        headers={"Authorization": "Bearer TESTTOKEN"},
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"] == "cron_run_already_running"
    assert body["run_id"] == existing_id


def test_cron_sync_reaps_stale_run_and_starts_new(client_with_auth, db):
    stale_started = datetime.now(timezone.utc) - timedelta(hours=2)
    with Session(db) as session:
        stale = CronRun(run_index=0, status="running", started_at=stale_started)
        session.add(stale)
        session.commit()
        session.refresh(stale)
        stale_id = str(stale.id)

    with patch("app.api.cron.run_public_channel_monitor",
               new_callable=AsyncMock, return_value=[]), \
         patch("app.api.cron.run_tiktok_profile_monitor",
               new_callable=AsyncMock, return_value=[]):
        response = client_with_auth.post(
            "/api/admin/cron/sync-all",
            headers={"Authorization": "Bearer TESTTOKEN"},
        )

    assert response.status_code == 202, response.text
    new_run_id = response.json()["run_id"]
    assert new_run_id != stale_id

    with Session(db) as session:
        stale_after = session.get(CronRun, uuid4().__class__(stale_id))
        assert stale_after.status == "failed"
        assert stale_after.error_message == "stale_run_timeout"
        assert stale_after.completed_at is not None


def test_list_cron_runs_returns_newest_first_and_respects_limit(client_with_auth, db):
    base = datetime.now(timezone.utc)
    with Session(db) as session:
        for i in range(5):
            is_newest_running = (i == 0)
            session.add(CronRun(
                run_index=i % 3,
                status="running" if is_newest_running else "completed",
                started_at=base - timedelta(hours=i),
                completed_at=None if is_newest_running else (base - timedelta(hours=i) + timedelta(minutes=10)),
                summary_json=None if is_newest_running else {"placeholder": i},
            ))
        session.commit()

    response = client_with_auth.get(
        "/api/admin/cron/runs?limit=3",
        headers={"Authorization": "Bearer TESTTOKEN"},
    )

    assert response.status_code == 200, response.text
    runs = response.json()
    assert len(runs) == 3
    timestamps = [datetime.fromisoformat(r["started_at"]) for r in runs]
    assert timestamps == sorted(timestamps, reverse=True)
    assert runs[0]["status"] == "running"
    assert runs[0]["duration_seconds"] is None
    assert runs[1]["duration_seconds"] is not None
    assert runs[1]["duration_seconds"] == 600.0
