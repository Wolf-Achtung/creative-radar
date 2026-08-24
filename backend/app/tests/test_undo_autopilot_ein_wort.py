"""Undo-Skript fuer die Autopilot-Fehlzuordnungen (Vorfall 24.08.2026).

Das Skript raeumt auf, was VOR dem Code-Fix in der DB gelandet ist:
Assets, die an generische Ein-Wort-Titel gebunden wurden. Vertragspunkte:

- Vorschau ist der Default und aendert NICHTS (ein versehentlicher
  Aufruf darf keine Daten anfassen).
- ``--apply`` loest die Zuordnung UND oeffnet die Kandidaten wieder —
  eine geloeste Zuordnung ohne offenen Vorschlag waere ein stiller
  Datenverlust.
- Mehrwortige Titel und Zuordnungen ausserhalb des Zeitfensters bleiben
  unangetastet.
- ``--behalten`` nimmt einzelne Titel aus (echte Ein-Wort-Filme).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import (
    Asset,
    CandidateStatus,
    Channel,
    Market,
    Post,
    Title,
    TitleCandidate,
)
from scripts import undo_autopilot_ein_wort as undo

IM_FENSTER = datetime(2026, 8, 24, 5, 38, tzinfo=timezone.utc)
DAVOR = datetime(2026, 8, 17, 5, 38, tzinfo=timezone.utc)


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


def _zuordnung(session, titel_name, *, wann):
    titel = Title(title_original=titel_name, active=True)
    session.add(titel)
    session.commit()
    session.refresh(titel)

    ch = Channel(
        name="pixar", platform="instagram", handle="pixar",
        url=f"https://x.test/{uuid4()}", market=Market.US,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    post = Post(
        channel_id=ch.id, platform="instagram",
        post_url=f"https://x.test/p/{uuid4()}",
    )
    session.add(post)
    session.commit()
    session.refresh(post)

    asset = Asset(
        post_id=post.id, title_id=titel.id,
        de_us_match_key="egal", updated_at=wann,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)

    kandidat = TitleCandidate(
        asset_id=asset.id, suggested_title=titel_name,
        confidence=0.90, status=CandidateStatus.RESOLVED,
    )
    session.add(kandidat)
    session.commit()
    session.refresh(kandidat)
    return asset, titel, kandidat


def _treffer(session, **kwargs):
    return undo._betroffene(
        session,
        seit=kwargs.get("seit", undo.DEFAULT_SEIT),
        bis=kwargs.get("bis", undo.DEFAULT_BIS),
        behalten=kwargs.get("behalten", set()),
    )


def test_vorschau_findet_die_fehlzuordnung_und_aendert_nichts(session):
    asset, titel, kandidat = _zuordnung(session, "Driven", wann=IM_FENSTER)

    treffer = _treffer(session)
    assert undo._vorschau(treffer) == 0

    session.refresh(asset)
    session.refresh(kandidat)
    assert asset.title_id == titel.id, "Die Vorschau darf nichts anfassen."
    assert kandidat.status == CandidateStatus.RESOLVED


def test_apply_loest_die_zuordnung_und_oeffnet_den_vorschlag(session):
    asset, _titel, kandidat = _zuordnung(session, "Personality", wann=IM_FENSTER)

    assert undo._anwenden(session, _treffer(session), ohne_rueckfrage=True) == 0

    session.refresh(asset)
    session.refresh(kandidat)
    assert asset.title_id is None
    assert asset.de_us_match_key is None
    assert kandidat.status == CandidateStatus.OPEN, (
        "Ohne offenen Vorschlag verschwaende der Fund still — der Post "
        "muss zurueck in die Pruef-Queue."
    )


def test_mehrwort_titel_bleibt_unangetastet(session):
    asset, titel, _k = _zuordnung(session, "Lügen über meine Mutter", wann=IM_FENSTER)

    assert _treffer(session) == []
    session.refresh(asset)
    assert asset.title_id == titel.id


def test_zuordnung_ausserhalb_des_fensters_bleibt_unangetastet(session):
    asset, titel, _k = _zuordnung(session, "Driven", wann=DAVOR)

    assert _treffer(session) == []
    session.refresh(asset)
    assert asset.title_id == titel.id, (
        "Aeltere, per Hand bestaetigte Zuordnungen darf das Skript nicht "
        "anfassen — es raeumt EINEN Lauf auf."
    )


def test_behalten_nimmt_echte_ein_wort_filme_aus(session):
    asset, titel, _k = _zuordnung(session, "Barbie", wann=IM_FENSTER)

    assert _treffer(session, behalten={"barbie"}) == []
    session.refresh(asset)
    assert asset.title_id == titel.id
