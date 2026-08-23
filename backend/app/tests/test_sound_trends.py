"""TikTok-Sound-Trends (22.08.2026) — musicMeta endlich ausgewertet.

Vertragspunkte:

- Extraktion liest den Spiegel-Schluessel ``_creative_radar_music``
  UND faellt auf das rohe ``musicMeta`` zurueck — der Altbestand wurde
  vor dem Spiegel gescrapt und darf nicht unsichtbar sein.
- Sounds unter ``min_posts`` fallen raus (ein Einzel-Post ist kein
  Trend); sortiert wird nach Median-Lift, dann Posts.
- Das Beispiel je Sound ist der Post mit dem hoechsten Lift.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Channel, Market
from app.services import sound_trends as st


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


@dataclass
class _FakePost:
    id: object
    channel_id: object
    platform: str = "tiktok"
    post_url: str = ""
    raw_payload: dict = field(default_factory=dict)


@dataclass
class _FakeCtx:
    usable: list = field(default_factory=list)
    lift_by_post: dict = field(default_factory=dict)


def _stub_ctx(monkeypatch, ctx):
    monkeypatch.setattr(st, "build_lift_context", lambda *a, **k: ctx)


def _sound(name, author="Composer", *, gespiegelt=True, original=False):
    meta = {"musicName": name, "musicAuthor": author, "musicOriginal": original}
    return {"_creative_radar_music": meta} if gespiegelt else {"musicMeta": meta}


def test_gruppiert_sortiert_nach_lift_und_nennt_das_top_beispiel(session, monkeypatch):
    kanal = _kanal(session, handle="a24")
    stark_1 = _FakePost("s1", kanal.id, post_url="https://t.test/s1", raw_payload=_sound("Hit"))
    stark_2 = _FakePost("s2", kanal.id, post_url="https://t.test/s2", raw_payload=_sound("Hit"))
    lahm_1 = _FakePost("l1", kanal.id, post_url="https://t.test/l1", raw_payload=_sound("Fluesterlied"))
    lahm_2 = _FakePost("l2", kanal.id, post_url="https://t.test/l2", raw_payload=_sound("Fluesterlied"))
    ctx = _FakeCtx(
        usable=[lahm_1, lahm_2, stark_1, stark_2],
        lift_by_post={"s1": 1.4, "s2": 3.0, "l1": 0.8, "l2": 0.9},
    )
    _stub_ctx(monkeypatch, ctx)

    ergebnis = st.compute_sound_trends(session)

    assert [s["name"] for s in ergebnis["sounds"]] == ["Hit", "Fluesterlied"], (
        "Staerkstes Signal (Median-Lift) zuerst, nicht Eingabe-Reihenfolge."
    )
    hit = ergebnis["sounds"][0]
    assert hit["posts"] == 2
    assert hit["median_lift"] == 2.2
    assert hit["kanaele"] == ["a24"]
    assert hit["beispiel_post_url"] == "https://t.test/s2", (
        "Das Beispiel ist der Post mit dem hoechsten Lift."
    )


def test_altbestand_ohne_spiegel_schluessel_zaehlt_ueber_musicmeta(session, monkeypatch):
    """Der Connector spiegelt musicMeta erst seit dem
    _creative_radar_music-Schluessel — Alt-Posts tragen es nur roh.
    Beide Formen muessen zaehlen, sonst ist der Bestand unsichtbar."""
    kanal = _kanal(session)
    alt_1 = _FakePost("a1", kanal.id, raw_payload=_sound("Retro", gespiegelt=False))
    alt_2 = _FakePost("a2", kanal.id, raw_payload=_sound("Retro", gespiegelt=False))
    ctx = _FakeCtx(usable=[alt_1, alt_2], lift_by_post={"a1": 1.0, "a2": 1.2})
    _stub_ctx(monkeypatch, ctx)

    ergebnis = st.compute_sound_trends(session)

    assert ergebnis["posts_mit_sound"] == 2
    assert [s["name"] for s in ergebnis["sounds"]] == ["Retro"]


def test_einzel_posts_sind_kein_trend(session, monkeypatch):
    kanal = _kanal(session)
    solo = _FakePost("x1", kanal.id, raw_payload=_sound("Einmalig"))
    ctx = _FakeCtx(usable=[solo], lift_by_post={"x1": 5.0})
    _stub_ctx(monkeypatch, ctx)

    ergebnis = st.compute_sound_trends(session)

    assert ergebnis["posts_mit_sound"] == 1
    assert ergebnis["sounds"] == [], "Unter min_posts ist ein Sound kein Trend."


def test_leerfall_kommt_mit_klartext_hinweis(session, monkeypatch):
    _stub_ctx(monkeypatch, _FakeCtx())

    ergebnis = st.compute_sound_trends(session)

    assert ergebnis["sounds"] == []
    assert "Sound-Metadaten" in ergebnis["note"]


async def test_admin_endpoint_liefert_die_auswertung(session, monkeypatch):
    from app.admin_session import require_admin_session
    from app.api import admin as admin_module
    from app.database import get_session
    from app.main import app

    monkeypatch.setenv("FEATURE_SOUND_TRENDS_ENABLED", "true")
    monkeypatch.setattr(
        admin_module, "compute_sound_trends",
        lambda session_: {"sounds": [], "note": None, "posts_mit_sound": 7},
    )

    def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[require_admin_session] = lambda: None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            antwort = await client.get("/api/admin/sound-trends")
    finally:
        app.dependency_overrides.clear()

    assert antwort.status_code == 200
    assert antwort.json()["posts_mit_sound"] == 7


async def test_admin_endpoint_503_bei_abgeschaltetem_flag(session, monkeypatch):
    """Feature-Flag-Gate (Arbeitsregel 23.08.2026)."""
    from app.admin_session import require_admin_session
    from app.database import get_session
    from app.main import app

    monkeypatch.delenv("FEATURE_SOUND_TRENDS_ENABLED", raising=False)

    def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[require_admin_session] = lambda: None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            antwort = await client.get("/api/admin/sound-trends")
    finally:
        app.dependency_overrides.clear()

    assert antwort.status_code == 503
    assert "FEATURE_SOUND_TRENDS_ENABLED" in antwort.json()["detail"]
