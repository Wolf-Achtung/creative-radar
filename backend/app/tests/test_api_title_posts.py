"""HTTP-layer tests for GET /api/insights/title/{title_id}/posts
(V3 Sprint 1, Commit 3) — film-centric post list grouped by market + platform.
Shared in-memory SQLite via StaticPool, auth off — mirrors test_api_pairs.py.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import Asset, AssetType, Channel, Market, Post, ReviewStatus, Title


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


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


def _channel(session, *, handle, platform, market) -> Channel:
    ch = Channel(id=uuid4(), name=handle, handle=handle,
                 url=f"https://x/{handle}", platform=platform, market=market)
    session.add(ch)
    session.commit()
    return ch


def _post(session, channel, url, *, views=100, likes=None, comments=None,
          shares=None, duration=None) -> Post:
    post = Post(id=uuid4(), channel_id=channel.id, platform=channel.platform,
                post_url=url, visible_views=views, visible_likes=likes,
                visible_comments=comments, visible_shares=shares,
                duration_seconds=duration)
    session.add(post)
    session.commit()
    return post


def _asset(session, post, title, *, thumb=None) -> Asset:
    a = Asset(id=uuid4(), post_id=post.id, title_id=title.id if title else None,
              asset_type=AssetType.UNKNOWN, review_status=ReviewStatus.NEW,
              thumbnail_url=thumb)
    session.add(a)
    session.commit()
    return a


def _markets_map(payload):
    """{market: {platform: [posts]}} for convenient assertions."""
    out = {}
    for m in payload["markets"]:
        out[m["market"]] = {p["platform"]: p["posts"] for p in m["platforms"]}
    return out


def test_unknown_title_returns_empty_wellformed_groups(client: TestClient):
    resp = client.get(f"/api/insights/title/{uuid4()}/posts")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total_posts"] == 0
    assert payload["title_original"] is None
    # Always the three DE/US/UK columns, each with empty platforms.
    assert [m["market"] for m in payload["markets"]] == ["DE", "US", "UK"]
    assert all(m["platforms"] == [] for m in payload["markets"])


def test_groups_posts_by_market_and_platform(client: TestClient, db):
    with Session(db) as session:
        title = Title(id=uuid4(), title_original="Mortal Kombat II", active=True)
        session.add(title)
        session.commit()
        title_id = str(title.id)

        ch_de_tt = _channel(session, handle="warnerde", platform="tiktok", market=Market.DE)
        ch_de_ig = _channel(session, handle="warnerdeig", platform="instagram", market=Market.DE)
        ch_us_tt = _channel(session, handle="warnerus", platform="tiktok", market=Market.US)

        de_tt_asset = _asset(session, _post(session, ch_de_tt, "https://d/1", views=500), title, thumb="t1.jpg")
        de_tt_asset_id = str(de_tt_asset.id)
        _asset(session, _post(session, ch_de_ig, "https://d/2", views=300), title)
        _asset(session, _post(session, ch_us_tt, "https://u/1", views=900), title)

    resp = client.get(f"/api/insights/title/{title_id}/posts")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["title_id"] == title_id
    assert payload["title_original"] == "Mortal Kombat II"
    assert payload["total_posts"] == 3

    mm = _markets_map(payload)
    assert set(mm["DE"].keys()) == {"tiktok", "instagram"}
    assert len(mm["DE"]["tiktok"]) == 1
    assert mm["DE"]["tiktok"][0]["views"] == 500
    assert mm["DE"]["tiktok"][0]["thumbnail_url"] == "t1.jpg"
    assert mm["DE"]["tiktok"][0]["market"] == "DE"
    # V3 Sprint 2: asset_id ist gesetzt → Frontend kann /api/thumbnails/{id} nutzen.
    assert mm["DE"]["tiktok"][0]["asset_id"] == de_tt_asset_id
    assert len(mm["US"]["tiktok"]) == 1
    assert mm["UK"] == {}  # no UK posts -> empty bucket


def test_dedupes_post_with_multiple_assets(client: TestClient, db):
    with Session(db) as session:
        title = Title(id=uuid4(), title_original="Solo", active=True)
        session.add(title)
        session.commit()
        title_id = str(title.id)
        ch = _channel(session, handle="warnerde", platform="tiktok", market=Market.DE)
        post = _post(session, ch, "https://d/dup", views=42)
        _asset(session, post, title)
        _asset(session, post, title)  # second asset, same post + title

    resp = client.get(f"/api/insights/title/{title_id}/posts")
    payload = resp.json()
    assert payload["total_posts"] == 1  # counted once


def _seed_three_de_tiktok(session):
    """Drei DE/TikTok-Posts mit unterschiedlichen Metriken im selben Bucket,
    plus ein views=0-Post (Engagement-Rate 0 → NULLS LAST)."""
    title = Title(id=uuid4(), title_original="Ranktest", active=True)
    session.add(title)
    session.commit()
    ch = _channel(session, handle="warnerde", platform="tiktok", market=Market.DE)
    # url -> (views, likes, comments): rate = (likes+comments)/views
    _asset(session, _post(session, ch, "https://d/a", views=1000, likes=10, comments=10), title)  # rate 0.02
    _asset(session, _post(session, ch, "https://d/b", views=200, likes=40, comments=20), title)   # rate 0.30
    _asset(session, _post(session, ch, "https://d/c", views=5000, likes=100, comments=50), title)  # rate 0.03
    _asset(session, _post(session, ch, "https://d/z", views=0, likes=5, comments=5), title)        # rate 0.0
    return str(title.id)


def _de_tiktok_urls(payload):
    return [p["post_url"] for p in _markets_map(payload)["DE"]["tiktok"]]


def test_schema_carries_engagement_fields_and_computed_rate(client: TestClient, db):
    with Session(db) as session:
        title = Title(id=uuid4(), title_original="Fields", active=True)
        session.add(title)
        session.commit()
        title_id = str(title.id)
        ch = _channel(session, handle="warnerde", platform="tiktok", market=Market.DE)
        _asset(session, _post(session, ch, "https://d/1", views=200, likes=40,
                              comments=20, shares=7, duration=15), title)

    payload = client.get(f"/api/insights/title/{title_id}/posts").json()
    post = _markets_map(payload)["DE"]["tiktok"][0]
    # Regression: bestehende 7 Felder unverändert vorhanden.
    for key in ("post_url", "platform", "market", "asset_id", "thumbnail_url",
                "views", "published_at"):
        assert key in post
    # Neue Felder durchgereicht + Rate berechnet = (40+20)/200 = 0.3.
    assert post["likes"] == 40
    assert post["comments"] == 20
    assert post["shares"] == 7
    assert post["duration_seconds"] == 15
    assert post["engagement_rate"] == pytest.approx(0.3)


def test_default_sort_is_engagement_rate_views_zero_last(client: TestClient, db):
    with Session(db) as session:
        title_id = _seed_three_de_tiktok(session)
    # Kein sort-Param → Default engagement: b(0.30) > c(0.03) > a(0.02) > z(0.0).
    payload = client.get(f"/api/insights/title/{title_id}/posts").json()
    assert _de_tiktok_urls(payload) == ["https://d/b", "https://d/c", "https://d/a", "https://d/z"]


def test_sort_views_descending(client: TestClient, db):
    with Session(db) as session:
        title_id = _seed_three_de_tiktok(session)
    payload = client.get(f"/api/insights/title/{title_id}/posts?sort=views").json()
    # views: c(5000) > a(1000) > b(200) > z(0).
    assert _de_tiktok_urls(payload) == ["https://d/c", "https://d/a", "https://d/b", "https://d/z"]


def test_sort_likes_descending(client: TestClient, db):
    with Session(db) as session:
        title_id = _seed_three_de_tiktok(session)
    payload = client.get(f"/api/insights/title/{title_id}/posts?sort=likes").json()
    # likes: c(100) > b(40) > a(10) > z(5).
    assert _de_tiktok_urls(payload) == ["https://d/c", "https://d/b", "https://d/a", "https://d/z"]


def test_invalid_sort_falls_back_to_engagement(client: TestClient, db):
    with Session(db) as session:
        title_id = _seed_three_de_tiktok(session)
    payload = client.get(f"/api/insights/title/{title_id}/posts?sort=bogus").json()
    assert _de_tiktok_urls(payload) == ["https://d/b", "https://d/c", "https://d/a", "https://d/z"]


def test_zero_and_null_views_no_division_error_rate_zero(client: TestClient, db):
    with Session(db) as session:
        title = Title(id=uuid4(), title_original="ZeroViews", active=True)
        session.add(title)
        session.commit()
        title_id = str(title.id)
        ch = _channel(session, handle="warnerde", platform="tiktok", market=Market.DE)
        _asset(session, _post(session, ch, "https://d/zero", views=0, likes=5, comments=5), title)
        _asset(session, _post(session, ch, "https://d/null", views=None, likes=3, comments=3), title)

    payload = client.get(f"/api/insights/title/{title_id}/posts").json()
    by_url = {p["post_url"]: p for p in _markets_map(payload)["DE"]["tiktok"]}
    assert by_url["https://d/zero"]["engagement_rate"] == 0.0
    assert by_url["https://d/null"]["engagement_rate"] == 0.0


def test_excludes_int_and_mixed_markets(client: TestClient, db):
    with Session(db) as session:
        title = Title(id=uuid4(), title_original="Globe", active=True)
        session.add(title)
        session.commit()
        title_id = str(title.id)
        ch_int = _channel(session, handle="intl", platform="tiktok", market=Market.INT)
        ch_de = _channel(session, handle="warnerde", platform="tiktok", market=Market.DE)
        _asset(session, _post(session, ch_int, "https://i/1"), title)
        _asset(session, _post(session, ch_de, "https://d/1"), title)

    resp = client.get(f"/api/insights/title/{title_id}/posts")
    payload = resp.json()
    # INT post dropped; only the DE one counts in the three-column view.
    assert payload["total_posts"] == 1
    mm = _markets_map(payload)
    assert len(mm["DE"]["tiktok"]) == 1
