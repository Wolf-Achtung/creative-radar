"""Tests for sync_titles_from_tmdb — TV/series ingest (Variante A coexistence).

TMDbClient.discover_movies/discover_series are monkeypatched to return canned
payloads; the real normalize_* + upsert logic runs against an in-memory DB."""
from __future__ import annotations

import asyncio
from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import Title
from app.services import title_sync
from app.services.tmdb_client import TMDbClient


@pytest.fixture(autouse=True)
def _network_achse_leer(monkeypatch):
    """Sprint §7 (22.08.2026): der Sync hat jetzt zusaetzlich den
    Streamer-Network-Pass. Diese Datei testet die COMPANY-Achse und
    die Upsert-Semantik — die Network-Achse wird leer gestubbt (ihre
    Tests: test_streamer_title_sync.py)."""
    async def leer(self, networks, *, language, region=None):
        return []
    monkeypatch.setattr(TMDbClient, "discover_series_by_network", leer, raising=False)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _patch_tmdb(monkeypatch, *, movies, series) -> None:
    """Patch the company-axis discover methods. ``movies``/``series`` may be a
    flat list (returned for every market) or a dict ``{market: [...]}`` to
    exercise per-market localization."""
    def _for(payload, market):
        return list(payload.get(market, [])) if isinstance(payload, dict) else list(payload)

    async def fake_movies(self, company_ids, language, region=None):
        return _for(movies, region)

    async def fake_series(self, company_ids, language, region=None):
        return _for(series, region)

    monkeypatch.setattr(TMDbClient, "discover_movies_by_company", fake_movies)
    monkeypatch.setattr(TMDbClient, "discover_series_by_company", fake_series)


def test_series_ingested_with_content_type_series(monkeypatch, session):
    _patch_tmdb(
        monkeypatch,
        movies=[],
        series=[{
            "id": 555, "name": "Murderbot", "original_name": "Murderbot",
            "first_air_date": "2026-05-16",
        }],
    )
    asyncio.run(title_sync.sync_titles_from_tmdb(session, markets=["US"], pairs=["disney"]))

    rows = session.exec(select(Title).where(Title.title_original == "Murderbot")).all()
    assert len(rows) == 1
    assert rows[0].content_type == "Series"
    assert rows[0].release_date_us == date(2026, 5, 16)
    assert rows[0].source == "TMDb"


def test_movie_path_unchanged_still_film(monkeypatch, session):
    _patch_tmdb(
        monkeypatch,
        movies=[{
            "id": 100, "title": "Dune: Part Two", "original_title": "Dune: Part Two",
            "release_date": "2026-03-01",
        }],
        series=[],
    )
    asyncio.run(title_sync.sync_titles_from_tmdb(session, markets=["US"], pairs=["disney"]))

    row = session.exec(select(Title).where(Title.tmdb_id == 100)).first()
    assert row is not None
    assert row.content_type == "Film"  # default, untouched by the movie path
    assert row.release_date_us == date(2026, 3, 1)


def test_variant_a_same_tmdb_id_film_and_series_coexist(monkeypatch, session):
    # TMDb movie- und tv-Namespaces überlappen: id 550 ist hier Film UND Serie.
    _patch_tmdb(
        monkeypatch,
        movies=[{
            "id": 550, "title": "Fight Club", "original_title": "Fight Club",
            "release_date": "2026-02-01",
        }],
        series=[{
            "id": 550, "name": "Fight Club: The Series", "original_name": "Fight Club: The Series",
            "first_air_date": "2026-02-02",
        }],
    )
    asyncio.run(title_sync.sync_titles_from_tmdb(session, markets=["US"], pairs=["disney"]))

    rows = session.exec(select(Title).where(Title.tmdb_id == 550)).all()
    assert len(rows) == 2, "film and series with same tmdb_id must coexist as two rows"
    by_type = {r.content_type: r for r in rows}
    assert set(by_type) == {"Film", "Series"}
    # Neither overwrote the other.
    assert by_type["Film"].title_original == "Fight Club"
    assert by_type["Series"].title_original == "Fight Club: The Series"

    # Idempotent: a second run updates in place, no duplication.
    asyncio.run(title_sync.sync_titles_from_tmdb(session, markets=["US"], pairs=["disney"]))
    rows2 = session.exec(select(Title).where(Title.tmdb_id == 550)).all()
    assert len(rows2) == 2


def test_per_market_dedup_populates_both_release_dates(monkeypatch, session):
    """Same title in both DE and US company-discover: the per-market dedup key
    must let BOTH passes upsert, so release_date_de AND release_date_us land and
    the DE-Verleihtitel survives in aliases (localization preserved)."""
    _patch_tmdb(
        monkeypatch,
        movies={
            "DE": [{"id": 277, "title": "Vaiana", "original_title": "Moana",
                    "release_date": "2026-07-10"}],
            "US": [{"id": 277, "title": "Moana", "original_title": "Moana",
                    "release_date": "2026-07-12"}],
        },
        series=[],
    )
    asyncio.run(title_sync.sync_titles_from_tmdb(session, markets=["DE", "US"], pairs=["disney"]))

    row = session.exec(select(Title).where(Title.tmdb_id == 277)).first()
    assert row is not None
    assert row.release_date_de == date(2026, 7, 10)
    assert row.release_date_us == date(2026, 7, 12)
    # DE-Verleihtitel "Vaiana" must be reachable as an alias for caption matching.
    assert "Vaiana" in (row.aliases or [])


def test_streamers_not_synced_by_default(monkeypatch, session):
    """Company-Achse bleibt Studio-Revier: ein voller Lauf (pairs=None)
    ruft den Company-Discover nur mit den 6 Produktionsstudio-Sets auf,
    nie mit einem Streamer-Set. Die Streamer laufen seit Sprint §7
    (22.08.2026) ueber die NETWORK-Achse — getestet in
    test_streamer_title_sync.py."""
    called_companies: list[str] = []

    async def rec_movies(self, company_ids, language, region=None):
        called_companies.append(company_ids)
        return []

    async def rec_series(self, company_ids, language, region=None):
        return []

    monkeypatch.setattr(TMDbClient, "discover_movies_by_company", rec_movies)
    monkeypatch.setattr(TMDbClient, "discover_series_by_company", rec_series)

    asyncio.run(title_sync.sync_titles_from_tmdb(session, markets=["US"]))

    from app.services.title_sync import PAIR_COMPANY_SETS
    expected = {"|".join(str(c) for c in v) for v in PAIR_COMPANY_SETS.values()}
    assert set(called_companies) == expected
    assert len(PAIR_COMPANY_SETS) == 6  # 6 production studios, no streamers
