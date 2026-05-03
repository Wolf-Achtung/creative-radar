"""HTTP-layer tests for POST /api/admin/youtube/sync/{channel_id}
(Sprint 5.2.3 Mini-Run 3).

Pattern mirrors test_api_channels_backwards_compat.py: a shared :memory:
SQLite engine via StaticPool, get_session dependency-overridden, and
auth_enabled flipped off so the global Bearer middleware lets the test
client through. The connector itself is patched at the module level to
avoid real HTTP — its own contract is covered in test_youtube_connector.

Covers:
- happy path: new videos persist as Posts, response shape matches the
  Sprint-5.2.3 contract (channel_id / platform / synced_videos /
  skipped_videos / errors / quota_units_used)
- idempotency: a re-sync with the same payload skips already-stored
  posts via the post_url unique check
- 404 when the channel UUID is unknown
- 400 when the channel exists but has the wrong platform
- 401 when YOUTUBE_API_KEY is missing
- 429 when the connector raises YouTubeQuotaExceededError
- platform-misconfig surfaces as 404 from the connector (handle unknown
  upstream → mapped to HTTP 404)
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.api.admin as admin_mod
from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import Channel, Post


def _shared_test_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def db():
    engine = _shared_test_engine()
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "youtube_api_key", "TEST-KEY", raising=False)

    def _override_session():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _seed_channel(engine, *, platform: str = "youtube", handle: str = "@netflix") -> Channel:
    with Session(engine) as session:
        channel = Channel(
            name="Netflix",
            platform=platform,
            url=f"https://www.youtube.com/{handle}",
            handle=handle,
            active=True,
            mvp=True,
        )
        session.add(channel)
        session.commit()
        session.refresh(channel)
        return channel


def _normalized_video(video_id: str) -> dict[str, Any]:
    """Stand-in for normalize_youtube_video output. Keeps the test focused
    on the endpoint's persist+respond logic rather than re-asserting the
    normalizer (which has its own coverage in test_youtube_connector)."""
    return {
        "platform": "youtube",
        "post_url": f"https://www.youtube.com/watch?v={video_id}",
        "caption": f"Trailer {video_id}",
        "image_url": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        "published_at": None,
        "owner_username": "Netflix",
        "visible_likes": 100,
        "visible_comments": 10,
        "visible_views": 1000,
        "visible_shares": None,
        "visible_bookmarks": None,
        "duration_seconds": 90,
        "external_id": video_id,
        "raw": {"id": video_id},
    }


def _patch_connector(*, fetch_return=None, fetch_exc=None, configured: bool = True):
    """Build a dict of attributes that mimic the lazy-imported connector
    surface inside the route. Lets each test pick its happy/error path."""
    from app.services import youtube_connector as real_yt

    def fake_fetch(handle_or_id: str, results_limit: int | None = None):
        if fetch_exc is not None:
            raise fetch_exc
        return ({"id": "UC..."}, fetch_return or [])

    def fake_normalize(item):
        return item  # tests pass already-normalized stand-ins

    return patch.multiple(
        "app.services.youtube_connector",
        fetch_channel_videos=fake_fetch,
        normalize_youtube_video=fake_normalize,
        is_youtube_configured=lambda: configured,
        YouTubeAuthError=real_yt.YouTubeAuthError,
        YouTubeQuotaExceededError=real_yt.YouTubeQuotaExceededError,
        YouTubeNotFoundError=real_yt.YouTubeNotFoundError,
    )


# ---------- Happy path -----------------------------------------------


def test_youtube_sync_persists_new_posts_and_reports_counts(client: TestClient, db):
    channel = _seed_channel(db)
    raw_items = [_normalized_video("v1"), _normalized_video("v2"), _normalized_video("v3")]

    with _patch_connector(fetch_return=raw_items):
        response = client.post(f"/api/admin/youtube/sync/{channel.id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["channel_id"] == str(channel.id)
    assert body["platform"] == "youtube"
    assert body["synced_videos"] == 3
    assert body["skipped_videos"] == 0
    assert body["errors"] == []
    assert body["quota_units_used"] == 3

    with Session(db) as session:
        posts = list(session.exec(__import__("sqlmodel").select(Post)).all())
    assert {p.post_url for p in posts} == {
        "https://www.youtube.com/watch?v=v1",
        "https://www.youtube.com/watch?v=v2",
        "https://www.youtube.com/watch?v=v3",
    }
    assert all(p.platform == "youtube" for p in posts)


# ---------- Idempotency ----------------------------------------------


def test_youtube_sync_skips_already_stored_videos(client: TestClient, db):
    channel = _seed_channel(db)
    items = [_normalized_video("v1"), _normalized_video("v2")]

    with _patch_connector(fetch_return=items):
        first = client.post(f"/api/admin/youtube/sync/{channel.id}")
    assert first.status_code == 200
    assert first.json()["synced_videos"] == 2

    # Re-sync with the same items + one new one — only the new one persists.
    items_with_extra = items + [_normalized_video("v3")]
    with _patch_connector(fetch_return=items_with_extra):
        second = client.post(f"/api/admin/youtube/sync/{channel.id}")
    body = second.json()
    assert second.status_code == 200
    assert body["synced_videos"] == 1
    assert body["skipped_videos"] == 2


# ---------- Error mapping --------------------------------------------


def test_youtube_sync_returns_404_when_channel_uuid_unknown(client: TestClient):
    bogus = uuid4()
    response = client.post(f"/api/admin/youtube/sync/{bogus}")
    assert response.status_code == 404


def test_youtube_sync_rejects_non_youtube_channel_with_400(client: TestClient, db):
    channel = _seed_channel(db, platform="instagram", handle="netflix")
    response = client.post(f"/api/admin/youtube/sync/{channel.id}")
    assert response.status_code == 400
    assert "platform" in response.json()["detail"].lower()


def test_youtube_sync_returns_401_when_api_key_missing(
    client: TestClient, db, monkeypatch: pytest.MonkeyPatch
):
    channel = _seed_channel(db)
    monkeypatch.setattr(settings, "youtube_api_key", None, raising=False)
    response = client.post(f"/api/admin/youtube/sync/{channel.id}")
    assert response.status_code == 401


def test_youtube_sync_maps_quota_exceeded_to_429(client: TestClient, db):
    from app.services.youtube_connector import YouTubeQuotaExceededError

    channel = _seed_channel(db)
    with _patch_connector(fetch_exc=YouTubeQuotaExceededError("daily quota")):
        response = client.post(f"/api/admin/youtube/sync/{channel.id}")
    assert response.status_code == 429


def test_youtube_sync_maps_not_found_from_connector_to_404(client: TestClient, db):
    from app.services.youtube_connector import YouTubeNotFoundError

    channel = _seed_channel(db)
    with _patch_connector(fetch_exc=YouTubeNotFoundError("no channel")):
        response = client.post(f"/api/admin/youtube/sync/{channel.id}")
    assert response.status_code == 404


def test_youtube_sync_maps_auth_error_to_401(client: TestClient, db):
    from app.services.youtube_connector import YouTubeAuthError

    channel = _seed_channel(db)
    with _patch_connector(fetch_exc=YouTubeAuthError("keyInvalid")):
        response = client.post(f"/api/admin/youtube/sync/{channel.id}")
    assert response.status_code == 401


# ---------- Lazy-import resilience -----------------------------------


def test_youtube_sync_returns_503_when_connector_module_unimportable(
    client: TestClient, db, monkeypatch: pytest.MonkeyPatch
):
    """Prove the in-function import / try-except-ImportError safety net
    actually catches a load-time failure of youtube_connector. We patch
    builtins.__import__ to raise for that specific module name."""
    channel = _seed_channel(db)
    real_import = admin_mod.__builtins__["__import__"] if isinstance(
        admin_mod.__builtins__, dict
    ) else __builtins__.__import__

    def fail_import(name, *args, **kwargs):
        if name == "app.services.youtube_connector":
            raise ImportError("simulated connector load failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fail_import)
    response = client.post(f"/api/admin/youtube/sync/{channel.id}")
    assert response.status_code == 503
    assert "youtube" in response.json()["detail"].lower()
