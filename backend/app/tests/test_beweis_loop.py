"""Beweis-Loop (Roadmap Schritt 3, 25.08.2026).

Das Vorher/Nachher ueber die Empfehlungs-Snapshots. Die Tests sichern
die Verbindungs-Entscheidungen:

1. "Umgesetzt" zaehlt NUR die Folgewoche der Snapshot-Woche — ein Post
   in derselben Woche kann nicht auf die Empfehlung reagiert haben.
2. "Wir" ist die Wir-Segment-Definition (is_own-Kanaele plus
   Wir-Projekt-Titel), keine Zweitdefinition.
3. "Gewirkt" ist Median-Lift >= 1 der umgesetzten Posts; ``null`` ohne
   Umsetzung — keine Wirkung ohne Versuch.
4. Ohne Snapshots bzw. ohne Wir-Markierung gibt es einen Klartext-
   Hinweis statt leerer Zahlen.
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
from app.models.entities import (
    Asset,
    Channel,
    Market,
    Post,
    RecommendationSnapshot,
    Title,
)
from app.services.beweis_loop import compute_beweis_loop

# Ein fester Montag als Anker: Snapshot-Woche ist die Woche davor,
# die Folgewoche beginnt an diesem Montag.
FOLGEWOCHE_MONTAG = date(2026, 8, 17)  # Montag, ISO 2026-W34
SNAPSHOT_JAHR, SNAPSHOT_WOCHE = 2026, 33
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)  # nach Ende der Folgewoche


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


def _channel(session: Session, *, own: bool = False) -> Channel:
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}",
        handle=f"handle-{uuid4().hex[:6]}",
        platform="tiktok",
        url=f"https://x.test/{uuid4()}",
        market=Market.DE,
        is_own=own,
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
    am: date | None = None,
    analysis: dict | None = None,
) -> Post:
    moment = datetime.combine(
        am or (FOLGEWOCHE_MONTAG + timedelta(days=2)),
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(hours=12)
    p = Post(
        channel_id=channel.id,
        platform=channel.platform,
        post_url=f"https://x.test/p/{uuid4()}",
        caption="x",
        detected_at=moment,
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


def _snapshot(session: Session, *, cells: list[dict] | None = None) -> None:
    session.add(
        RecommendationSnapshot(
            iso_year=SNAPSHOT_JAHR,
            iso_week=SNAPSHOT_WOCHE,
            window_days=90,
            cells=cells if cells is not None else [
                {"dimension": "tone", "value": "humorous", "median_lift": 1.6},
            ],
        )
    )
    session.commit()


def _humor(confidence: float = 0.9) -> dict:
    return {"tone": "humorous", "confidence": confidence}


def _baseline(session: Session, kanal: Channel, *, tage_vor_folgewoche: int = 30) -> None:
    """Vier Posts mit Aktivierung 0.1, deutlich vor der Folgewoche —
    die Kanal-Baseline, an der der Lift gemessen wird."""
    for i in range(4):
        _post(
            session, kanal, likes=100,
            am=FOLGEWOCHE_MONTAG - timedelta(days=tage_vor_folgewoche + i),
        )


def test_ohne_snapshots_kommt_eine_note(session: Session):
    _channel(session, own=True)
    ergebnis = compute_beweis_loop(session, now=NOW)
    assert "Snapshot" in ergebnis["note"]
    assert ergebnis["wochen"] == []


def test_ohne_wir_markierung_kommt_eine_note(session: Session):
    _snapshot(session)
    _channel(session, own=False)
    ergebnis = compute_beweis_loop(session, now=NOW)
    assert "Wir" in ergebnis["note"]


def test_umgesetzt_und_gewirkt_in_der_folgewoche(session: Session):
    _snapshot(session)
    kanal = _channel(session, own=True)
    _baseline(session, kanal)
    # Zwei humorvolle Wir-Posts in der Folgewoche: Lift 4 und 2 —
    # Median 3, ueber dem Kanal-Schnitt.
    _post(session, kanal, likes=400, analysis=_humor())
    _post(
        session, kanal, likes=200,
        am=FOLGEWOCHE_MONTAG + timedelta(days=4), analysis=_humor(),
    )
    # Ein NICHT-humorvoller Post in der Folgewoche zaehlt nicht.
    _post(session, kanal, likes=300)

    ergebnis = compute_beweis_loop(session, now=NOW)
    assert ergebnis["note"] is None
    woche = ergebnis["wochen"][0]
    assert woche["week"] == "2026-W33"
    assert woche["folgewoche_start"] == FOLGEWOCHE_MONTAG.isoformat()
    assert woche["folgewoche_abgeschlossen"] is True
    zelle = woche["zellen"][0]
    assert zelle["umgesetzt"] == 2
    assert zelle["median_lift_wir"] == 3.0
    assert zelle["gewirkt"] is True
    assert ergebnis["summe"] == {"empfehlungen": 1, "umgesetzt": 1, "gewirkt": 1}


def test_posts_der_snapshot_woche_zaehlen_nicht(session: Session):
    """Der Mutations-Anker: ein humorvoller Post in der SNAPSHOT-Woche
    selbst kann nicht auf die Empfehlung reagiert haben."""
    _snapshot(session)
    kanal = _channel(session, own=True)
    _baseline(session, kanal)
    _post(
        session, kanal, likes=400,
        am=FOLGEWOCHE_MONTAG - timedelta(days=3), analysis=_humor(),
    )
    ergebnis = compute_beweis_loop(session, now=NOW)
    zelle = ergebnis["wochen"][0]["zellen"][0]
    assert zelle["umgesetzt"] == 0
    assert zelle["gewirkt"] is None
    assert zelle["median_lift_wir"] is None


def test_fremde_posts_zaehlen_nicht_wir_projekt_titel_schon(session: Session):
    _snapshot(session)
    fremd = _channel(session, own=False)
    _baseline(session, fremd)
    # Fremder humorvoller Post in der Folgewoche: kein Wir-Post …
    _post(session, fremd, likes=400, analysis=_humor())
    # … ausser sein Asset zeigt auf ein Wir-Projekt.
    projekt = Title(title_original="Unser Film", is_own_project=True)
    session.add(projekt)
    session.commit()
    session.refresh(projekt)
    projekt_post = _post(
        session, fremd, likes=200,
        am=FOLGEWOCHE_MONTAG + timedelta(days=3), analysis=_humor(),
    )
    session.add(Asset(post_id=projekt_post.id, title_id=projekt.id))
    session.commit()

    ergebnis = compute_beweis_loop(session, now=NOW)
    zelle = ergebnis["wochen"][0]["zellen"][0]
    assert zelle["umgesetzt"] == 1


def test_unter_dem_schnitt_ist_nicht_gewirkt(session: Session):
    _snapshot(session)
    kanal = _channel(session, own=True)
    _baseline(session, kanal)
    # Aktivierung 0.05 gegen Baseline 0.1 → Lift 0.5.
    _post(session, kanal, likes=50, analysis=_humor())
    ergebnis = compute_beweis_loop(session, now=NOW)
    zelle = ergebnis["wochen"][0]["zellen"][0]
    assert zelle["umgesetzt"] == 1
    assert zelle["gewirkt"] is False


def test_laufende_folgewoche_ist_markiert(session: Session):
    _snapshot(session)
    _channel(session, own=True)
    # "Heute" mitten in der Folgewoche.
    mitten = datetime.combine(
        FOLGEWOCHE_MONTAG + timedelta(days=2),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    ergebnis = compute_beweis_loop(session, now=mitten)
    assert ergebnis["wochen"][0]["folgewoche_abgeschlossen"] is False


# ---------- Endpoint ---------------------------------------------------


def test_endpoint_ist_ohne_flag_aus(client, monkeypatch):
    monkeypatch.delenv("FEATURE_BEWEIS_LOOP_ENABLED", raising=False)
    antwort = client.get("/api/admin/beweis-loop")
    assert antwort.status_code == 503
    assert "FEATURE_BEWEIS_LOOP_ENABLED" in antwort.json()["detail"]


def test_endpoint_liefert_die_auswertung(client, session, monkeypatch):
    monkeypatch.setenv("FEATURE_BEWEIS_LOOP_ENABLED", "true")
    _snapshot(session)
    _channel(session, own=True)
    antwort = client.get("/api/admin/beweis-loop")
    assert antwort.status_code == 200
    assert antwort.json()["wochen"][0]["week"] == "2026-W33"
