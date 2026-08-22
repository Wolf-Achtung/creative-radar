"""Streamer-Originals im Titel-Sync (Sprint §7, 22.08.2026).

Die 3 Streamer-Pairs (netflix, primevideo, paramountplus) bekamen bis
heute keinen Titel-Sync — die Hauptursache der "Titel fehlt im
Katalog"-Vorschlaege auf Streamer-Kanaelen. Serien laufen jetzt ueber
die TMDb-Network-Achse (``with_networks`` auf /discover/tv).

Vertragspunkte:
- Jeder Streamer wird je Markt einmal discovered; Serien landen als
  Series-Rows mit Genres/Aliases (derselbe Upsert wie der Studio-Pass).
- Das seen_keys-Dedup teilt sich den Namensraum mit dem Studio-Pass —
  eine Serie, die beide Achsen liefern, wird nicht doppelt upserted.
- Kill-Switch ``STREAMER_TITLE_SYNC_ENABLED`` skippt ohne Network-Call.
- Streamer-FILME bleiben bewusst aus (kein Network-Filter fuer Filme;
  with_watch_providers zoege den Lizenz-Katalog in die Whitelist) —
  STREAMER_COMPANY_SETS startet leer und wird erst nach Verifikation
  kuratiert.
"""
from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.models.entities import Title
from app.services import title_sync as ts


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


@pytest.fixture(autouse=True)
def _stages_stumm(monkeypatch):
    """Anreicherung + Genre-Backfill stummschalten — diese Tests
    beobachten nur den Streamer-Pass."""
    async def _leer(session_, *, client=None, max_titles=None):
        return {}
    monkeypatch.setattr(ts, "enrich_titles_without_tmdb_id", _leer)
    monkeypatch.setattr(ts, "backfill_missing_genres", _leer)


def _serie(tmdb_id, name, *, genre_ids=(18,)):
    return {
        "id": tmdb_id,
        "name": name,
        "original_name": name,
        "first_air_date": "2026-05-01",
        "genre_ids": list(genre_ids),
        "popularity": 50.0,
    }


class _FakeClient:
    """Discover-Stub: Studio-Achse leer (oder konfigurierbar), Network-
    Achse liefert je Netzwerk-String vorbereitete Serien. Nutzt die
    ECHTE Normalisierung, damit der Feld-Vertrag mitgeprueft wird."""

    def __init__(self, serien_je_netzwerk=None, studio_serien=None):
        self.serien_je_netzwerk = serien_je_netzwerk or {}
        self.studio_serien = studio_serien or []
        self.network_abrufe: list[tuple[str, str]] = []

    @staticmethod
    def tmdb_region(market):
        return market

    async def discover_movies_by_company(self, *a, **k):
        return []

    async def discover_series_by_company(self, *a, **k):
        return list(self.studio_serien)

    async def discover_series_by_network(self, networks, *, language, region=None):
        self.network_abrufe.append((networks, language))
        return list(self.serien_je_netzwerk.get(networks, []))

    from app.services.tmdb_client import TMDbClient as _Echt
    normalize_tmdb_movie = _Echt.normalize_tmdb_movie
    normalize_tmdb_series = _Echt.normalize_tmdb_series
    _genre_names = _Echt._genre_names
    _MOVIE_GENRES = _Echt._MOVIE_GENRES
    _TV_GENRES = _Echt._TV_GENRES


def _mit_client(monkeypatch, client):
    monkeypatch.setattr(ts, "TMDbClient", lambda: client)
    # tmdb_region wird als Klassen-Staticmethod aufgerufen:
    monkeypatch.setattr(ts.TMDbClient, "tmdb_region", staticmethod(lambda m: m), raising=False)


async def test_streamer_serien_landen_als_series_rows(session, monkeypatch):
    monkeypatch.setattr(settings, "streamer_title_sync_enabled", True, raising=False)
    client = _FakeClient(serien_je_netzwerk={
        "213": [_serie(9001, "Stranger Tides")],
        "1024": [_serie(9002, "Prime Saga")],
        "4330": [_serie(9003, "Mountain Head")],
    })
    _mit_client(monkeypatch, client)

    result = await ts.sync_titles_from_tmdb(session, markets=["DE"], pairs=["lionsgate"])

    rows = session.exec(select(Title)).all()
    namen = sorted(t.title_original for t in rows)
    assert namen == ["Mountain Head", "Prime Saga", "Stranger Tides"]
    assert all(t.content_type == "Series" and t.tmdb_id for t in rows)
    assert all(t.genres == ["Drama"] for t in rows), "Genres muessen aus genre_ids gemappt sein."
    assert result["streamer"]["upserted_count"] == 3
    assert ("213", "de-DE") in client.network_abrufe


async def test_dedup_mit_dem_studio_pass_verhindert_doppel_upsert(session, monkeypatch):
    """Eine Sony-produzierte Netflix-Serie kommt ueber BEIDE Achsen —
    sie darf nur einmal upserted werden."""
    monkeypatch.setattr(settings, "streamer_title_sync_enabled", True, raising=False)
    doppelt = _serie(9100, "Beide Achsen")
    client = _FakeClient(
        studio_serien=[doppelt],
        serien_je_netzwerk={"213": [doppelt]},
    )
    _mit_client(monkeypatch, client)

    result = await ts.sync_titles_from_tmdb(session, markets=["DE"], pairs=["lionsgate"])

    rows = session.exec(select(Title).where(Title.tmdb_id == 9100)).all()
    assert len(rows) == 1
    assert result["deduped_count"] >= 1
    assert result["streamer"]["upserted_count"] == 0, (
        "Der Studio-Pass hat die Serie schon upserted — der Streamer-"
        "Pass darf sie nur als Duplikat zaehlen."
    )


async def test_kill_switch_skippt_ohne_network_call(session, monkeypatch):
    monkeypatch.setattr(settings, "streamer_title_sync_enabled", False, raising=False)
    client = _FakeClient(serien_je_netzwerk={"213": [_serie(9001, "X")]})
    _mit_client(monkeypatch, client)

    result = await ts.sync_titles_from_tmdb(session, markets=["DE"], pairs=["lionsgate"])

    assert client.network_abrufe == []
    assert result["streamer"] == {"enabled": False}
    assert session.exec(select(Title)).all() == []


def test_streamer_film_companies_starten_bewusst_leer():
    assert ts.STREAMER_COMPANY_SETS == {}, (
        "Company-IDs fuer Streamer-Filme erst nach Verifikation gegen "
        "bekannte Titel eintragen (Muster diag_resolve_company_ids) — "
        "geratene IDs ziehen Fremdstoff in die Whitelist."
    )
