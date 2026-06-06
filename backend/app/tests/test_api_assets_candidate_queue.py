"""HTTP-layer tests for GET /api/assets — candidate_queue default + pagination
+ N+1-Batch (Performance-Fix "Treffer prüfen").

Shared in-memory SQLite via StaticPool, auth off — mirrors test_api_title_posts.py.
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
from app.models.entities import (
    Asset,
    AssetType,
    CandidateStatus,
    Channel,
    Market,
    Post,
    ReviewStatus,
    Title,
    TitleCandidate,
)


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


def _channel(session, *, handle="warnerde", market=Market.DE) -> Channel:
    ch = Channel(id=uuid4(), name=handle, handle=handle,
                 url=f"https://x/{handle}", platform="tiktok", market=market)
    session.add(ch)
    session.commit()
    return ch


def _asset(session, channel, *, title=None, status=ReviewStatus.NEW) -> Asset:
    post = Post(id=uuid4(), channel_id=channel.id, platform="tiktok",
                post_url=f"https://x/{uuid4()}", visible_views=10)
    session.add(post)
    session.commit()
    a = Asset(id=uuid4(), post_id=post.id, title_id=title.id if title else None,
              asset_type=AssetType.UNKNOWN, review_status=status)
    session.add(a)
    session.commit()
    return a


def _candidate(session, asset, *, status=CandidateStatus.OPEN, suggested="Wicked") -> TitleCandidate:
    c = TitleCandidate(id=uuid4(), asset_id=asset.id, suggested_title=suggested,
                       confidence=0.8, status=status)
    session.add(c)
    session.commit()
    return c


def test_candidate_queue_returns_only_assets_with_open_candidate(client, db):
    with Session(db) as session:
        ch = _channel(session)
        a_open = _asset(session, ch)
        a_open_id = str(a_open.id)
        _candidate(session, a_open, status=CandidateStatus.OPEN)
        a_resolved = _asset(session, ch)
        _candidate(session, a_resolved, status=CandidateStatus.RESOLVED)
        _asset(session, ch)  # no candidate at all

    resp = client.get("/api/assets?candidate_queue=true")
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()}
    assert ids == {a_open_id}  # only the OPEN-candidate asset


def test_candidate_queue_includes_already_assigned_asset(client, db):
    """Andor-WARN-Fall: ein bereits (falsch) zugeordnetes Asset mit OPEN-
    Candidate erscheint weiterhin in der Queue (Frontend zeigt WARN)."""
    with Session(db) as session:
        ch = _channel(session)
        title = Title(id=uuid4(), title_original="Andor", active=True)
        session.add(title)
        session.commit()
        a = _asset(session, ch, title=title)
        a_id = str(a.id)
        title_id = str(title.id)
        _candidate(session, a, status=CandidateStatus.OPEN, suggested="Wicked")

    resp = client.get("/api/assets?candidate_queue=true")
    rows = resp.json()
    assert [r["id"] for r in rows] == [a_id]
    # Batch-Join lieferte Channel + Title korrekt (kein N+1-Verlust).
    assert rows[0]["channel_name"] == "warnerde"
    assert rows[0]["title_id"] == title_id


def test_pagination_limits_and_offsets(client, db):
    with Session(db) as session:
        ch = _channel(session)
        for _ in range(5):
            _asset(session, ch)

    first = client.get("/api/assets?limit=2&offset=0").json()
    second = client.get("/api/assets?limit=2&offset=2").json()
    assert len(first) == 2
    assert len(second) == 2
    # newest-first ordering → disjoint pages
    assert {r["id"] for r in first}.isdisjoint({r["id"] for r in second})


def test_default_limit_caps_payload(client, db):
    with Session(db) as session:
        ch = _channel(session)
        for _ in range(60):
            _asset(session, ch)

    rows = client.get("/api/assets").json()
    assert len(rows) == 50  # default limit, not all 60
