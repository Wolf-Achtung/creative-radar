"""Projekt-Start-Brief (22.08.2026) — das Radar VOR der Arbeit.

Fuer ein Wir-Projekt liefert der Brief die aktuell ueberperformenden
Muster mit Referenz-Posts. Vertragspunkte:

- Empfohlen sind NUR ``over``-Zellen (MACHEN-Auswahl wie Playbook und
  Wir-Segment), sortiert nach breakout_z absteigend.
- Der Genre-Standort zeigt die Zelle des Projekt-Genres — auch wenn
  sie NICHT ueberperformt (der Standort ist Information, keine
  Empfehlung); ohne Genre kommt kein geratener Wert.
- Beispiel-Posts sind die staerksten der Zelle (Lift absteigend), mit
  Bild-Vorrang gespeicherte Evidence vor CDN-Thumbnail.
- Der Admin-Endpoint liefert 404 fuer unbekannte Titel.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Asset, Channel, Market, Post, Title
from app.services import projekt_start_brief as psb


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


def _kanal(session, handle="pixar"):
    ch = Channel(
        name=handle, platform="tiktok", handle=handle,
        url=f"https://x.test/{uuid4()}", market=Market.US,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


def _post(session, channel, caption=None):
    post = Post(
        channel_id=channel.id, platform=channel.platform,
        post_url=f"https://x.test/p/{uuid4()}", caption=caption,
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    return post


def _titel(session, *, genres=None):
    t = Title(title_original=f"Film {uuid4().hex[:6]}", genres=genres or [])
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


@dataclass
class _FakeCtx:
    usable: list = field(default_factory=list)
    lift_by_post: dict = field(default_factory=dict)


def _zelle(value, verdict, *, lift=1.2, z=3.0, n=40):
    return SimpleNamespace(
        value=value, breakout_verdict=verdict,
        median_lift=lift, breakout_z=z, sample_size=n,
    )


def _stub(monkeypatch, ctx, dimensions, members_by_cell):
    monkeypatch.setattr(psb, "build_lift_context", lambda *a, **k: ctx)
    monkeypatch.setattr(
        psb, "compute_trailer_patterns",
        lambda *a, **k: SimpleNamespace(dimensions=dimensions),
    )
    monkeypatch.setattr(
        psb, "posts_for_cell",
        lambda session_, ctx_, dim, value: list(members_by_cell.get((dim, value), [])),
    )


def test_empfiehlt_nur_over_zellen_und_zeigt_den_genre_standort(session, monkeypatch):
    titel = _titel(session, genres=["Drama", "Thriller"])
    kanal = _kanal(session)
    p1 = _post(session, kanal)
    ctx = _FakeCtx(usable=[p1], lift_by_post={p1.id: 1.8})
    _stub(
        monkeypatch, ctx,
        {
            "genre": [
                _zelle("Drama", "neutral", lift=1.02, z=0.4, n=18),
                _zelle("SciFi", "over", lift=1.31, z=4.7, n=52),
            ],
            "cover_kinetik": [_zelle("title_card", "over", lift=1.18, z=3.1, n=33)],
        },
        {("genre", "SciFi"): [p1], ("cover_kinetik", "title_card"): [p1]},
    )

    brief = psb.compute_projekt_start_brief(session, titel.id)

    assert [e["value"] for e in brief["empfehlungen"]] == ["SciFi", "title_card"], (
        "Empfohlen sind nur over-Zellen, staerkstes Signal (z) zuerst — "
        "das neutrale Projekt-Genre ist Standort, keine Empfehlung."
    )
    assert brief["genre_standort"] == {
        "value": "Drama", "verdict": "neutral",
        "median_lift": 1.02, "breakout_z": 0.4, "sample_size": 18,
    }
    assert brief["title"]["genre"] == "Drama"


def test_beispiele_sind_die_staerksten_posts_mit_evidence_vorrang(session, monkeypatch):
    titel = _titel(session, genres=["Drama"])
    kanal = _kanal(session, handle="a24")
    schwach = _post(session, kanal, caption="schwach")
    stark = _post(session, kanal, caption="stark")
    # Der starke Post traegt ZWEI Assets: zuerst ein CDN-Thumbnail,
    # danach eine gespeicherte Evidence — das gespeicherte Bild muss
    # gewinnen (alte CDN-Links sind tot, Wolf-Befund 21.08.).
    cdn_asset = Asset(post_id=stark.id, thumbnail_url="https://cdn.test/x.jpg")
    session.add(cdn_asset)
    session.commit()
    evidence_asset = Asset(post_id=stark.id, visual_evidence_url="evidence/x.jpg")
    session.add(evidence_asset)
    session.commit()
    session.refresh(evidence_asset)

    ctx = _FakeCtx(
        usable=[schwach, stark],
        lift_by_post={schwach.id: 1.1, stark.id: 3.0},
    )
    _stub(
        monkeypatch, ctx,
        {"genre": [_zelle("Drama", "over")]},
        # Absichtlich schwach zuerst — die Sortierung muss sie drehen.
        {("genre", "Drama"): [schwach, stark]},
    )

    brief = psb.compute_projekt_start_brief(session, titel.id, examples_per_cell=1)

    [emp] = brief["empfehlungen"]
    [beispiel] = emp["beispiele"]
    assert beispiel["lift"] == 3.0, "Das staerkste Beispiel zuerst, nicht das erstbeste."
    assert beispiel["handle"] == "a24"
    assert beispiel["asset_id"] == str(evidence_asset.id), (
        "Gespeicherte Evidence schlaegt das CDN-Thumbnail."
    )


def test_titel_ohne_genre_liefert_keinen_geratenen_standort(session, monkeypatch):
    titel = _titel(session, genres=[])
    ctx = _FakeCtx()
    _stub(monkeypatch, ctx, {"genre": [_zelle("Drama", "over")]}, {})

    brief = psb.compute_projekt_start_brief(session, titel.id)

    assert brief["genre_standort"] is None
    assert brief["title"]["genre"] is None


def test_unbekannter_titel_wirft_value_error(session):
    with pytest.raises(ValueError):
        psb.compute_projekt_start_brief(session, uuid4())


async def _get_brief(session, title_id):
    from app.admin_session import require_admin_session
    from app.database import get_session
    from app.main import app

    def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[require_admin_session] = lambda: None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(f"/api/admin/projekt-start-brief/{title_id}")
    finally:
        app.dependency_overrides.clear()


async def test_endpoint_liefert_brief_und_404(session, monkeypatch):
    from app.api import admin as admin_module

    titel = _titel(session, genres=["Drama"])
    monkeypatch.setattr(
        admin_module, "compute_projekt_start_brief",
        lambda session_, title_id, window_days=90: {"title": {"id": str(title_id)}},
    )
    antwort = await _get_brief(session, titel.id)
    assert antwort.status_code == 200
    assert antwort.json()["title"]["id"] == str(titel.id)

    def _raise(session_, title_id, window_days=90):
        raise ValueError("Titel nicht gefunden")

    monkeypatch.setattr(admin_module, "compute_projekt_start_brief", _raise)
    antwort = await _get_brief(session, uuid4())
    assert antwort.status_code == 404
