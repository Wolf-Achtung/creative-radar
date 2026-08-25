"""Zeigt aktive Titel, die sich einen Normalnamen teilen (25.08.2026).

Anlass
======

Das Katalog-Nachladen hat am 25.08. zwei Titel angelegt und wollte
dieselben zwei in der naechsten Runde WIEDER anlegen. Ursache war ein
``lookup.get(name)``, das den mehrdeutigen Zustand des Katalog-Lookups
(Schluessel da, Wert ``None``) nicht vom fehlenden Schluessel trennte:
ein mehrdeutiger Name fiel in den TMDb-Pfad und bekam eine weitere
Zeile — was den Namen noch mehrdeutiger machte.

Der Code ist repariert. Dieses Skript beantwortet die andere Haelfte:
was steht jetzt in der Datenbank?

Was es zeigt
============

Gruppen aktiver Titel mit gleichem Normalnamen (dieselbe Normalisierung
wie im Katalog-Lookup, Aliases zaehlen dort mit — hier bewusst NUR
Haupt- und Lokaltitel, damit die Ausgabe lesbar bleibt). Je Gruppe:
Anlagedatum, TMDb-ID, Quelle und die Zahl zugeordneter Assets.

Nicht jede Doublette ist ein Fehler: es gibt echte Namensgleichheit
zwischen einem Film und einer Serie. Die Spalten sagen, welcher Fall
vorliegt — zwei Zeilen ohne ``tmdb_id`` und mit gleicher Quelle sind
verdaechtig, ein Film von 1994 neben einer Serie von 2026 nicht.

Read-only, kein LLM-Aufruf, beliebig wiederholbar.

Wo ausfuehren
=============

    source ~/.creative-radar/db.env
    cd backend && python3 -m scripts.diag_titel_doubletten

Nur die heute entstandenen:

    python3 -m scripts.diag_titel_doubletten --tage 1
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone


def _db_bruecke() -> None:
    if any(
        os.environ.get(v)
        for v in ("DATABASE_URL", "DATABASE_PRIVATE_URL", "DATABASE_PUBLIC_URL")
    ):
        return
    cr_db_url = (os.environ.get("CR_DB_URL") or "").strip()
    if cr_db_url:
        os.environ["DATABASE_URL"] = cr_db_url


_db_bruecke()

from sqlmodel import Session, select  # noqa: E402

from app.models.entities import Asset, Title  # noqa: E402
from app.services.candidate_autopilot import _normalize  # noqa: E402


def _engine():
    from app.database import engine

    return engine


def _gruppen(session: Session) -> dict[str, list[Title]]:
    """Normalname -> Titel, nur wo es mehr als einen gibt."""
    nach_name: dict[str, list[Title]] = defaultdict(list)
    for t in session.exec(select(Title).where(Title.active == True)).all():  # noqa: E712
        for name in {_normalize(t.title_original), _normalize(t.title_local)}:
            if name and t not in nach_name[name]:
                nach_name[name].append(t)
    return {name: ts for name, ts in nach_name.items() if len(ts) > 1}


def _assets_je_titel(session: Session, titel: list[Title]) -> dict:
    ids = {t.id for t in titel}
    zaehler: dict = defaultdict(int)
    for a in session.exec(select(Asset).where(Asset.title_id.in_(ids))).all():
        zaehler[a.title_id] += 1
    return zaehler


def _jung(t: Title, tage: int | None) -> bool:
    if tage is None:
        return True
    stand = getattr(t, "created_at", None)
    if stand is None:
        return False
    if stand.tzinfo is None:
        stand = stand.replace(tzinfo=timezone.utc)
    return stand >= datetime.now(timezone.utc) - timedelta(days=tage)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--tage", type=int, default=None,
        help="nur Gruppen, in denen MINDESTENS ein Titel so jung ist",
    )
    args = p.parse_args()

    with Session(_engine()) as session:
        gruppen = _gruppen(session)
        if args.tage is not None:
            gruppen = {
                n: ts for n, ts in gruppen.items()
                if any(_jung(t, args.tage) for t in ts)
            }
        if not gruppen:
            print("Keine aktiven Titel teilen sich einen Normalnamen.")
            print("\nREAD-ONLY — es wurde nichts geaendert.")
            return 0

        print(f"{len(gruppen)} Namen mit mehr als einem aktiven Titel.\n")
        for name in sorted(gruppen):
            titel = gruppen[name]
            zaehler = _assets_je_titel(session, titel)
            print(f"  {name!r}:")
            for t in titel:
                stand = getattr(t, "created_at", None)
                datum = stand.date().isoformat() if stand else "—"
                print(
                    f"    {t.title_original!r}  tmdb_id={t.tmdb_id or '—'}  "
                    f"typ={t.content_type or '—'}  quelle={t.source or '—'}  "
                    f"angelegt={datum}  assets={zaehler.get(t.id, 0)}"
                )
            print()

    print("READ-ONLY — es wurde nichts geaendert.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
