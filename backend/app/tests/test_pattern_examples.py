"""Beispiel-Posts hinter den Muster-Zellen (Aufwertung B, 20.08.2026).

``GET /api/insights/patterns/examples`` macht aus einem Zellen-Befund
("langform laeuft ueber Schnitt") Referenzmaterial: die staerksten
Posts der Zelle mit Lift, Kanal und Original-Caption. Kern-Zusage:
die Zugehoerigkeit kommt aus ``posts_for_cell`` und folgt damit EXAKT
den Regeln der Zellen-Zaehlung — inklusive Konfidenz-Filter fuer
modell-erzeugte Dimensionen. Eine Zweitimplementierung im Endpoint
wuerde frueher oder spaeter Posts zeigen, die in der Zelle gar nicht
mitgezaehlt wurden.

Fixtures spiegeln ``test_title_genres.py`` (messbare Views sind
Pflicht, sonst wirft ``build_lift_context`` den Post raus).
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

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


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


def _titel_mit_asset(session: Session, post: Post, *, genres: list[str]) -> Title:
    titel = Title(title_original=f"Titel-{uuid4().hex[:6]}", genres=genres)
    session.add(titel)
    session.commit()
    session.refresh(titel)
    session.add(Asset(post_id=post.id, title_id=titel.id))
    session.commit()
    return titel


def test_endpoint_ist_ohne_flag_aus(client, monkeypatch):
    monkeypatch.delenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", raising=False)
    antwort = client.get(
        "/api/insights/patterns/examples?dimension=genre&value=Romance"
    )
    assert antwort.status_code == 503
    assert "FEATURE_TRAILER_INTELLIGENCE_ENABLED" in antwort.json()["detail"]


def test_liefert_die_staerksten_posts_der_zelle_lift_absteigend(
    client, session, monkeypatch
):
    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")
    kanal = _channel(session)
    # Gleiche Views, aufsteigende Likes: die Aktivierung — und damit der
    # Lift gegen den Kanal-Median — steigt mit den Likes. Der staerkste
    # Post ist der mit den meisten Likes.
    posts = [
        _post(session, kanal, likes=(i + 1) * 100, caption=f"caption-{i}")
        for i in range(7)
    ]
    for p in posts:
        _titel_mit_asset(session, p, genres=["Romance"])
    _titel_mit_asset(session, _post(session, kanal, likes=50), genres=["Horror"])

    antwort = client.get(
        "/api/insights/patterns/examples?dimension=genre&value=Romance"
    )
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["cell_size"] == 7
    # Default-Limit 5 — und der Horror-Post gehoert nicht in die Zelle.
    assert len(daten["examples"]) == 5
    assert daten["examples"][0]["post_url"] == posts[-1].post_url
    lifts = [ex["lift"] for ex in daten["examples"]]
    assert lifts == sorted(lifts, reverse=True), (
        "Beispiele muessen nach Lift absteigend stehen — der staerkste "
        "Post zuerst, das ist das Referenzmaterial."
    )
    assert daten["examples"][0]["caption"] == "caption-6"
    assert daten["examples"][0]["platform"] == "tiktok"


def test_konfidenz_filter_gilt_auch_fuer_die_beispiele(
    client, session, monkeypatch
):
    """Die Zellen-Zaehlung schliesst modell-erzeugte Werte unter der
    Konfidenz-Schwelle aus — die Beispiele muessen dieselben Posts
    sehen. Sonst zeigt die aufgeklappte Zeile Posts, die in der Zelle
    nicht mitgezaehlt wurden."""
    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")
    kanal = _channel(session)
    for i in range(4):
        _post(
            session, kanal, likes=(i + 1) * 100,
            analysis={"tone": "emotional", "confidence": 0.9},
        )
    for i in range(3):
        _post(
            session, kanal, likes=(i + 1) * 150,
            analysis={"tone": "emotional", "confidence": 0.2},
        )

    antwort = client.get(
        "/api/insights/patterns/examples?dimension=tone&value=emotional"
    )
    assert antwort.status_code == 200
    assert antwort.json()["cell_size"] == 4


def test_unbekannte_dimension_ist_422(client, session, monkeypatch):
    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")
    kanal = _channel(session)
    for _ in range(4):
        _post(session, kanal)
    antwort = client.get(
        "/api/insights/patterns/examples?dimension=quatsch&value=x"
    )
    assert antwort.status_code == 422


@pytest.mark.vertrag
def test_frontend_ruft_denselben_pfad_wie_das_backend_anbietet():
    """Der Aufklapper laedt ``/api/insights/patterns/examples`` mit
    ``dimension``/``value``. Wird der Pfad oder ein Parametername auf
    EINER Seite umbenannt, bleiben beide Seiten fuer sich gruen (der
    Frontend-Test mockt den Client) — und jede Zeile zeigt still
    'Keine Beispiel-Posts verfügbar'."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    client_js = (repo_root / "frontend" / "src" / "api" / "client.js").read_text(
        encoding="utf-8"
    )
    assert "/api/insights/patterns/examples" in client_js
    assert "dimension, value" in client_js

    # app.routes zeigt nur die Top-Level-Mounts — die eingehaengten
    # Router loest erst das OpenAPI-Schema auf.
    assert "/api/insights/patterns/examples" in app.openapi()["paths"]


