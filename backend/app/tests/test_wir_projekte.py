"""Wir-Projekte (22.08.2026) — die Wir-Einheit ist der Titel.

Wolfs Befund: Trailerhaus betreut keine kompletten Kunden-Kanaele,
sondern arbeitet projektweise — auf einem Verleih-Kanal ist ein Post
von ihnen und zwanzig nicht. Das Kanal-Haekchen (``channel.is_own``)
waere dort schlicht falsch. Diese Tests nageln fest:

- ein Post zaehlt als "gemacht", wenn sein Asset auf einen Titel mit
  ``is_own_project`` gemappt ist — auch auf einem FREMDEN Kanal
  (echter DB-Pfad ueber ``_title_by_post``),
- Kanal- und Titel-Weg sind eine UNION, keiner ersetzt den anderen,
- die Markierung selbst laeuft ueber PATCH /api/titles/{id} und
  aendert ausschliesslich ``is_own_project``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Asset, Channel, Market, Post, Title
from app.services import wir_segment as ws

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def session():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        with Session(eng) as s:
            yield s
    finally:
        eng.dispose()


def _kanal(session, *, is_own=False):
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}", platform="tiktok",
        url=f"https://x.test/{uuid4()}", market=Market.US, is_own=is_own,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


def _titel(session, *, is_own_project=False, name=None):
    t = Title(
        title_original=name or f"Film {uuid4().hex[:6]}",
        is_own_project=is_own_project,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _post_mit_asset(session, channel, title=None):
    post = Post(
        channel_id=channel.id, platform=channel.platform,
        post_url=f"https://x.test/p/{uuid4()}",
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    asset = Asset(post_id=post.id, title_id=title.id if title else None)
    session.add(asset)
    session.commit()
    return post


@dataclass
class _FakeCtx:
    usable: list = field(default_factory=list)
    lift_by_post: dict = field(default_factory=dict)


def _zelle(value, verdict="over", *, z=3.0, lift=1.2):
    return SimpleNamespace(
        value=value, breakout_verdict=verdict, breakout_z=z, median_lift=lift,
    )


def _stub_statistik(monkeypatch, ctx, dimensions, members_by_cell):
    monkeypatch.setattr(ws, "build_lift_context", lambda *a, **k: ctx)
    monkeypatch.setattr(
        ws, "compute_trailer_patterns",
        lambda *a, **k: SimpleNamespace(dimensions=dimensions),
    )
    monkeypatch.setattr(
        ws, "posts_for_cell",
        lambda session_, ctx_, dim, value: members_by_cell.get((dim, value), []),
    )


def test_projekt_post_auf_fremdem_kanal_zaehlt_als_gemacht(session, monkeypatch):
    """Der Kernfall: KEIN Kanal ist markiert, aber der Titel ist ein
    Wir-Projekt — der Post auf dem Verleih-Kanal zaehlt trotzdem. Die
    Post→Titel-Zuordnung laeuft hier ueber den ECHTEN ``_title_by_post``-
    Pfad (Asset-Row in der DB), nicht ueber einen Stub."""
    verleih = _kanal(session, is_own=False)
    projekt = _titel(session, is_own_project=True, name="Lügen über meine Mutter")
    anderer = _titel(session, is_own_project=False)
    p_projekt = _post_mit_asset(session, verleih, projekt)
    p_fremd = _post_mit_asset(session, verleih, anderer)

    ctx = _FakeCtx(
        usable=[p_projekt, p_fremd],
        lift_by_post={p_projekt.id: 1.4, p_fremd.id: 9.0},
    )
    _stub_statistik(
        monkeypatch, ctx,
        {"genre": [_zelle("Drama")]},
        {("genre", "Drama"): [p_projekt, p_fremd]},
    )

    ergebnis = ws.compute_wir_segment(session, now=NOW)

    assert ergebnis["own_channels"] == 0
    assert ergebnis["own_project_titles"] == 1
    assert ergebnis["note"] is None, (
        "Projekt-Titel allein genuegen — der Hinweis darf nicht mehr "
        "nach Kanal-Haekchen verlangen."
    )
    assert ergebnis["eigene_posts_im_fenster"] == 1
    [zeile] = ergebnis["zeilen"]
    assert zeile["gemacht"] == 1
    assert zeile["gewirkt_median_lift"] == 1.4, (
        "Nur der Projekt-Post zaehlt — der fremde 9.0-Post desselben "
        "Kanals darf das Ergebnis nicht schoenen."
    )


def test_kanal_und_projekt_sind_eine_union(session, monkeypatch):
    """Beide Wege gleichzeitig: ein Post ueber das Kanal-Haekchen, einer
    ueber den Projekt-Titel, einer ueber gar nichts → gemacht == 2."""
    wir_kanal = _kanal(session, is_own=True)
    verleih = _kanal(session, is_own=False)
    projekt = _titel(session, is_own_project=True)
    p_kanal = _post_mit_asset(session, wir_kanal)
    p_projekt = _post_mit_asset(session, verleih, projekt)
    p_fremd = _post_mit_asset(session, verleih)

    ctx = _FakeCtx(
        usable=[p_kanal, p_projekt, p_fremd],
        lift_by_post={p_kanal.id: 2.0, p_projekt.id: 1.0, p_fremd.id: 9.0},
    )
    _stub_statistik(
        monkeypatch, ctx,
        {"genre": [_zelle("Drama")]},
        {("genre", "Drama"): [p_kanal, p_projekt, p_fremd]},
    )

    ergebnis = ws.compute_wir_segment(session, now=NOW)

    assert ergebnis["own_channels"] == 1
    assert ergebnis["own_project_titles"] == 1
    assert ergebnis["eigene_posts_im_fenster"] == 2
    [zeile] = ergebnis["zeilen"]
    assert zeile["gemacht"] == 2, (
        "Kanal-Weg und Titel-Weg sind eine UNION — keiner ersetzt den "
        "anderen."
    )
    assert zeile["gewirkt_median_lift"] == 1.5


async def _patch_title(session, title_id, body):
    from app.admin_session import require_admin_session
    from app.database import get_session
    from app.main import app

    def _session_override():
        yield session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[require_admin_session] = lambda: None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.patch(f"/api/titles/{title_id}", json=body)
    finally:
        app.dependency_overrides.clear()


async def test_patch_endpoint_setzt_und_loescht_die_projekt_markierung(session):
    titel = _titel(session)
    assert titel.is_own_project is False

    antwort = await _patch_title(session, titel.id, {"is_own_project": True})
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["is_own_project"] is True
    session.refresh(titel)
    assert titel.is_own_project is True

    antwort = await _patch_title(session, titel.id, {"is_own_project": False})
    assert antwort.json()["is_own_project"] is False


async def test_patch_endpoint_404_fuer_unbekannten_titel(session):
    antwort = await _patch_title(session, uuid4(), {"is_own_project": True})
    assert antwort.status_code == 404
