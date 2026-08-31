"""``GET /api/titles/stats/whitelist`` — die Kacheln über der
Titel-Whitelist (31.08.2026).

Wolfs Befund: die Kachel meldete **"Neue Titel diese Woche: 37.978"**
bei 19.464 aktiven Titeln — mehr "neue" als überhaupt vorhanden. Sie
las ``latest_run.upserted_count``, und der zählt jeden Upsert des
letzten Sync-Laufs, Insert UND Update. Der Sync zieht wöchentlich die
vollen Slates aller Studios und Streamer; fast alle Zeilen existieren
schon, und ein Titel wird je Markt-Achse erneut angefasst.

Beide Wörter im Label waren falsch: nicht "neu" (überwiegend
Aktualisierungen) und nicht "diese Woche" (der letzte Lauf, wann immer
der war). Sichtbar wurde es daran, dass die Zahl über einen ganzen Tag
unverändert stand, während 29 echte Titel dazukamen.

Der Endpoint hatte bis heute KEINEN Test — deshalb konnte die Zahl
jahrelang etwas anderes behaupten, als das Label sagt.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models.entities import Title, TitleSyncRun

JETZT = datetime.now(timezone.utc)


@pytest.fixture()
def client_und_session():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    with Session(eng) as session:
        app.dependency_overrides[get_session] = lambda: session
        try:
            yield TestClient(app), session
        finally:
            app.dependency_overrides.clear()


def _titel(session, name, *, created_at, active=True):
    titel = Title(title_original=name, active=active)
    titel.created_at = created_at
    session.add(titel)
    session.commit()
    return titel


def test_neue_titel_zaehlt_die_letzten_sieben_tage(client_und_session):
    client, session = client_und_session
    _titel(session, "Frisch A", created_at=JETZT - timedelta(days=1))
    _titel(session, "Frisch B", created_at=JETZT - timedelta(hours=2))
    _titel(session, "Frisch C", created_at=JETZT - timedelta(days=6, hours=23))
    _titel(session, "Alt", created_at=JETZT - timedelta(days=8))

    daten = client.get("/api/titles/stats/whitelist").json()

    assert daten["new_titles_this_week"] == 3, (
        "Genau die Zeilen der letzten sieben Tage — der Titel von vor "
        "acht Tagen gehoert nicht mehr in 'diese Woche'."
    )
    assert daten["active_titles"] == 4


def test_neue_titel_haengt_nicht_mehr_am_sync_upsert_zaehler(client_und_session):
    """Der eigentliche Fehler. Ein Sync-Lauf, der 37.978 Zeilen
    ANGEFASST hat (fast alles Aktualisierungen), darf die Kachel nicht
    fuellen — sonst meldet sie mehr 'neue' Titel als der Katalog
    ueberhaupt hat."""
    client, session = client_und_session
    _titel(session, "Einziger Neuzugang", created_at=JETZT - timedelta(days=2))
    lauf = TitleSyncRun(
        upserted_count=37978, fetched_count=40000,
        date_from=(JETZT - timedelta(days=7)).date(), date_to=JETZT.date(),
    )
    session.add(lauf)
    session.commit()

    daten = client.get("/api/titles/stats/whitelist").json()

    assert daten["new_titles_this_week"] == 1, (
        f"upserted_count={lauf.upserted_count} ist die Zahl der "
        "beruehrten Zeilen des letzten Laufs, nicht die der neuen Titel."
    )
    assert daten["new_titles_this_week"] <= daten["active_titles"], (
        "Es kann nie mehr neue Titel geben als Titel insgesamt — genau "
        "diese Unmoeglichkeit stand am 31.08.2026 im Admin."
    )


def test_neue_titel_zaehlt_auch_von_hand_angelegte(client_und_session):
    """Die Kachel soll den echten Katalog-Zuwachs zeigen, nicht nur den
    Sync-Anteil: Katalog-Nachladen und die Hand-Anlage aus der Queue
    zaehlen mit (am 31.08. waren das 29 von 29)."""
    client, session = client_und_session
    manuell = _titel(session, "Aus der Queue", created_at=JETZT - timedelta(hours=1))
    manuell.source = "Manual"
    session.add(manuell)
    session.commit()

    daten = client.get("/api/titles/stats/whitelist").json()

    assert daten["new_titles_this_week"] == 1


def test_leerer_katalog_meldet_null_statt_zu_kippen(client_und_session):
    client, session = client_und_session

    daten = client.get("/api/titles/stats/whitelist").json()

    assert daten["new_titles_this_week"] == 0
    assert daten["active_titles"] == 0
    assert daten["last_sync"] is None
    assert daten["open_title_candidates"] == 0
