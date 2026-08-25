"""Release-Countdown je Wir-Projekt (Roadmap Schritt 2, 25.08.2026).

Der Countdown verbindet drei vorhandene Auswertungen zu einem Plan pro
Projekt. Die Tests sichern die Verbindungs-Entscheidungen:

1. Der Markt-Benchmark ist der Median der Kampagnenstarts ueber ALLE
   ausgewerteten Timing-Titel — nicht die Anzeige-Top-20.
2. Die Phase kommt aus dem Release-Datum (Pre-Launch / Release-Woche /
   Nach dem Start), die Namen sind das lifecycle-Vokabular.
3. Die Phasen-Muster rechnen NUR auf den Posts der jeweiligen Phase —
   mit der Statistik des Muster-Berichts (Mindest-Stichprobe, z-Test).
   Eine Zelle, die Posts anderer Phasen mitzaehlt, waere ein falscher
   Plan.
4. Ein Wir-Projekt ohne Release-Datum verschwindet nicht, es traegt
   einen Hinweis — sonst wundert sich Wolf, warum ein markiertes
   Projekt fehlt.
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
from app.models.entities import Asset, Channel, Market, Post, Title
from app.services.release_countdown import compute_release_countdown

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
HEUTE = NOW.date()


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session(engine) -> Session:
    with Session(engine) as s:
        yield s


@pytest.fixture
def client(engine, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "user_auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "admin_auth_enabled", False, raising=False)

    def _override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _channel(session: Session, *, market: Market = Market.DE) -> Channel:
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}",
        handle=f"handle-{uuid4().hex[:6]}",
        platform="tiktok",
        url=f"https://x.test/{uuid4()}",
        market=market,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


def _post(
    session: Session,
    channel: Channel,
    *,
    likes: int = 100,
    days_ago: float = 1,
    analysis: dict | None = None,
) -> Post:
    p = Post(
        channel_id=channel.id,
        platform=channel.platform,
        post_url=f"https://x.test/p/{uuid4()}",
        caption="x",
        detected_at=NOW - timedelta(days=days_ago),
        visible_views=1000,
        visible_likes=likes,
        visible_comments=0,
        visible_bookmarks=0,
        raw_payload={},
        analysis=analysis,
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _titel(
    session: Session,
    *,
    name: str | None = None,
    release_de: datetime | None = None,
    own: bool = False,
) -> Title:
    t = Title(
        title_original=name or f"Titel-{uuid4().hex[:6]}",
        release_date_de=release_de.date() if release_de else None,
        is_own_project=own,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _zuordnen(session: Session, post: Post, titel: Title) -> None:
    session.add(Asset(post_id=post.id, title_id=titel.id))
    session.commit()


def _markt_kampagne(
    session: Session, *, start_vorlauf_tage: int, release_in_tagen: int = 30
) -> Title:
    """Ein Markt-Titel mit drei Posts; der frueheste definiert den
    Kampagnenstart (start_vorlauf_tage vor dem Release)."""
    kanal = _channel(session)
    titel = _titel(session, release_de=NOW + timedelta(days=release_in_tagen))
    for offset in (0, 5, 10):
        vorlauf = start_vorlauf_tage - offset
        # Post-Moment = NOW - days_ago; Vorlauf = Release - Moment =
        # release_in_tagen + days_ago → days_ago = vorlauf - release_in_tagen.
        post = _post(session, kanal, days_ago=vorlauf - release_in_tagen)
        _zuordnen(session, post, titel)
    # Baseline-Futter, damit der Kanal im Lift-Kontext nicht stoert.
    for _ in range(4):
        _post(session, kanal)
    return titel


def test_ohne_wir_projekte_kommt_eine_note(session: Session):
    ergebnis = compute_release_countdown(session, now=NOW)
    assert ergebnis["projekte"] == []
    assert "Wir-Projekt" in ergebnis["note"]


def test_markt_median_ueber_alle_timing_titel(session: Session):
    _markt_kampagne(session, start_vorlauf_tage=70)
    _markt_kampagne(session, start_vorlauf_tage=84)
    _markt_kampagne(session, start_vorlauf_tage=98)
    _titel(session, name="Unser Film", release_de=NOW + timedelta(days=63), own=True)

    ergebnis = compute_release_countdown(session, now=NOW)
    assert ergebnis["markt_kampagnenstart"]["titel_basis"] == 3
    assert ergebnis["markt_kampagnenstart"]["median_vorlauf_tage"] == 84


def test_phasen_aus_dem_release_datum(session: Session):
    _titel(session, name="Frueh", release_de=NOW + timedelta(days=63), own=True)
    _titel(session, name="Jetzt", release_de=NOW + timedelta(days=2), own=True)
    _titel(session, name="Vorbei", release_de=NOW - timedelta(days=60), own=True)

    ergebnis = compute_release_countdown(session, now=NOW)
    phase_by_name = {z["titel"]: z["phase"] for z in ergebnis["projekte"]}
    assert phase_by_name == {
        "Frueh": "pre_launch",
        "Jetzt": "launch",
        "Vorbei": "post_launch",
    }
    # Naechster Release zuerst: Vorbei (negativ) < Jetzt < Frueh.
    assert [z["titel"] for z in ergebnis["projekte"]] == ["Vorbei", "Jetzt", "Frueh"]


def test_projekt_ohne_release_datum_traegt_hinweis_und_steht_hinten(session: Session):
    _titel(session, name="Mit Datum", release_de=NOW + timedelta(days=30), own=True)
    _titel(session, name="Ohne Datum", own=True)

    ergebnis = compute_release_countdown(session, now=NOW)
    assert [z["titel"] for z in ergebnis["projekte"]] == ["Mit Datum", "Ohne Datum"]
    ohne = ergebnis["projekte"][1]
    assert ohne["phase"] is None
    assert "Kein Release-Datum" in ohne["hinweis"]


def test_eigener_kampagnenstart_ist_der_frueheste_post_im_band(session: Session):
    kanal = _channel(session)
    projekt = _titel(
        session, name="Unser Film", release_de=NOW + timedelta(days=21), own=True
    )
    # Post vor 7 Tagen → 28 Tage Vorlauf; Post vor 2 Tagen → 23 Tage.
    frueh = _post(session, kanal, days_ago=7)
    spaet = _post(session, kanal, days_ago=2)
    _zuordnen(session, frueh, projekt)
    _zuordnen(session, spaet, projekt)
    # Ein Post WEIT ausserhalb des Bands (2 Jahre alt) zaehlt nicht als
    # Kampagnenstart — Set-Foto-Logik wie im Kampagnen-Timing.
    uralt = _post(session, kanal, days_ago=730)
    _zuordnen(session, uralt, projekt)
    for _ in range(4):
        _post(session, kanal)

    ergebnis = compute_release_countdown(session, now=NOW)
    zeile = ergebnis["projekte"][0]
    assert zeile["eigene_posts"] == 3
    assert zeile["eigener_start_vorlauf_tage"] == 28


def test_phasen_muster_rechnen_nur_auf_der_phase(session: Session):
    """Der Mutations-Anker: humorvolle Posts gibt es in Pre-Launch UND
    Post-Launch — die Pre-Launch-Zelle darf nur die 6 Pre-Launch-Posts
    zaehlen. Wuerde die Phasen-Einschraenkung fehlen, staende dort 12."""
    kanaele = [_channel(session) for _ in range(6)]
    for kanal in kanaele:
        for _ in range(4):
            _post(session, kanal)  # Baseline: Aktivierung 0.1
        _post(
            session, kanal, likes=400,  # Lift 4 → Breakout
            analysis={
                "tone": "humorous", "lifecycle_stage": "pre_launch",
                "confidence": 0.9,
            },
        )
        _post(
            session, kanal, likes=100,  # Lift 1 → kein Breakout
            analysis={
                "tone": "humorous", "lifecycle_stage": "post_launch",
                "confidence": 0.9,
            },
        )
    _titel(session, name="Frueh", release_de=NOW + timedelta(days=63), own=True)
    _titel(session, name="Vorbei", release_de=NOW - timedelta(days=60), own=True)

    ergebnis = compute_release_countdown(session, now=NOW)
    pre = ergebnis["phasen_muster"]["pre_launch"]
    humor = [z for z in pre if z["dimension"] == "tone" and z["value"] == "humorous"]
    assert humor, f"tone=humorous fehlt in pre_launch: {pre}"
    assert humor[0]["sample_size"] == 6
    assert humor[0]["breakout_verdict"] == "over"
    # In Post-Launch ist humorous unauffaellig (kein Breakout) — eine
    # neutrale Zelle gehoert nicht in den Plan.
    post = ergebnis["phasen_muster"].get("post_launch", [])
    assert not [
        z for z in post if z["dimension"] == "tone" and z["value"] == "humorous"
    ]


# ---------- Endpoint ---------------------------------------------------


def test_endpoint_ist_ohne_flag_aus(client, monkeypatch):
    monkeypatch.delenv("FEATURE_RELEASE_COUNTDOWN_ENABLED", raising=False)
    antwort = client.get("/api/admin/release-countdown")
    assert antwort.status_code == 503
    assert "FEATURE_RELEASE_COUNTDOWN_ENABLED" in antwort.json()["detail"]


def test_endpoint_liefert_projekte(client, session, monkeypatch):
    monkeypatch.setenv("FEATURE_RELEASE_COUNTDOWN_ENABLED", "true")
    _titel(session, name="Unser Film", release_de=NOW + timedelta(days=63), own=True)
    antwort = client.get("/api/admin/release-countdown")
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["projekte"][0]["titel"] == "Unser Film"
    assert daten["projekte"][0]["phase"] == "pre_launch"