def test_beispiele_tragen_die_asset_id_des_aeltesten_bild_assets(
    client, session, monkeypatch
):
    """Thumbnails (21.08.2026): jedes Beispiel nennt das aelteste Asset
    des Posts, das eine Bildquelle traegt (Evidence-Key oder
    CDN-Thumbnail) — der Proxy /api/thumbnails/{asset_id} macht daraus
    das Vorschaubild. Assets OHNE Bildquelle zaehlen nicht; ein Post
    ganz ohne brauchbares Asset bekommt null statt eines Platzhalters."""
    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")
    kanal = _channel(session)
    posts = [_post(session, kanal) for _ in range(6)]
    for p_ in posts:
        _titel_mit_asset(session, p_, genres=["Romance"])

    # posts[0]: erstes Asset ohne Bild (das Titel-Asset aus der Fixture),
    # zweites MIT Thumbnail -> das zweite gewinnt.
    mit_bild = Asset(post_id=posts[0].id, thumbnail_url="https://cdn.test/a.jpg")
    session.add(mit_bild)
    session.commit()
    session.refresh(mit_bild)

    antwort = client.get(
        "/api/insights/patterns/examples"
        "?dimension=genre&value=Romance&window_days=30"
    )
    assert antwort.status_code == 200
    beispiele = {e["post_url"]: e for e in antwort.json()["examples"]}

    assert beispiele[posts[0].post_url]["asset_id"] == str(mit_bild.id)
    # Die uebrigen Posts haben nur das bildlose Titel-Asset -> null.
    andere = [e for u, e in beispiele.items() if u != posts[0].post_url]
    assert andere and all(e["asset_id"] is None for e in andere)


def test_gespeichertes_bild_schlaegt_aelteren_cdn_link(
    client, session, monkeypatch
):
    """Bild-Vorrang (21.08.2026): die Karten zeigen die staerksten —
    oft Wochen alten — Posts, deren Instagram-CDN-Links tot sind. Ein
    Asset mit GESPEICHERTEM Bild (visual_evidence_url, laedt immer)
    gewinnt deshalb gegen ein aelteres Asset mit blossem CDN-Thumbnail."""
    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")
    kanal = _channel(session)
    posts = [_post(session, kanal) for _ in range(6)]
    for p_ in posts:
        _titel_mit_asset(session, p_, genres=["Romance"])

    alt_cdn = Asset(post_id=posts[0].id, thumbnail_url="https://cdn.test/tot.jpg")
    session.add(alt_cdn)
    session.commit()
    neu_gespeichert = Asset(
        post_id=posts[0].id, visual_evidence_url="evidence/asset_1.jpg"
    )
    session.add(neu_gespeichert)
    session.commit()
    session.refresh(neu_gespeichert)

    antwort = client.get(
        "/api/insights/patterns/examples"
        "?dimension=genre&value=Romance&window_days=30"
    )
    beispiele = {e["post_url"]: e for e in antwort.json()["examples"]}
    assert beispiele[posts[0].post_url]["asset_id"] == str(neu_gespeichert.id)
