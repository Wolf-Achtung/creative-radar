"""Block 2 — async-refactor + per-channel-error-isolation tests.

These tests exercise the new behaviour added in
``api/monitor._run_apify_sync_for_platform_async``:

* per-channel try/except: a single defective channel must not take down
  the rest of the platform sync, and the failure lands in
  ``summary_json["platforms"][p]["failed_channels"]``;
* ``asyncio.gather`` parallelism: items inside a channel run concurrently
  rather than sequentially;
* Semaphore enforcement: the OpenAI concurrency budget caps the number
  of in-flight ``analyze_creative_text_async`` calls to the configured
  ceiling (default 5).

Each test stubs the slow IO calls (OpenAI + httpx-screenshot) so the
test stays deterministic and fast — the goal is to verify the wiring,
not the SDKs themselves.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.api.monitor import (
    DEFAULT_HTTPX_CONCURRENCY,
    DEFAULT_OPENAI_CONCURRENCY,
    _run_apify_sync_for_platform_async,
)
from app.models.entities import Asset, Channel, Market, Post
from app.services import asset_screenshot_persistence as persistence_mod
from app.services.screenshot_capture import VisualEvidenceResult


# --------------------------------------------------------------------------
# Fixture: file-backed SQLite so concurrent Sessions in the async pipeline
# don't fight over a single shared connection (StaticPool would).
# --------------------------------------------------------------------------


@pytest.fixture
def engine():
    fd, path = tempfile.mkstemp(prefix="cr_block2_", suffix=".db")
    os.close(fd)
    eng = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()
        try:
            os.unlink(path)
        except OSError:
            pass


def _seed_channels(engine, handles: list[str]) -> list[Channel]:
    channels: list[Channel] = []
    with Session(engine) as session:
        for h in handles:
            ch = Channel(
                id=uuid4(),
                name=h,
                handle=h,
                url=f"https://www.instagram.com/{h}/",
                platform="instagram",
                market=Market.US,
                active=True,
                mvp=True,
            )
            session.add(ch)
            channels.append(ch)
        session.commit()
        for ch in channels:
            session.refresh(ch)
    return channels


def _ig_raw(slug: str, owner: str) -> dict:
    return {
        "url": f"https://www.instagram.com/p/{slug}/",
        "ownerUsername": owner,
        "displayUrl": f"https://cdn.example/{slug}.jpg",
        "caption": f"caption for {slug}",
        "timestamp": "2026-05-01T12:00:00Z",
    }


# Re-use the same normalize used in production so tests touch the real path.
from app.services.apify_connector import normalize_public_item  # noqa: E402


def _stub_capture(asset):
    return VisualEvidenceResult(status="captured", evidence_url=f"evidence/{asset.id}.jpg")


async def _stub_capture_async(asset):
    return _stub_capture(asset)


# --------------------------------------------------------------------------
# Block-2 Test 1 — 1 channel of 3 raises, the other two succeed.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_channel_of_three_raises_others_succeed(engine, monkeypatch):
    """Block-2 PC-3: per-channel try/except. A single defective channel
    triggers a ``failed_channels`` entry but does NOT stop the platform sync.
    The two healthy channels still produce ``created_assets`` rows."""
    seeded = _seed_channels(engine, ["good_a", "broken_b", "good_c"])
    raw_items = [
        _ig_raw("a-1", "good_a"),
        _ig_raw("a-2", "good_a"),
        _ig_raw("b-1", "broken_b"),
        _ig_raw("b-2", "broken_b"),
        _ig_raw("c-1", "good_c"),
    ]

    # The channel `broken_b` raises in Phase 1 (DB write). We simulate this
    # by patching ``_phase_a_create_post`` to raise selectively.
    from app.api import monitor as monitor_mod
    real_phase1 = monitor_mod._phase_a_create_post

    def selective_phase1(eng, *, item, channel_id, platform, only_whitelist_matches):
        # `broken_b` channel is identified by its known handle via DB lookup
        with Session(eng) as s:
            ch = s.get(Channel, channel_id)
            if ch and ch.handle == "broken_b":
                raise RuntimeError("simulated broken channel")
        return real_phase1(
            eng,
            item=item,
            channel_id=channel_id,
            platform=platform,
            only_whitelist_matches=only_whitelist_matches,
        )

    monkeypatch.setattr(monitor_mod, "_phase_a_create_post", selective_phase1)
    monkeypatch.setattr(persistence_mod, "capture_asset_screenshot_async", _stub_capture_async)

    summary = await _run_apify_sync_for_platform_async(
        engine=engine,
        channels=seeded,
        raw_items=raw_items,
        platform="instagram",
        normalize=normalize_public_item,
        only_whitelist_matches=False,
    )

    # 3 of 5 items succeeded (good_a x2, good_c x1). 2 broken_b items raised.
    assert summary["created_assets"] == 3
    assert summary["skipped_other"] == 2
    assert summary["processed_channels"] == 3

    # `broken_b` lands in failed_channels because all of its items raised.
    assert len(summary["failed_channels"]) == 1
    fc = summary["failed_channels"][0]
    assert fc["handle"] == "broken_b"
    assert fc["error_class"] == "AllItemsFailed"
    assert fc["failed_items"] == 2

    # DB confirms the 3 healthy Assets actually committed.
    with Session(engine) as s:
        rows = list(s.exec(
            __import__("sqlmodel").select(Asset)  # type: ignore[attr-defined]
        ).all())
        assert len(rows) == 3


# --------------------------------------------------------------------------
# Block-2 Test 2 — All 3 channels raise → status "completed", failed_channels[3].
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_three_channels_raise_status_completed_failed_channels_three(
    engine, monkeypatch,
):
    """Block-2 PC-3 follow-up: when every channel fails, the platform
    sync still returns a normal summary (no exception bubbles up). The
    summary's ``failed_channels`` carries one entry per channel and
    ``created_assets`` is 0. Cron's outer guard would still mark the
    overall run ``completed``, not ``failed``."""
    seeded = _seed_channels(engine, ["bad_x", "bad_y", "bad_z"])
    raw_items = [
        _ig_raw("x-1", "bad_x"),
        _ig_raw("y-1", "bad_y"),
        _ig_raw("z-1", "bad_z"),
    ]

    from app.api import monitor as monitor_mod

    def phase1_always_raises(eng, *, item, channel_id, platform, only_whitelist_matches):
        raise RuntimeError("simulated total outage")

    monkeypatch.setattr(monitor_mod, "_phase_a_create_post", phase1_always_raises)
    monkeypatch.setattr(persistence_mod, "capture_asset_screenshot_async", _stub_capture_async)

    summary = await _run_apify_sync_for_platform_async(
        engine=engine,
        channels=seeded,
        raw_items=raw_items,
        platform="instagram",
        normalize=normalize_public_item,
        only_whitelist_matches=False,
    )

    assert summary["created_assets"] == 0
    assert summary["skipped_other"] == 3  # one per item
    assert len(summary["failed_channels"]) == 3
    handles = {fc["handle"] for fc in summary["failed_channels"]}
    assert handles == {"bad_x", "bad_y", "bad_z"}
    assert all(fc["error_class"] == "AllItemsFailed" for fc in summary["failed_channels"])


# --------------------------------------------------------------------------
# Sprint FU-1 (B2-α follow-up) — zero-yield channels surface in the summary.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_yield_channels_reported(engine, monkeypatch):
    """Sprint FU-1: channels passed to the Apify actor run that came back
    with no items in the batched dataset must surface in
    ``summary["zero_yield_channels"]`` + ``summary["zero_yield_count"]``.

    Pre-FU-1 they vanished silently in ``_group_items_by_channel``. The
    motivating case is the UK-Zombie pattern from May 2026: 25 of 29
    UK-Channels returned 0 Posts in 14d, with no failed_channels entry
    and no logger.warning — undebuggable from cron summary alone.

    Three channels seeded, Apify-mock returns items for only one. We
    expect the other two in ``zero_yield_channels`` (disjoint from
    ``failed_channels``, which stays empty because there was no actual
    runtime error)."""
    seeded = _seed_channels(engine, ["yields_a", "zero_b", "zero_c"])
    raw_items = [
        _ig_raw("a-1", "yields_a"),
        _ig_raw("a-2", "yields_a"),
    ]

    monkeypatch.setattr(persistence_mod, "capture_asset_screenshot_async", _stub_capture_async)

    summary = await _run_apify_sync_for_platform_async(
        engine=engine,
        channels=seeded,
        raw_items=raw_items,
        platform="instagram",
        normalize=normalize_public_item,
        only_whitelist_matches=False,
    )

    assert summary["created_assets"] == 2
    assert summary["failed_channels"] == []
    assert summary["zero_yield_count"] == 2

    zero_yield_handles = {z["handle"] for z in summary["zero_yield_channels"]}
    assert zero_yield_handles == {"zero_b", "zero_c"}

    # Audit payload contract: each entry carries enough metadata to
    # identify the channel without needing a DB join.
    for entry in summary["zero_yield_channels"]:
        assert set(entry.keys()) == {"channel_id", "handle", "platform", "market", "url"}
        assert entry["platform"] == "instagram"
        assert entry["market"] == "Market.US"  # str(channel.market) on the enum
        assert entry["url"].endswith(f"{entry['handle']}/")


# --------------------------------------------------------------------------
# Block-2 Test 3 — asyncio.gather actually runs items concurrently.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_asset_creation_runs_in_parallel(engine, monkeypatch):
    """Block-2 PC-2: items inside a channel run concurrently via
    ``asyncio.gather``, NOT sequentially.

    The concurrency claim is carried by a deterministic in-flight counter:
    the stub records how many OpenAI calls overlap, and we assert the peak
    reaches the configured ``DEFAULT_OPENAI_CONCURRENCY`` ceiling. With 10
    items and a 0.1s stub the gather fills the semaphore before any call
    returns, so a sequential regime (peak 1) is excluded structurally —
    independent of wall-clock time.

    Flake-Fix (2026-06-29): the previous wall-clock-only assertion
    (``elapsed < 0.6``) was calibrated to the old ``openai_concurrency=5``
    (2 waves * 0.1s = ~0.2s). Block 2.5 dialed the budget to 3, moving the
    floor to ``ceil(10/3) = 4`` waves * 0.1s = ~0.4s; per-item DB/screenshot
    overhead then routinely pushed the total past 0.6s while concurrency was
    perfectly fine. The in-flight check below is timing-independent; the
    wall-clock bound is only a generous hang/deadlock guard.
    """
    seeded = _seed_channels(engine, ["fastch"])
    raw_items = [_ig_raw(f"par-{i}", "fastch") for i in range(10)]

    in_flight = 0
    max_seen = 0
    lock = asyncio.Lock()

    async def slow_counting_openai_stub(**kwargs):
        nonlocal in_flight, max_seen
        async with lock:
            in_flight += 1
            max_seen = max(max_seen, in_flight)
        # 0.1s is long enough that every slot of the semaphore is occupied
        # simultaneously before the first call returns → peak == ceiling.
        await asyncio.sleep(0.1)
        async with lock:
            in_flight -= 1
        return {
            "asset_type": None,
            "language": "en",
            "ai_summary_de": "x",
            "ai_summary_en": "y",
            "ai_trend_notes": "z",
            "confidence_score": 0.5,
            "review_status": None,
        }

    from app.api import monitor as monitor_mod
    monkeypatch.setattr(monitor_mod, "analyze_creative_text_async", slow_counting_openai_stub)
    monkeypatch.setattr(persistence_mod, "capture_asset_screenshot_async", _stub_capture_async)

    loop = asyncio.get_event_loop()
    started = loop.time()
    summary = await _run_apify_sync_for_platform_async(
        engine=engine,
        channels=seeded,
        raw_items=raw_items,
        platform="instagram",
        normalize=normalize_public_item,
        only_whitelist_matches=False,
    )
    elapsed = loop.time() - started

    assert summary["created_assets"] == 10
    # The real concurrency claim: the gather fills the OpenAI semaphore to
    # its configured ceiling. ``==`` (not ``<=``) catches BOTH an
    # under-parallel regression (peak would drop) and a semaphore breach
    # (peak would exceed). Constant imported, never hardcoded, so a future
    # budget change drags this assertion along instead of silently passing.
    assert max_seen == DEFAULT_OPENAI_CONCURRENCY, (
        f"expected peak in-flight == {DEFAULT_OPENAI_CONCURRENCY}, saw {max_seen}"
    )
    # Generous wall-clock sanity: its only job is excluding a hang/deadlock,
    # NOT proving concurrency (the in-flight check above does that). 10 *
    # 0.1s sequential would be ~1.0s; 2.0s leaves wide headroom for a loaded
    # CI box so this never flakes.
    assert elapsed < 2.0, f"sync did not complete promptly: {elapsed:.3f}s"


# --------------------------------------------------------------------------
# Block-2 Test 4 — Semaphore caps in-flight to ``openai_concurrency``.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semaphore_limits_concurrent_openai_calls(engine, monkeypatch):
    """Block-2 PC-2: with 50 items and ``openai_concurrency=5``, no more
    than 5 OpenAI calls are ever in-flight simultaneously. We track the
    in-flight count via a shared counter in the stub."""
    seeded = _seed_channels(engine, ["bigch"])
    raw_items = [_ig_raw(f"sem-{i}", "bigch") for i in range(50)]

    in_flight = 0
    max_seen = 0
    lock = asyncio.Lock()

    async def counting_openai_stub(**kwargs):
        nonlocal in_flight, max_seen
        async with lock:
            in_flight += 1
            max_seen = max(max_seen, in_flight)
        # tiny await so the scheduler interleaves
        await asyncio.sleep(0.01)
        async with lock:
            in_flight -= 1
        return {
            "asset_type": None,
            "language": "en",
            "ai_summary_de": "x",
            "ai_summary_en": "y",
            "ai_trend_notes": "z",
            "confidence_score": 0.5,
            "review_status": None,
        }

    from app.api import monitor as monitor_mod
    monkeypatch.setattr(monitor_mod, "analyze_creative_text_async", counting_openai_stub)
    monkeypatch.setattr(persistence_mod, "capture_asset_screenshot_async", _stub_capture_async)

    summary = await _run_apify_sync_for_platform_async(
        engine=engine,
        channels=seeded,
        raw_items=raw_items,
        platform="instagram",
        normalize=normalize_public_item,
        only_whitelist_matches=False,
        openai_concurrency=5,
        httpx_concurrency=DEFAULT_HTTPX_CONCURRENCY,
    )

    assert summary["created_assets"] == 50
    assert max_seen <= 5, f"semaphore breached: max in-flight was {max_seen}"
    # And it actually used some parallelism — single-thread stubs would
    # never exceed 1, which would be a degenerate test pass.
    assert max_seen >= 2, "no parallelism observed — semaphore test is degenerate"


def test_default_concurrency_constants_are_sane():
    """Guard against an accidental refactor that drops the budgets to 0
    (which would deadlock asyncio.gather under a non-zero semaphore).

    Block 2.5 dialed these from 5/10 (PR #79) down to 3/5 after the pool
    storm of 2026-05-06 09:24 UTC. If a future refactor wants to bump
    them back up, it MUST also bump ``DB_POOL_TOTAL_BUDGET`` and re-run
    the smoke test (``scripts/smoke_test_async_pool.py``)."""
    from app.api.monitor import (
        ASSET_CREATION_HTTPX_CONCURRENCY,
        ASSET_CREATION_OPENAI_CONCURRENCY,
    )

    assert DEFAULT_OPENAI_CONCURRENCY >= 1
    assert DEFAULT_HTTPX_CONCURRENCY >= 1
    # Block 2.5 spec.
    assert ASSET_CREATION_OPENAI_CONCURRENCY == 3
    assert ASSET_CREATION_HTTPX_CONCURRENCY == 5
    # Backwards-compat aliases must track the canonical names.
    assert DEFAULT_OPENAI_CONCURRENCY == ASSET_CREATION_OPENAI_CONCURRENCY
    assert DEFAULT_HTTPX_CONCURRENCY == ASSET_CREATION_HTTPX_CONCURRENCY


# --------------------------------------------------------------------------
# Block 2.5 — Pool-tuning regression guards.
#
# These two tests catch the two ways PR #79 broke production:
# 1) someone disabling pool_pre_ping (which let stale connections leak
#    into the running pool and surfaced as InvalidatePoolError after
#    the upstream Postgres reset);
# 2) someone bumping the asset-creation concurrency above the pool
#    budget (which is the original "1044 connection cycles" storm).
# --------------------------------------------------------------------------


def test_engine_pool_pre_ping_enabled():
    """The production engine MUST have ``pool_pre_ping=True``. Postgres
    bounces idle connections; without pre-ping the next checkout dies
    with "connection reset by peer" mid-cron."""
    from app.database import engine

    pool = engine.pool
    # Both QueuePool and the SQLAlchemy default pool expose _pre_ping.
    pre_ping = getattr(pool, "_pre_ping", None)
    assert pre_ping is True, (
        "pool_pre_ping must stay enabled — see Block 2.5 incident report "
        "(connection-pool storm 2026-05-06 09:24 UTC)."
    )


def test_concurrency_constants_within_pool_budget():
    """Sum of concurrent OpenAI + httpx tasks ≤ pool ceiling.

    Each in-flight task MAY hold one DB connection (Phase A or Phase C);
    if the gather fan-out exceeds ``pool_size + max_overflow`` we get
    the same connection storm that took down PR #79.

    SQLite tests don't go through QueuePool (NullPool / SingletonPool
    has its own semantics); ``DB_POOL_TOTAL_BUDGET`` is 0 there and the
    test no-ops — its real teeth are on Postgres production."""
    from app.api.monitor import (
        ASSET_CREATION_HTTPX_CONCURRENCY,
        ASSET_CREATION_OPENAI_CONCURRENCY,
    )
    from app.database import DB_POOL_TOTAL_BUDGET

    if DB_POOL_TOTAL_BUDGET == 0:
        # SQLite path — nothing to enforce; the smoke test (Postgres-only)
        # carries the real validation.
        return

    in_flight_max = (
        ASSET_CREATION_OPENAI_CONCURRENCY + ASSET_CREATION_HTTPX_CONCURRENCY
    )
    assert in_flight_max <= DB_POOL_TOTAL_BUDGET, (
        f"Concurrency budget {in_flight_max} exceeds pool budget "
        f"{DB_POOL_TOTAL_BUDGET}. Re-run smoke test before bumping."
    )
