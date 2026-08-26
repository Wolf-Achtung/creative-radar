"""Beständigkeit und Bewährung (26.08.2026) — Wiederkehr statt Einzelwochen-Befund.

Der Muster-Bericht prüft ~40 Zellen pro Woche gegen |z| >= 2; ein bis
zwei Zufallstreffer sind zu ERWARTEN (steht als Note im Bericht). Der
Unterschied zwischen einem Zufallstreffer und echtem Signal ist die
Wiederkehr — und die war bisher unsichtbar. Zwei Auswertungen auf den
persistierten Wochen-Snapshots:

1. ``annotiere_bestaendigkeit``: jede belastbare Zelle des Live-Berichts
   sagt, die wievielte Woche in Folge sie ihr Verdikt trägt. Gezählt
   wird nur, was Snapshots BELEGEN — Lücken beenden die Zählung, ein
   Altformat-Snapshot (nur over, kein verdict-Feld) sagt zu under
   nichts.
2. ``compute_bewaehrung``: das System misst seine eigene Trefferquote —
   wie viele Empfehlungen (over) einer Woche standen im Snapshot der
   Folgewoche noch? Reiner Row-Vergleich, kein Neu-Rechnen.

Dazu der Vertrag mit dem Beweis-Loop: under-Zellen stehen jetzt mit im
Snapshot, sind aber KEINE Empfehlungen — der Loop filtert auf over.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import Channel, Market, Post, RecommendationSnapshot
from app.services import recommendation_snapshot as rs
from app.services.beweis_loop import compute_beweis_loop

# Mittwoch, KW 35/2026 (Montag = 24.08.).
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


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
    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")

    def _override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _snap(session: Session, year: int, week: int, cells: list[dict]) -> None:
    session.add(RecommendationSnapshot(
        iso_year=year, iso_week=week, window_days=90, cells=cells,
    ))
    session.commit()


def _eintrag(dimension: str, value: str, verdict: str = "over") -> dict:
    return {
        "dimension": dimension, "value": value, "breakout_verdict": verdict,
        "median_lift": 1.2, "breakout_z": 3.0, "sample_size": 40,
    }


def _alt_eintrag(dimension: str, value: str) -> dict:
    """Row-Eintrag im Format vor dem 26.08.2026 — kein verdict-Feld,
    per Konstruktion over."""
    return {
        "dimension": dimension, "value": value,
        "median_lift": 1.2, "breakout_z": 3.0, "sample_size": 40,
    }


def _bericht(*zellen: tuple[str, str, str]) -> dict:
    """Minimaler Berichts-Payload wie nach ``apply_weekly_trend``."""
    dimensions: dict[str, list[dict]] = {}
    for dimension, value, verdict in zellen:
        dimensions.setdefault(dimension, []).append(
            {"value": value, "breakout_verdict": verdict}
        )
    return {"dimensions": dimensions}


def _zelle(data: dict, dimension: str, value: str) -> dict:
    return next(c for c in data["dimensions"][dimension] if c["value"] == value)


# ---------- Beständigkeit ------------------------------------------------


def test_streak_zaehlt_nachgewiesene_vorwochen(session):
    _snap(session, 2026, 33, [_eintrag("genre", "SciFi")])
    _snap(session, 2026, 34, [_eintrag("genre", "SciFi"), _eintrag("tone", "emotional")])

    data = rs.annotiere_bestaendigkeit(
        session,
        _bericht(
            ("genre", "SciFi", "over"),
            ("tone", "emotional", "over"),
            ("format", "trailer", "over"),
        ),
        now=NOW,
    )

    assert _zelle(data, "genre", "SciFi")["wochen_in_folge"] == 3
    assert _zelle(data, "tone", "emotional")["wochen_in_folge"] == 2
    # Vorwochen-Snapshot existiert und kennt die Zelle nicht: neu.
    assert _zelle(data, "format", "trailer")["wochen_in_folge"] == 1


def test_ohne_vorwochen_snapshot_keine_aussage(session):
    data = rs.annotiere_bestaendigkeit(
        session, _bericht(("genre", "SciFi", "over")), now=NOW
    )
    assert _zelle(data, "genre", "SciFi")["wochen_in_folge"] is None


def test_luecke_beendet_die_zaehlung(session):
    """KW 33 fehlt: die Historie belegt nur KW 34 — "3. Woche in Folge"
    waere eine Behauptung ohne Beleg, auch wenn KW 32 die Zelle trug."""
    _snap(session, 2026, 32, [_eintrag("genre", "SciFi")])
    _snap(session, 2026, 34, [_eintrag("genre", "SciFi")])

    data = rs.annotiere_bestaendigkeit(
        session, _bericht(("genre", "SciFi", "over")), now=NOW
    )
    assert _zelle(data, "genre", "SciFi")["wochen_in_folge"] == 2


def test_under_gegen_altformat_row_sagt_nichts(session):
    """Altformat-Rows haben under nie aufgezeichnet — "war damals nicht
    under" laesst sich daraus nicht lesen. Over bleibt voll auswertbar."""
    _snap(session, 2026, 34, [_alt_eintrag("genre", "SciFi")])

    data = rs.annotiere_bestaendigkeit(
        session,
        _bericht(("genre", "SciFi", "over"), ("tone", "humorous", "under")),
        now=NOW,
    )
    assert _zelle(data, "genre", "SciFi")["wochen_in_folge"] == 2
    assert _zelle(data, "tone", "humorous")["wochen_in_folge"] is None


