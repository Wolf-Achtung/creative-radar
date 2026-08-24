"""Wächter für ``scripts/undo_ki_zuordnungen.py`` (Vorfall 24.08.2026).

Der erste Lauf der KI-Prüfung ordnete 11 Assets zu, davon mehrere
falsch — "Sam & Cat" landete beim Titel "CAT", ein Parks-and-Rec-Post
bei einem erfundenen Spin-off. Das Skript räumt diese Zuordnungen ab.
Die Achse ist ``llm_checked_at``: nur der Assist setzt dieses Feld.
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
    Title,
    TitleCandidate,
)
from scripts import undo_ki_zuordnungen as undo

IM_FENSTER = datetime(2026, 8, 24, 9, 50, tzinfo=timezone.utc)
DAVOR = datetime(2026, 8, 20, 9, 50, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _kanal(session):
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}", platform="instagram",
        url=f"https://x.test/{uuid4()}", market=Market.US,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


def _ki_zuordnung(session, titelname, *, wann, notiz="KI: begruendung"):
    kanal = _kanal(session)
    titel = Title(title_original=titelname, active=True)
    session.add(titel)
    session.commit()
    session.refresh(titel)

    post = Post(
        channel_id=kanal.id, platform=kanal.platform,
        post_url=f"https://x.test/p/{uuid4()}", caption="x",
        detected_at=IM_FENSTER, raw_payload={},
    )
    session.add(post)
    session.commit()
    session.refresh(post)

    asset = Asset(post_id=post.id, title_id=titel.id, de_us_match_key="k")
    session.add(asset)
    session.commit()
    session.refresh(asset)

    cand = TitleCandidate(
        asset_id=asset.id, suggested_title=titelname, confidence=0.9,
        status=CandidateStatus.RESOLVED, llm_checked_at=wann, llm_note=notiz,
    )
    session.add(cand)
    session.commit()
    session.refresh(cand)
    return asset, titel, cand


def _treffer(session, behalten=frozenset()):
    return undo._betroffene(
        session, seit=undo.DEFAULT_SEIT, bis=undo.DEFAULT_BIS,
        behalten=set(behalten),
    )


def test_vorschau_aendert_nichts(session, capsys):
    asset, titel, cand = _ki_zuordnung(session, "CAT", wann=IM_FENSTER)

    assert undo._vorschau(_treffer(session)) == 0

    session.refresh(asset)
    session.refresh(cand)
    assert asset.title_id == titel.id
    assert cand.status == CandidateStatus.RESOLVED
    assert "VORSCHAU" in capsys.readouterr().out


def test_vorschau_zeigt_die_begruendung_des_modells(session, capsys):
    _ki_zuordnung(
        session, "CAT", wann=IM_FENSTER,
        notiz="Die Caption bewirbt explizit 'Sam & Cat'.",
    )

    undo._vorschau(_treffer(session))

    ausgabe = capsys.readouterr().out
    assert "Sam & Cat" in ausgabe, (
        "An der Begruendung erkennt man die falsche Zuordnung am "
        "schnellsten — ohne sie muesste man jeden Post einzeln aufrufen."
    )


def test_apply_loest_die_zuordnung_und_oeffnet_den_vorschlag(session):
    asset, _titel, cand = _ki_zuordnung(session, "CAT", wann=IM_FENSTER)

    assert undo._anwenden(session, _treffer(session), ohne_rueckfrage=True) == 0

    session.refresh(asset)
    session.refresh(cand)
    assert asset.title_id is None
    assert asset.de_us_match_key is None
    assert cand.status == CandidateStatus.OPEN


def test_apply_leert_den_ki_marker(session):
    """Sonst gilt der Kandidat als 'schon geprueft' und die
    nachgeschaerfte Pruefung sieht ihn nie wieder an."""
    _asset, _titel, cand = _ki_zuordnung(session, "CAT", wann=IM_FENSTER)

    undo._anwenden(session, _treffer(session), ohne_rueckfrage=True)

    session.refresh(cand)
    assert cand.llm_checked_at is None


def test_zuordnung_ausserhalb_des_fensters_bleibt_unangetastet(session):
    asset, titel, _cand = _ki_zuordnung(session, "CAT", wann=DAVOR)

    assert _treffer(session) == []
    session.refresh(asset)
    assert asset.title_id == titel.id


def test_handzuordnung_ohne_ki_marker_bleibt_unangetastet(session):
    """Nur der Assist setzt ``llm_checked_at``. Was ein Mensch bestaetigt
    hat, darf das Skript nicht anfassen."""
    asset, titel, cand = _ki_zuordnung(session, "CAT", wann=IM_FENSTER)
    cand.llm_checked_at = None
    session.add(cand)
    session.commit()

    assert _treffer(session) == []
    session.refresh(asset)
    assert asset.title_id == titel.id


def test_behalten_nimmt_richtige_zuordnungen_aus(session):
    asset, titel, _cand = _ki_zuordnung(session, "Cars", wann=IM_FENSTER)

    assert _treffer(session, behalten={"cars"}) == []
    session.refresh(asset)
    assert asset.title_id == titel.id


def test_apply_behauptet_nicht_es_habe_nichts_geaendert(session, capsys):
    _ki_zuordnung(session, "CAT", wann=IM_FENSTER)

    undo._anwenden(session, _treffer(session), ohne_rueckfrage=True)

    ausgabe = capsys.readouterr().out
    assert "nichts geaendert" not in ausgabe
    assert "--apply" not in ausgabe
    assert "Fertig: 1 KI-Zuordnungen geloest" in ausgabe


def test_cr_db_url_wird_als_database_url_uebernommen(monkeypatch):
    for var in ("DATABASE_URL", "DATABASE_PRIVATE_URL", "DATABASE_PUBLIC_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CR_DB_URL", "postgresql://u:p@host:5432/db")

    undo._db_bruecke()

    assert os.environ["DATABASE_URL"] == "postgresql://u:p@host:5432/db"


def test_engine_wird_erst_beim_aufruf_importiert():
    quelle = inspect.getsource(undo)
    kopf = quelle.split("def _db_bruecke")[0]

    assert "from app.database import" not in kopf
    assert "from app.database import engine" in inspect.getsource(undo._engine)
