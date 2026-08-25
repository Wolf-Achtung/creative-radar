"""Waechter fuer ``scripts/titel_doubletten_aufraeumen`` (25.08.2026).

Das Skript legt Katalog-Zeilen still und verschiebt Assets. Ein Fehler
hier ist teuer und von Hand kaum zu finden — deshalb steht jede Kante
als eigener Fall.

Die wichtigste Zusicherung ist eine Unterlassung: Gruppen, in denen
MEHRERE Zeilen Assets tragen, werden nicht angefasst. Sie koennen
echte Namensgleichheit sein (ein Film von 1994 neben einer Serie von
2026); automatisch zusammenzulegen waere genau der Fehler, den das
Skript verhindern soll.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import Asset, Channel, Market, Post, Title
from scripts import titel_doubletten_aufraeumen as cleanup


@pytest.fixture()
def session():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _titel(session, original, *, tmdb_id=None, assets=0, active=True):
    t = Title(title_original=original, tmdb_id=tmdb_id, active=active)
    session.add(t)
    session.commit()
    session.refresh(t)
    for _ in range(assets):
        ch = Channel(
            name=f"ch-{uuid4().hex[:6]}", platform="instagram",
            url=f"https://x.test/{uuid4()}", market=Market.US,
        )
        session.add(ch)
        session.commit()
        session.refresh(ch)
        post = Post(
            channel_id=ch.id, platform=ch.platform,
            post_url=f"https://x.test/p/{uuid4()}", raw_payload={},
        )
        session.add(post)
        session.commit()
        session.refresh(post)
        session.add(Asset(post_id=post.id, title_id=t.id))
    session.commit()
    return t


def test_leere_doublette_wird_erkannt(session):
    """Wolfs Lanterns-Fall: zwei tote Manual-Zeilen neben der echten."""
    leer1 = _titel(session, "Lanterns")
    leer2 = _titel(session, "Lanterns")
    echt = _titel(session, "Lanterns", tmdb_id=95350, assets=1)

    treffer = cleanup._leere_doubletten(session, cleanup._gruppen(session))

    assert {t.id for t in treffer} == {leer1.id, leer2.id}
    assert echt.id not in {t.id for t in treffer}


def test_eine_zeile_bleibt_wenn_alle_leer_sind(session):
    """Schutz vor Uebereifer: haette KEINE Zeile ein Asset, wuerde ein
    naiver Filter alle stilllegen — der Name verschwaende ganz aus dem
    Katalog, statt eindeutig zu werden."""
    _titel(session, "Lanterns")
    _titel(session, "Lanterns")

    treffer = cleanup._leere_doubletten(session, cleanup._gruppen(session))

    assert len(treffer) == 1


def test_gruppe_mit_zwei_belegten_zeilen_ist_strittig(session):
    """Kein Automatismus. "The Fox" von 1974 neben "The Fox" von 2026
    ist keine Doublette."""
    _titel(session, "The Fox", assets=1)
    _titel(session, "The Fox", tmdb_id=42, assets=1)

    strittig = cleanup._strittige(session, cleanup._gruppen(session))

    assert list(strittig) == ["the fox"]
    assert cleanup._leere_doubletten(session, cleanup._gruppen(session)) == []


def test_anker_ist_die_tmdb_zeile(session):
    """TMDb schlaegt Handarbeit: diese Zeile traegt Genres, Alias und
    Datum, die Manual-Zeile traegt nichts davon."""
    manuell = _titel(session, "The Fox", assets=3)
    aus_tmdb = _titel(session, "The Fox", tmdb_id=42, assets=1)

    anker = cleanup._anker(session, [manuell, aus_tmdb])

    assert anker.id == aus_tmdb.id


def test_anker_faellt_auf_die_meisten_assets_zurueck(session):
    """Ohne TMDb-Zeile entscheidet das Gewicht — sonst muessten mehr
    Assets umziehen als noetig."""
    klein = _titel(session, "The Fox", assets=1)
    gross = _titel(session, "The Fox", assets=4)

    assert cleanup._anker(session, [klein, gross]).id == gross.id


def test_zusammenlegen_verschiebt_assets_und_legt_still(session):
    manuell = _titel(session, "The Fox", assets=2)
    aus_tmdb = _titel(session, "The Fox", tmdb_id=42, assets=1)

    anker, verschoben = cleanup._zusammenlegen(session, [manuell, aus_tmdb])
    session.commit()

    assert anker.id == aus_tmdb.id
    assert verschoben == 2
    session.refresh(manuell)
    assert manuell.active is False
    am_anker = session.exec(select(Asset).where(Asset.title_id == aus_tmdb.id)).all()
    assert len(am_anker) == 3


def test_zusammenlegen_setzt_den_match_key_neu(session):
    """Der ``de_us_match_key`` folgt dem Titel — bleibt er auf dem alten
    Namen stehen, zaehlt der Post im Wochenvergleich weiter zur
    stillgelegten Zeile."""
    manuell = _titel(session, "The Fox", assets=1)
    aus_tmdb = _titel(session, "The Fox", tmdb_id=42)

    cleanup._zusammenlegen(session, [manuell, aus_tmdb])
    session.commit()

    asset = session.exec(select(Asset).where(Asset.title_id == aus_tmdb.id)).first()
    assert asset.de_us_match_key == "the-fox"


def test_inaktive_zeilen_bilden_keine_gruppe(session):
    """Stillgelegtes steht nicht im Lookup und blockiert nichts — ein
    zweiter Lauf darf deshalb nichts mehr finden."""
    _titel(session, "Lanterns", active=False)
    _titel(session, "Lanterns", assets=1)

    assert cleanup._gruppen(session) == {}
