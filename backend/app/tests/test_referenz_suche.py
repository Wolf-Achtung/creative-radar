"""Referenz-Suche (Roadmap Schritt 1, 25.08.2026).

``GET /api/insights/referenzen`` ist die Moodboard-Werkbank: Facetten-
Filter ueber die analysierten Posts, sortiert nach Kanal-normiertem
Lift. Kern-Zusagen, die diese Tests sichern:

1. Zugehoerigkeit kommt aus ``facetten_werte_je_post`` und folgt damit
   EXAKT den Regeln der Muster-Zellen — Konfidenz-Filter auf modell-
   erzeugten Dimensionen, Vision-Konfidenz auf den Cover-Dimensionen.
   Was hier "Horror mit Titel im Bild" heisst, heisst im Muster-Panel
   dasselbe.
2. Facetten schneiden sich; ein unbekannter WERT ist eine leere
   Treffermenge (der Wertevorrat ist offen), eine unbekannte DIMENSION
   ist ein Fehler (422 — sonst filtert ein Tippfehler still nichts).
3. Die Facetten-Zaehlung zaehlt die GEFILTERTE Menge — sie beantwortet
   "was kann ich von hier aus noch einengen".
4. Gespeicherte Bilder schlagen CDN-Thumbnails (Wolf-Befund 21.08.:
   alte Instagram-CDN-Links sind tot).

Fixtures spiegeln ``test_pattern_examples.py`` (messbare Views sind
Pflicht, Kanal-Baseline braucht >= 4 Posts).
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
from app.services.referenz_suche import suche_referenzen

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


def _channel(session: Session, *, platform: str = "tiktok") -> Channel:
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}",
        handle=f"handle-{uuid4().hex[:6]}",
        platform=platform,
        url=f"https://x.test/{uuid4()}",
        market=Market.US,
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


def _titel_mit_asset(
    session: Session, post: Post, *, genres: list[str], name: str | None = None
) -> Title:
    titel = Title(
        title_original=name or f"Titel-{uuid4().hex[:6]}", genres=genres
    )
    session.add(titel)
    session.commit()
    session.refresh(titel)
    session.add(Asset(post_id=post.id, title_id=titel.id))
    session.commit()
    return titel


def _vision_asset(
    session: Session,
    post: Post,
    *,
    score: float = 0.9,
    mit_titel: bool = True,
) -> Asset:
    a = Asset(
        post_id=post.id,
        visual_analysis_status="analyzed",
        visual_confidence_score=score,
        has_title_placement=mit_titel,
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


# ---------- Service ----------------------------------------------------


def test_facetten_schneiden_sich(session: Session):
    kanal = _channel(session)
    horror_frage = _post(session, kanal, likes=400, caption="Traust du dich?")
    horror_ohne = _post(session, kanal, likes=300, caption="Nur im Kino.")
    romance = _post(session, kanal, likes=200, caption="Wer kommt mit?")
    _post(session, kanal, likes=100, caption="Basis")
    _titel_mit_asset(session, horror_frage, genres=["Horror"])
    _titel_mit_asset(session, horror_ohne, genres=["Horror"])
    _titel_mit_asset(session, romance, genres=["Romance"])

    ergebnis = suche_referenzen(
        session,
        facetten={"genre": "Horror", "caption_frage": "mit_frage"},
        now=NOW,
    )
    assert ergebnis["gesamt"] == 1
    assert ergebnis["treffer"][0]["post_url"] == horror_frage.post_url
    assert ergebnis["treffer"][0]["genre"] == "Horror"


def test_unbekannte_dimension_wirft(session: Session):
    kanal = _channel(session)
    for i in range(4):
        _post(session, kanal, likes=(i + 1) * 100)
    with pytest.raises(ValueError, match="Unbekannte Facette"):
        suche_referenzen(session, facetten={"gibtsnicht": "x"}, now=NOW)


def test_unbekannter_wert_ist_leere_treffermenge(session: Session):
    kanal = _channel(session)
    for i in range(4):
        _post(session, kanal, likes=(i + 1) * 100)
    ergebnis = suche_referenzen(
        session, facetten={"genre": "Gibtsnicht"}, now=NOW
    )
    assert ergebnis["gesamt"] == 0
    assert ergebnis["treffer"] == []


def test_sortiert_nach_lift_und_limit_trennt_gesamt(session: Session):
    kanal = _channel(session)
    posts = [
        _post(session, kanal, likes=(i + 1) * 100, caption=f"caption-{i}")
        for i in range(6)
    ]
    ergebnis = suche_referenzen(session, limit=3, now=NOW)
    assert ergebnis["gesamt"] == 6
    assert len(ergebnis["treffer"]) == 3
    assert ergebnis["treffer"][0]["post_url"] == posts[-1].post_url
    lifts = [t["lift"] for t in ergebnis["treffer"]]
    assert lifts == sorted(lifts, reverse=True)


def test_min_lift_filtert(session: Session):
    kanal = _channel(session)
    # Median der Aktivierung liegt zwischen den vier Posts; nur der
    # staerkste erreicht das Doppelte des Medians.
    _post(session, kanal, likes=100)
    _post(session, kanal, likes=100)
    _post(session, kanal, likes=120)
    stark = _post(session, kanal, likes=400)
    ergebnis = suche_referenzen(session, min_lift=2.0, now=NOW)
    assert [t["post_url"] for t in ergebnis["treffer"]] == [stark.post_url]


def test_platform_filtert(session: Session):
    tiktok = _channel(session, platform="tiktok")
    youtube = _channel(session, platform="youtube")
    for i in range(4):
        _post(session, tiktok, likes=(i + 1) * 100)
        _post(session, youtube, likes=(i + 1) * 100)
    ergebnis = suche_referenzen(session, platform="youtube", now=NOW)
    assert ergebnis["gesamt"] == 4
    assert all(t["platform"] == "youtube" for t in ergebnis["treffer"])


def test_facetten_zaehlung_zaehlt_die_gefilterte_menge(session: Session):
    kanal = _channel(session)
    horror_frage = _post(session, kanal, likes=400, caption="Traust du dich?")
    horror_ohne = _post(session, kanal, likes=300, caption="Nur so.")
    romance = _post(session, kanal, likes=200, caption="Wer kommt mit?")
    _post(session, kanal, likes=100, caption="Basis")
    _titel_mit_asset(session, horror_frage, genres=["Horror"])
    _titel_mit_asset(session, horror_ohne, genres=["Horror"])
    _titel_mit_asset(session, romance, genres=["Romance"])

    alles = suche_referenzen(session, now=NOW)
    genre_alle = {e["wert"]: e["anzahl"] for e in alles["facetten_zaehlung"]["genre"]}
    assert genre_alle == {"Horror": 2, "Romance": 1}

    gefiltert = suche_referenzen(
        session, facetten={"caption_frage": "mit_frage"}, now=NOW
    )
    genre_gefiltert = {
        e["wert"]: e["anzahl"] for e in gefiltert["facetten_zaehlung"]["genre"]
    }
    assert genre_gefiltert == {"Horror": 1, "Romance": 1}, (
        "Die Zaehlung muss die GEFILTERTE Menge zaehlen — sie sagt, was "
        "sich von hier aus noch einengen laesst."
    )


def test_cover_facette_respektiert_vision_konfidenz(session: Session):
    kanal = _channel(session)
    sicher = _post(session, kanal, likes=400)
    unsicher = _post(session, kanal, likes=300)
    _post(session, kanal, likes=200)
    _post(session, kanal, likes=100)
    _vision_asset(session, sicher, score=0.9, mit_titel=True)
    _vision_asset(session, unsicher, score=0.3, mit_titel=True)

    ergebnis = suche_referenzen(
        session, facetten={"cover_titel": "mit_titel"}, now=NOW
    )
    assert [t["post_url"] for t in ergebnis["treffer"]] == [sicher.post_url], (
        "Ein Vision-Ergebnis unter der Konfidenz-Schwelle darf nicht in "
        "die Cover-Facette zaehlen — dieselbe Regel wie im Muster-Panel."
    )


def test_modell_dimension_respektiert_post_konfidenz(session: Session):
    kanal = _channel(session)
    sicher = _post(
        session, kanal, likes=400,
        analysis={"tone": "humorous", "confidence": 0.9},
    )
    _post(
        session, kanal, likes=300,
        analysis={"tone": "humorous", "confidence": 0.3},
    )
    _post(session, kanal, likes=200)
    _post(session, kanal, likes=100)

    ergebnis = suche_referenzen(session, facetten={"tone": "humorous"}, now=NOW)
    assert [t["post_url"] for t in ergebnis["treffer"]] == [sicher.post_url]


def test_gespeichertes_bild_schlaegt_cdn(session: Session):
    kanal = _channel(session)
    post = _post(session, kanal, likes=400)
    for i in range(3):
        _post(session, kanal, likes=(i + 1) * 100)
    cdn = Asset(post_id=post.id, thumbnail_url="https://cdn.test/bild.jpg")
    session.add(cdn)
    session.commit()
    gespeichert = Asset(
        post_id=post.id, visual_evidence_url="evidence/bild.jpg"
    )
    session.add(gespeichert)
    session.commit()
    session.refresh(gespeichert)

    ergebnis = suche_referenzen(session, now=NOW)
    treffer = {t["post_url"]: t for t in ergebnis["treffer"]}
    assert treffer[post.post_url]["asset_id"] == str(gespeichert.id)


def test_treffer_traegt_titel_und_kanal(session: Session):
    kanal = _channel(session)
    post = _post(session, kanal, likes=400)
    for i in range(3):
        _post(session, kanal, likes=(i + 1) * 100)
    _titel_mit_asset(session, post, genres=["Horror"], name="Steckerlfisch Fiasko")

    ergebnis = suche_referenzen(session, now=NOW)
    erster = ergebnis["treffer"][0]
    assert erster["titel"] == "Steckerlfisch Fiasko"
    assert erster["genre"] == "Horror"
    assert erster["channel_handle"] == kanal.handle
    assert erster["market"] == "US"


# ---------- Endpoint ---------------------------------------------------


def test_endpoint_ist_ohne_flag_aus(client, monkeypatch):
    monkeypatch.delenv("FEATURE_REFERENZ_SUCHE_ENABLED", raising=False)
    antwort = client.get("/api/insights/referenzen")
    assert antwort.status_code == 503
    assert "FEATURE_REFERENZ_SUCHE_ENABLED" in antwort.json()["detail"]


def test_endpoint_parst_facetten(client, session, monkeypatch):
    monkeypatch.setenv("FEATURE_REFERENZ_SUCHE_ENABLED", "true")
    kanal = _channel(session)
    horror = _post(session, kanal, likes=400)
    for i in range(3):
        _post(session, kanal, likes=(i + 1) * 100)
    _titel_mit_asset(session, horror, genres=["Horror"])

    antwort = client.get(
        "/api/insights/referenzen?facette=genre=Horror&min_lift=1.5"
    )
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["facetten"] == {"genre": "Horror"}
    assert daten["gesamt"] == 1
    assert daten["treffer"][0]["post_url"] == horror.post_url


def test_endpoint_unlesbare_facette_ist_422(client, monkeypatch):
    monkeypatch.setenv("FEATURE_REFERENZ_SUCHE_ENABLED", "true")
    antwort = client.get("/api/insights/referenzen?facette=genreHorror")
    assert antwort.status_code == 422
    assert "nicht lesbar" in antwort.json()["detail"]


def test_endpoint_unbekannte_dimension_ist_422(client, session, monkeypatch):
    monkeypatch.setenv("FEATURE_REFERENZ_SUCHE_ENABLED", "true")
    kanal = _channel(session)
    for i in range(4):
        _post(session, kanal, likes=(i + 1) * 100)
    antwort = client.get("/api/insights/referenzen?facette=gibtsnicht=x")
    assert antwort.status_code == 422
    assert "Unbekannte Facette" in antwort.json()["detail"]
