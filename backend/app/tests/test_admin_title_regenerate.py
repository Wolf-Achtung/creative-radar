"""Tests for POST /api/admin/insights/title/regenerate (C5). Calls the route
function directly (router auth deps bypassed); LLM mocked, real sqlite table."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.admin import regenerate_title_insight
from app.models.entities import (
    Asset, AssetType, Channel, Market, Post, ReviewStatus, Title,
    TitleInsightReport as TitleInsightReportRow,
)
from app.services import insight_engine as engine_module
from app.services import title_brief as tb


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_title_ep_", suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        try:
            os.unlink(path)
        except OSError:
            pass


def _seed(session, *, now):
    title = Title(id=uuid4(), title_original="Mortal Kombat", content_type="Film")
    session.add(title)
    ch = Channel(id=uuid4(), name="primevideo", handle="primevideo",
                 url="https://x/primevideo", platform="instagram", market=Market.US)
    session.add(ch)
    session.commit()
    post = Post(id=uuid4(), channel_id=ch.id, platform="instagram",
                post_url="https://ig/p/seed", detected_at=now,
                visible_likes=200, visible_comments=20, visible_views=2000)
    session.add(post)
    session.commit()
    session.add(Asset(id=uuid4(), post_id=post.id, title_id=title.id,
                      asset_type=AssetType.UNKNOWN, review_status=ReviewStatus.NEW))
    session.commit()


def _fake_msg(payload: dict, *, stop_reason="tool_use"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", input=payload)],
        usage=SimpleNamespace(input_tokens=4000, output_tokens=1500),
        stop_reason=stop_reason,
    )


_BODY = {
    "headline": "Mortal Kombat zieht auf IG US",
    "tldr": "IG US trägt.",
    "plattform_vergleich": "IG US 230 Reaktionen.",
    "data_caveats": ["dünn"],
}


def _patch_llm(monkeypatch, msg):
    monkeypatch.setattr(tb, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(engine_module, "messages_create_strict_json", MagicMock(return_value=msg))
    monkeypatch.setattr(engine_module, "record_anthropic_call", MagicMock())


def test_endpoint_generates_and_returns_ok(db, monkeypatch):
    now = datetime(2026, 6, 4, tzinfo=timezone.utc)
    _patch_llm(monkeypatch, _fake_msg(_BODY))
    with Session(db) as session:
        _seed(session, now=now)
        resp = regenerate_title_insight(
            title="Mortal Kombat", tmdb_id=None, window_days=30, replace=True, session=session,
        )
    assert resp["status"] == "ok"
    assert resp["title_original"] == "Mortal Kombat"
    assert resp["llm_output"]["headline"] == "Mortal Kombat zieht auf IG US"
    assert resp["window_days"] == 30


def test_endpoint_404_unknown_title(db):
    with Session(db) as session:
        with pytest.raises(HTTPException) as ei:
            regenerate_title_insight(title="No Such Title", tmdb_id=None, window_days=30,
                                     replace=False, session=session)
    assert ei.value.status_code == 404


def _seed_mk_pair(session, *, now):
    """Two same-substring titles: the 2021 film and its sequel. Posts/assets
    hang on MKII so the tmdb_id path has something to aggregate."""
    mk = Title(id=uuid4(), title_original="Mortal Kombat", content_type="Film", tmdb_id=460465)
    mk2 = Title(id=uuid4(), title_original="Mortal Kombat II", content_type="Film", tmdb_id=931285)
    session.add(mk)
    session.add(mk2)
    ch = Channel(id=uuid4(), name="warner", handle="warner",
                 url="https://x/warner", platform="instagram", market=Market.US)
    session.add(ch)
    session.commit()
    post = Post(id=uuid4(), channel_id=ch.id, platform="instagram",
                post_url="https://ig/p/mk2", detected_at=now,
                visible_likes=300, visible_comments=30, visible_views=3000)
    session.add(post)
    session.commit()
    session.add(Asset(id=uuid4(), post_id=post.id, title_id=mk2.id,
                      asset_type=AssetType.UNKNOWN, review_status=ReviewStatus.NEW))
    session.commit()
    return mk, mk2


def test_endpoint_400_when_no_param(db):
    with Session(db) as session:
        with pytest.raises(HTTPException) as ei:
            regenerate_title_insight(title=None, tmdb_id=None,
                                     window_days=30, replace=False, session=session)
    assert ei.value.status_code == 400


def test_endpoint_tmdb_id_resolves_sequel(db, monkeypatch):
    """tmdb_id=931285 must resolve MKII deterministically — never the 2021 film
    that shares the name substring."""
    now = datetime(2026, 6, 4, tzinfo=timezone.utc)
    _patch_llm(monkeypatch, _fake_msg(_BODY))
    with Session(db) as session:
        _, mk2 = _seed_mk_pair(session, now=now)
        mk2_id = str(mk2.id)
        resp = regenerate_title_insight(
            title=None, tmdb_id=931285, window_days=30, replace=True, session=session,
        )
        assert resp["status"] == "ok"
        assert resp["title_id"] == mk2_id
        assert resp["title_original"] == "Mortal Kombat II"


def test_endpoint_ambiguous_name_returns_409(db):
    """Substring 'Mortal' matches both titles with no exact hit -> 409 with the
    candidate list, never a silent guess."""
    now = datetime(2026, 6, 4, tzinfo=timezone.utc)
    with Session(db) as session:
        mk, mk2 = _seed_mk_pair(session, now=now)
        expected = {str(mk.id), str(mk2.id)}
        with pytest.raises(HTTPException) as ei:
            regenerate_title_insight(title="Mortal", tmdb_id=None,
                                     window_days=30, replace=True, session=session)
        assert ei.value.status_code == 409
        detail = ei.value.detail
        assert detail["error"] == "ambiguous_title"
        ids = {c["title_id"] for c in detail["candidates"]}
        assert ids == expected


def test_endpoint_exact_name_is_deterministic(db, monkeypatch):
    """Exact 'Mortal Kombat' resolves the 2021 film even though MKII shares the
    substring — the exact branch wins before substring ambiguity."""
    now = datetime(2026, 6, 4, tzinfo=timezone.utc)
    _patch_llm(monkeypatch, _fake_msg(_BODY))
    with Session(db) as session:
        mk, _ = _seed_mk_pair(session, now=now)
        mk_id = str(mk.id)
        resp = regenerate_title_insight(
            title="Mortal Kombat", tmdb_id=None, window_days=30, replace=True, session=session,
        )
        assert resp["title_id"] == mk_id
        assert resp["title_original"] == "Mortal Kombat"


def test_endpoint_replace_overwrites(db, monkeypatch):
    now = datetime(2026, 6, 4, tzinfo=timezone.utc)
    _patch_llm(monkeypatch, _fake_msg({**_BODY, "headline": "V1"}))
    with Session(db) as session:
        _seed(session, now=now)
        regenerate_title_insight(title="Mortal Kombat", tmdb_id=None, window_days=30, replace=True, session=session)
        monkeypatch.setattr(engine_module, "messages_create_strict_json",
                            MagicMock(return_value=_fake_msg({**_BODY, "headline": "V2"})))
        resp = regenerate_title_insight(title="Mortal Kombat", tmdb_id=None, window_days=30, replace=True, session=session)
        assert resp["llm_output"]["headline"] == "V2"
        rows = session.exec(select(TitleInsightReportRow)).all()
        assert len(rows) == 1
