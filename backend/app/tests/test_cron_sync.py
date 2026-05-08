"""Tests for the cron sync endpoint (Sprint 5.3.5 + background-task hotfix)."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import Asset, Channel, CronRun
from app.services import asset_screenshot_persistence as persistence_mod
from app.services.screenshot_capture import VisualEvidenceResult


def _engine_for_path(path: str):
    # Block 2: switched from in-memory + StaticPool to a file-backed SQLite
    # so multiple SQLAlchemy Sessions can hold their own connections. The
    # async asset-creation path opens a fresh ``Session(engine)`` per task
    # and runs them via ``asyncio.gather``; under StaticPool all tasks
    # would have shared one connection and serialised at the SQLite mutex
    # in ways that lost commits.
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_cron_", suffix=".db")
    os.close(fd)
    engine = _engine_for_path(path)
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        try:
            os.unlink(path)
        except OSError:
            pass


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

    async def fake_capture_async(asset):
        return fake_capture(asset)

    with patch("app.api.cron.run_public_channel_monitor",
               new_callable=AsyncMock, return_value=[fake_ig_item]), \
         patch("app.api.cron.run_tiktok_profile_monitor",
               new_callable=AsyncMock, return_value=[]), \
         patch.object(persistence_mod, "capture_asset_screenshot", side_effect=fake_capture), \
         patch.object(persistence_mod, "capture_asset_screenshot_async", side_effect=fake_capture_async):
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


# --------------------------------------------------------------------------
# Sprint Beta — auto-vision after sync.
#
# These tests stub out analyze_asset_visual entirely; they verify the
# cron-side wiring (skip-when-empty, FIFO ordering, cap enforcement,
# summary block shape), not the vision call itself. Vision behaviour is
# covered exhaustively by test_visual_analysis.py.
# --------------------------------------------------------------------------


def _ig_item(slug: str, owner: str = "netflixde") -> dict:
    return {
        "url": f"https://www.instagram.com/p/{slug}/",
        "ownerUsername": owner,
        "displayUrl": f"https://cdn.example/{slug}.jpg",
        "caption": f"caption {slug}",
        "timestamp": "2026-05-01T12:00:00Z",
    }


def _stub_capture(asset):
    return VisualEvidenceResult(status="captured", evidence_url=f"evidence/{asset.id}.jpg")


async def _stub_capture_async(asset):
    return _stub_capture(asset)


def _make_vision_stub(call_log: list):
    """Returns a fake analyze_asset_visual that records every asset id and
    flips status to 'analyzed', mirroring the real success path."""
    def fake(session, asset):
        call_log.append(asset.id)
        asset.visual_analysis_status = "analyzed"
        session.add(asset)
        session.commit()
        session.refresh(asset)
        return asset
    return fake


def test_cron_run_with_no_new_assets_skips_vision(client_with_auth, db):
    _seed_ig_channel(db, handle="netflixde")
    call_log: list = []

    with patch("app.api.cron.run_public_channel_monitor",
               new_callable=AsyncMock, return_value=[]), \
         patch("app.api.cron.run_tiktok_profile_monitor",
               new_callable=AsyncMock, return_value=[]), \
         patch("app.api.cron.analyze_asset_visual", side_effect=_make_vision_stub(call_log)):
        response = client_with_auth.post(
            "/api/admin/cron/sync-all",
            headers={"Authorization": "Bearer TESTTOKEN"},
        )

    assert response.status_code == 202, response.text
    assert call_log == []  # vision never fired

    with Session(db) as session:
        run = session.get(CronRun, uuid4().__class__(response.json()["run_id"]))
        assert run.status == "completed"
        # No assets created -> no vision block at all.
        assert "vision" not in run.summary_json


def test_cron_run_below_cap_analyzes_all_new_assets(client_with_auth, db, monkeypatch):
    _seed_ig_channel(db, handle="netflixde")
    monkeypatch.setattr(settings, "cron_vision_max_assets_per_run", 10, raising=False)
    call_log: list = []

    items = [_ig_item(f"below-cap-{i}") for i in range(3)]

    with patch("app.api.cron.run_public_channel_monitor",
               new_callable=AsyncMock, return_value=items), \
         patch("app.api.cron.run_tiktok_profile_monitor",
               new_callable=AsyncMock, return_value=[]), \
         patch.object(persistence_mod, "capture_asset_screenshot", side_effect=_stub_capture), \
         patch.object(persistence_mod, "capture_asset_screenshot_async", side_effect=_stub_capture_async), \
         patch("app.api.cron.analyze_asset_visual", side_effect=_make_vision_stub(call_log)):
        response = client_with_auth.post(
            "/api/admin/cron/sync-all",
            headers={"Authorization": "Bearer TESTTOKEN"},
        )

    assert response.status_code == 202, response.text
    assert len(call_log) == 3, "all 3 new assets should have been analyzed"

    with Session(db) as session:
        run = session.get(CronRun, uuid4().__class__(response.json()["run_id"]))
        vision = run.summary_json["vision"]
        assert vision["attempted"] == 3
        assert vision["succeeded"] == 3
        assert vision["skipped_cap"] == 0
        assert vision["text_fallback"] == 0
        assert vision["fetch_failed"] == 0
        assert vision["vision_error"] == 0
        assert vision["estimated_cost_usd"] == round(3 * 0.015, 4)
        assert isinstance(vision["duration_seconds"], (int, float))


def test_cron_run_above_cap_analyzes_fifo_and_leaves_rest(client_with_auth, db, monkeypatch):
    _seed_ig_channel(db, handle="netflixde")
    monkeypatch.setattr(settings, "cron_vision_max_assets_per_run", 2, raising=False)
    call_log: list = []

    # Five posts arriving in a deterministic order; the cron sync persists
    # them in this order, and the FIFO cap should pick the first two.
    items = [_ig_item(f"above-cap-{i}") for i in range(5)]

    with patch("app.api.cron.run_public_channel_monitor",
               new_callable=AsyncMock, return_value=items), \
         patch("app.api.cron.run_tiktok_profile_monitor",
               new_callable=AsyncMock, return_value=[]), \
         patch.object(persistence_mod, "capture_asset_screenshot", side_effect=_stub_capture), \
         patch.object(persistence_mod, "capture_asset_screenshot_async", side_effect=_stub_capture_async), \
         patch("app.api.cron.analyze_asset_visual", side_effect=_make_vision_stub(call_log)):
        response = client_with_auth.post(
            "/api/admin/cron/sync-all",
            headers={"Authorization": "Bearer TESTTOKEN"},
        )

    assert response.status_code == 202, response.text
    assert len(call_log) == 2, "exactly the cap should have been analyzed"

    with Session(db) as session:
        from app.models.entities import Post  # local import: no need at module top

        run = session.get(CronRun, uuid4().__class__(response.json()["run_id"]))
        vision = run.summary_json["vision"]
        assert vision["attempted"] == 2
        assert vision["succeeded"] == 2
        assert vision["skipped_cap"] == 3

        analyzed = list(session.exec(
            select(Asset).where(Asset.visual_analysis_status == "analyzed")
        ).all())
        pending = list(session.exec(
            select(Asset).where(Asset.visual_analysis_status == "pending")
        ).all())
        assert len(analyzed) == 2
        assert len(pending) == 3

        # FIFO guarantee under Block-2 async refactor: the asset_ids list the
        # cron driver hands to the vision step preserves item-input order
        # (asyncio.gather returns in submission order). We can no longer
        # assert on created_at because parallel commits race on timestamps;
        # verify the semantic directly via post_url -> input-slug mapping.
        analyzed_ids = [a.id for a in analyzed]
        analyzed_post_urls = [
            session.get(Post, a.post_id).post_url for a in analyzed
        ]
        analyzed_slugs = sorted(
            url.rstrip("/").rsplit("/", 1)[-1] for url in analyzed_post_urls
        )
        assert analyzed_slugs == ["above-cap-0", "above-cap-1"], (
            f"FIFO broken: analyzed slugs {analyzed_slugs} are not the first two inputs"
        )
        # Call log must contain exactly those two ids.
        assert sorted(call_log, key=str) == sorted(analyzed_ids, key=str)


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


# ---------- Sprint 4: YouTube cron leg --------------------------------------


def _seed_yt_channel(db, *, handle: str = "Netflix") -> Channel:
    with Session(db) as session:
        ch = Channel(
            id=uuid4(),
            name=handle,
            handle=handle,
            url=f"https://www.youtube.com/@{handle}",
            platform="youtube",
            active=True,
            mvp=True,
        )
        session.add(ch)
        session.commit()
        session.refresh(ch)
        return ch


def test_cron_sync_youtube_skips_when_not_configured(db, monkeypatch):
    """Sprint 4 — without YOUTUBE_API_KEY the YT leg returns a structured
    skip-summary instead of crashing the cron run. Wolf can verify
    deployment readiness by reading summary['platforms']['youtube'] in
    the cron_run row."""
    import asyncio
    from app.api.cron import _execute_youtube_sync
    monkeypatch.setattr("app.api.cron.is_youtube_configured", lambda: False)

    with Session(db) as session:
        result = asyncio.run(_execute_youtube_sync(session, run_index=0, created_asset_ids=[]))

    assert result == {"skipped": True, "reason": "youtube_not_configured"}


def test_cron_sync_youtube_runs_when_configured(db, monkeypatch):
    """Configured YT path: select_channels_for_cron returns the seeded
    YT channel, fetch_channel_videos is mocked to return one raw video,
    _run_apify_sync_for_platform_async is mocked to assert the per-channel
    contract (channels=[ch], platform='youtube'). Counters propagate into
    the summary block."""
    import asyncio
    from unittest.mock import AsyncMock
    from app.api import cron as cron_module

    ch = _seed_yt_channel(db, handle="NetflixDE")
    monkeypatch.setattr(cron_module, "is_youtube_configured", lambda: True)

    fake_video = {"id": "vid-123", "snippet": {"title": "trailer", "channelTitle": "NetflixDE"}, "_creative_radar_channel_id": "UCxxx"}
    monkeypatch.setattr(
        cron_module, "fetch_channel_videos",
        lambda handle, limit, *, channel_id_hint=None: ({"id": "UCxxx"}, [fake_video]),
    )

    captured_calls: list[dict] = []

    async def fake_sync(*, engine, channels, raw_items, platform, normalize, only_whitelist_matches):
        captured_calls.append({"platform": platform, "n_channels": len(channels), "n_items": len(raw_items)})
        # Mirror the real helper's return shape: ``created_assets`` /
        # ``skipped_no_whitelist_match`` are the historical Sprint-5.3.5
        # keys; the YT aggregator translates them back via the
        # _HELPER_COUNTER_KEY_MAP. Sprint-4.5 bug-2 fix.
        return {
            "created_assets": 1,
            "skipped_existing": 0,
            "skipped_no_whitelist_match": 0,
            "skipped_other": 0,
            "asset_ids": [uuid4()], "failed_channels": [],
        }
    monkeypatch.setattr(cron_module, "_run_apify_sync_for_platform_async", AsyncMock(side_effect=fake_sync))

    with Session(db) as session:
        result = asyncio.run(cron_module._execute_youtube_sync(session, run_index=0, created_asset_ids=[]))

    assert result["channels_checked"] == 1
    assert result["raw_items"] == 1
    assert result["created"] == 1
    assert result["quota_units_used"] == 3
    assert result["failed_channels"] == []
    # Per-channel contract: one helper call per YT channel.
    assert captured_calls == [{"platform": "youtube", "n_channels": 1, "n_items": 1}]


def test_cron_sync_youtube_isolates_per_channel_errors(db, monkeypatch):
    """One bad handle (NotFound or generic API error) must not stop the
    others. Quota error breaks the loop early — quota is account-wide,
    further calls only burn the rest of the day's allowance."""
    import asyncio
    from unittest.mock import AsyncMock
    from app.api import cron as cron_module
    from app.services.youtube_connector import (
        YouTubeNotFoundError, YouTubeQuotaExceededError,
    )

    ch_a = _seed_yt_channel(db, handle="GoodChannel")
    ch_b = _seed_yt_channel(db, handle="BadChannel")
    ch_c = _seed_yt_channel(db, handle="QuotaChannel")
    ch_d = _seed_yt_channel(db, handle="ShouldNotRun")

    monkeypatch.setattr(cron_module, "is_youtube_configured", lambda: True)
    # Bypass the A/B-Class rotation logic — at run_index=0 with 4
    # B-Class channels the third-slicing would only return the first
    # 2, hiding the loop-break we want to test. The selection logic
    # itself has its own coverage in test_cron_channel_selection.py.
    monkeypatch.setattr(
        cron_module, "select_channels_for_cron",
        lambda session, platform, run_index: [ch_a, ch_b, ch_c, ch_d],
    )

    def fake_fetch(handle, limit, *, channel_id_hint=None):
        if handle == "GoodChannel":
            return ({"id": "UC1"}, [{"id": "v1", "snippet": {"channelTitle": handle}}])
        if handle == "BadChannel":
            raise YouTubeNotFoundError(f"404 channelNotFound: {handle}")
        if handle == "QuotaChannel":
            raise YouTubeQuotaExceededError("quotaExceeded")
        # ShouldNotRun must never be reached because Quota breaks the loop.
        raise AssertionError(f"loop should have broken before reaching {handle}")
    monkeypatch.setattr(cron_module, "fetch_channel_videos", fake_fetch)

    async def fake_sync(*, channels, raw_items, **kw):
        # Helper-Format mit historischen Keys (siehe Sprint-4.5 bug 2).
        return {"created_assets": 1, "skipped_existing": 0, "skipped_no_whitelist_match": 0, "skipped_other": 0, "asset_ids": [], "failed_channels": []}
    monkeypatch.setattr(cron_module, "_run_apify_sync_for_platform_async", AsyncMock(side_effect=fake_sync))

    with Session(db) as session:
        result = asyncio.run(cron_module._execute_youtube_sync(session, run_index=0, created_asset_ids=[]))

    error_classes = [f["error_class"] for f in result["failed_channels"]]
    assert "not_found" in error_classes
    assert "quota_exceeded" in error_classes
    # Two errors recorded — Good succeeded, ShouldNotRun never reached.
    assert len(result["failed_channels"]) == 2
    assert result["created"] == 1  # GoodChannel went through


