"""Raeumt Katalog-Doubletten auf (25.08.2026). Vorschau ist die Vorgabe.

Anlass
======

``POST /api/titles`` antwortete seit jeher ``{}`` (behoben in #436).
Das Frontend las daraus ``title.id`` -> ``undefined``, schickte kein
``title_id`` an ``reviewAsset`` und meldete trotzdem Erfolg. Jeder
Klick auf "Titel anlegen" hinterliess damit eine Katalog-Zeile OHNE
Asset — und beim naechsten Post desselben Werks noch eine.

In Wolfs Production standen danach:

    'lanterns':
      'Lanterns'  tmdb_id=—      Manual  assets=0
      'Lanterns'  tmdb_id=—      Manual  assets=0
      'Lanterns'  tmdb_id=95350  TMDb    assets=1

Der Schaden geht weiter als die zwei toten Zeilen. Ein Name mit
mehreren aktiven Titeln gilt im Katalog-Lookup als "Menschensache" —
Autopilot, KI-Assist und Katalog-Nachladen lassen ihn danach ALLE
liegen. Die Doubletten blockieren also jeden kuenftigen Lanterns-Post.

Zwei Faelle, zwei Grade von Sicherheit
======================================

**Leere Doubletten** (kein einziges Asset, und mindestens eine Zeile
der Gruppe bleibt uebrig) werden stillgelegt. Das ist unstrittig: an
ihnen haengt nichts, und ohne sie loest der Lookup den Namen wieder
eindeutig auf. ``active=False`` statt Loeschen — umkehrbar.

**Gruppen, in denen mehrere Zeilen Assets tragen**, werden nur
GEMELDET. Sie koennen echte Namensgleichheit sein (ein Film von 1994
neben einer Serie von 2026) — das automatisch zusammenzulegen waere
genau der Fehler, den dieses Skript verhindern soll. Wer eine solche
Gruppe zusammenlegen will, nennt sie ausdruecklich:

    --zusammenlegen "the beauty of ballroom"

Dann wandern alle Assets auf den Anker (die Zeile mit ``tmdb_id``,
sonst die mit den meisten Assets), inklusive ``de_us_match_key``, und
der Rest wird stillgelegt.

Wo ausfuehren
=============

    source ~/.creative-radar/db.env
    cd backend && python3 -m scripts.titel_doubletten_aufraeumen

Schreiben:

    python3 -m scripts.titel_doubletten_aufraeumen --apply --yes
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict


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
from app.services.match_key import slugify_match_key  # noqa: E402


def _engine():
    from app.database import engine

    return engine


def _gruppen(session: Session) -> dict[str, list[Title]]:
    nach_name: dict[str, list[Title]] = defaultdict(list)
    for t in session.exec(select(Title).where(Title.active == True)).all():  # noqa: E712
        for name in {_normalize(t.title_original), _normalize(t.title_local)}:
            if name and t not in nach_name[name]:
                nach_name[name].append(t)
    return {name: ts for name, ts in nach_name.items() if len(ts) > 1}


def _asset_zahl(session: Session, titel: list[Title]) -> dict:
    ids = {t.id for t in titel}
    zaehler: dict = defaultdict(int)
    for a in session.exec(select(Asset).where(Asset.title_id.in_(ids))).all():
        zaehler[a.title_id] += 1
    return zaehler


def _leere_doubletten(session: Session, gruppen: dict) -> list[Title]:
    """Handgemachter Schutt: OHNE ``tmdb_id`` und ohne ein Asset.

    Die erste Fassung sagte nur "ohne Asset" — und die Vorschau gegen
    Production meldete prompt **639 Zeilen** zum Stilllegen: "The
    Mummy", "Cape Fear", "Little Women", "It", "Ocean's Eleven". Das
    sind echte, verschiedene Werke gleichen Namens aus dem TMDb-
    Katalog. Sie haben null Assets, weil ihnen noch kein beobachteter
    Post zugeordnet wurde — bei 19.000 Titeln trifft das auf fast alle
    zu. "Kein Asset" heisst also NICHT "Schutt"; die Regel haette 639
    legitime Katalog-Zeilen stillgelegt, und der Matcher haette diese
    Filme nie wieder gefunden.

    Der echte Schutt stammt aus dem kaputten "Titel anlegen" (#436):
    handgemachte Zeilen ohne ``tmdb_id``, an denen nichts haengt. Eine
    TMDb-Zeile wird hier NIE angefasst, auch wenn sie leer ist —
    Namensgleichheit im Katalog ist ein Zustand, kein Fehler.

    Bleibt der Schutz vor Uebereifer: waere die ganze Gruppe solcher
    Schutt, bliebe eine Zeile stehen, damit der Name nicht ganz aus dem
    Katalog verschwindet.
    """
    treffer: list[Title] = []
    for titel in gruppen.values():
        zaehler = _asset_zahl(session, titel)
        schutt = [
            t for t in titel
            if t.tmdb_id is None and zaehler.get(t.id, 0) == 0
        ]
        if len(schutt) == len(titel):
            schutt = schutt[1:]  # eine bleibt stehen
        treffer.extend(t for t in schutt if t not in treffer)
    return treffer


def _strittige(session: Session, gruppen: dict) -> dict[str, list[Title]]:
    """Gruppen mit mehr als einer Zeile, die Assets traegt."""
    strittig = {}
    for name, titel in gruppen.items():
        zaehler = _asset_zahl(session, titel)
        if sum(1 for t in titel if zaehler.get(t.id, 0) > 0) > 1:
            strittig[name] = titel
    return strittig


def _anker(session: Session, titel: list[Title]) -> Title:
    """Die Zeile, auf die zusammengelegt wird: TMDb schlaegt Handarbeit
    (sie traegt Genres, Alias und Datum), sonst die mit den meisten
    Assets."""
    mit_tmdb = [t for t in titel if t.tmdb_id]
    if len(mit_tmdb) == 1:
        return mit_tmdb[0]
    zaehler = _asset_zahl(session, titel)
    return max(titel, key=lambda t: zaehler.get(t.id, 0))


def _zusammenlegen(session: Session, titel: list[Title]) -> tuple[Title, int]:
    anker = _anker(session, titel)
    verschoben = 0
    for t in titel:
        if t.id == anker.id:
            continue
        for a in session.exec(select(Asset).where(Asset.title_id == t.id)).all():
            a.title_id = anker.id
            a.de_us_match_key = slugify_match_key(
                anker.franchise or anker.title_original
            )
            session.add(a)
            verschoben += 1
        t.active = False
        session.add(t)
    return anker, verschoben


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true", help="schreiben statt zeigen")
    p.add_argument("--yes", action="store_true", help="ohne Rueckfrage")
    p.add_argument(
        "--zusammenlegen", action="append", default=[], metavar="NORMALNAME",
        help="strittige Gruppe ausdruecklich zusammenlegen (mehrfach moeglich)",
    )
    args = p.parse_args()

    with Session(_engine()) as session:
        gruppen = _gruppen(session)
        if not gruppen:
            print("Keine aktiven Titel teilen sich einen Normalnamen.")
            return 0

        leere = _leere_doubletten(session, gruppen)
        strittig = _strittige(session, gruppen)

        print(f"{len(gruppen)} Namen mit mehr als einem aktiven Titel.\n")
        print(f"Leere Doubletten (werden stillgelegt): {len(leere)}")
        for t in leere:
            print(f"  {t.title_original!r}  tmdb_id={t.tmdb_id or '—'}  quelle={t.source or '—'}")

        gewaehlt = {n.strip().lower() for n in args.zusammenlegen}
        offen = {n: ts for n, ts in strittig.items() if n not in gewaehlt}
        print(f"\nStrittige Gruppen (mehrere Zeilen mit Assets): {len(strittig)}")
        for name, titel in strittig.items():
            marke = "wird zusammengelegt" if name in gewaehlt else "bleibt, Entscheidung noetig"
            print(f"  {name!r} — {marke}")
            zaehler = _asset_zahl(session, titel)
            for t in titel:
                print(
                    f"    {t.title_original!r}  tmdb_id={t.tmdb_id or '—'}  "
                    f"assets={zaehler.get(t.id, 0)}"
                )
        if offen:
            print(
                "\n  Zum Zusammenlegen den Namen ausdruecklich nennen:\n"
                + "\n".join(f'    --zusammenlegen "{n}"' for n in offen)
            )

        if not args.apply:
            print("\nVORSCHAU — es wurde nichts geaendert.")
            return 0
        if not args.yes:
            try:
                if input("\nWirklich aufraeumen? [ja/NEIN] ").strip().lower() != "ja":
                    print("Abgebrochen.")
                    return 1
            except EOFError:
                return 1

        for t in leere:
            t.active = False
            session.add(t)
        verschoben_gesamt = 0
        for name in gewaehlt:
            if name in strittig:
                _anker_titel, verschoben = _zusammenlegen(session, strittig[name])
                verschoben_gesamt += verschoben
        session.commit()
        print(
            f"\nFertig: {len(leere)} Doubletten stillgelegt, "
            f"{verschoben_gesamt} Assets verschoben."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
