"""Autopilot-Fehlzuordnungen an Ein-Wort-Titel (Vorfall 24.08.2026).

Der Montags-Cron ordnete 83 Assets automatisch Titeln wie "Driven",
"Personality" oder "كتالوج" zu — generischen Ein-Wort-Titeln aus dem
neuen Streamer-Katalog. Ursachenkette:

  Matcher: Einzelwort-Substring -> ``substring_weak``, Confidence 0.90,
           ausdruecklich "non-safe, needs corroboration"; seine eigene
           Auto-Tag-Marke liegt bei 0.95.
  Autopilot: prueft gegen 0.85 -> bestaetigt genau diese Zufallstreffer.

Vertragspunkte hier:

- Ein-Wort-Titel unter 0.95 werden NICHT mehr automatisch zugeordnet und
  erscheinen getrennt als ``skipped_weak_single_word`` im Summary.
- Ein-Wort-Titel MIT echtem Volltreffer (Confidence 1.0) gehen weiter
  durch — "Barbie" bleibt zuordenbar.
- Mehrwortige Titel bleiben bei der alten Schwelle: eine mehrteilige
  Phrase in einer Caption ist starke Evidenz.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.models.entities import (
    Asset,
    CandidateStatus,
    Channel,
    Market,
    Post,
    Title,
    TitleCandidate,
)
from app.services.candidate_autopilot import run_candidate_autopilot


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


def _asset(session):
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
    asset = Asset(post_id=post.id)
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def _titel(session, name):
    t = Title(title_original=name, active=True)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _kandidat(session, asset, name, confidence):
    k = TitleCandidate(
        asset_id=asset.id, suggested_title=name,
        confidence=confidence, status=CandidateStatus.OPEN,
    )
    session.add(k)
    session.commit()
    session.refresh(k)
    return k


def test_ein_wort_titel_mit_0_90_wird_nicht_mehr_zugeordnet(session):
    """Der Vorfall selbst: 'Driven' mit der Substring-Confidence 0.90."""
    _titel(session, "Driven")
    asset = _asset(session)
    kandidat = _kandidat(session, asset, "Driven", 0.90)

    summary = run_candidate_autopilot(session)

    assert summary.auto_assigned == 0, (
        "0.90 ist die Substring-Confidence des Matchers — 'needs "
        "corroboration'. Der Autopilot darf sie nicht bestaetigen."
    )
    assert summary.skipped_weak_single_word == 1
    session.refresh(asset)
    session.refresh(kandidat)
    assert asset.title_id is None
    assert kandidat.status == CandidateStatus.OPEN, (
        "Der Vorschlag muss in der Queue bleiben, nicht still verschwinden."
    )


def test_ein_wort_titel_mit_volltreffer_geht_weiter_durch(session):
    """'Barbie' als exakter Volltreffer (1.0) bleibt zuordenbar — der
    Schutz darf echte Ein-Wort-Filme nicht blockieren."""
    titel = _titel(session, "Barbie")
    asset = _asset(session)
    _kandidat(session, asset, "Barbie", 1.0)

    summary = run_candidate_autopilot(session)

    assert summary.auto_assigned == 1
    assert summary.skipped_weak_single_word == 0
    session.refresh(asset)
    assert asset.title_id == titel.id


def test_mehrwort_titel_bleibt_bei_der_alten_schwelle(session):
    """Eine mehrteilige Phrase in einer Caption ist starke Evidenz —
    hier bleibt 0.85 die Marke, sonst waere der Autopilot wertlos."""
    titel = _titel(session, "Lügen über meine Mutter")
    asset = _asset(session)
    _kandidat(session, asset, "Lügen über meine Mutter", 0.90)

    summary = run_candidate_autopilot(session)

    assert summary.auto_assigned == 1
    assert summary.skipped_weak_single_word == 0
    session.refresh(asset)
    assert asset.title_id == titel.id


def test_schwelle_ist_ueber_settings_steuerbar(session, monkeypatch):
    """Not-Aus: wer die Ein-Wort-Schwelle wieder senkt, bekommt das alte
    Verhalten — ohne Deploy."""
    monkeypatch.setattr(
        settings, "candidate_autopilot_min_confidence_single_word", 0.85,
        raising=False,
    )
    _titel(session, "Driven")
    asset = _asset(session)
    _kandidat(session, asset, "Driven", 0.90)

    summary = run_candidate_autopilot(session)

    assert summary.auto_assigned == 1


def test_echte_low_confidence_bleibt_low_confidence(session):
    """Abgrenzung der Zaehler: ein Ein-Wort-Kandidat UNTER der normalen
    Schwelle ist kein Fall des neuen Schutzes, sondern schlicht zu
    schwach — sonst waere die Zahl im Summary nicht lesbar."""
    _titel(session, "Driven")
    asset = _asset(session)
    _kandidat(session, asset, "Driven", 0.40)

    summary = run_candidate_autopilot(session)

    assert summary.auto_assigned == 0
    assert summary.skipped_weak_single_word == 0
    assert summary.skipped_low_confidence == 1
