"""Undo fuer die Autopilot-Fehlzuordnungen an Ein-Wort-Titel (24.08.2026).

Wolf-owned-Script. Vorfall: Der Montags-Cron vom 24.08. hat 83 Assets
automatisch Titeln wie "Driven", "Personality", "Classified" oder
"كتالوج" zugeordnet — generischen Ein-Wort-Titeln, die mit dem
Streamer-Katalog (8.940 Serien) in die Whitelist gekommen sind.

Ursache: Der Matcher stuft einen Einzelwort-Substring-Treffer bewusst
als ``substring_weak`` mit Confidence 0.90 ein ("non-safe, needs
corroboration"); seine eigene Auto-Tag-Marke liegt deshalb bei 0.95.
Der Autopilot pruefte gegen 0.85 und bestaetigte diese Zufallstreffer.
Der Code-Fix hebt die Schwelle fuer Ein-Wort-Titel auf 0.95 und haelt
generische Woerter aus dem Substring-Pfad heraus; dieses Skript raeumt
auf, was VOR dem Fix schon in der DB steht.

Was das Skript macht
====================

Es sucht Assets, die

  * einem Titel zugeordnet sind, dessen Name aus EINEM Wort besteht,
  * in einem Zeitfenster zugeordnet wurden (``--seit``/``--bis``, per
    Default der Montags-Lauf vom 24.08.2026),

und macht die Zuordnung rueckgaengig: ``title_id`` und
``de_us_match_key`` zurueck auf NULL, und die dazu geschlossenen
Kandidaten von ``resolved`` zurueck auf ``open``, damit sie wieder in
der Pruef-Queue erscheinen. Nichts wird geloescht.

Drei Modi
=========

**Default (ohne Flags) — Vorschau. AENDERT NICHTS:**

    cd backend && python -m scripts.undo_autopilot_ein_wort

Zeigt die betroffenen Assets, gruppiert nach Titel, mit Anzahl.

**Schreiben:**

    cd backend && python -m scripts.undo_autopilot_ein_wort --apply --yes

**Einzelne Titel ausnehmen** (echte Ein-Wort-Filme, die korrekt
zugeordnet wurden — z. B. "Barbie"):

    ... --behalten Barbie --behalten Wednesday

Wo ausfuehren
=============

Ueberall, wo eine Verbindung zur Produktions-DB steht. Lokal mit der
db.env genuegt — das Skript liest ``CR_DB_URL`` selbst:

    source ~/.creative-radar/db.env
    cd backend && python -m scripts.undo_autopilot_ein_wort

Alternativ in der Railway-Shell des Backend-Service; dort ist
``DATABASE_URL`` gesetzt und das Arbeitsverzeichnis ist schon das
Backend, ``cd backend`` entfaellt.

Grenze, ehrlich benannt: Das Skript kann nicht wissen, WELCHE der
Ein-Wort-Zuordnungen richtig war. Es macht alle im Fenster rueckgaengig
und schiebt sie in die Hand-Pruefung — dort entscheidet der Mensch (oder
die KI-Pruefung im naechsten Cron-Lauf). Das ist die konservative
Richtung: lieber einmal zu viel pruefen als falsche Daten im Report.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone

from sqlmodel import Session, select


def _db_bruecke() -> None:
    """Akzeptiert ``CR_DB_URL``, nicht nur ``DATABASE_URL``.

    Im Repo leben zwei Konventionen nebeneinander: ``app.database``
    loest ``DATABASE_URL`` (bzw. ``DATABASE_PRIVATE_URL`` /
    ``DATABASE_PUBLIC_URL`` / ``PG*``) auf, die lokalen Diagnose-Skripte
    lesen ``CR_DB_URL``. ``~/.creative-radar/db.env`` setzt ``CR_DB_URL``
    — wer sie sourcet und dieses Skript startet, lief ohne diese Bruecke
    in "Keine gueltige Datenbank-Konfiguration gefunden": eine Meldung,
    die nach etwas fragt, das er gerade gesetzt zu haben glaubt.
    Identisch zu ``scripts/diag_citation_rate.py:_has_db_config``.
    """
    if any(
        os.environ.get(v)
        for v in ("DATABASE_URL", "DATABASE_PRIVATE_URL", "DATABASE_PUBLIC_URL")
    ):
        return
    cr_db_url = (os.environ.get("CR_DB_URL") or "").strip()
    if cr_db_url:
        os.environ["DATABASE_URL"] = cr_db_url


def _engine():
    """Erst nach ``_db_bruecke`` importieren — ``app.database`` loest die
    URL beim Import auf, ein Import oben im Modul waere also zu frueh."""
    from app.database import engine

    return engine


# Die Bruecke MUSS vor dem ersten ``app.*``-Import laufen, nicht erst in
# ``main()``: ``app.models.entities`` zieht ``app.config`` nach, und
# pydantic baut ``settings`` beim Import aus der Umgebung. Ein spaeter
# gesetztes ``os.environ["DATABASE_URL"]`` sieht ``settings`` nie mehr —
# ``resolve_database_url()`` liest ``settings.database_url``, nicht die
# Umgebung. Genau daran ist der erste Versuch dieses Fixes gescheitert.
_db_bruecke()

from app.models.entities import (  # noqa: E402
    Asset,
    CandidateStatus,
    Title,
    TitleCandidate,
)


# Der Montags-Cron vom 24.08.2026: Autopilot lief um 05:38 UTC.
# Grosszuegiges Fenster um den Lauf, damit nichts durchrutscht.
DEFAULT_SEIT = datetime(2026, 8, 24, 5, 0, tzinfo=timezone.utc)
DEFAULT_BIS = datetime(2026, 8, 24, 6, 30, tzinfo=timezone.utc)


def _ist_ein_wort(name: str | None) -> bool:
    return bool(name) and " " not in " ".join(name.lower().split())


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _betroffene(
    session: Session,
    *,
    seit: datetime,
    bis: datetime,
    behalten: set[str],
) -> list[tuple[Asset, Title]]:
    """Assets im Fenster, die an einem Ein-Wort-Titel haengen."""
    titel = {
        t.id: t
        for t in session.exec(select(Title)).all()
        if _ist_ein_wort(t.title_original)
        and t.title_original.strip().lower() not in behalten
    }
    if not titel:
        return []
    treffer: list[tuple[Asset, Title]] = []
    for asset in session.exec(
        select(Asset).where(Asset.title_id.in_(list(titel.keys())))
    ).all():
        moment = _as_utc(asset.updated_at)
        if moment is None or not (seit <= moment <= bis):
            continue
        treffer.append((asset, titel[asset.title_id]))
    return treffer


def _vorschau(treffer: list[tuple[Asset, Title]]) -> int:
    if not treffer:
        print("Keine Ein-Wort-Zuordnungen im Fenster gefunden — nichts zu tun.")
        return 0
    zaehler = Counter(t.title_original for _, t in treffer)
    print(f"{len(treffer)} Zuordnungen an {len(zaehler)} Ein-Wort-Titel im Fenster:\n")
    for name, anzahl in zaehler.most_common():
        print(f"  {anzahl:4d} x  {name}")
    print(
        "\nVORSCHAU — es wurde nichts geaendert."
        "\nZum Ausfuehren: --apply --yes"
        "\nEinzelne Titel ausnehmen: --behalten <Titel> (mehrfach moeglich)"
    )
    return 0


def _bestaetigen() -> bool:
    print("\nZuordnungen werden geloest und die Kandidaten wieder geoeffnet.")
    try:
        antwort = input("Fortfahren? ('ja' eingeben): ").strip().lower()
    except EOFError:
        return False
    return antwort == "ja"


def _anwenden(
    session: Session, treffer: list[tuple[Asset, Title]], *, ohne_rueckfrage: bool
) -> int:
    if not treffer:
        print("Nichts zu tun.")
        return 0
    _vorschau(treffer)
    if not ohne_rueckfrage and not _bestaetigen():
        print("Abgebrochen — nichts geaendert.")
        return 1

    geloest = 0
    wieder_offen = 0
    for asset, _titel in treffer:
        asset.title_id = None
        asset.de_us_match_key = None
        asset.updated_at = datetime.now(timezone.utc)
        session.add(asset)
        geloest += 1
        for kandidat in session.exec(
            select(TitleCandidate).where(
                TitleCandidate.asset_id == asset.id,
                TitleCandidate.status == CandidateStatus.RESOLVED,
            )
        ).all():
            kandidat.status = CandidateStatus.OPEN
            kandidat.updated_at = datetime.now(timezone.utc)
            session.add(kandidat)
            wieder_offen += 1
    session.commit()
    print(
        f"\nFertig: {geloest} Zuordnungen geloest, "
        f"{wieder_offen} Vorschlaege wieder in der Pruef-Queue."
    )
    return 0


def _zeit(raw: str) -> datetime:
    moment = datetime.fromisoformat(raw)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Macht Autopilot-Zuordnungen an generische Ein-Wort-Titel "
            "rueckgaengig (Vorfall 24.08.2026). Default: Vorschau."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Aenderungen schreiben")
    parser.add_argument("--yes", action="store_true", help="ohne Rueckfrage")
    parser.add_argument("--seit", type=_zeit, default=DEFAULT_SEIT)
    parser.add_argument("--bis", type=_zeit, default=DEFAULT_BIS)
    parser.add_argument(
        "--behalten",
        action="append",
        default=[],
        metavar="TITEL",
        help="Titel, dessen Zuordnungen bleiben (mehrfach moeglich)",
    )
    args = parser.parse_args(argv)
    behalten = {t.strip().lower() for t in args.behalten if t.strip()}

    with Session(_engine()) as session:
        treffer = _betroffene(
            session, seit=args.seit, bis=args.bis, behalten=behalten
        )
        if args.apply:
            return _anwenden(session, treffer, ohne_rueckfrage=args.yes)
        return _vorschau(treffer)


if __name__ == "__main__":
    sys.exit(main())
