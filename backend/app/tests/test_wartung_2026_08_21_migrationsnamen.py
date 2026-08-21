"""Migrations-Namen-Wächter (21.08.2026).

Anlass: Migration e7f3a9c258d1 schrieb ``ALTER TABLE
creative_radar.title_candidate`` — die Tabelle heisst aber
``titlecandidate``, weil ``TitleCandidate`` kein ``__tablename__``
setzt und der SQLModel-Default den Klassennamen nur kleinschreibt,
OHNE Unterstriche einzufuegen. Beide Railway-Deploys (prod + staging)
brachen im Pre-Deploy ab. Kein Test konnte das sehen: der
SQLite-Testpfad ueberspringt die Postgres-DDL, und CI bootstrappt per
``create_all`` statt ueber die Alembic-Kette.

Dieser Wächter liest deshalb die Migrations-QUELLTEXTE: jeder
schema-qualifizierte Tabellenname in ALTER/CREATE/INSERT/UPDATE/DELETE-
Statements muss eine Tabelle sein, die SQLModel.metadata kennt.
Enum-Typen (CREATE TYPE ... ``channel_segment``) und reine
Docstring-Erwaehnungen bleiben aussen vor, weil nur DML/DDL-Praefixe
gematcht werden.
"""
from __future__ import annotations

import re
from pathlib import Path

from sqlmodel import SQLModel

import app.models.entities  # noqa: F401 — fuellt SQLModel.metadata

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"

# Tabellen, die eine Migration selbst anlegt und die bewusst NICHT als
# Entity existieren (Scratch-/Rollback-Tabellen der Migration).
MIGRATIONS_EIGENE_TABELLEN = {"_segment_backfill_rollback"}

_STATEMENT = re.compile(
    r"(?:ALTER\s+TABLE|CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|"
    r"INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"
    r"(?:\{SCHEMA\}|creative_radar)\.(\w+)",
    re.IGNORECASE,
)


def _referenzierte_tabellen() -> dict[str, set[str]]:
    """Migrations-Datei → Menge der per DDL/DML angefassten Tabellennamen."""
    treffer: dict[str, set[str]] = {}
    for datei in sorted(MIGRATIONS.glob("*.py")):
        namen = set(_STATEMENT.findall(datei.read_text(encoding="utf-8")))
        if namen:
            treffer[datei.name] = namen
    return treffer


def test_wächter_findet_ueberhaupt_statements():
    """Selbsttest: liefert der Regex nichts, prueft der Wächter nichts."""
    alle = _referenzierte_tabellen()
    assert alle, "Kein einziges Statement gefunden — Regex oder Pfad kaputt."
    assert any("titlecandidate" in namen for namen in alle.values()), (
        "Die korrigierte e7f3a9c258d1 muss 'titlecandidate' anfassen — "
        "sonst prueft dieser Wächter an der falschen Stelle."
    )


def test_jeder_migrations_tabellenname_existiert_als_entity():
    """Der Fehler vom 21.08.: 'title_candidate' statt 'titlecandidate'.

    SQLModel-Default-Namen sind der kleingeschriebene Klassenname OHNE
    Unterstrich — wer eine Migration von Hand schreibt, raet den Namen
    leicht falsch, und kein SQLite-Test bemerkt es. Deshalb: jeder in
    einer Migration angefasste Tabellenname muss in SQLModel.metadata
    stehen (oder ausdruecklich als Migrations-eigene Tabelle gelistet
    sein).
    """
    bekannte = {t.name for t in SQLModel.metadata.tables.values()}
    bekannte |= MIGRATIONS_EIGENE_TABELLEN

    fehler = {
        datei: sorted(namen - bekannte)
        for datei, namen in _referenzierte_tabellen().items()
        if namen - bekannte
    }
    assert not fehler, (
        "Migrationen fassen Tabellen an, die keine Entity kennt — beim "
        f"Deploy bricht 'alembic upgrade head' ab: {fehler}"
    )
