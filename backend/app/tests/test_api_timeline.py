"""HTTP-layer tests for GET /api/insights/timeline (V3 Sprint 6) — the
descriptive per-market weekly time series. Variante A: metrics are computed
FRESH per discrete ISO week from the Post tables, not from the persisted
30-day-rolling brief aggregates. insight_report rows only define the week
axis. Shared in-memory SQLite, auth off — mirrors test_api_title_posts.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.database import get_session
from app.main import app
from app.api.insights import _handles_for_pair, _iso_week_monday
from app.models.entities import Channel, InsightReport as InsightReportRow, Market, Post
from app.services.insight_engine import PAIRS

PAIR = "warnerbros"


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


def _pool_handles() -> list[str]:
    handles = sorted(set(_handles_for_pair(PAIRS[PAIR])))
    assert len(handles) >= 2, "warnerbros pair needs >=2 channel handles for the test"
    return handles


def _channel(session, *, handle, market) -> Channel:
    ch = Channel(id=uuid4(), name=handle, handle=handle,
                 url=f"https://x/{handle}", platform="tiktok", market=market)
    session.add(ch)
    session.commit()
    return ch


def _brief(session, iso_year, iso_week) -> None:
    session.add(InsightReportRow(
        pair_key=PAIR, iso_year=iso_year, iso_week=iso_week,
        aggregation={}, llm_output={},
        generated_at=datetime.now(timezone.utc), model="test",
    ))
    session.commit()


def _post(session, channel, iso_year, iso_week, *, views, likes=0, comments=0) -> None:
    # published_at = Dienstag der ISO-Woche (sicher im Wochen-Bucket).
    pub = _iso_week_monday(iso_year, iso_week) + timedelta(days=1, hours=12)
    session.add(Post(id=uuid4(), channel_id=channel.id, platform="tiktok",
                     post_url=f"https://x/p/{uuid4()}", published_at=pub,
                     visible_views=views, visible_likes=likes,
                     visible_comments=comments))
    session.commit()


def test_timeline_gapless_axis_and_fresh_metrics(client, db):
    """Briefe in KW 19, 20, 22 (Lücke bei 21) → lückenlose Achse [19,20,21,22];
    Metriken pro KW frisch aus den Posts; ER = Σ(likes+comments)/Σ(views)."""
    h = _pool_handles()
    with Session(db) as s:
        de = _channel(s, handle=h[0], market=Market.DE)
        us = _channel(s, handle=h[1], market=Market.US)
        for wk in (19, 20, 22):
            _brief(s, 2026, wk)
        # DE: KW19 zwei Posts → Σviews=1000+0=1000, ER=(80+20)/1000=0.1
        _post(s, de, 2026, 19, views=1000, likes=80, comments=20)
        _post(s, de, 2026, 19, views=0, likes=5, comments=5)   # views=0 → nicht im ER-Nenner
        # US: KW20 ein Post → views=500, ER=10/500=0.02
        _post(s, us, 2026, 20, views=500, likes=10, comments=0)

    r = client.get(f"/api/insights/timeline?pair={PAIR}")
    assert r.status_code == 200
    body = r.json()
    assert body["pair_key"] == PAIR

    # Lückenlose Achse 19..22, Lücke (21) NICHT zusammengeschoben.
    axis = [(w["iso_year"], w["iso_week"]) for w in body["weeks"]]
    assert axis == [(2026, 19), (2026, 20), (2026, 21), (2026, 22)]

    assert set(body["markets"].keys()) == {"DE", "US", "UK"}
    for m in ("DE", "US", "UK"):
        assert len(body["markets"][m]) == len(axis)  # positionsgleich zur Achse

    de_pts = body["markets"]["DE"]
    assert de_pts[0]["views"] == 1000 and de_pts[0]["posts"] == 2
    assert abs(de_pts[0]["er"] - 0.1) < 1e-9          # views=0-Post aus ER-Nenner raus
    # KW20/21/22 für DE leer:
    assert de_pts[1]["views"] == 0 and de_pts[1]["er"] is None and de_pts[1]["posts"] == 0
    assert de_pts[2]["views"] == 0 and de_pts[2]["er"] is None  # Lücken-KW 21

    us_pts = body["markets"]["US"]
    assert us_pts[1]["views"] == 500 and abs(us_pts[1]["er"] - 0.02) < 1e-9
    # UK hat keine Channels/Posts → durchgehend leer, kein Crash:
    assert all(p["views"] == 0 and p["er"] is None for p in body["markets"]["UK"])


def test_timeline_weeks_param_limits_to_last_n(client, db):
    h = _pool_handles()
    with Session(db) as s:
        _channel(s, handle=h[0], market=Market.DE)
        for wk in (19, 20, 21, 22):
            _brief(s, 2026, wk)

    r = client.get(f"/api/insights/timeline?pair={PAIR}&weeks=2")
    assert r.status_code == 200
    axis = [(w["iso_year"], w["iso_week"]) for w in r.json()["weeks"]]
    assert axis == [(2026, 21), (2026, 22)]  # nur die letzten 2 Achsen-Wochen


def test_timeline_no_briefs_returns_empty(client, db):
    r = client.get(f"/api/insights/timeline?pair={PAIR}")
    assert r.status_code == 200
    body = r.json()
    assert body["weeks"] == []
    assert body["markets"] == {"DE": [], "US": [], "UK": []}


def test_timeline_unknown_pair_404(client):
    r = client.get("/api/insights/timeline?pair=does-not-exist")
    assert r.status_code == 404
