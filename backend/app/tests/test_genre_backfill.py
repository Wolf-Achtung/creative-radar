"""Genre-Backfill (21.08.2026) — TMDb-Details fuer Titel mit
``tmdb_id`` und leerer Genre-Liste.

Der Company-Discover pflegt nur die Studio-Slates. Streamer-Originals
und Kandidaten-Titel tragen eine ``tmdb_id``, laufen aber durch keinen
Discover — nach der Genre-Nachruestung (#376) blieben ihre Genres
dauerhaft leer (Prod-Befund 21.08.: Titel-Zuordnung 67 %, Genre-
Abdeckung nur 52 %). Der Backfill schliesst genau diese Luecke.

Vertragspunkte:
- Leere Genre-Listen werden ueber den Details-Endpoint gefuellt,
  Serien ueber ``/tv``, Filme ueber ``/movie``.
- Vorhandene Genres bleiben unangetastet — ohne einen einzigen Call.
- Der Stueckzahl-Deckel greift und ``uebrig`` zaehlt ehrlich.
- Ein toter Titel (404) stoppt nicht den Lauf.
- ``sync_titles_from_tmdb`` haengt den Backfill an jeden Lauf an.
"""
from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

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


class _FakeClient:
    """Details-Stub: zeichnet jeden Abruf auf und liefert je tmdb_id
    vorbereitete Antworten (oder wirft)."""

    def __init__(self, antworten: dict[int, dict | Exception]):
        self.antworten = antworten
        self.abrufe: list[tuple[int, bool]] = []

    async def get_title_details(self, tmdb_id: int, *, is_series: bool) -> dict:
        self.abrufe.append((tmdb_id, is_series))
        antwort = self.antworten.get(tmdb_id, {"genres": []})
        if isinstance(antwort, Exception):
            raise antwort
        return antwort


def _titel(session, tmdb_id, *, genres=None, content_type="Film", name=None):
    t = Title(
        tmdb_id=tmdb_id,
        title_original=name or f"T-{tmdb_id}",
        source="TMDb",
        content_type=content_type,
        genres=genres or [],
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


async def test_fuellt_leere_genres_ueber_den_richtigen_endpoint(session):
    film = _titel(session, 100, content_type="Film")
    serie = _titel(session, 200, content_type="Series")
    client = _FakeClient({
        100: {"genres": [{"id": 1, "name": "Science Fiction"}, {"id": 2, "name": "Action"}]},
        200: {"genres": [{"id": 3, "name": "Mystery"}]},
    })

    ergebnis = await ts.backfill_missing_genres(session, client=client)

    session.refresh(film)
    session.refresh(serie)
    # Reihenfolge bleibt TMDb-Reihenfolge: erstes = primaeres Genre.
    assert film.genres == ["Science Fiction", "Action"]
    assert serie.genres == ["Mystery"]
    assert (100, False) in client.abrufe and (200, True) in client.abrufe, (
        "Serien muessen ueber /tv laufen, Filme ueber /movie — sonst "
        "liefert TMDb 404 oder den falschen Titel."
    )
    assert ergebnis["gefuellt"] == 2 and ergebnis["fehler"] == 0


async def test_vorhandene_genres_bleiben_unangetastet_ohne_api_call(session):
    fertig = _titel(session, 300, genres=["Romance"])
    client = _FakeClient({300: {"genres": [{"id": 9, "name": "Horror"}]}})

    ergebnis = await ts.backfill_missing_genres(session, client=client)

    session.refresh(fertig)
    assert fertig.genres == ["Romance"], "Gefuellte Genres duerfen nicht ueberschrieben werden."
    assert client.abrufe == [], "Fuer gefuellte Titel darf kein Details-Call rausgehen."
    assert ergebnis["kandidaten"] == 0


async def test_stueckzahl_deckel_greift_und_uebrig_zaehlt(session):
    for i in range(5):
        _titel(session, 400 + i)
    client = _FakeClient({
        400 + i: {"genres": [{"id": 1, "name": "Drama"}]} for i in range(5)
    })

    ergebnis = await ts.backfill_missing_genres(session, client=client, max_titles=3)

    assert ergebnis["gefuellt"] == 3
    assert ergebnis["uebrig"] == 2, (
        "Was nicht mehr in den Lauf passt, muss als uebrig ausgewiesen "
        "werden — stilles Liegenlassen war der Vision-Fehler vom 20.08."
    )
    assert len(client.abrufe) == 3


async def test_toter_titel_stoppt_nicht_den_lauf(session):
    kaputt = _titel(session, 500)
    heil = _titel(session, 501)
    client = _FakeClient({
        500: RuntimeError("404 von TMDb"),
        501: {"genres": [{"id": 1, "name": "Comedy"}]},
    })

    ergebnis = await ts.backfill_missing_genres(session, client=client)

    session.refresh(heil)
    session.refresh(kaputt)
    assert heil.genres == ["Comedy"]
    assert kaputt.genres == []
    assert ergebnis["fehler"] == 1 and ergebnis["gefuellt"] == 1


async def test_sync_haengt_den_backfill_an_jeden_lauf(session, monkeypatch):
    aufrufe = []

    async def _backfill_stub(session_, *, client=None, max_titles=None):
        aufrufe.append(True)
        return {"gefuellt": 7}

    class _LeererDiscoverClient:
        @staticmethod
        def tmdb_region(market):
            return market

        async def discover_movies_by_company(self, *a, **k):
            return []

        async def discover_series_by_company(self, *a, **k):
            return []

    monkeypatch.setattr(ts, "TMDbClient", _LeererDiscoverClient)
    monkeypatch.setattr(ts, "backfill_missing_genres", _backfill_stub)

    result = await ts.sync_titles_from_tmdb(session, markets=["DE"], pairs=["lionsgate"])

    assert aufrufe == [True], (
        "Ohne den angehaengten Backfill bekaemen Streamer- und "
        "Kandidaten-Titel weiterhin nie Genres."
    )
    assert result["genre_backfill"] == {"gefuellt": 7}
