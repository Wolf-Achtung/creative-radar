"""Waechter fuer ``scripts/diag_titel_doubletten`` (25.08.2026).

Das Skript beantwortet die Frage nach dem Doubletten-Bug: was steht
jetzt in der Datenbank? Traegt seine Gruppierung falsch, meldet es
Entwarnung, wo Zeilen doppelt liegen — oder Alarm bei echter
Namensgleichheit zwischen einem Film und einer Serie.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Title
from scripts import diag_titel_doubletten as diag


@pytest.fixture()
def session():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _titel(session, original, *, local=None, active=True, alter_tage=0):
    t = Title(
        title_original=original, title_local=local, active=active,
        created_at=datetime.now(timezone.utc) - timedelta(days=alter_tage),
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def test_gleicher_name_bildet_eine_gruppe(session):
    _titel(session, "Lanterns")
    _titel(session, "Lanterns")

    gruppen = diag._gruppen(session)

    assert list(gruppen) == ["lanterns"]
    assert len(gruppen["lanterns"]) == 2


def test_einzelner_titel_ist_keine_gruppe(session):
    _titel(session, "Lanterns")
    assert diag._gruppen(session) == {}


def test_verschiedene_schreibweisen_kollidieren_nicht(session):
    """Grenze, ehrlich festgehalten: ``_normalize`` faltet NUR Gross-
    schreibung und Leerraum — keine Akzente, keine Satzzeichen. "Léon –
    Der Profi" und "Leon - Der Profi" sind fuer den Katalog-Lookup zwei
    verschiedene Namen, also auch fuer diesen Bericht.

    Das ist konsistent, nicht bequem: der Bericht soll zeigen, wo der
    Lookup mehrdeutig wird. Fast-Doubletten mit anderer Zeichensetzung
    blockieren ihn nicht — sie sind ein anderes Problem und brauchten
    einen anderen Vergleich."""
    _titel(session, "Léon – Der Profi")
    _titel(session, "Leon - Der Profi")

    assert diag._gruppen(session) == {}


def test_inaktive_titel_zaehlen_nicht(session):
    """Der Katalog-Lookup liest nur aktive Titel — ein stillgelegter
    kollidiert mit nichts."""
    _titel(session, "Lanterns")
    _titel(session, "Lanterns", active=False)

    assert diag._gruppen(session) == {}


def test_lokaltitel_kollidiert_mit_originaltitel(session):
    """Der Lookup nimmt beide Namen auf. Wer nur ``title_original``
    vergleicht, uebersieht genau die Kollision, die den Autopiloten
    dann blockiert."""
    _titel(session, "The Fox")
    _titel(session, "Der Fuchs", local="The Fox")

    assert "the fox" in diag._gruppen(session)


def test_fenster_filtert_alte_gruppen(session):
    _titel(session, "Lanterns", alter_tage=400)
    _titel(session, "Lanterns", alter_tage=400)

    gruppen = diag._gruppen(session)
    jung = {
        n: ts for n, ts in gruppen.items()
        if any(diag._jung(t, 1) for t in ts)
    }
    assert gruppen and jung == {}
