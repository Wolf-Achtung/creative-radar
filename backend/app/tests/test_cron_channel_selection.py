"""Tests for app.services.cron_channel_selection (Sprint 5.3.5)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Asset, AssetType, Channel, Post, ReviewStatus
from app.services.cron_channel_selection import compute_run_index, select_channels_for_cron


def _engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_channel(session: Session, *, handle: str, platform: str = "instagram",
                  mvp: bool = True, active: bool = True) -> Channel:
    ch = Channel(
        id=uuid4(),
        name=handle,
        handle=handle,
        url=f"https://www.instagram.com/{handle}/",
        platform=platform,
        active=active,
        mvp=mvp,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


def _seed_assets(session: Session, channel: Channel, *, count: int,
                 days_ago: int = 1) -> None:
    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    for _ in range(count):
        post = Post(channel_id=channel.id, post_url=f"https://example/{uuid4()}",
                    platform=channel.platform, created_at=created)
        session.add(post)
        session.commit()
        session.refresh(post)
        asset = Asset(
            post_id=post.id,
            asset_type=AssetType.UNKNOWN,
            review_status=ReviewStatus.NEW,
            created_at=created,
        )
        session.add(asset)
        session.commit()


def test_select_channels_a_class_meets_threshold():
    engine = _engine()
    with Session(engine) as session:
        active = _seed_channel(session, handle="active_one")
        inactive = _seed_channel(session, handle="inactive_one")
        _seed_assets(session, active, count=3)

        selected = select_channels_for_cron(session, "instagram", run_index=0,
                                             a_class_threshold=1, a_class_max=10)

    handles = {c.handle for c in selected}
    assert "active_one" in handles
    assert "inactive_one" in handles


def test_select_channels_b_class_rotates_across_three_runs():
    engine = _engine()
    with Session(engine) as session:
        b_handles = [f"b{i:02d}" for i in range(9)]
        for h in b_handles:
            _seed_channel(session, handle=h)

        run0 = select_channels_for_cron(session, "instagram", run_index=0,
                                         a_class_threshold=1, a_class_max=10)
        run1 = select_channels_for_cron(session, "instagram", run_index=1,
                                         a_class_threshold=1, a_class_max=10)
        run2 = select_channels_for_cron(session, "instagram", run_index=2,
                                         a_class_threshold=1, a_class_max=10)

    seen = set()
    for run in (run0, run1, run2):
        seen.update(c.handle for c in run)
    assert seen == set(b_handles)
    assert {c.handle for c in run0} & {c.handle for c in run1} == set()
    assert {c.handle for c in run1} & {c.handle for c in run2} == set()


def test_select_channels_a_class_max_caps_per_run_but_demoted_overflow_rotates_via_b_class():
    """A-class is capped per-run for cost, but channels above the cap must
    not be lost — they fall into B-class rotation so they still get synced
    eventually. Concrete: 5 hot channels, cap=2 → 2 A-class + the other 3
    distributed across the three B-class run-thirds, so each run sees
    2 + ceil(3/3) = 3 channels and the three runs together cover all 5."""
    engine = _engine()
    with Session(engine) as session:
        handles = [f"hot{i}" for i in range(5)]
        for h in handles:
            ch = _seed_channel(session, handle=h)
            _seed_assets(session, ch, count=10)

        seen_overall: set[str] = set()
        for run_index in range(3):
            run = select_channels_for_cron(session, "instagram", run_index=run_index,
                                            a_class_threshold=1, a_class_max=2)
            assert len(run) <= 3, f"run {run_index} oversized: {[c.handle for c in run]}"
            seen_overall.update(c.handle for c in run)

    assert seen_overall == set(handles)


def test_select_channels_filters_inactive_and_non_mvp():
    engine = _engine()
    with Session(engine) as session:
        _seed_channel(session, handle="active_mvp")
        _seed_channel(session, handle="inactive", active=False)
        _seed_channel(session, handle="not_mvp", mvp=False)
        _seed_channel(session, handle="wrong_platform", platform="tiktok")

        selected = select_channels_for_cron(session, "instagram", run_index=0,
                                             a_class_threshold=1, a_class_max=10)

    handles = {c.handle for c in selected}
    assert handles == {"active_mvp"}


def test_select_channels_threshold_excludes_low_activity_from_a_class():
    engine = _engine()
    with Session(engine) as session:
        hot = _seed_channel(session, handle="hot")
        cold = _seed_channel(session, handle="cold")
        _seed_assets(session, hot, count=5)
        _seed_assets(session, cold, count=1)

        selected = select_channels_for_cron(session, "instagram", run_index=0,
                                             a_class_threshold=3, a_class_max=10)

    handles_run0 = {c.handle for c in selected}
    assert "hot" in handles_run0
    cold_seen = False
    for run_index in range(3):
        with Session(engine) as session:
            run = select_channels_for_cron(session, "instagram", run_index=run_index,
                                            a_class_threshold=3, a_class_max=10)
        if any(c.handle == "cold" for c in run):
            cold_seen = True
            break
    assert cold_seen


def test_compute_run_index_is_deterministic_and_bounded():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    indexes = {compute_run_index(base + timedelta(days=d)) for d in range(30)}
    assert indexes == {0, 1, 2}
    assert compute_run_index(base) == compute_run_index(base + timedelta(days=9))
