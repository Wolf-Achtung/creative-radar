"""Tests for scripts.backfill_vision. analyze_asset_visual is monkeypatched —
no OpenAI/network. Covers dry-run (no calls/writes), budget self-stop,
idempotent pending-only selection, oldest-first order, and the image-source
projection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import Asset, AssetType, Channel, Market, Post, ReviewStatus


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make(session: Session, *, status: str = "pending",
          with_image: bool = True, created_at: datetime | None = None,
          platform: str = "instagram") -> Asset:
    channel = Channel(
        id=uuid4(), name=f"Ch-{uuid4().hex[:6]}", platform=platform,
        url=f"https://example.com/c/{uuid4().hex[:6]}", market=Market.US,
    )
    session.add(channel)
    session.commit()
    post = Post(
        id=uuid4(), channel_id=channel.id,
        post_url=f"https://example.com/p/{uuid4().hex[:8]}",
        detected_at=datetime.now(timezone.utc),
    )
    session.add(post)
    session.commit()
    asset = Asset(
        id=uuid4(), post_id=post.id,
        asset_type=AssetType.UNKNOWN, review_status=ReviewStatus.NEW,
        visual_analysis_status=status,
        thumbnail_url="https://cdn.example/x.jpg" if with_image else None,
        created_at=created_at or datetime.now(timezone.utc),
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def _stub_analyze(call_log: list):
    def fake(session, asset):
        call_log.append(asset.id)
        asset.visual_analysis_status = "analyzed"
        session.add(asset)
        session.commit()
        session.refresh(asset)
        return asset
    return fake


def test_dry_run_makes_no_calls_and_projects_cost(monkeypatch, session):
    from scripts import backfill_vision as backfill

    _make(session, with_image=True)
    _make(session, with_image=True)
    _make(session, with_image=False)  # no image -> free, not in projection

    monkeypatch.setattr(
        backfill, "analyze_asset_visual",
        lambda s, a: pytest.fail("dry-run must not call analyze_asset_visual"),
    )

    summary = backfill.run(session, apply=False, cost_per_call=0.015)

    assert summary["mode"] == "dry_run"
    assert summary["total_pending"] == 3
    assert summary["with_image"] == 2
    assert summary["projected_cost_usd"] == round(2 * 0.015, 4)
    assert summary["attempted"] == 0


def test_apply_processes_pending_oldest_first(monkeypatch, session):
    from scripts import backfill_vision as backfill

    base = datetime.now(timezone.utc) - timedelta(days=5)
    ids = [_make(session, created_at=base + timedelta(hours=i)).id for i in range(3)]
    call_log: list = []
    monkeypatch.setattr(backfill, "analyze_asset_visual", _stub_analyze(call_log))

    summary = backfill.run(session, apply=True, budget_usd=60.0)

    assert summary["attempted"] == 3
    assert summary["succeeded"] == 3
    assert call_log == ids  # oldest-first
    # Idempotent: a second run finds nothing pending.
    call_log2: list = []
    monkeypatch.setattr(backfill, "analyze_asset_visual", _stub_analyze(call_log2))
    summary2 = backfill.run(session, apply=True)
    assert summary2["total_pending"] == 0
    assert call_log2 == []


def test_budget_self_stop(monkeypatch, session):
    from scripts import backfill_vision as backfill

    for _ in range(5):
        _make(session, with_image=True)
    call_log: list = []
    monkeypatch.setattr(backfill, "analyze_asset_visual", _stub_analyze(call_log))

    # Budget for exactly 2 calls at $0.015.
    summary = backfill.run(session, apply=True, budget_usd=0.03, cost_per_call=0.015)

    assert summary["attempted"] == 2
    assert summary["budget_stopped"] is True
    assert summary["remaining_after_stop"] == 3
    assert summary["spent_usd"] == round(2 * 0.015, 4)
    # 3 assets remain pending — never touched.
    pending = list(session.exec(
        select(Asset).where(Asset.visual_analysis_status == "pending")
    ).all())
    assert len(pending) == 3


def test_platform_filter_selects_only_that_platform(monkeypatch, session):
    from scripts import backfill_vision as backfill

    yt = [_make(session, platform="youtube").id for _ in range(2)]
    _make(session, platform="instagram")
    _make(session, platform="tiktok")
    call_log: list = []
    monkeypatch.setattr(backfill, "analyze_asset_visual", _stub_analyze(call_log))

    summary = backfill.run(session, apply=True, platform="youtube")

    assert summary["platform"] == "youtube"
    assert summary["total_pending"] == 2
    assert sorted(call_log, key=str) == sorted(yt, key=str)


def test_no_platform_filter_processes_all(monkeypatch, session):
    from scripts import backfill_vision as backfill

    _make(session, platform="youtube")
    _make(session, platform="instagram")
    _make(session, platform="tiktok")
    call_log: list = []
    monkeypatch.setattr(backfill, "analyze_asset_visual", _stub_analyze(call_log))

    summary = backfill.run(session, apply=True)  # no platform -> unchanged behaviour

    assert summary["platform"] is None
    assert summary["total_pending"] == 3
    assert len(call_log) == 3


def test_apply_skips_non_pending(monkeypatch, session):
    from scripts import backfill_vision as backfill

    _make(session, status="analyzed")
    _make(session, status="fetch_failed")
    call_log: list = []
    monkeypatch.setattr(backfill, "analyze_asset_visual", _stub_analyze(call_log))

    summary = backfill.run(session, apply=True)

    assert summary["total_pending"] == 0
    assert call_log == []
