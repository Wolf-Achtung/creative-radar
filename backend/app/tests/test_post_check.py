"""Post-Check (Roadmap-Ausbau, 25.08.2026).

Die Blickrichtungs-Umkehr: ein Entwurf wird VOR dem Posten gegen die
aktuellen Befunde gehalten. Kern-Zusagen:

1. Die Entwurfs-Werte kommen aus den ECHTEN Extraktoren der
   DIMENSIONS-Registry — ein Entwurf landet im selben Bucket wie ein
   echter Post mit denselben Eigenschaften. Keine Zweitimplementierung,
   die auseinanderlaufen kann.
2. Befund-Abbildung: over → gut, under → achtung (mit dem staerksten
   besser laufenden Geschwister-Wert als Tipp), insufficient/fehlend →
   "kein Befund" statt geratenem Urteil.
3. Selbstauskunft (Format, Tonfall) laeuft ueber den normalen
   analysis-Pfad — dieselbe Zell-Zuordnung wie bei analysierten Posts.
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
from app.models.entities import Channel, Market, Post
from app.services.post_check import pruefe_post

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


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

    def _override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _channel(session: Session) -> Channel:
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}",
        platform="tiktok",
        url=f"https://x.test/{uuid4()}",
        market=Market.DE,
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
    caption: str = "x",
    analysis: dict | None = None,
) -> Post:
    p = Post(
        channel_id=channel.id,
        platform=channel.platform,
        post_url=f"https://x.test/p/{uuid4()}",
        caption=caption,
        detected_at=NOW - timedelta(days=1),
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


def _frage_korpus(session: Session) -> None:
    """Sechs Kanaele: je vier Basis-Posts ohne Frage (kein Breakout)
    und ein Frage-Post mit Lift 4 (Breakout). Ergibt belastbare Zellen:
    mit_frage → over, ohne_frage → under."""
    for _ in range(6):
        kanal = _channel(session)
        for _ in range(4):
            _post(session, kanal, likes=100, caption="Nur so.")
        _post(session, kanal, likes=400, caption="Traust du dich?")


def _check(ergebnis: dict, dimension: str) -> dict:
    treffer = [z for z in ergebnis["checks"] if z["dimension"] == dimension]
    assert treffer, f"{dimension} fehlt: {[z['dimension'] for z in ergebnis['checks']]}"
    return treffer[0]


def test_entwurf_laeuft_durch_die_echten_extraktoren(session: Session):
    _frage_korpus(session)
    ergebnis = pruefe_post(session, caption="Traust du dich?", now=NOW)
    zeile = _check(ergebnis, "caption_frage")
    assert zeile["wert"] == "mit_frage"
    assert zeile["befund"] == "gut"
    assert "funktioniert gerade" in zeile["satz"]
    # Laengen-Bucket wie bei echten Posts: 15 Zeichen → kurz.
    assert _check(ergebnis, "caption_laenge")["wert"] == "kurz"


def test_under_gibt_achtung_mit_geschwister_tipp(session: Session):
    _frage_korpus(session)
    ergebnis = pruefe_post(session, caption="Ohne jede Frage.", now=NOW)
    zeile = _check(ergebnis, "caption_frage")
    assert zeile["wert"] == "ohne_frage"
    assert zeile["befund"] == "achtung"
    assert zeile["satz"].endswith("funktioniert gerade nicht.")
    assert zeile["tipp"] == "Gerade besser: Caption mit Frage."


def test_duenne_daten_geben_kein_urteil(session: Session):
    # Nur ein Kanal mit vier Posts: keine Zelle wird belastbar.
    kanal = _channel(session)
    for i in range(4):
        _post(session, kanal, likes=(i + 1) * 100)
    ergebnis = pruefe_post(session, caption="Traust du dich?", now=NOW)
    zeile = _check(ergebnis, "caption_frage")
    assert zeile["befund"] == "kein_befund"
    assert "keinen belastbaren Befund" in zeile["satz"]
    assert ergebnis["zusammenfassung"]["achtung"] == 0


def test_selbstauskunft_tonfall_nutzt_den_analysis_pfad(session: Session):
    for _ in range(6):
        kanal = _channel(session)
        for _ in range(4):
            _post(session, kanal, likes=100)
        _post(
            session, kanal, likes=400,
            analysis={"tone": "humorous", "confidence": 0.9},
        )
    ergebnis = pruefe_post(session, caption="x", ton_wert="humorous", now=NOW)
    zeile = _check(ergebnis, "tone")
    assert zeile["wert"] == "humorous"
    assert zeile["befund"] == "gut"
    # Ohne Selbstauskunft taucht die Dimension nicht auf — kein Raten.
    ohne = pruefe_post(session, caption="x", now=NOW)
    assert not [z for z in ohne["checks"] if z["dimension"] == "tone"]


def test_titel_im_bild_prueft_die_cover_zelle(session: Session):
    kanal = _channel(session)
    for i in range(4):
        _post(session, kanal, likes=(i + 1) * 100)
    ergebnis = pruefe_post(session, caption="x", titel_im_bild=True, now=NOW)
    zeile = _check(ergebnis, "cover_titel")
    assert zeile["wert"] == "mit_titel"
    ohne = pruefe_post(session, caption="x", now=NOW)
    assert not [z for z in ohne["checks"] if z["dimension"] == "cover_titel"]


def test_basis_und_fenster_stehen_im_ergebnis(session: Session):
    _frage_korpus(session)
    ergebnis = pruefe_post(session, caption="x", now=NOW)
    assert ergebnis["window_days"] == 90
    assert ergebnis["basis"]["posts"] == 30
    assert ergebnis["basis"]["kanaele"] == 6


# ---------- Endpoint ---------------------------------------------------


def test_endpoint_ist_ohne_flag_aus(client, monkeypatch):
    monkeypatch.delenv("FEATURE_POST_CHECK_ENABLED", raising=False)
    antwort = client.post("/api/insights/post-check", json={"caption": "x"})
    assert antwort.status_code == 503
    assert "FEATURE_POST_CHECK_ENABLED" in antwort.json()["detail"]


def test_endpoint_weist_unbekanntes_format_ab(client, monkeypatch):
    monkeypatch.setenv("FEATURE_POST_CHECK_ENABLED", "true")
    antwort = client.post(
        "/api/insights/post-check",
        json={"caption": "x", "format": "gibtsnicht"},
    )
    assert antwort.status_code == 422
    assert "Unbekanntes Format" in antwort.json()["detail"]


def test_endpoint_liefert_checks(client, session, monkeypatch):
    monkeypatch.setenv("FEATURE_POST_CHECK_ENABLED", "true")
    _frage_korpus(session)
    antwort = client.post(
        "/api/insights/post-check",
        json={"caption": "Traust du dich?", "titel_im_bild": False},
    )
    assert antwort.status_code == 200
    daten = antwort.json()
    frage = [z for z in daten["checks"] if z["dimension"] == "caption_frage"]
    assert frage and frage[0]["befund"] == "gut"
