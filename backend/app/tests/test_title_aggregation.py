"""Tests for aggregate_title — title-centric aggregation across
platforms/markets/channels/pairs. In-memory sqlite, no LLM."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Asset, AssetType, Channel, Market, Post, ReviewStatus, Title
from app.services.title_aggregation import (
    AmbiguousTitleError,
    _resolve_title,
    aggregate_title,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _channel(session, *, handle, platform, market) -> Channel:
    ch = Channel(
        id=uuid4(), name=f"{handle}-name", handle=handle,
        url=f"https://example.com/{handle}", platform=platform, market=market,
    )
    session.add(ch)
    session.commit()
    return ch


def _post_with_asset(session, *, channel, title, detected_at,
                     likes, comments, shares, saves, views, n_assets=1) -> Post:
    post = Post(
        id=uuid4(), channel_id=channel.id, platform=channel.platform,
        post_url=f"https://example.com/p/{uuid4().hex[:8]}",
        detected_at=detected_at,
        visible_likes=likes, visible_comments=comments, visible_shares=shares,
        visible_bookmarks=saves, visible_views=views,
    )
    session.add(post)
    session.commit()
    for _ in range(n_assets):  # several assets of the SAME title on one post
        session.add(Asset(
            id=uuid4(), post_id=post.id, title_id=title.id,
            asset_type=AssetType.UNKNOWN, review_status=ReviewStatus.NEW,
        ))
    session.commit()
    return post


def _title(session) -> Title:
    t = Title(id=uuid4(), title_original="Test Movie", content_type="Film",
              franchise="Test Franchise", tmdb_id=999)
    session.add(t)
    session.commit()
    return t


def test_aggregate_title_groups_by_platform_market_channel(session):
    now = datetime(2026, 6, 4, tzinfo=timezone.utc)
    title = _title(session)
    # "warnerbros" is a real PAIRS handle -> exercises pair mapping.
    ch_tt_de = _channel(session, handle="warnerbros", platform="tiktok", market=Market.DE)
    ch_ig_us = _channel(session, handle="someig", platform="instagram", market=Market.US)

    # tiktok/DE: two posts (one carries 2 assets of the same title -> dedupe).
    _post_with_asset(session, channel=ch_tt_de, title=title, detected_at=now - timedelta(days=2),
                     likes=100, comments=10, shares=5, saves=5, views=1000, n_assets=2)  # eng 120
    _post_with_asset(session, channel=ch_tt_de, title=title, detected_at=now - timedelta(days=3),
                     likes=40, comments=5, shares=0, saves=0, views=500)  # eng 45
    # instagram/US: one strong post.
    _post_with_asset(session, channel=ch_ig_us, title=title, detected_at=now - timedelta(days=1),
                     likes=200, comments=20, shares=0, saves=10, views=2000)  # eng 230
    # out-of-window old post (60d) -> excluded from windowed stats, counts in span.
    _post_with_asset(session, channel=ch_tt_de, title=title, detected_at=now - timedelta(days=60),
                     likes=1, comments=1, shares=0, saves=0, views=10)

    agg = aggregate_title(session, "Test Movie", window_days=30, now=now)
    assert agg is not None

    # Stammdaten
    assert agg.title_original == "Test Movie"
    assert agg.content_type == "Film"
    assert agg.franchise == "Test Franchise"

    # Window vs full span
    assert agg.total_posts == 3            # old post excluded
    assert agg.total_posts_all_time == 4   # span counts everything
    assert agg.first_post_at == now - timedelta(days=60)
    assert agg.last_post_at == now - timedelta(days=1)

    # Dedupe: the 2-asset post counts once.
    assert agg.total_engagement == 120 + 45 + 230

    # Per platform
    plat = {p.platform: p for p in agg.platforms}
    assert plat["tiktok"].post_count == 2
    assert plat["tiktok"].engagement_sum == 165
    assert plat["instagram"].post_count == 1
    assert plat["instagram"].engagement_sum == 230
    assert plat["instagram"].top_post.engagement_sum == 230

    # Per market
    mkt = {m.market: m for m in agg.markets}
    assert mkt["DE"].post_count == 2 and mkt["DE"].engagement_sum == 165
    assert mkt["US"].post_count == 1 and mkt["US"].engagement_sum == 230

    # Channels + cross-pair mapping
    handles = {c.channel_handle: c for c in agg.channels}
    assert handles["warnerbros"].post_count == 2
    assert "warnerbros" in handles["warnerbros"].pair_keys  # mapped via PAIRS
    assert handles["someig"].pair_keys == []                # not in PAIRS

    # Top post overall is the IG one.
    assert agg.top_posts[0].engagement_sum == 230
    assert agg.pair_keys == ["warnerbros"]

    # Weekly buckets present (3 windowed posts across a couple of weeks).
    assert sum(w.post_count for w in agg.weekly) == 3


def test_aggregate_title_returns_none_for_unknown(session):
    _title(session)
    assert aggregate_title(session, "No Such Title", now=datetime(2026, 6, 4, tzinfo=timezone.utc)) is None


def test_aggregate_title_resolves_by_substring(session):
    title = _title(session)
    now = datetime(2026, 6, 4, tzinfo=timezone.utc)
    ch = _channel(session, handle="warnerbros", platform="tiktok", market=Market.DE)
    _post_with_asset(session, channel=ch, title=title, detected_at=now - timedelta(days=1),
                     likes=10, comments=1, shares=0, saves=0, views=100)
    # substring match ("Test" -> "Test Movie")
    agg = aggregate_title(session, "Test", now=now)
    assert agg is not None and agg.title_original == "Test Movie"


# --- _resolve_title: deterministic, ambiguity -> error (Defekt 1) ----------

def _named_title(session, *, name, tmdb_id) -> Title:
    t = Title(id=uuid4(), title_original=name, content_type="Film", tmdb_id=tmdb_id)
    session.add(t)
    session.commit()
    return t


def test_resolve_exact_unique(session):
    t = _named_title(session, name="Solo Title", tmdb_id=1)
    assert _resolve_title(session, "Solo Title").id == t.id
    # case-insensitive
    assert _resolve_title(session, "solo title").id == t.id


def test_resolve_by_uuid_and_uuid_string(session):
    t = _named_title(session, name="By Id", tmdb_id=2)
    assert _resolve_title(session, t.id).id == t.id
    assert _resolve_title(session, str(t.id)).id == t.id


def test_resolve_two_exact_raises_ambiguous(session):
    a = _named_title(session, name="Dup", tmdb_id=10)
    b = _named_title(session, name="dup", tmdb_id=11)  # same name, diff case
    with pytest.raises(AmbiguousTitleError) as exc:
        _resolve_title(session, "Dup")
    ids = {c["title_id"] for c in exc.value.candidates}
    assert ids == {str(a.id), str(b.id)}
    # candidate carries tmdb_id for disambiguation
    assert {c["tmdb_id"] for c in exc.value.candidates} == {10, 11}


def test_resolve_single_substring(session):
    t = _named_title(session, name="The Matrix Reloaded", tmdb_id=20)
    assert _resolve_title(session, "Reloaded").id == t.id


def test_resolve_multi_substring_no_exact_raises_ambiguous(session):
    a = _named_title(session, name="Mortal Kombat", tmdb_id=460465)
    b = _named_title(session, name="Mortal Kombat II", tmdb_id=931285)
    with pytest.raises(AmbiguousTitleError) as exc:
        _resolve_title(session, "Mortal")  # no exact, two substrings
    ids = {c["title_id"] for c in exc.value.candidates}
    assert ids == {str(a.id), str(b.id)}


def test_resolve_no_match_returns_none(session):
    _named_title(session, name="Only One", tmdb_id=30)
    assert _resolve_title(session, "Nope") is None


def test_resolve_mk_exact_is_deterministic(session):
    """Exact 'Mortal Kombat' resolves the 2021 film deterministically even
    though 'Mortal Kombat II' shares the substring; MKII is only reachable via
    its exact name / tmdb_id / title_id, never an alphabetical guess."""
    mk = _named_title(session, name="Mortal Kombat", tmdb_id=460465)
    mk2 = _named_title(session, name="Mortal Kombat II", tmdb_id=931285)
    assert _resolve_title(session, "Mortal Kombat").id == mk.id
    assert _resolve_title(session, "Mortal Kombat II").id == mk2.id
