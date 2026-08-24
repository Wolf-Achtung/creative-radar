"""Wächter für ``scripts/ki_pruefung_neu_anstossen.py`` (24.08.2026).

#426 hat der KI-Prüfung beigebracht, den beworbenen Titel zu verwerten.
Nach dem Deploy meldete der Knopf trotzdem "1 neu geprüft": ``llm_checked_at``
unterscheidet nicht zwischen "schon geprüft" und "von einer schwächeren
Prüfung geprüft". Das Skript löst diesen Knoten — und darf dabei
nichts anfassen, was bereits zugeordnet ist.
"""
from __future__ import annotations

import inspect
import os
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
    TitleCandidate,
)
from scripts import ki_pruefung_neu_anstossen as neu

GEPRUEFT_AM = datetime(2026, 8, 24, 9, 50, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _kandidat(session, *, status=CandidateStatus.OPEN, geprueft=GEPRUEFT_AM,
              notiz="KI unsicher: Der Post bewirbt 'Lanterns'."):
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}", platform="instagram",
        url=f"https://x.test/{uuid4()}", market=Market.US,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    post = Post(
        channel_id=ch.id, platform=ch.platform,
        post_url=f"https://x.test/p/{uuid4()}", caption="x",
        detected_at=GEPRUEFT_AM, raw_payload={},
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    asset = Asset(post_id=post.id)
    session.add(asset)
    session.commit()
    session.refresh(asset)
    cand = TitleCandidate(
        asset_id=asset.id, suggested_title="driven", confidence=0.9,
        status=status, llm_checked_at=geprueft, llm_note=notiz,
    )
    session.add(cand)
    session.commit()
    session.refresh(cand)
    return cand


def test_vorschau_aendert_nichts(session, capsys):
    cand = _kandidat(session)

    assert neu._vorschau(neu._betroffene(session)) == 0

    session.refresh(cand)
    assert cand.llm_checked_at is not None
    assert "VORSCHAU" in capsys.readouterr().out


def test_apply_loescht_den_marker(session):
    cand = _kandidat(session)

    assert neu._anwenden(session, neu._betroffene(session), ohne_rueckfrage=True) == 0

    session.refresh(cand)
    assert cand.llm_checked_at is None, (
        "Ohne geloeschten Marker sieht der verbesserte Pruefer den "
        "Kandidaten nie wieder an."
    )
    assert cand.llm_note is None


def test_zugeordnete_kandidaten_bleiben_unberuehrt(session):
    """``resolved`` heisst: hier haengt eine Zuordnung dran. Die darf ein
    Marker-Reset nicht aufweichen — dafuer gibt es undo_ki_zuordnungen."""
    cand = _kandidat(session, status=CandidateStatus.RESOLVED)

    assert neu._betroffene(session) == []
    session.refresh(cand)
    assert cand.llm_checked_at is not None


def test_ungepruefte_kandidaten_tauchen_nicht_auf(session):
    """Wer noch keinen Marker hat, braucht kein Zuruecksetzen — er steht
    ohnehin in der naechsten Runde."""
    cand = _kandidat(session, geprueft=None)

    assert neu._betroffene(session) == []
    session.refresh(cand)
    assert cand.llm_checked_at is None


def test_vorschau_gruppiert_nach_urteil(session, capsys):
    """Die Aufstellung soll zeigen, WELCHE Urteile neu drankommen —
    sonst ist die Zahl allein keine Entscheidungsgrundlage."""
    _kandidat(session, notiz="KI unsicher: Der Post bewirbt 'Lanterns'.")
    _kandidat(session, notiz="KI unsicher: kein Titelbezug.")
    _kandidat(session, notiz="KI: kein passender Katalog-Titel gefunden.")

    neu._vorschau(neu._betroffene(session))

    ausgabe = capsys.readouterr().out
    assert "3 offene Kandidaten" in ausgabe
    assert "KI unsicher" in ausgabe
    assert "2" in ausgabe


def test_cr_db_url_wird_als_database_url_uebernommen(monkeypatch):
    for var in ("DATABASE_URL", "DATABASE_PRIVATE_URL", "DATABASE_PUBLIC_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CR_DB_URL", "postgresql://u:p@host:5432/db")

    neu._db_bruecke()

    assert os.environ["DATABASE_URL"] == "postgresql://u:p@host:5432/db"


def test_engine_wird_erst_beim_aufruf_importiert():
    quelle = inspect.getsource(neu)
    kopf = quelle.split("def _db_bruecke")[0]

    assert "from app.database import" not in kopf
    assert "from app.database import engine" in inspect.getsource(neu._engine)