def test_under_streak_im_neuformat(session):
    _snap(session, 2026, 34, [_eintrag("tone", "humorous", "under")])
    data = rs.annotiere_bestaendigkeit(
        session, _bericht(("tone", "humorous", "under")), now=NOW
    )
    assert _zelle(data, "tone", "humorous")["wochen_in_folge"] == 2


def test_neutral_und_insufficient_bleiben_unangetastet(session):
    _snap(session, 2026, 34, [_eintrag("genre", "SciFi")])
    data = rs.annotiere_bestaendigkeit(
        session,
        _bericht(("genre", "Drama", "neutral"), ("genre", "Doku", "insufficient")),
        now=NOW,
    )
    assert "wochen_in_folge" not in _zelle(data, "genre", "Drama")
    assert "wochen_in_folge" not in _zelle(data, "genre", "Doku")


# ---------- Bewährung ----------------------------------------------------


def test_bewaehrung_vergleicht_wochenpaare(session):
    _snap(session, 2026, 33, [
        _eintrag("genre", "A"), _eintrag("genre", "B"), _eintrag("genre", "C"),
    ])
    _snap(session, 2026, 34, [_eintrag("genre", "A"), _eintrag("genre", "B")])
    _snap(session, 2026, 35, [_eintrag("genre", "A")])

    ergebnis = rs.compute_bewaehrung(session)

    # KW 35 hat keine Folgewoche — kein Paar. Neueste zuerst.
    assert [w["week"] for w in ergebnis["wochen"]] == ["2026-W34", "2026-W33"]
    w34 = ergebnis["wochen"][0]
    assert (w34["empfohlen"], w34["bestaetigt"]) == (2, 1)
    assert w34["folgewoche"] == "2026-W35"
    w33 = ergebnis["wochen"][1]
    assert (w33["empfohlen"], w33["bestaetigt"]) == (3, 2)
    assert ergebnis["gesamt"] == {
        "wochen_paare": 2, "empfohlen": 5, "bestaetigt": 3, "quote": 0.6,
    }
    assert "note" not in ergebnis


def test_bewaehrung_ohne_paare_hat_eine_ehrliche_note(session):
    _snap(session, 2026, 35, [_eintrag("genre", "A")])
    ergebnis = rs.compute_bewaehrung(session)
    assert ergebnis["wochen"] == []
    assert ergebnis["gesamt"]["quote"] is None
    assert "zwei aufeinanderfolgenden Snapshot-Wochen" in ergebnis["note"]


