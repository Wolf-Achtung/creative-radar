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

import inspect
import os
import subprocess
import sys
from pathlib import Path
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


# --- Aufrufbarkeit: die db.env-Falle -------------------------------------
#
# ``~/.creative-radar/db.env`` setzt ``CR_DB_URL``; ``app.database`` kennt
# aber nur ``DATABASE_URL`` & Co. Ohne Bruecke bricht das Skript mit
# "Keine gueltige Datenbank-Konfiguration gefunden" ab — bei einem
# Nutzer, der die Verbindung gerade gesetzt zu haben glaubt. Genau das
# Muster steht schon in ``scripts/diag_citation_rate.py``.


def test_cr_db_url_wird_als_database_url_uebernommen(monkeypatch):
    for var in ("DATABASE_URL", "DATABASE_PRIVATE_URL", "DATABASE_PUBLIC_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CR_DB_URL", "postgresql://u:p@host:5432/db")

    undo._db_bruecke()

    assert os.environ["DATABASE_URL"] == "postgresql://u:p@host:5432/db"


def test_gesetzte_database_url_wird_nicht_ueberschrieben(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://echt:x@host/prod")
    monkeypatch.setenv("CR_DB_URL", "postgresql://alt:y@host/anders")

    undo._db_bruecke()

    assert os.environ["DATABASE_URL"] == "postgresql://echt:x@host/prod", (
        "Wer DATABASE_URL explizit setzt (Railway-Shell), meint sie auch — "
        "eine alte CR_DB_URL in der Shell darf sie nicht verdraengen."
    )


def test_engine_wird_erst_beim_aufruf_importiert():
    """Der Import von ``app.database`` loest die DB-URL auf. Steht er oben
    im Modul, laeuft er VOR der Bruecke — dann half die Bruecke nicht."""
    quelle = inspect.getsource(undo)
    kopf = quelle.split("def _db_bruecke")[0]

    assert "from app.database import" not in kopf, (
        "app.database darf nicht auf Modulebene importiert werden, sonst "
        "greift _db_bruecke zu spaet."
    )
    assert "from app.database import engine" in inspect.getsource(undo._engine)


def test_cr_db_url_traegt_bis_zum_verbindungsaufbau(tmp_path):
    """Der Wächter fuer den eigentlichen Fehler: die Bruecke muss VOR dem
    ersten ``app.*``-Import greifen.

    Im selben Prozess laesst sich das nicht pruefen — ``settings`` ist
    da laengst gebaut. Also ein echter Aufruf in einem Subprozess, genau
    so, wie er nach ``source ~/.creative-radar/db.env`` aussieht. Der
    erste Versuch dieses Fixes rief ``_db_bruecke()`` in ``main()`` auf
    und lief trotzdem in "Keine gueltige Datenbank-Konfiguration".
    """
    umgebung = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "DATABASE_URL",
            "DATABASE_PRIVATE_URL",
            "DATABASE_PUBLIC_URL",
            "ALLOW_SQLITE_FALLBACK",
        }
    }
    umgebung["CR_DB_URL"] = "postgresql://u:p@ungueltig.invalid:5432/db"

    ergebnis = subprocess.run(
        [sys.executable, "-m", "scripts.undo_autopilot_ein_wort"],
        cwd=Path(__file__).resolve().parents[2],
        env=umgebung,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert "Keine gültige Datenbank-Konfiguration" not in ergebnis.stderr, (
        "CR_DB_URL wurde nicht uebernommen — die Bruecke laeuft zu spaet, "
        "nach dem Import von app.config."
    )
    # Erwartet ist jetzt ein Verbindungsfehler auf den erfundenen Host:
    # die Konfiguration wurde also akzeptiert.
    assert "ungueltig.invalid" in ergebnis.stderr


# --- Ausgabe: sagt der Lauf die Wahrheit ueber sich selbst? --------------
#
# Am 24.08.2026 druckte der Apply-Lauf die komplette Vorschau-Fusszeile —
# "VORSCHAU — es wurde nichts geaendert" und "Zum Ausfuehren: --apply
# --yes" — obwohl er gerade 83 Zuordnungen loeste. Wolf sah das mitten in
# seinem eigenen --apply-Lauf und musste annehmen, sein Flag sei
# ignoriert worden. Die bestehenden Tests pruefen nur die WIRKUNG; die
# Ausgabe war ungeprueft. Deshalb hier.


def test_apply_behauptet_nicht_es_habe_nichts_geaendert(session, capsys):
    _zuordnung(session, "Driven", wann=IM_FENSTER)

    undo._anwenden(session, _treffer(session), ohne_rueckfrage=True)

    ausgabe = capsys.readouterr().out
    assert "nichts geaendert" not in ausgabe, (
        "Der Apply-Lauf darf nicht behaupten, er habe nichts geaendert."
    )
    assert "--apply" not in ausgabe, (
        "Der Apply-Lauf darf nicht zu --apply auffordern — er IST der "
        "Apply-Lauf."
    )
    assert "Fertig: 1 Zuordnungen geloest" in ausgabe
    # Die Aufstellung selbst bleibt: sie zeigt, was angefasst wurde.
    assert "Driven" in ausgabe


def test_vorschau_sagt_weiterhin_dass_nichts_geschieht(session, capsys):
    _zuordnung(session, "Driven", wann=IM_FENSTER)

    undo._vorschau(_treffer(session))

    ausgabe = capsys.readouterr().out
    assert "VORSCHAU — es wurde nichts geaendert." in ausgabe
    assert "--apply --yes" in ausgabe
    assert "Fertig" not in ausgabe
