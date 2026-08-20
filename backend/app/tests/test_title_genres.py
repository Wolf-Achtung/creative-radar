"""Trailer-Intelligence Genre-Nachrüstung (20.08.2026).

Die Muster-Aggregation (``services/trailer_patterns.py``, Stufe 1
Schritt 2) konnte bis heute keine Genre-Aussage treffen: ``title``
hatte keine Genre-Spalte, und die TMDb-``genre_ids`` wurden beim
Normalisieren verworfen. Dieser Durchgang schliesst die Kette:

    TMDb genre_ids → normalize (Namen, Reihenfolge) → title.genres
    → _genre_by_post → Dimension "genre" im Muster-Bericht
    → GET /api/insights/patterns (Flag-gegatet) → PatternsBlock.jsx

Jeder Pfeil hat hier seinen Test. Die Fixtures spiegeln die der
Bestandsdatei ``test_trailer_patterns.py`` (messbare Views sind
Pflicht — ohne sie wirft ``build_lift_context`` den Post raus, bevor
irgendeine Dimension ihn sieht).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import Asset, Channel, Market, Post, Title
from app.services import trailer_patterns as tp
from app.services.title_sync import _upsert_normalized_title
from app.services.tmdb_client import TMDbClient

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


def _post(session: Session, channel: Channel, *, views: int = 1000, likes: int = 100) -> Post:
    p = Post(
        channel_id=channel.id,
        platform=channel.platform,
        post_url=f"https://x.test/p/{uuid4()}",
        caption="x",
        detected_at=NOW - timedelta(days=1),
        visible_views=views,
        visible_likes=likes,
        visible_comments=0,
        visible_bookmarks=0,
        raw_payload={},
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


# ---------------------------------------------------------------------
# 1 — TMDb-Normalisierung: genre_ids → Namen
# ---------------------------------------------------------------------


def test_normalize_movie_uebersetzt_genre_ids_in_namen():
    client = TMDbClient(api_key="test")
    normalized = client.normalize_tmdb_movie(
        {"id": 1, "original_title": "X", "title": "X", "genre_ids": [10749, 35, 99999]}
    )
    # Reihenfolge bleibt TMDbs Reihenfolge (erstes = primaeres Genre);
    # unbekannte IDs fallen still raus — lieber ein fehlendes Genre als
    # ein geratenes.
    assert normalized["genres"] == ["Romance", "Comedy"]


def test_normalize_series_nutzt_die_tv_tabelle():
    client = TMDbClient(api_key="test")
    normalized = client.normalize_tmdb_series(
        {"id": 2, "original_name": "Y", "name": "Y", "genre_ids": [10765, 18]}
    )
    # 10765 existiert NUR im TV-Namensraum — der Test faellt, wenn
    # jemand beide Pfade auf die Movie-Tabelle zusammenlegt.
    assert normalized["genres"] == ["Sci-Fi & Fantasy", "Drama"]


# ---------------------------------------------------------------------
# 2 — Title-Sync: Reihenfolge erhalten, nichts loeschen
# ---------------------------------------------------------------------


def test_sync_schreibt_genres_in_reihenfolge(session):
    _upsert_normalized_title(
        session,
        {"tmdb_id": 77, "title_original": "Herz aus Stahl",
         "aliases": [], "genres": ["Romance", "Drama"]},
        "DE", is_series=False,
    )
    session.commit()
    titel = session.exec(select(Title).where(Title.tmdb_id == 77)).one()
    assert titel.genres == ["Romance", "Drama"], (
        "Die TMDb-Reihenfolge muss erhalten bleiben — das erste Genre ist "
        "das primaere, danach gruppiert die Muster-Aggregation."
    )


def test_sync_ohne_genres_loescht_keine_vorhandenen(session):
    """Nicht jeder Sync-Pfad liefert Genres (aeltere Fixtures, Mock).
    Eine leere Antwort darf den Bestand nicht zuruecksetzen."""
    _upsert_normalized_title(
        session,
        {"tmdb_id": 78, "title_original": "Nordlicht",
         "aliases": [], "genres": ["Horror"]},
        "DE", is_series=False,
    )
    _upsert_normalized_title(
        session,
        {"tmdb_id": 78, "title_original": "Nordlicht", "aliases": []},
        "US", is_series=False,
    )
    session.commit()
    titel = session.exec(select(Title).where(Title.tmdb_id == 78)).one()
    assert titel.genres == ["Horror"]


# ---------------------------------------------------------------------
# 3 — Genre-Dimension im Muster-Bericht
# ---------------------------------------------------------------------


def test_genre_erscheint_als_dimension_im_bericht(session):
    kanal = _channel(session)
    posts = [_post(session, kanal) for _ in range(6)]
    for p in posts[:3]:
        _titel_mit_asset(session, p, genres=["Romance", "Drama"])

    report = tp.compute_trailer_patterns(session, window_days=30, now=NOW)

    assert "genre" in report.dimensions
    werte = {c.value for c in report.dimensions["genre"]}
    # Primaeres Genre ist "Romance" — "Drama" (Zweit-Genre) bildet KEINE
    # eigene Zelle: ein Post darf nicht doppelt zaehlen.
    assert werte == {"Romance"}
    zelle = report.dimensions["genre"][0]
    assert zelle.sample_size == 3


def test_genre_dimension_ueberlebt_den_konfidenz_filter(session):
    """Genre ist ein TMDb-Fakt, kein Klassifikator-Ergebnis. Posts OHNE
    jede Analyse (confidence fehlt) muessen trotzdem in die Genre-Zelle
    — sonst kostet der Konfidenz-Filter Abdeckung, ohne Qualitaet zu
    bringen (dasselbe Argument wie bei duration/music)."""
    kanal = _channel(session)
    posts = [_post(session, kanal) for _ in range(6)]
    for p in posts:
        _titel_mit_asset(session, p, genres=["Horror"])

    report = tp.compute_trailer_patterns(session, window_days=30, now=NOW)

    zelle = next(c for c in report.dimensions["genre"] if c.value == "Horror")
    assert zelle.sample_size == 6


def test_niedrige_genre_abdeckung_steht_als_note_im_bericht(session):
    kanal = _channel(session)
    posts = [_post(session, kanal) for _ in range(6)]
    _titel_mit_asset(session, posts[0], genres=["Comedy"])

    report = tp.compute_trailer_patterns(session, window_days=30, now=NOW)

    assert any("Genre-Abdeckung" in note for note in report.notes), (
        "Bei 1/6 Genre-Abdeckung muss der Bericht das ausweisen — eine "
        "Genre-Aussage ueber einen 17-%-Ausschnitt ist sonst nicht als "
        "solche erkennbar."
    )


def test_titel_ohne_genres_erzeugt_keine_leere_zelle(session):
    kanal = _channel(session)
    posts = [_post(session, kanal) for _ in range(6)]
    _titel_mit_asset(session, posts[0], genres=[])

    report = tp.compute_trailer_patterns(session, window_days=30, now=NOW)

    assert report.dimensions["genre"] == []


# ---------------------------------------------------------------------
# 4 — Public-Endpoint hinter dem Flag
# ---------------------------------------------------------------------


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


def test_endpoint_ist_ohne_flag_aus(client, monkeypatch):
    monkeypatch.delenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", raising=False)
    antwort = client.get("/api/insights/patterns")
    assert antwort.status_code == 503
    assert "FEATURE_TRAILER_INTELLIGENCE_ENABLED" in antwort.json()["detail"]


def test_endpoint_liefert_mit_flag_den_bericht(client, session, monkeypatch):
    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")
    kanal = _channel(session)
    posts = [_post(session, kanal) for _ in range(6)]
    for p in posts:
        _titel_mit_asset(session, p, genres=["Romance"])

    antwort = client.get("/api/insights/patterns?window_days=30")
    assert antwort.status_code == 200
    daten = antwort.json()
    # Dieselbe Form wie GET /api/admin/patterns (report.to_dict) — das
    # Frontend liest dimensions/notes/posts_with_baseline.
    assert daten["window_days"] == 30
    assert "genre" in daten["dimensions"]
    assert daten["dimensions"]["genre"][0]["value"] == "Romance"
    assert daten["posts_with_baseline"] == 6


def test_admin_endpoint_bleibt_ungegatet(client, monkeypatch):
    """Wolf sieht die Auswertung in Production WEITER, auch ohne Flag —
    nur der User-Endpoint ist gegatet. Der Admin-Endpoint verlangt
    stattdessen die Admin-Session (401/403 statt 503)."""
    monkeypatch.delenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", raising=False)
    antwort = client.get("/api/admin/patterns")
    assert antwort.status_code != 503


# ---------------------------------------------------------------------
# 5 — Vertrag ueber die Grenze: der Feature-Key heisst auf beiden
# Seiten gleich
# ---------------------------------------------------------------------


@pytest.mark.vertrag
def test_frontend_liest_denselben_feature_key_wie_health_liefert():
    """PatternsBlock entscheidet zur Laufzeit ueber
    ``health.features.trailer_intelligence``, ob er rendert. Wird der
    Key auf EINER Seite umbenannt, bleiben beide Seiten fuer sich gruen
    (der Frontend-Test mockt health) — und das Panel verschwindet still
    aus Staging. Genau die Fehlerklasse, fuer die der vertrag-Job
    existiert."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    block = (repo_root / "frontend" / "src" / "PatternsBlock.jsx").read_text(
        encoding="utf-8"
    )
    assert "features?.trailer_intelligence" in block, (
        "PatternsBlock.jsx liest features.trailer_intelligence nicht mehr — "
        "entweder wurde der Key umbenannt (dann auch in api/health.py) oder "
        "das Gate ist weg."
    )

    client = TestClient(app)
    antwort = client.get("/api/health").json()
    assert "trailer_intelligence" in antwort.get("features", {}), (
        "GET /api/health liefert den Key trailer_intelligence nicht mehr — "
        "das Frontend-Gate laeuft dann ins Leere und Staging zeigt kein Panel."
    )
