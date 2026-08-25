"""Waechter fuer ``scripts/diag_geschlossene_luecken`` (25.08.2026).

Anlass: Wolfs Korrektur. Die Pruef-Queue steht auf 0, weil er sie von
Hand geleert hat — nicht durch den Cron. Kurz zuvor war das Katalog-
Nachladen fertig geworden. Ein Fall, den jemand auf ``ignored`` setzt,
obwohl der Post ein nicht katalogisiertes Werk bewirbt, ist fuer das
Feature dauerhaft unsichtbar: es sieht nur OFFENE Kandidaten.

Die Auswahl dieses Skripts ist die ganze Aussage — traegt sie falsch,
sagt der Bericht "nichts begraben", obwohl etwas begraben ist. Deshalb
steht jede Kante hier als eigener Fall.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
from scripts import diag_geschlossene_luecken as diag

JETZT = datetime.now(timezone.utc)


@pytest.fixture()
def session():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _kandidat(
    session, *, status, mit_titel=False, marker=True, alter_tage=0,
):
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}", platform="instagram",
        url=f"https://x.test/{uuid4()}", market=Market.US,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    post = Post(
        channel_id=ch.id, platform=ch.platform,
        post_url=f"https://x.test/p/{uuid4()}", caption="Lanterns",
        detected_at=JETZT, raw_payload={},
    )
    session.add(post)
    session.commit()
    session.refresh(post)

    title_id = None
    if mit_titel:
        titel = Title(title_original=f"Werk {uuid4().hex[:4]}", active=True)
        session.add(titel)
        session.commit()
        session.refresh(titel)
        title_id = titel.id

    asset = Asset(post_id=post.id, title_id=title_id)
    session.add(asset)
    session.commit()
    session.refresh(asset)

    notiz = (
        f"KI: bewirbt 'Lanterns' {diag.NICHT_IM_KATALOG} — begruendung"
        if marker else "KI: kein passender Katalog-Titel"
    )
    cand = TitleCandidate(
        asset_id=asset.id, suggested_title="Lanterns", confidence=0.9,
        status=status, llm_note=notiz,
        updated_at=JETZT - timedelta(days=alter_tage),
    )
    session.add(cand)
    session.commit()
    session.refresh(cand)
    return cand


def test_geschlossen_und_titellos_wird_gefunden(session):
    """Der Kernfall: von Hand weggeklickt, Asset blieb ohne Titel."""
    _kandidat(session, status=CandidateStatus.IGNORED)

    treffer = diag._geschlossen_ohne_titel(session, tage=2)

    assert len(treffer) == 1
    cand, _asset = treffer[0]
    assert cand.status == CandidateStatus.IGNORED


def test_geschlossen_mit_titel_ist_kein_fall(session):
    """Wer den Titel von Hand angelegt hat, hat alles richtig gemacht —
    das Asset traegt eine title_id. Wuerde das hier auftauchen, waere
    der Bericht ein Fehlalarm ueber saubere Handarbeit."""
    _kandidat(session, status=CandidateStatus.RESOLVED, mit_titel=True)

    assert diag._geschlossen_ohne_titel(session, tage=2) == []


def test_offene_kandidaten_zaehlen_nicht(session):
    """Offene Faelle sieht das Nachladen selbst — sie sind nicht
    begraben und gehoeren nicht in diesen Bericht."""
    _kandidat(session, status=CandidateStatus.OPEN)

    assert diag._geschlossen_ohne_titel(session, tage=2) == []


def test_ausserhalb_des_fensters_zaehlt_nicht(session):
    """Ohne Fenster liefe der Bericht ueber den gesamten Altbestand und
    wuerde die Handarbeit von heute darin ertraenken."""
    _kandidat(session, status=CandidateStatus.IGNORED, alter_tage=30)

    assert diag._geschlossen_ohne_titel(session, tage=2) == []
    assert len(diag._geschlossen_ohne_titel(session, tage=60)) == 1


def test_naiver_zeitstempel_wirft_nicht(session):
    """Aeltere Zeilen tragen ``updated_at`` ohne tzinfo. Ein nackter
    Vergleich wirft dann TypeError — und das Skript stuerbe genau bei
    dem Bestand ab, fuer den es gebaut wurde."""
    cand = _kandidat(session, status=CandidateStatus.IGNORED)
    cand.updated_at = JETZT.replace(tzinfo=None)
    session.add(cand)
    session.commit()

    assert len(diag._geschlossen_ohne_titel(session, tage=2)) == 1


def test_bericht_trennt_marker_von_ohne(session, capsys):
    """Nur die Marker-Gruppe waere ein Fall fuer das Nachladen. Wirft
    der Bericht beides zusammen, liest sich jede Handablage wie ein
    verlorener Titel."""
    _kandidat(session, status=CandidateStatus.IGNORED, marker=True)
    _kandidat(session, status=CandidateStatus.IGNORED, marker=False)

    diag._bericht(diag._geschlossen_ohne_titel(session, tage=2), 2)
    ausgabe = capsys.readouterr().out

    assert "Davon mit Katalog-Luecken-Marker: 1" in ausgabe
    assert "Ohne Marker: 1" in ausgabe


def test_resolved_ohne_titel_wird_namentlich_genannt(session, capsys):
    """Die Frage, die der Bericht am 25.08. nicht beantworten konnte:
    WELCHE zwei Faelle sind das? ``resolved`` ohne Titel ist die
    Signatur des Fehlers aus #436 — nur mit Namen und Zeitpunkt laesst
    sich sagen, ob der Fix greift oder etwas durchrutscht."""
    _kandidat(session, status=CandidateStatus.RESOLVED, marker=False)

    diag._bericht(diag._geschlossen_ohne_titel(session, tage=2), 2)
    ausgabe = capsys.readouterr().out

    assert "1 x resolved ohne Titel" in ausgabe
    assert "'Lanterns'" in ausgabe


def test_verworfene_vorschlaege_werden_nur_gezaehlt(session, capsys):
    """``ignored`` heisst: jemand hat den Vorschlag bewusst verworfen.
    Das Asset bleibt absichtlich ohne Titel — kein Fall, keine Liste,
    sonst ertraenkt die Handarbeit die echten Verdachtsfaelle."""
    _kandidat(session, status=CandidateStatus.IGNORED, marker=False)

    diag._bericht(diag._geschlossen_ohne_titel(session, tage=2), 2)
    ausgabe = capsys.readouterr().out

    assert "1 x ignored — verworfene Vorschlaege, kein Fall." in ausgabe
    assert "resolved ohne Titel" not in ausgabe


def test_leerer_bericht_sagt_entwarnung(session, capsys):
    diag._bericht([], 2)
    assert "Nichts gefunden" in capsys.readouterr().out
