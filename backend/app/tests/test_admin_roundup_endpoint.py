"""Tests fuer den manuellen Segment-Roundup-Trigger-Endpoint
(``POST /api/admin/roundups/generate``) — Master-Plan-Schritt-3 Pilot.

Vier Garantien:

1. Feature-Flag-Gate: bei ``FEATURE_SEGMENT_ROUNDUPS_ENABLED != "true"``
   liefert der Endpoint 503. Wolf-Festlegung 25.05.: Pilot-Auslosse-
   Pfad ist via Env-Toggle ein-/ausschaltbar ohne Deploy.
2. Unbekanntes Segment liefert 404 mit klarer Fehlermeldung.
3. Gueltiger Trigger: Endpoint ruft ``generate_and_persist_roundup``
   und gibt das Audit-Payload (channels_evaluated, total_posts,
   cost_usd_cents, …) zurueck.
4. Auth: Endpoint sitzt hinter der globalen Bearer-Middleware
   (gleiche Auth-Mechanik wie ``/api/admin/insights/regenerate``).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import Channel, ChannelSegment, Post, SegmentRoundup
from app.services import anthropic_client as anthropic_module
from app.services import segment_roundup as roundup_module


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_roundup_endpoint_", suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )
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
def client(db, monkeypatch: pytest.MonkeyPatch):
    """TestClient mit Bearer-Auth aktiv (gleiche Mechanik wie die anderen
    admin-endpoint-Tests). Default: Feature-Flag aus — pro Test bei
    Bedarf via ``monkeypatch.setenv`` aktivieren."""
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "TESTTOKEN", raising=False)
    # Feature-Flag konsequent off, damit Tests ihre eigene Einstellung
    # explizit machen (default-off entspricht Production-Default).
    monkeypatch.delenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", raising=False)

    def _override():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed_channel(db_engine, *, handle: str, segment: ChannelSegment | None,
                  active: bool = True) -> Channel:
    with Session(db_engine) as session:
        ch = Channel(
            id=uuid4(),
            name=handle,
            handle=handle,
            url=f"https://www.instagram.com/{handle}",
            platform="instagram",
            market="US",
            active=active,
            mvp=True,
            segment=segment,
        )
        session.add(ch)
        session.commit()
        session.refresh(ch)
        return ch


def _seed_post(db_engine, channel_id, *, days_ago: int, engagement: int) -> None:
    with Session(db_engine) as session:
        post = Post(
            id=uuid4(),
            channel_id=channel_id,
            post_url=f"https://example.com/{uuid4().hex[:8]}",
            platform="instagram",
            published_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
            detected_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
            caption=f"test #cinema {uuid4().hex[:4]}",
            visible_likes=engagement,
            visible_comments=0,
            visible_bookmarks=0,
            visible_shares=0,
            visible_views=engagement * 10,
            duration_seconds=20,
            raw_payload={},
        )
        session.add(post)
        session.commit()


def _patch_anthropic_ok(monkeypatch) -> MagicMock:
    body = {
        "headline": "endpoint test",
        "tldr": "kurze Synthese",
        "what_ran": ["trailer", "still"],
        "channels_in_focus": ["@a24"],
        "themes": ["festival"],
        "data_caveats": ["1 channel ohne posts"],
    }
    text_block = SimpleNamespace(type="text", text=json.dumps(body))
    usage = SimpleNamespace(input_tokens=2500, output_tokens=400)
    message = SimpleNamespace(content=[text_block], usage=usage)
    mock = MagicMock(return_value=message)
    monkeypatch.setattr(anthropic_module, "messages_create_text", mock)
    monkeypatch.setattr(roundup_module, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(
        roundup_module, "record_anthropic_call",
        lambda *a, **kw: None,
    )
    return mock


# ---------------------------------------------------------------------------
# Test 1 — Feature-Flag off → 503
# ---------------------------------------------------------------------------

def test_endpoint_returns_503_when_feature_flag_off(client, db, monkeypatch):
    monkeypatch.delenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", raising=False)
    response = client.post(
        "/api/admin/roundups/generate",
        params={"segment": "us_major"},
        headers={"Authorization": "Bearer TESTTOKEN"},
    )
    assert response.status_code == 503
    assert "FEATURE_SEGMENT_ROUNDUPS_ENABLED" in response.text


# ---------------------------------------------------------------------------
# Test 2 — Unbekanntes Segment → 404
# ---------------------------------------------------------------------------

def test_endpoint_returns_404_for_unknown_segment(client, monkeypatch):
    monkeypatch.setenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", "true")
    response = client.post(
        "/api/admin/roundups/generate",
        params={"segment": "fr_major"},
        headers={"Authorization": "Bearer TESTTOKEN"},
    )
    assert response.status_code == 404
    body = response.json()
    assert "fr_major" in body["detail"]
    # Hint: erlaubte Werte werden mit-gemeldet
    assert "us_major" in body["detail"]


# ---------------------------------------------------------------------------
# Test 3 — Gueltiger Lauf gibt Audit-Payload + persistiert die Row
# ---------------------------------------------------------------------------

def test_endpoint_runs_roundup_end_to_end(client, db, monkeypatch):
    monkeypatch.setenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", "true")
    ch = _seed_channel(db, handle="a24", segment=ChannelSegment.US_INDEPENDENT)
    _seed_post(db, ch.id, days_ago=3, engagement=500)
    mock_anthropic = _patch_anthropic_ok(monkeypatch)

    response = client.post(
        "/api/admin/roundups/generate",
        params={"segment": "us_independent", "window_days": 14, "top_posts_n": 5},
        headers={"Authorization": "Bearer TESTTOKEN"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["segment"] == "us_independent"
    assert body["window_days"] == 14
    assert body["channels_evaluated"] == 1
    assert body["channels_with_posts"] == 1
    assert body["total_posts"] == 1
    assert body["llm_output_present"] is True
    assert body["input_tokens"] == 2500
    assert body["output_tokens"] == 400
    assert mock_anthropic.call_count == 1

    # Row in DB persistiert
    with Session(db) as session:
        rows = list(session.exec(
            __import__("sqlmodel").select(SegmentRoundup)
            .where(SegmentRoundup.segment == ChannelSegment.US_INDEPENDENT)
        ).all())
        assert len(rows) == 1
        assert rows[0].llm_output["headline"] == "endpoint test"


# ---------------------------------------------------------------------------
# Test 4 — Auth-Middleware greift (ohne Bearer → 401)
# ---------------------------------------------------------------------------

def test_endpoint_requires_bearer_auth(client, monkeypatch):
    monkeypatch.setenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", "true")
    response = client.post(
        "/api/admin/roundups/generate",
        params={"segment": "us_major"},
        # kein Authorization-Header
    )
    assert response.status_code == 401