def test_bewaehrung_wertet_nur_empfehlungen(session):
    """Under-Zellen stehen fuer den Bestaendigkeits-Ausweis in der Row,
    sind aber keine Empfehlungen — sie duerfen weder als empfohlen noch
    als bestaetigt zaehlen."""
    _snap(session, 2026, 34, [
        _eintrag("genre", "A"), _eintrag("tone", "humorous", "under"),
    ])
    _snap(session, 2026, 35, [
        _eintrag("genre", "A"), _eintrag("tone", "humorous", "under"),
    ])

    ergebnis = rs.compute_bewaehrung(session)

    assert ergebnis["gesamt"] == {
        "wochen_paare": 1, "empfohlen": 1, "bestaetigt": 1, "quote": 1.0,
    }


# ---------- Vertrag mit dem Beweis-Loop ----------------------------------


def test_beweis_loop_filtert_under_zellen(session):
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}", platform="instagram",
        url=f"https://x.test/{uuid4()}", market=Market.US, is_own=True,
    )
    session.add(ch)
    session.commit()
    _snap(session, 2026, 33, [
        _eintrag("genre", "SciFi"),
        _eintrag("tone", "humorous", "under"),
        _alt_eintrag("format", "trailer"),
    ])

    ergebnis = compute_beweis_loop(session, now=NOW)

    # over + Altformat (= over) zaehlen, under nicht.
    assert ergebnis["summe"]["empfehlungen"] == 2
    werte = {z["value"] for z in ergebnis["wochen"][0]["zellen"]}
    assert werte == {"SciFi", "trailer"}


# ---------- Endpoints ----------------------------------------------------


def test_bewaehrung_endpoint_ist_ohne_flag_aus(client, monkeypatch):
    monkeypatch.delenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", raising=False)
    r = client.get("/api/insights/patterns/bewaehrung")
    assert r.status_code == 503
    assert "FEATURE_TRAILER_INTELLIGENCE_ENABLED" in r.json()["detail"]


def test_bewaehrung_endpoint_liefert_die_quote(client, session):
    _snap(session, 2026, 33, [_eintrag("genre", "A"), _eintrag("genre", "B")])
    _snap(session, 2026, 34, [_eintrag("genre", "A")])

    r = client.get("/api/insights/patterns/bewaehrung")

    assert r.status_code == 200
    data = r.json()
    assert data["gesamt"]["quote"] == 0.5
    assert data["wochen"][0]["week"] == "2026-W33"


def _post(session: Session, channel: Channel, *, likes: int, fmt: str) -> None:
    session.add(Post(
        channel_id=channel.id, platform=channel.platform,
        post_url=f"https://x.test/p/{uuid4()}", caption="x",
        detected_at=datetime.now(timezone.utc) - timedelta(days=1),
        visible_views=1000, visible_likes=likes, visible_comments=0,
        visible_bookmarks=0, raw_payload={},
        analysis={"format": fmt, "confidence": 0.9},
    ))
    session.commit()


def test_patterns_endpoint_annotiert_wochen_in_folge(client, session):
    """Der Public-Endpoint haengt den Ausweis an — aber nur ungefiltert:
    die Snapshots rechnen ueber alle Maerkte, gegen einen markt-
    gefilterten Bericht waeren sie nicht dieselbe Messung."""
    for _ in range(3):
        ch = Channel(
            name=f"ch-{uuid4().hex[:6]}", platform="instagram",
            url=f"https://x.test/{uuid4()}", market=Market.US,
        )
        session.add(ch)
        session.commit()
        session.refresh(ch)
        for _ in range(20):
            _post(session, ch, likes=100, fmt="clip")
        for _ in range(2):
            _post(session, ch, likes=100, fmt="trailer")
        for _ in range(8):
            _post(session, ch, likes=250, fmt="trailer")
    vorwoche = (date.today() - timedelta(weeks=1)).isocalendar()
    _snap(session, vorwoche.year, vorwoche.week, [_eintrag("format", "trailer")])

    data = client.get("/api/insights/patterns").json()
    trailer = next(c for c in data["dimensions"]["format"] if c["value"] == "trailer")
    assert trailer["breakout_verdict"] == "over"
    assert trailer["wochen_in_folge"] == 2

    gefiltert = client.get("/api/insights/patterns", params={"market": "US"}).json()
    trailer_us = next(
        c for c in gefiltert["dimensions"]["format"] if c["value"] == "trailer"
    )
    assert "wochen_in_folge" not in trailer_us
