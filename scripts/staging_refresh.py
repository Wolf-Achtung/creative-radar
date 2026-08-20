"""Staging-Refresh: kopiert die Produktions-Daten in die Staging-DB.

Zweck (Entscheidung 20.08.2026): Staging zeigt echte Produktionsdaten,
damit neue Auswertungen (Trailer-Intelligence) gegen den echten Bestand
entwickelt werden koennen — als KOPIE. Staging liest nie live aus der
Prod-DB und schreibt erst recht nicht hinein. Was auf Staging passiert,
kann Produktion nicht beruehren; der naechste Refresh setzt Staging
wieder auf den Prod-Stand.

Laeuft auf dem Mac. Einzige Voraussetzung sind die Postgres-Client-
Tools (pg_dump, pg_restore, psql) — kein Python-Paket:

    brew install libpq && brew link --force libpq

Aufruf (URLs liegen in ~/.creative-radar/, nie im Repo):

    source ~/.creative-radar/db.env          # CR_DB_URL         = Produktion (Quelle)
    source ~/.creative-radar/staging.env     # CR_STAGING_DB_URL = Staging (Ziel)
    python3 scripts/staging_refresh.py --ziel-host <staging-db-host>

    # Erst pruefen (Standard ist eine Trockenuebung), dann:
    python3 scripts/staging_refresh.py --ziel-host <staging-db-host> --ausfuehren

``--ziel-host`` ist die Sicherung gegen die einzige Katastrophe, die
dieses Skript anrichten koennte: Quelle und Ziel vertauscht. Der Wert
muss im Ziel vorkommen und darf in der Quelle NICHT vorkommen — sonst
bricht das Skript ab, bevor irgendetwas passiert. Denselben Wert traegt
das Staging-Backend als ``STAGING_EXPECTED_DB_HOST`` (Boot-Check in
app/database.py); ein Host, zwei Schloesser.

Was kopiert wird: das Schema ``creative_radar`` komplett (Tabellen,
Daten, Alembic-Stand). Auf dem Ziel wird das Schema vorher verworfen —
ein Refresh ist ein Reset, Staging-Handstaende ueberleben ihn nicht.

Hinweis: die DB-URLs (samt Passwort) erscheinen fuer die Dauer von
pg_dump/pg_restore in der lokalen Prozessliste. Auf dem eigenen Rechner
in Ordnung; auf geteilten Maschinen dieses Skript nicht verwenden.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCHEMA = "creative_radar"


class RichtungsFehler(RuntimeError):
    """Quelle und Ziel sind nicht eindeutig getrennt — nichts anfassen."""


def pruefe_richtung(quelle: str, ziel: str, ziel_host: str) -> None:
    """Die Sicherung. Wirft ``RichtungsFehler``, wenn nicht zweifelsfrei
    feststeht, dass ``ziel`` die Staging-DB ist und ``quelle`` nicht.

    Bewusst Pflicht-Argument statt Erraten aus der URL: Railway-Hosts
    sehen sich aehnlich (``*.railway.internal``, ``*.proxy.rlwy.net``),
    und eine Heuristik, die einmal falsch raet, restauriert in die
    Produktion. Der Mensch nennt den Staging-Host, das Skript prueft ihn
    gegen BEIDE Seiten.
    """
    if not quelle:
        raise RichtungsFehler("CR_DB_URL (Quelle/Produktion) ist nicht gesetzt.")
    if not ziel:
        raise RichtungsFehler("CR_STAGING_DB_URL (Ziel/Staging) ist nicht gesetzt.")
    if not ziel_host or not ziel_host.strip():
        raise RichtungsFehler("--ziel-host ist leer.")
    ziel_host = ziel_host.strip()
    if quelle == ziel:
        raise RichtungsFehler(
            "Quelle und Ziel sind DIESELBE URL. Abbruch — ein Refresh in die "
            "eigene Quelle wuerde die Produktion loeschen."
        )
    if ziel_host not in ziel:
        raise RichtungsFehler(
            f"--ziel-host {ziel_host!r} kommt in CR_STAGING_DB_URL nicht vor. "
            f"Entweder ist der Host falsch oder die Ziel-URL ist nicht die "
            f"Staging-DB. Abbruch."
        )
    if ziel_host in quelle:
        raise RichtungsFehler(
            f"--ziel-host {ziel_host!r} kommt AUCH in CR_DB_URL (Quelle) vor "
            f"und unterscheidet die beiden damit nicht. Abbruch — mit einem "
            f"eindeutigeren Host neu aufrufen."
        )


WERKZEUGE = ("pg_dump", "pg_restore", "psql")


def _werkzeuge_vorhanden() -> None:
    fehlend = [w for w in WERKZEUGE if shutil.which(w) is None]
    if fehlend:
        sys.exit(
            f"Fehlt im PATH: {', '.join(fehlend)}. Installieren mit:\n"
            f"    brew install libpq && brew link --force libpq"
        )


def _schema_verwerfen(ziel: str) -> None:
    """Loescht das Ziel-Schema. Angelegt wird es NICHT — der Dump bringt
    sein eigenes ``CREATE SCHEMA`` mit, und ein vorab angelegtes Schema
    liesse pg_restore mit "already exists" meckern.

    Bewusst ueber ``psql`` statt ueber ein Python-Paket: pg_dump und
    pg_restore braucht dieses Skript ohnehin, und ``psql`` kommt mit
    denselben ``brew install libpq`` mit. Eine Installation, keine
    Python-Abhaengigkeit — das Skript laeuft mit dem System-Python.
    """
    subprocess.run(
        ["psql", "--quiet", "--no-psqlrc", "--set=ON_ERROR_STOP=1",
         "--command", f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE', ziel],
        check=True,
    )


def refresh(quelle: str, ziel: str, ziel_host: str, *, ausfuehren: bool) -> None:
    pruefe_richtung(quelle, ziel, ziel_host)
    if not ausfuehren:
        print("Trockenuebung — es passiert nichts. Der echte Lauf wuerde:")
        print(f"  1. pg_dump  --schema={SCHEMA} aus der Quelle (CR_DB_URL)")
        print(f"  2. auf dem Ziel: DROP SCHEMA {SCHEMA} CASCADE")
        print("  3. pg_restore in das Ziel (CR_STAGING_DB_URL)")
        print("Zum Ausfuehren: --ausfuehren anhaengen.")
        return

    _werkzeuge_vorhanden()
    with tempfile.TemporaryDirectory(prefix="cr_staging_refresh_") as tmp:
        dump = Path(tmp) / "prod.dump"
        print(f"1/3 pg_dump (Schema {SCHEMA}) aus der Produktion …")
        subprocess.run(
            ["pg_dump", "--format=custom", f"--schema={SCHEMA}",
             "--no-owner", "--no-privileges", f"--file={dump}", quelle],
            check=True,
        )
        print(f"    {dump.stat().st_size / 1_048_576:.1f} MiB")
        print(f"2/3 Ziel-Schema {SCHEMA} verwerfen …")
        _schema_verwerfen(ziel)
        print("3/3 pg_restore in die Staging-DB …")
        subprocess.run(
            ["pg_restore", "--no-owner", "--no-privileges",
             f"--dbname={ziel}", str(dump)],
            check=True,
        )
    print("Fertig. Staging traegt jetzt den Prod-Stand von gerade eben.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--ziel-host", required=True,
        help="Host der Staging-DB; muss im Ziel vorkommen und in der Quelle fehlen.",
    )
    parser.add_argument(
        "--ausfuehren", action="store_true",
        help="Wirklich kopieren. Ohne dieses Flag: Trockenuebung.",
    )
    args = parser.parse_args()
    try:
        refresh(
            os.environ.get("CR_DB_URL", ""),
            os.environ.get("CR_STAGING_DB_URL", ""),
            args.ziel_host,
            ausfuehren=args.ausfuehren,
        )
    except RichtungsFehler as fehler:
        sys.exit(f"ABBRUCH: {fehler}")


if __name__ == "__main__":
    main()
