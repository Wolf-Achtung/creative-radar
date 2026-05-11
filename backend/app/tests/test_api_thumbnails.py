"""HTTP-layer tests for the Sprint 5c thumbnail proxy.

Pattern matches test_api_channels_backwards_compat.py: an in-memory
SQLite engine via StaticPool, ``get_session`` dependency-overridden,
``auth_enabled`` flipped off (the auth middleware is covered by its
own test). On top of that, ``httpx.AsyncClient`` is replaced inside
``app.api.thumbnails`` with a recording fake so we can assert the
outgoing Referer / User-Agent and exercise the cache + stale-while-
error paths without touching the network.

The cache lives at ``/tmp/thumbnail_cache/`` in production. Tests
redirect ``CACHE_DIR`` to a per-test ``tmp_path`` directory so the
filesystem state is isolated and noisy machines don't poison
each-other's runs.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Optional
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import thumbnails as thumbnails_module
from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import Asset, Channel, Market, Post


# ---------- Test fixtures --------------------------------------------------


def _shared_test_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class _RecordingResponse:
    def __init__(self, *, status_code: int = 200, content: bytes = b"image-bytes"):
        self.status_code = status_code
        self.content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.invalid")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=request, response=response,
            )


class _RecordingClient:
    """Stand-in for ``httpx.AsyncClient``. Records the most recent
    GET-call's URL + headers; the test inspects these to verify
    plattform-spezifische Referer/User-Agent. ``response`` and
    ``raise_exc`` are knobs the test can flip per case."""

    def __init__(
        self,
        *,
        response: Optional[_RecordingResponse] = None,
        raise_exc: Optional[BaseException] = None,
    ):
        self.response = response or _RecordingResponse()
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url: str, *, headers=None, follow_redirects=True):
        self.calls.append({"url": url, "headers": dict(headers or {})})
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


@pytest.fixture
def isolated_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cache_dir = tmp_path / "thumbnail_cache"
    monkeypatch.setattr(thumbnails_module, "CACHE_DIR", cache_dir)
    return cache_dir


@pytest.fixture
def fake_httpx(monkeypatch: pytest.MonkeyPatch):
    """Drop-in factory for ``httpx.AsyncClient``. Tests can mutate
    ``recorder`` to set the response or raise."""
    recorder = _RecordingClient()

    def _factory(**_kwargs):
        return recorder

    monkeypatch.setattr(thumbnails_module.httpx, "AsyncClient", _factory)
    return recorder


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)

    test_engine = _shared_test_engine()
    SQLModel.metadata.create_all(test_engine)

    def _override_session():
        with Session(test_engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    try:
        with Session(test_engine) as bootstrap:
            yield TestClient(app), bootstrap
    finally:
        app.dependency_overrides.pop(get_session, None)


def _make_asset(
    session: Session,
    *,
    thumbnail_url: Optional[str],
    visual_evidence_url: Optional[str] = None,
) -> Asset:
    """Minimal Channel + Post + Asset chain — enough to satisfy the
    foreign-key constraints. The test only ever reads the Asset row.

    F0.1-Capture-Pipeline-Fix: ``visual_evidence_url`` accepts the
    R2-Object-Key (or legacy ``/storage/...`` path) that the capture
    pipeline writes via ``persist_asset_screenshot_async``."""
    channel = Channel(
        name="Test Channel",
        platform="tiktok",
        url="https://www.tiktok.com/@test",
        handle="test",
        market=Market.US,
    )
    session.add(channel)
    session.commit()
    session.refresh(channel)

    post = Post(
        channel_id=channel.id,
        platform="tiktok",
        post_url=f"https://tiktok.com/@test/video/{uuid4()}",
    )
    session.add(post)
    session.commit()
    session.refresh(post)

    asset = Asset(
        post_id=post.id,
        thumbnail_url=thumbnail_url,
        visual_evidence_url=visual_evidence_url,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def _cache_path(cache_dir: Path, source_url: str) -> Path:
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.bin"


# ---------- 404 paths ------------------------------------------------------


def test_thumbnail_returns_404_for_missing_asset(client, isolated_cache_dir, fake_httpx):
    test_client, _ = client
    response = test_client.get(f"/api/thumbnails/{uuid4()}")
    assert response.status_code == 404
    assert fake_httpx.calls == []  # kein Source-Fetch für unbekannte ID


def test_thumbnail_returns_400_for_invalid_uuid(client, isolated_cache_dir, fake_httpx):
    test_client, _ = client
    response = test_client.get("/api/thumbnails/not-a-uuid")
    assert response.status_code == 400


def test_thumbnail_returns_404_for_asset_without_thumbnail_url(client, isolated_cache_dir, fake_httpx):
    test_client, db = client
    asset = _make_asset(db, thumbnail_url=None)
    response = test_client.get(f"/api/thumbnails/{asset.id}")
    assert response.status_code == 404
    assert fake_httpx.calls == []


# ---------- Header strategy ------------------------------------------------


def test_thumbnail_uses_tiktok_referer(client, isolated_cache_dir, fake_httpx):
    test_client, db = client
    src = "https://p19-common-sign.tiktokcdn-us.com/abcdef.jpg"
    asset = _make_asset(db, thumbnail_url=src)

    response = test_client.get(f"/api/thumbnails/{asset.id}")

    assert response.status_code == 200
    assert response.content == b"image-bytes"
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.headers["cache-control"] == "public, max-age=604800"
    assert len(fake_httpx.calls) == 1
    sent_headers = fake_httpx.calls[0]["headers"]
    assert sent_headers["Referer"] == "https://www.tiktok.com/"
    assert "Mozilla/5.0" in sent_headers["User-Agent"]


def test_thumbnail_uses_instagram_referer(client, isolated_cache_dir, fake_httpx):
    test_client, db = client
    src = "https://scontent-lax3-1.cdninstagram.com/feed/test.jpg"
    asset = _make_asset(db, thumbnail_url=src)

    test_client.get(f"/api/thumbnails/{asset.id}")

    sent_headers = fake_httpx.calls[0]["headers"]
    assert sent_headers["Referer"] == "https://www.instagram.com/"


def test_thumbnail_youtube_has_user_agent_but_no_referer(client, isolated_cache_dir, fake_httpx):
    test_client, db = client
    src = "https://i.ytimg.com/vi/abcd1234/hqdefault.jpg"
    asset = _make_asset(db, thumbnail_url=src)

    test_client.get(f"/api/thumbnails/{asset.id}")

    sent_headers = fake_httpx.calls[0]["headers"]
    assert "Referer" not in sent_headers
    assert "Mozilla/5.0" in sent_headers["User-Agent"]


# ---------- Cache behaviour ------------------------------------------------


def test_thumbnail_returns_cached_file_within_ttl(client, isolated_cache_dir, fake_httpx):
    test_client, db = client
    src = "https://p19-common-sign.tiktokcdn-us.com/cached.jpg"
    asset = _make_asset(db, thumbnail_url=src)

    isolated_cache_dir.mkdir(parents=True, exist_ok=True)
    _cache_path(isolated_cache_dir, src).write_bytes(b"cached image data")

    response = test_client.get(f"/api/thumbnails/{asset.id}")

    assert response.status_code == 200
    assert response.content == b"cached image data"
    assert fake_httpx.calls == []  # frischer Cache → kein Source-Fetch


def test_thumbnail_refetches_after_ttl_expired(client, isolated_cache_dir, fake_httpx):
    test_client, db = client
    src = "https://p19-common-sign.tiktokcdn-us.com/stale.jpg"
    asset = _make_asset(db, thumbnail_url=src)

    isolated_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(isolated_cache_dir, src)
    cache_file.write_bytes(b"old cached data")
    old_mtime = time.time() - 8 * 24 * 60 * 60  # 8 Tage alt → über TTL
    import os
    os.utime(cache_file, (old_mtime, old_mtime))

    fake_httpx.response = _RecordingResponse(content=b"fresh data")

    response = test_client.get(f"/api/thumbnails/{asset.id}")

    assert response.status_code == 200
    assert response.content == b"fresh data"
    assert len(fake_httpx.calls) == 1
    # Cache wurde mit den frischen Bytes überschrieben
    assert cache_file.read_bytes() == b"fresh data"


def test_thumbnail_returns_stale_when_refetch_fails(client, isolated_cache_dir, fake_httpx):
    """Stale-while-error: Source-403/Timeout während eines Re-Fetches
    darf den Browser nicht ohne Bild lassen, solange wir noch eine alte
    Cache-Datei haben."""
    test_client, db = client
    src = "https://p19-common-sign.tiktokcdn-us.com/stale-but-served.jpg"
    asset = _make_asset(db, thumbnail_url=src)

    isolated_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(isolated_cache_dir, src)
    cache_file.write_bytes(b"stale data")
    old_mtime = time.time() - 8 * 24 * 60 * 60
    import os
    os.utime(cache_file, (old_mtime, old_mtime))

    fake_httpx.raise_exc = httpx.HTTPError("403 forbidden")

    response = test_client.get(f"/api/thumbnails/{asset.id}")

    assert response.status_code == 200
    assert response.content == b"stale data"


def test_thumbnail_returns_404_when_no_cache_and_fetch_fails(client, isolated_cache_dir, fake_httpx):
    test_client, db = client
    src = "https://p19-common-sign.tiktokcdn-us.com/cold-and-broken.jpg"
    asset = _make_asset(db, thumbnail_url=src)

    fake_httpx.raise_exc = httpx.HTTPError("403 forbidden")

    response = test_client.get(f"/api/thumbnails/{asset.id}")

    assert response.status_code == 404


def test_thumbnail_writes_cache_on_first_fetch(client, isolated_cache_dir, fake_httpx):
    test_client, db = client
    src = "https://p19-common-sign.tiktokcdn-us.com/new.jpg"
    asset = _make_asset(db, thumbnail_url=src)

    fake_httpx.response = _RecordingResponse(content=b"freshly fetched")

    test_client.get(f"/api/thumbnails/{asset.id}")

    cache_file = _cache_path(isolated_cache_dir, src)
    assert cache_file.exists()
    assert cache_file.read_bytes() == b"freshly fetched"


# ---------- F0.1 capture-pipeline fix --------------------------------------


def test_thumbnail_redirects_to_r2_when_visual_evidence_url_is_object_key(
    client, isolated_cache_dir, fake_httpx,
):
    """F0.1-Capture-Pipeline-Fix: wenn die Capture-Pipeline einen R2-
    Object-Key in ``asset.visual_evidence_url`` geschrieben hat, leitet
    der Thumbnail-Endpoint per 302 auf die R2-aufgelöste URL um — kein
    CDN-Hotlink-Proxy-Roundtrip, kein 7-Tage-Stale-Cache. Der CDN-
    ``thumbnail_url`` bleibt als Audit-Trail in der DB, wird aber ignoriert,
    solange ``visual_evidence_url`` greift."""
    test_client, db = client
    object_key = "evidence/test-asset-1234.jpg"
    asset = _make_asset(
        db,
        thumbnail_url="https://p19-common-sign.tiktokcdn-us.com/expired-cdn.jpg",
        visual_evidence_url=object_key,
    )

    response = test_client.get(
        f"/api/thumbnails/{asset.id}", follow_redirects=False,
    )

    assert response.status_code == 302, response.text
    # LocalFileStorage (Default in Tests) löst Object-Keys nach
    # ``/storage/<key>`` auf — das Frontend absolutisiert das via
    # ``buildProxyImageUrl`` gegen die API-Origin.
    assert response.headers["location"] == f"/storage/{object_key}"
    # Kein CDN-Fetch — der Hotlink-Proxy-Pfad wurde nicht angefasst.
    assert fake_httpx.calls == []


def test_thumbnail_falls_back_to_cdn_proxy_when_visual_evidence_url_missing(
    client, isolated_cache_dir, fake_httpx,
):
    """Backward-Compat-Garantie: Assets aus der Pre-F0.1-Phase (oder
    Captures, in denen ``persist_asset_screenshot_async`` die R2-Schreibung
    geskippt hat — PC-1 Skip-and-Log-Policy) haben ``visual_evidence_url=
    NULL``. Diese Assets müssen weiterhin durch den CDN-Hotlink-Proxy
    (Sprint 5c) bedient werden, exakt wie vor dem Fix. Sonst fallen alle
    historischen Brief-Cards auf den Plattform-Akronym-Fallback."""
    test_client, db = client
    src = "https://p19-common-sign.tiktokcdn-us.com/legacy.jpg"
    asset = _make_asset(db, thumbnail_url=src, visual_evidence_url=None)

    fake_httpx.response = _RecordingResponse(content=b"legacy-cdn-bytes")

    response = test_client.get(f"/api/thumbnails/{asset.id}")

    assert response.status_code == 200
    assert response.content == b"legacy-cdn-bytes"
    # Hotlink-Proxy ist tatsächlich angesprungen — der CDN-Fallback-Pfad
    # läuft unverändert weiter.
    assert len(fake_httpx.calls) == 1
    assert fake_httpx.calls[0]["url"] == src
