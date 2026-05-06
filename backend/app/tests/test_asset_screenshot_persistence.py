"""Sprint 5.3.6 — tests for screenshot persistence in the four
asset-creating sync paths.

Strategy: patch ``capture_asset_screenshot`` at the module where the
helper imports it (``app.services.asset_screenshot_persistence``). All
four sync paths funnel through that helper, so a single patch point
covers each path without depending on real S3 / Local Storage / httpx.

Each path is exercised twice:
- persist: capture returns a valid ``VisualEvidenceResult`` -> Asset
  row is created with ``visual_evidence_url`` set
- skip-and-log (PC-1): capture either returns a non-captured status
  *or* raises -> Asset row is still created, ``visual_evidence_url``
  remains None, and the sync stats still count the asset as ``created``
  (not ``errored``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import (
    Asset,
    AssetType,
    Channel,
    Market,
    Post,
    Priority,
    ReviewStatus,
)
from app.services import asset_screenshot_persistence as persistence_mod
from app.services.asset_screenshot_persistence import persist_asset_screenshot
from app.services.screenshot_capture import VisualEvidenceResult


# ---------- Helper unit tests ----------------------------------------


def _new_asset(**kwargs) -> Asset:
    return Asset(
        id=uuid4(),
        post_id=uuid4(),
        asset_type=AssetType.UNKNOWN,
        review_status=ReviewStatus.NEW,
        **kwargs,
    )


def test_persist_helper_writes_evidence_url_on_success():
    asset = _new_asset(screenshot_url="https://cdn.example/x.jpg")
    fake_result = VisualEvidenceResult(
        status="captured",
        evidence_url=f"evidence/{asset.id}_abc.jpg",
        source_url="https://cdn.example/x.jpg",
        captured_at="2026-05-03T20:00:00+00:00",
    )
    with patch.object(persistence_mod, "capture_asset_screenshot", return_value=fake_result):
        persist_asset_screenshot(asset)

    assert asset.visual_evidence_url == fake_result.evidence_url
    assert asset.visual_evidence_status == "captured"
    assert asset.visual_source_url == "https://cdn.example/x.jpg"


def test_persist_helper_skips_and_logs_when_capture_returns_failed_status(caplog):
    asset = _new_asset(screenshot_url="https://cdn.example/x.jpg")
    fake_result = VisualEvidenceResult(status="fetch_failed")

    with patch.object(persistence_mod, "capture_asset_screenshot", return_value=fake_result):
        with caplog.at_level("WARNING", logger=persistence_mod.logger.name):
            persist_asset_screenshot(asset)

    assert asset.visual_evidence_url is None
    assert asset.visual_evidence_status == "fetch_failed"
    assert any("capture failed for asset" in r.message for r in caplog.records)


def test_persist_helper_skips_and_logs_when_capture_raises(caplog):
    asset = _new_asset(screenshot_url="https://cdn.example/x.jpg")

    def boom(_a):
        raise RuntimeError("CDN unreachable")

    with patch.object(persistence_mod, "capture_asset_screenshot", side_effect=boom):
        with caplog.at_level("WARNING", logger=persistence_mod.logger.name):
            persist_asset_screenshot(asset)

    assert asset.visual_evidence_url is None
    assert any("capture failed for asset" in r.message for r in caplog.records)
    assert any("CDN unreachable" in r.message for r in caplog.records)


# ---------- Shared infra for sync-path integration tests --------------


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
    monkeypatch.setattr(settings, "openai_api_key", None, raising=False)
    monkeypatch.setattr(settings, "apify_api_token", "TEST", raising=False)
    monkeypatch.setattr(settings, "apify_instagram_actor_id", "test/instagram", raising=False)
    monkeypatch.setattr(settings, "apify_tiktok_actor_id", "test/tiktok", raising=False)

    def _override_session():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _seed_channel(db, *, platform: str = "instagram", handle: str = "netflixde") -> Channel:
    with Session(db) as session:
        url = (
            f"https://www.instagram.com/{handle}/"
            if platform == "instagram"
            else f"https://www.tiktok.com/@{handle}"
        )
        ch = Channel(
            name=f"{platform}-{handle}",
            platform=platform,
            url=url,
            handle=handle,
            market=Market.DE,
            priority=Priority.B,
            active=True,
            mvp=True,
        )
        session.add(ch)
        session.commit()
        session.refresh(ch)
        return ch


def _captured_result(asset_id) -> VisualEvidenceResult:
    return VisualEvidenceResult(
        status="captured",
        evidence_url=f"evidence/{asset_id}_test.jpg",
        source_url="https://cdn.example/x.jpg",
        captured_at="2026-05-03T20:00:00+00:00",
    )


# ---------- Apify Instagram monitor ----------------------------------


def _ig_raw_item(post_url="https://www.instagram.com/p/AAA/"):
    return {
        "url": post_url,
        "displayUrl": "https://cdn.example/ig.jpg",
        "caption": "Trailer drop",
        "ownerUsername": "netflixde",
        "timestamp": "2026-05-01T12:00:00Z",
    }


def test_apify_instagram_monitor_persists_screenshot(client, db):
    channel = _seed_channel(db, platform="instagram", handle="netflixde")

    captured: list[str] = []

    def fake_capture(asset):
        result = _captured_result(asset.id)
        captured.append(result.evidence_url)
        return result

    with patch("app.api.monitor.run_public_channel_monitor",
               new_callable=AsyncMock, return_value=[_ig_raw_item()]), \
         patch.object(persistence_mod, "capture_asset_screenshot", side_effect=fake_capture):
        response = client.post(
            "/api/monitor/apify-instagram",
            json={"channel_ids": [str(channel.id)], "max_channels": 1,
                  "results_limit_per_channel": 1, "only_whitelist_matches": False},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created_assets"] == 1
    assert len(captured) == 1

    with Session(db) as session:
        asset = session.exec(select(Asset)).one()
        assert asset.visual_evidence_url == captured[0]
        assert asset.visual_evidence_status == "captured"


def test_apify_instagram_monitor_skip_and_log_on_capture_failure(client, db, caplog):
    channel = _seed_channel(db, platform="instagram", handle="netflixde")

    def failing_capture(_asset):
        raise RuntimeError("CDN unreachable")

    with patch("app.api.monitor.run_public_channel_monitor",
               new_callable=AsyncMock, return_value=[_ig_raw_item()]), \
         patch.object(persistence_mod, "capture_asset_screenshot", side_effect=failing_capture):
        with caplog.at_level("WARNING", logger=persistence_mod.logger.name):
            response = client.post(
                "/api/monitor/apify-instagram",
                json={"channel_ids": [str(channel.id)], "max_channels": 1,
                      "results_limit_per_channel": 1, "only_whitelist_matches": False},
            )

    assert response.status_code == 200, response.text
    # PC-1: still counted as created, not errored.
    assert response.json()["created_assets"] == 1

    with Session(db) as session:
        asset = session.exec(select(Asset)).one()
        assert asset.visual_evidence_url is None

    assert any("capture failed for asset" in r.message for r in caplog.records)


# ---------- Apify TikTok monitor -------------------------------------


def _tiktok_raw_item(post_url="https://www.tiktok.com/@netflix/video/123"):
    return {
        "webVideoUrl": post_url,
        "id": "123",
        "text": "Behind the scenes",
        "authorMeta": {"name": "netflix"},
        "videoMeta": {"coverUrl": "https://cdn.example/tt.jpg", "duration": 17},
        "createTimeISO": "2026-05-01T12:00:00Z",
    }


def test_apify_tiktok_monitor_persists_screenshot(client, db):
    channel = _seed_channel(db, platform="tiktok", handle="netflix")

    captured: list[str] = []

    def fake_capture(asset):
        result = _captured_result(asset.id)
        captured.append(result.evidence_url)
        return result

    with patch("app.api.monitor.run_tiktok_profile_monitor",
               new_callable=AsyncMock, return_value=[_tiktok_raw_item()]), \
         patch.object(persistence_mod, "capture_asset_screenshot", side_effect=fake_capture):
        response = client.post(
            "/api/monitor/apify-tiktok",
            json={"channel_ids": [str(channel.id)], "usernames": [],
                  "max_channels": 1, "results_limit_per_channel": 1,
                  "only_whitelist_matches": False},
        )

    assert response.status_code == 200, response.text
    assert response.json()["created_assets"] == 1
    assert len(captured) == 1

    with Session(db) as session:
        asset = session.exec(select(Asset)).one()
        assert asset.visual_evidence_url == captured[0]


def test_apify_tiktok_monitor_skip_and_log_on_capture_failure(client, db, caplog):
    channel = _seed_channel(db, platform="tiktok", handle="netflix")

    def failing_capture(_asset):
        raise RuntimeError("S3 timeout")

    with patch("app.api.monitor.run_tiktok_profile_monitor",
               new_callable=AsyncMock, return_value=[_tiktok_raw_item()]), \
         patch.object(persistence_mod, "capture_asset_screenshot", side_effect=failing_capture):
        with caplog.at_level("WARNING", logger=persistence_mod.logger.name):
            response = client.post(
                "/api/monitor/apify-tiktok",
                json={"channel_ids": [str(channel.id)], "usernames": [],
                      "max_channels": 1, "results_limit_per_channel": 1,
                      "only_whitelist_matches": False},
            )

    assert response.status_code == 200, response.text
    assert response.json()["created_assets"] == 1

    with Session(db) as session:
        asset = session.exec(select(Asset)).one()
        assert asset.visual_evidence_url is None

    assert any("capture failed for asset" in r.message for r in caplog.records)


# ---------- Manual import (frontend) ---------------------------------


def test_manual_import_persists_screenshot(client, db):
    channel = _seed_channel(db, platform="instagram", handle="manualimport")

    captured: list[str] = []

    def fake_capture(asset):
        result = _captured_result(asset.id)
        captured.append(result.evidence_url)
        return result

    with patch.object(persistence_mod, "capture_asset_screenshot", side_effect=fake_capture):
        response = client.post(
            "/api/posts/manual-import",
            json={
                "channel_id": str(channel.id),
                "post_url": "https://www.instagram.com/p/MANUAL1/",
                "asset_type": "Trailer",
                "screenshot_url": "https://cdn.example/manual.jpg",
            },
        )

    assert response.status_code == 200, response.text
    assert len(captured) == 1
    with Session(db) as session:
        asset = session.exec(select(Asset)).one()
        assert asset.visual_evidence_url == captured[0]


def test_manual_import_skip_and_log_on_capture_failure(client, db, caplog):
    channel = _seed_channel(db, platform="instagram", handle="manualimport")

    def failing_capture(_asset):
        raise RuntimeError("storage unavailable")

    with patch.object(persistence_mod, "capture_asset_screenshot", side_effect=failing_capture):
        with caplog.at_level("WARNING", logger=persistence_mod.logger.name):
            response = client.post(
                "/api/posts/manual-import",
                json={
                    "channel_id": str(channel.id),
                    "post_url": "https://www.instagram.com/p/MANUAL2/",
                    "asset_type": "Trailer",
                    "screenshot_url": "https://cdn.example/manual.jpg",
                },
            )

    assert response.status_code == 200, response.text
    with Session(db) as session:
        asset = session.exec(select(Asset)).one()
        assert asset.visual_evidence_url is None
    assert any("capture failed for asset" in r.message for r in caplog.records)


# ---------- analyze_instagram_link -----------------------------------


async def _fake_preview(_url):
    return {
        "image_url": "https://cdn.example/iglink.jpg",
        "caption": "Linked IG post",
        "title": "Linked IG post",
    }


def test_analyze_instagram_link_persists_screenshot(client, db):
    channel = _seed_channel(db, platform="instagram", handle="iglink")

    captured: list[str] = []

    def fake_capture(asset):
        result = _captured_result(asset.id)
        captured.append(result.evidence_url)
        return result

    with patch("app.api.posts.fetch_public_preview", side_effect=_fake_preview), \
         patch.object(persistence_mod, "capture_asset_screenshot", side_effect=fake_capture):
        response = client.post(
            "/api/posts/analyze-instagram-link",
            json={
                "post_url": "https://www.instagram.com/p/IGLINK1/",
                "channel_id": str(channel.id),
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["already_exists"] is False
    assert len(captured) == 1
    with Session(db) as session:
        asset = session.exec(select(Asset)).one()
        assert asset.visual_evidence_url == captured[0]


def test_analyze_instagram_link_skip_and_log_on_capture_failure(client, db, caplog):
    channel = _seed_channel(db, platform="instagram", handle="iglink")

    def failing_capture(_asset):
        raise RuntimeError("image unreadable")

    with patch("app.api.posts.fetch_public_preview", side_effect=_fake_preview), \
         patch.object(persistence_mod, "capture_asset_screenshot", side_effect=failing_capture):
        with caplog.at_level("WARNING", logger=persistence_mod.logger.name):
            response = client.post(
                "/api/posts/analyze-instagram-link",
                json={
                    "post_url": "https://www.instagram.com/p/IGLINK2/",
                    "channel_id": str(channel.id),
                },
            )

    assert response.status_code == 200, response.text
    with Session(db) as session:
        asset = session.exec(select(Asset)).one()
        assert asset.visual_evidence_url is None
    assert any("capture failed for asset" in r.message for r in caplog.records)


# ---------- post_analyzer (Sprint 5.3.1) -----------------------------


def test_analyze_post_persists_screenshot(monkeypatch):
    """post_analyzer.analyze_post creates an Asset for posts that have a
    discoverable image URL — the helper hooks before session.add so the
    Asset row carries visual_evidence_url + the asset_url is bridged to
    visual_source_url so capture's _candidate_sources can find it."""
    from types import SimpleNamespace

    from app.models.entities import (
        AcquisitionStrategy,
        QualityTier,
    )
    from app.services import post_analyzer

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(settings, "anthropic_haiku_model", "claude-haiku-4-5", raising=False)
    monkeypatch.setattr(settings, "anthropic_sonnet_model", "claude-sonnet-4-6", raising=False)
    monkeypatch.setattr(settings, "anthropic_haiku_input_per_1k_usd", 0.001, raising=False)
    monkeypatch.setattr(settings, "anthropic_haiku_output_per_1k_usd", 0.005, raising=False)
    monkeypatch.setattr(settings, "anthropic_sonnet_input_per_1k_usd", 0.003, raising=False)
    monkeypatch.setattr(settings, "anthropic_sonnet_output_per_1k_usd", 0.015, raising=False)
    from app.services import cost_log as cost_log_module
    monkeypatch.setattr(cost_log_module, "engine", engine)

    session = Session(engine)
    channel = Channel(
        name="YT", platform="youtube",
        url="https://www.youtube.com/@yt", handle="yt",
        market=Market.INT, priority=Priority.B,
        quality_tier=QualityTier.P1,
        acquisition_strategy=AcquisitionStrategy.YOUTUBE_API,
        active=True, mvp=True,
    )
    session.add(channel)
    session.commit()
    session.refresh(channel)
    post = Post(
        channel_id=channel.id, platform="youtube",
        post_url=f"https://yt.test/{uuid4()}",
        caption="Trailer",
        raw_payload={"snippet": {"thumbnails": {"maxres": {"url": "https://i.ytimg.com/vi/x/maxresdefault.jpg"}}}},
        published_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    session.add(post)
    session.commit()
    session.refresh(post)

    captured: list[str] = []

    def fake_capture(asset):
        # The bridge in post_analyzer must have copied asset_url onto
        # visual_source_url so capture can find a candidate source.
        assert asset.visual_source_url == "https://i.ytimg.com/vi/x/maxresdefault.jpg"
        result = _captured_result(asset.id)
        captured.append(result.evidence_url)
        return result

    fake_msg = SimpleNamespace(
        content=[SimpleNamespace(text="ok")],
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )
    haiku_msg = SimpleNamespace(
        content=[SimpleNamespace(text='{"format":"trailer","tone":"suspenseful","confidence":0.9}')],
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )
    sonnet_msg = SimpleNamespace(
        content=[SimpleNamespace(text='{"purpose":"release_week","lifecycle_stage":"launch","confidence":0.8}')],
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )
    with patch.object(post_analyzer, "messages_create_vision", return_value=fake_msg), \
         patch.object(post_analyzer, "messages_create_text", side_effect=[haiku_msg, sonnet_msg]), \
         patch.object(persistence_mod, "capture_asset_screenshot", side_effect=fake_capture):
        result = post_analyzer.analyze_post(session, post)
        session.commit()

    assert result.status == "analyzed"
    assert len(captured) == 1
    asset = session.exec(select(Asset).where(Asset.post_id == post.id)).one()
    assert asset.visual_evidence_url == captured[0]


def test_analyze_post_skip_and_log_on_capture_failure(monkeypatch, caplog):
    from types import SimpleNamespace

    from app.models.entities import (
        AcquisitionStrategy,
        QualityTier,
    )
    from app.services import post_analyzer

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(settings, "anthropic_haiku_model", "claude-haiku-4-5", raising=False)
    monkeypatch.setattr(settings, "anthropic_sonnet_model", "claude-sonnet-4-6", raising=False)
    monkeypatch.setattr(settings, "anthropic_haiku_input_per_1k_usd", 0.001, raising=False)
    monkeypatch.setattr(settings, "anthropic_haiku_output_per_1k_usd", 0.005, raising=False)
    monkeypatch.setattr(settings, "anthropic_sonnet_input_per_1k_usd", 0.003, raising=False)
    monkeypatch.setattr(settings, "anthropic_sonnet_output_per_1k_usd", 0.015, raising=False)
    from app.services import cost_log as cost_log_module
    monkeypatch.setattr(cost_log_module, "engine", engine)

    session = Session(engine)
    channel = Channel(
        name="YT", platform="youtube",
        url="https://www.youtube.com/@yt", handle="yt",
        market=Market.INT, priority=Priority.B,
        quality_tier=QualityTier.P1,
        acquisition_strategy=AcquisitionStrategy.YOUTUBE_API,
        active=True, mvp=True,
    )
    session.add(channel)
    session.commit()
    session.refresh(channel)
    post = Post(
        channel_id=channel.id, platform="youtube",
        post_url=f"https://yt.test/{uuid4()}",
        caption="Trailer",
        raw_payload={"snippet": {"thumbnails": {"maxres": {"url": "https://i.ytimg.com/vi/x/maxresdefault.jpg"}}}},
        published_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    session.add(post)
    session.commit()
    session.refresh(post)

    fake_msg = SimpleNamespace(
        content=[SimpleNamespace(text="ok")],
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )
    haiku_msg = SimpleNamespace(
        content=[SimpleNamespace(text='{"format":"trailer","tone":"suspenseful","confidence":0.9}')],
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )
    sonnet_msg = SimpleNamespace(
        content=[SimpleNamespace(text='{"purpose":"release_week","lifecycle_stage":"launch","confidence":0.8}')],
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )

    def failing_capture(_asset):
        raise RuntimeError("CDN unreachable")

    with patch.object(post_analyzer, "messages_create_vision", return_value=fake_msg), \
         patch.object(post_analyzer, "messages_create_text", side_effect=[haiku_msg, sonnet_msg]), \
         patch.object(persistence_mod, "capture_asset_screenshot", side_effect=failing_capture):
        with caplog.at_level("WARNING", logger=persistence_mod.logger.name):
            result = post_analyzer.analyze_post(session, post)
            session.commit()

    # Per PC-1: analyze_post still reports success; Asset row exists
    # without visual_evidence_url.
    assert result.status == "analyzed"
    asset = session.exec(select(Asset).where(Asset.post_id == post.id)).one()
    assert asset.visual_evidence_url is None
    assert any("capture failed for asset" in r.message for r in caplog.records)