def test_youtube_aggregator_translates_helper_counter_keys(db, monkeypatch):
    """Sprint 4.5 bug 2 regression test — _run_apify_sync_for_platform_async
    returns counters under historical key names (created_assets,
    skipped_no_whitelist_match) that differ from the YT aggregator's
    internal names. The aggregator must translate via
    _HELPER_COUNTER_KEY_MAP, otherwise persisted videos appear as
    created=0 in the cron summary even when Phase A/C ran fine.

    Before the fix, this test would have asserted created==0 (the bug)
    even though the helper ran 7 created + 2 existing items through. After
    the fix, the YT summary correctly reflects 7 created and 2 existing."""
    import asyncio
    from unittest.mock import AsyncMock
    from app.api import cron as cron_module

    ch = _seed_yt_channel(db, handle="MappingTest")
    monkeypatch.setattr(cron_module, "is_youtube_configured", lambda: True)
    monkeypatch.setattr(
        cron_module, "select_channels_for_cron",
        lambda session, platform, run_index: [ch],
    )
    monkeypatch.setattr(
        cron_module, "fetch_channel_videos",
        lambda handle, limit, *, channel_id_hint=None: (
            {"id": "UCx"}, [{"id": f"v{i}"} for i in range(9)]
        ),
    )

    async def fake_sync(*, channels, raw_items, **kw):
        return {
            "created_assets": 7,
            "skipped_existing": 2,
            "skipped_no_whitelist_match": 0,
            "skipped_other": 0,
            "asset_ids": [uuid4() for _ in range(7)],
            "failed_channels": [],
            "processed_channels": 1,
        }
    monkeypatch.setattr(cron_module, "_run_apify_sync_for_platform_async", AsyncMock(side_effect=fake_sync))

    with Session(db) as session:
        result = asyncio.run(cron_module._execute_youtube_sync(session, run_index=0, created_asset_ids=[]))

    assert result["created"] == 7, "created counter must aggregate from helper's created_assets key"
    assert result["skipped_existing"] == 2
    assert result["skipped_no_match"] == 0
    assert result["skipped_other"] == 0
