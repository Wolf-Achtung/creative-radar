"""Platin 4 (2026-07-13) — Breakout-Feed über alle Pairs.

Verifiziert ``compute_breakout_feed`` (Service-Ebene: sammelt
``ChannelStats.breakouts`` über alle aktivierten Pairs/Plattformen/Märkte
ein, filtert auf ``multiplier >= min_multiplier``, sortiert nach
``weighted_score`` desc) sowie ``GET /api/admin/breakouts`` (Routing).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.database import get_session
from app.main import app
from app.services import insight_engine as engine_module
from app.services.insight_engine import compute_breakout_feed
from app.schemas.insights import (
    BreakoutScore,
    ChannelStats,
    PairAggregation,
    PlatformAggregation,
    RankedPost,
    TitleCoverage,
)


def _engine_for_path(path: str):
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_breakout_feed_", suffix=".db")
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


def _empty_title_coverage() -> TitleCoverage:
    return TitleCoverage.model_construct(
        titles_in_both_markets=[], de_only_titles=[], us_only_titles=[],
        de_assets_with_title=0, de_assets_total=0, us_assets_with_title=0,
        us_assets_total=0, uk_only_titles=[], uk_assets_with_title=0,
        uk_assets_total=0, overall_coverage_pct=0.0,
    )


def _ranked_post_with_score(post_url: str, *, multiplier: float, weighted_score: float, views: int = 1000) -> RankedPost:
    return RankedPost.model_construct(
        post_url=post_url, caption_excerpt=f"caption {post_url}", platform="tiktok",
        published_at=datetime(2026, 5, 10, tzinfo=timezone.utc), duration_seconds=20,
        views=views, likes=10, comments=1, saves=0, shares=0,
        engagement_sum=11, activation_rate=0.01,
        title_local=None, title_original=None, franchise=None,
        thumbnail_url=None, content_type=None, asset_id=None,
        breakout_score=BreakoutScore(
            z_score=weighted_score, multiplier=multiplier, weighted_score=weighted_score,
            decay_weight=1.0, baseline_mean=100.0, baseline_std=10.0, sample_size=6,
        ),
    )


def _channel_with_breakouts(handle: str, market: str, breakouts: list[RankedPost]) -> ChannelStats:
    return ChannelStats.model_construct(
        handle=handle, market=market, channel_id=f"{handle}-id", channel_found=True,
        posts_count=len(breakouts) + 4, assets_count=len(breakouts) + 4, coverage_pct=0.0,
        top_hashtags=[], avg_caption_length=0.0, avg_duration_seconds=None,
        duration_buckets={}, top_posts=[], avg_engagement=0.0, avg_activation_rate=0.0,
        historical_top_posts=[], ranked_posts=breakouts, breakouts=breakouts,
    )


def _aggregation_with_breakouts(pair_key: str, de_breakouts: list[RankedPost]) -> PairAggregation:
    de_channel = _channel_with_breakouts(pair_key, "DE", de_breakouts)
    platform_agg = PlatformAggregation.model_construct(
        platform="tiktok", de_channel=de_channel, us_channel=None, uk_channel=None,
        cross_market_matches=[], title_coverage=_empty_title_coverage(), notes=[],
    )
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    return PairAggregation.model_construct(
        pair_key=pair_key, pair_label=f"{pair_key} test", platform="tiktok",
        window_days=30, window_start=now - timedelta(days=30), window_end=now,
        iso_week=20, iso_year=2026,
        de_channel=de_channel, us_channel=None, uk_channel=None,
        cross_market_matches=[], title_coverage=_empty_title_coverage(),
        notes=[], per_platform=[platform_agg],
    )


def test_compute_breakout_feed_filters_by_multiplier_and_sorts(monkeypatch, db):
    monkeypatch.setitem(engine_module.PAIRS, "pair-a", {"label": "Pair A", "enabled": True})
    monkeypatch.setitem(engine_module.PAIRS, "pair-b", {"label": "Pair B", "enabled": True})
    monkeypatch.setitem(engine_module.PAIRS, "pair-disabled", {"label": "Disabled", "enabled": False})

    fixtures = {
        "pair-a": _aggregation_with_breakouts("pair-a", [
            _ranked_post_with_score("https://x/a1", multiplier=1.5, weighted_score=1.0),  # below threshold
            _ranked_post_with_score("https://x/a2", multiplier=3.0, weighted_score=2.5),
        ]),
        "pair-b": _aggregation_with_breakouts("pair-b", [
            _ranked_post_with_score("https://x/b1", multiplier=5.0, weighted_score=4.0),
        ]),
    }

    def fake_aggregate_pair(session, pair_key, *, window_days=30, now=None):
        if pair_key == "pair-disabled":
            raise AssertionError("disabled pair must not be aggregated")
        return fixtures[pair_key]

    monkeypatch.setattr(engine_module, "aggregate_pair", fake_aggregate_pair)

    with Session(db) as session:
        entries = compute_breakout_feed(session, min_multiplier=2.0)

    # Nur die beiden >= 2.0x-Posts, sortiert nach weighted_score desc.
    assert [e["post_url"] for e in entries] == ["https://x/b1", "https://x/a2"]
    assert entries[0]["pair_label"] == "Pair B"
    assert entries[0]["multiplier"] == 5.0
    assert entries[0]["market"] == "DE"


def test_compute_breakout_feed_respects_limit(monkeypatch, db):
    posts = [_ranked_post_with_score(f"https://x/p{i}", multiplier=3.0, weighted_score=float(10 - i)) for i in range(5)]
    monkeypatch.setitem(engine_module.PAIRS, "pair-many", {"label": "Pair Many", "enabled": True})
    monkeypatch.setattr(
        engine_module, "aggregate_pair",
        lambda session, pk, *, window_days=30, now=None: _aggregation_with_breakouts(pk, posts),
    )
    with Session(db) as session:
        entries = compute_breakout_feed(session, min_multiplier=2.0, limit=2)
    assert len(entries) == 2
    assert entries[0]["post_url"] == "https://x/p0"


def test_compute_breakout_feed_skips_pair_on_aggregation_error(monkeypatch, db):
    monkeypatch.setitem(engine_module.PAIRS, "pair-broken", {"label": "Broken", "enabled": True})
    monkeypatch.setitem(engine_module.PAIRS, "pair-ok", {"label": "OK", "enabled": True})

    def fake_aggregate_pair(session, pair_key, *, window_days=30, now=None):
        if pair_key == "pair-broken":
            raise RuntimeError("boom")
        if pair_key == "pair-ok":
            return _aggregation_with_breakouts(pair_key, [
                _ranked_post_with_score("https://x/ok1", multiplier=3.0, weighted_score=1.0),
            ])
        # Andere (echte, aus dem Produktions-PAIRS-Dict geerbte) Pairs
        # sollen den Test nicht verfaelschen — Fehler statt Fixture-Daten,
        # via try/except in compute_breakout_feed sauber uebersprungen.
        raise RuntimeError(f"unexpected pair in test: {pair_key!r}")

    monkeypatch.setattr(engine_module, "aggregate_pair", fake_aggregate_pair)
    with Session(db) as session:
        entries = compute_breakout_feed(session, min_multiplier=2.0)
    assert [e["post_url"] for e in entries] == ["https://x/ok1"]


@pytest.fixture
def client(db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)

    def _override_session():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_breakouts_route_returns_feed(client, monkeypatch, db):
    import app.api.admin as admin_module

    def fake_compute_breakout_feed(session, *, window_days=30, now=None, limit=20, min_multiplier=2.0):
        return [{
            "pair_key": "netflix", "pair_label": "Netflix test", "platform": "tiktok",
            "market": "DE", "post_url": "https://x/1", "caption_excerpt": "cap",
            "views": 1000, "engagement_sum": 50, "published_at": None,
            "multiplier": 3.2, "weighted_score": 2.1, "z_score": 2.0,
        }]

    monkeypatch.setattr(admin_module, "compute_breakout_feed", fake_compute_breakout_feed)

    resp = client.get("/api/admin/breakouts?limit=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["entries"][0]["pair_key"] == "netflix"


def test_breakouts_route_empty_feed(client, monkeypatch, db):
    import app.api.admin as admin_module
    monkeypatch.setattr(
        admin_module, "compute_breakout_feed",
        lambda session, *, window_days=30, now=None, limit=20, min_multiplier=2.0: [],
    )
    resp = client.get("/api/admin/breakouts")
    assert resp.status_code == 200
    assert resp.json() == {"count": 0, "min_multiplier": 2.0, "entries": []}
