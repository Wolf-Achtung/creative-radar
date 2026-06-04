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


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _patch_tmdb(monkeypatch, *, movies: list[dict], series: list[dict]) -> None:
    async def fake_movies(self, region, language, date_from, date_to):
        return list(movies)

    async def fake_series(self, region, language, date_from, date_to):
        return list(series)

    monkeypatch.setattr(TMDbClient, "discover_movies", fake_movies)
    monkeypatch.setattr(TMDbClient, "discover_series", fake_series)


def test_series_ingested_with_content_type_series(monkeypatch, session):
    _patch_tmdb(
        monkeypatch,
        movies=[],
        series=[{
            "id": 555, "name": "Murderbot", "original_name": "Murderbot",
            "first_air_date": "2026-05-16",
        }],
    )
    asyncio.run(title_sync.sync_titles_from_tmdb(session, markets=["US"]))

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
    asyncio.run(title_sync.sync_titles_from_tmdb(session, markets=["US"]))

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
    asyncio.run(title_sync.sync_titles_from_tmdb(session, markets=["US"]))

    rows = session.exec(select(Title).where(Title.tmdb_id == 550)).all()
    assert len(rows) == 2, "film and series with same tmdb_id must coexist as two rows"
    by_type = {r.content_type: r for r in rows}
    assert set(by_type) == {"Film", "Series"}
    # Neither overwrote the other.
    assert by_type["Film"].title_original == "Fight Club"
    assert by_type["Series"].title_original == "Fight Club: The Series"

    # Idempotent: a second run updates in place, no duplication.
    asyncio.run(title_sync.sync_titles_from_tmdb(session, markets=["US"]))
    rows2 = session.exec(select(Title).where(Title.tmdb_id == 550)).all()
    assert len(rows2) == 2
