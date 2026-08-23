"""Kampagnen-Timing (22.08.2026) — wann startet die Trailer-Welle?

Rechnet ausschliesslich auf vorhandenen Daten (Release-Dates am Titel,
Post-Zeitstempel, Titel-Zuordnung ueber Assets). Vertragspunkte:

- Marktgerechtes Release-Datum: DE-Kanaele messen gegen
  ``release_date_de``, andere gegen ``release_date_us`` (mit Fallback).
- Titel unter ``min_posts`` fallen raus (Einzel-Post ist kein Muster);
  Posts ausserhalb des 26-Wochen/8-Wochen-Bands ebenso.
- Der Kanal-Wert ist der Median der KAMPAGNENSTARTS (fruehester Post
  je Titel), nicht der Median aller Posts.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Asset, Channel, Market, Post, Title
from app.services.campaign_timing import compute_campaign_timing


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


def _kanal(session, *, market, handle):
    ch = Channel(
        name=handle, platform="instagram", handle=handle,
        url=f"https://x.test/{uuid4()}", market=market,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


def _titel(session, *, de=None, us=None, name="Film"):
    t = Title(title_original=name, release_date_de=de, release_date_us=us)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _post(session, channel, title, published_at):
    post = Post(
        channel_id=channel.id, platform=channel.platform,
        post_url=f"https://x.test/p/{uuid4()}", published_at=published_at,
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    session.add(Asset(post_id=post.id, title_id=title.id))
    session.commit()
    return post


def test_de_kanal_misst_gegen_deutsches_release_datum(session):
    """Der Kernfall: gleicher Titel, zwei Maerkte, zwei Release-Daten —
    jeder Kanal wird gegen SEIN Datum gemessen. US-Start 2026-10-01,
    DE-Start vier Wochen spaeter: derselbe Kalendertag eines Posts
    ergibt fuer den DE-Kanal 28 Tage mehr Vorlauf."""
    titel = _titel(session, de=date(2026, 10, 29), us=date(2026, 10, 1), name="Boo")
    de = _kanal(session, market=Market.DE, handle="tobis")
    us = _kanal(session, market=Market.US, handle="pixar")
    # Beide posten am selben Tag, 70 Tage vor dem US-Start.
    tag = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    for _ in range(3):
        _post(session, de, titel, tag)
        _post(session, us, titel, tag)

    ergebnis = compute_campaign_timing(session)

    assert ergebnis["titel_ausgewertet"] == 1
    starts = {k["handle"]: k["median_kampagnenstart_tage"] for k in ergebnis["kanaele"]}
    assert starts["pixar"] == 70
    assert starts["tobis"] == 98, (
        "Der DE-Kanal muss gegen release_date_de gemessen werden — "
        "28 Tage mehr Vorlauf als der US-Kanal am selben Kalendertag."
    )


def test_duenne_titel_und_posts_ausserhalb_des_bands_fallen_raus(session):
    us = _kanal(session, market=Market.US, handle="a24")
    aktiv = _titel(session, us=date(2026, 10, 1), name="Aktiv")
    duenn = _titel(session, us=date(2026, 10, 1), name="Duenn")
    for tage_vor in (60, 40, 5):
        _post(
            session, us, aktiv,
            datetime(2026, 10, 1, tzinfo=timezone.utc)
            - __import__("datetime").timedelta(days=tage_vor),
        )
    # Ausserhalb des Bands: ein Set-Foto ein Jahr vor Release.
    _post(session, us, aktiv, datetime(2025, 10, 1, tzinfo=timezone.utc))
    # Nur zwei Posts -> unter min_posts, kein Kampagnen-Muster.
    _post(session, us, duenn, datetime(2026, 9, 1, tzinfo=timezone.utc))
    _post(session, us, duenn, datetime(2026, 9, 2, tzinfo=timezone.utc))

    ergebnis = compute_campaign_timing(session)

    assert ergebnis["titel_ausgewertet"] == 1
    [titel_row] = ergebnis["titel"]
    assert titel_row["title_original"] == "Aktiv"
    assert titel_row["posts"] == 3, "Der Ein-Jahres-Post liegt ausserhalb des Bands."
    assert titel_row["kampagnenstart_vorlauf_tage"] == 60
    assert titel_row["letzter_post_vorlauf_tage"] == 5


def test_kurve_zaehlt_posts_je_woche_vor_release(session):
    us = _kanal(session, market=Market.US, handle="a24")
    titel = _titel(session, us=date(2026, 10, 1), name="Kurve")
    import datetime as dt
    for tage_vor in (70, 68, 10):
        _post(
            session, us, titel,
            datetime(2026, 10, 1, tzinfo=timezone.utc) - dt.timedelta(days=tage_vor),
        )

    ergebnis = compute_campaign_timing(session)

    kurve = {k["wochen_vor_release"]: k["posts"] for k in ergebnis["kurve"]}
    assert kurve == {10: 1, 9: 1, 1: 1}, (
        "70 Tage = Woche 10, 68 Tage = Woche 9, 10 Tage = Woche 1 — "
        "die Kurve buckelt nach ganzen Wochen vor Release."
    )


def test_leerfall_kommt_mit_klartext_hinweis(session):
    _titel(session, us=date(2026, 10, 1))

    ergebnis = compute_campaign_timing(session)

    assert ergebnis["titel_ausgewertet"] == 0
    assert "Prüf-Queue" in ergebnis["note"]


async def test_admin_endpoint_liefert_die_auswertung(session, monkeypatch):
    from app.admin_session import require_admin_session
    from app.api import admin as admin_module
    from app.database import get_session
    from app.main import app

    monkeypatch.setenv("FEATURE_KAMPAGNEN_TIMING_ENABLED", "true")
    monkeypatch.setattr(
        admin_module, "compute_campaign_timing",
        lambda session_: {"titel_ausgewertet": 4, "note": None},
    )

    def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[require_admin_session] = lambda: None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            antwort = await client.get("/api/admin/kampagnen-timing")
    finally:
        app.dependency_overrides.clear()

    assert antwort.status_code == 200
    assert antwort.json()["titel_ausgewertet"] == 4


async def test_admin_endpoint_503_bei_abgeschaltetem_flag(session, monkeypatch):
    """Feature-Flag-Gate (Arbeitsregel 23.08.2026)."""
    from app.admin_session import require_admin_session
    from app.database import get_session
    from app.main import app

    monkeypatch.delenv("FEATURE_KAMPAGNEN_TIMING_ENABLED", raising=False)

    def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[require_admin_session] = lambda: None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            antwort = await client.get("/api/admin/kampagnen-timing")
    finally:
        app.dependency_overrides.clear()

    assert antwort.status_code == 503
    assert "FEATURE_KAMPAGNEN_TIMING_ENABLED" in antwort.json()["detail"]
