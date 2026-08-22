"""Anreicherung manuell angelegter Titel (22.08.2026).

Die Entscheidungs-Queue legt Titel per Klick an — ohne ``tmdb_id``.
Ohne tmdb_id: keine Genres (der Backfill greift nur MIT tmdb_id),
keine Aliases fuer den Auto-Matcher. Diese Stage verknuepft solche
Titel per TMDb-Namens-Suche.

Sicherheits-Vertrag, den diese Tests festnageln:
- Verknuepft wird NUR bei genau EINEM exakten Namens-Treffer —
  Allerweltsnamen mit mehreren Treffern bleiben unangetastet.
- Der Treffer muss aktuell sein (kein Namensvetter von 1983).
- Akzent-tolerant: "Beware Boiuna" trifft "Beware Boiúna".
- Serien laufen ueber /search/tv und setzen content_type.
- ``sync_titles_from_tmdb`` haengt die Stage an jeden Lauf an.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import Title, TitleKeyword
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


AKTUELL = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()


class _FakeSearchClient:
    """Such-Stub: liefert je Query vorbereitete Movie-/TV-Ergebnisse und
    zeichnet die Abrufe auf. Normalisierung nutzt die echten Methoden."""

    def __init__(self, filme=None, serien=None):
        self.filme = filme or {}
        self.serien = serien or {}
        self.abrufe: list[tuple[str, str]] = []

    async def search_movies(self, query, *, language="de-DE"):
        self.abrufe.append(("movie", query))
        return self.filme.get(query, [])

    async def search_series(self, query, *, language="de-DE"):
        self.abrufe.append(("tv", query))
        return self.serien.get(query, [])

    # Die echte Normalisierung aus dem echten Client wiederverwenden —
    # so testet der Test auch den Feld-Vertrag (genre_ids -> Namen).
    from app.services.tmdb_client import TMDbClient as _Echt
    normalize_tmdb_movie = _Echt.normalize_tmdb_movie
    normalize_tmdb_series = _Echt.normalize_tmdb_series
    _genre_names = _Echt._genre_names
    _MOVIE_GENRES = _Echt._MOVIE_GENRES
    _TV_GENRES = _Echt._TV_GENRES


def _titel(session, name, **kwargs):
    t = Title(title_original=name, active=True, source="manual", **kwargs)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _film(tmdb_id, title, *, original=None, datum=AKTUELL, genre_ids=(18,)):
    return {
        "id": tmdb_id,
        "title": title,
        "original_title": original or title,
        "release_date": datum,
        "genre_ids": list(genre_ids),
    }


async def test_eindeutiger_treffer_wird_verknuepft_und_angereichert(session):
    titel = _titel(session, "Beware Boiuna")
    client = _FakeSearchClient(filme={
        "Beware Boiuna": [_film(7001, "Beware Boiúna", genre_ids=[27, 53])],
    })

    ergebnis = await ts.enrich_titles_without_tmdb_id(session, client=client)

    session.refresh(titel)
    assert ergebnis["verknuepft"] == 1
    assert titel.tmdb_id == 7001, "Akzent-Differenz darf den exakten Treffer nicht verhindern."
    assert titel.genres == ["Horror", "Thriller"]
    assert "Beware Boiúna" in (titel.aliases or [])
    alias_rows = session.exec(select(TitleKeyword).where(TitleKeyword.title_id == titel.id)).all()
    assert any(k.keyword == "Beware Boiúna" for k in alias_rows), (
        "Der Auto-Matcher liest Alias-Keywords — ohne sie bleibt der "
        "TMDb-Name fuer kuenftige Posts unsichtbar."
    )


async def test_mehrdeutiger_name_bleibt_unangetastet(session):
    titel = _titel(session, "Daniel")
    client = _FakeSearchClient(filme={
        "Daniel": [
            _film(1, "Daniel"),
            _film(2, "Daniel"),
        ],
    })

    ergebnis = await ts.enrich_titles_without_tmdb_id(session, client=client)

    session.refresh(titel)
    assert titel.tmdb_id is None, (
        "Zwei exakte Treffer = keine Entscheidung. Ein falsch "
        "verknuepfter Film waere schlimmer als keiner."
    )
    assert ergebnis["verknuepft"] == 0 and ergebnis["unklar"] == 1


async def test_alter_namensvetter_wird_nicht_verknuepft(session):
    titel = _titel(session, "Westwell")
    client = _FakeSearchClient(filme={
        "Westwell": [_film(3, "Westwell", datum="1983-05-01")],
    })

    ergebnis = await ts.enrich_titles_without_tmdb_id(session, client=client)

    session.refresh(titel)
    assert titel.tmdb_id is None, (
        "Das Radar beobachtet laufende Kampagnen — ein Namensvetter "
        "von 1983 ist praktisch immer der falsche Film."
    )
    assert ergebnis["unklar"] == 1


async def test_serie_laeuft_ueber_tv_suche_und_setzt_content_type(session):
    titel = _titel(session, "Westwell")
    client = _FakeSearchClient(
        filme={"Westwell": []},
        serien={"Westwell": [{
            "id": 9001,
            "name": "Westwell",
            "original_name": "Westwell",
            "first_air_date": AKTUELL,
            "genre_ids": [18],
        }]},
    )

    ergebnis = await ts.enrich_titles_without_tmdb_id(session, client=client)

    session.refresh(titel)
    assert ergebnis["verknuepft"] == 1
    assert titel.tmdb_id == 9001
    assert titel.content_type == "Series"
    assert ("tv", "Westwell") in client.abrufe


async def test_titel_mit_tmdb_id_bekommen_keinen_such_call(session):
    _titel(session, "Schon verknuepft", tmdb_id=42)
    client = _FakeSearchClient()

    ergebnis = await ts.enrich_titles_without_tmdb_id(session, client=client)

    assert client.abrufe == []
    assert ergebnis["kandidaten"] == 0


async def test_sync_haengt_die_anreicherung_an_jeden_lauf(session, monkeypatch):
    aufrufe = []

    async def _enrich_stub(session_, *, client=None, max_titles=None):
        aufrufe.append(True)
        return {"verknuepft": 3}

    async def _backfill_stub(session_, *, client=None, max_titles=None):
        return {"gefuellt": 0}

    class _LeererDiscoverClient:
        @staticmethod
        def tmdb_region(market):
            return market

        async def discover_movies_by_company(self, *a, **k):
            return []

        async def discover_series_by_company(self, *a, **k):
            return []

    monkeypatch.setattr(ts, "TMDbClient", _LeererDiscoverClient)
    monkeypatch.setattr(ts, "enrich_titles_without_tmdb_id", _enrich_stub)
    monkeypatch.setattr(ts, "backfill_missing_genres", _backfill_stub)

    result = await ts.sync_titles_from_tmdb(session, markets=["DE"], pairs=["lionsgate"])

    assert aufrufe == [True], (
        "Ohne den Anhang blieben manuell angelegte Titel dauerhaft ohne "
        "tmdb_id — und damit ohne Genres und Matcher-Aliases."
    )
    assert result["manual_enrich"] == {"verknuepft": 3}
