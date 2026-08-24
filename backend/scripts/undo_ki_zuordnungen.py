"""Undo fuer Zuordnungen der KI-Pruefung (Vorfall 24.08.2026).

Anlass: Der erste Lauf des LLM-Assists ueber die wieder geoeffneten
Kandidaten ordnete 11 Assets zu, davon mehrere falsch — und zwei
wiederholten genau den Substring-Fehler, gegen den der Autopilot-Schutz
(#418) angetreten war:

  * Ein Post, der "Sam & Cat" bewarb, ging an den Katalog-Titel "CAT".
  * Ein Post ueber Parks and Recreation ging an ein erfundenes Spin-off
    namens "Living the Dream".
  * "American Hostage" ging an den Titel "Hostage".

Der Prompt ist in #422 geschaerft. Dieses Skript raeumt auf, was VOR der
Schaerfung schon in der DB steht.

Warum ein zweites Skript
========================

``undo_autopilot_ein_wort.py`` filtert nach EIN-WORT-Titeln im
Cron-Zeitfenster — das trifft "CAT" und "Hostage", aber nicht
"Living the Dream" oder "Michael: Part Two". Die KI-Zuordnungen haben
eine andere Achse: ``TitleCandidate.llm_checked_at``, den Zeitstempel
des KI-Urteils. Nur der Assist setzt dieses Feld, der Filter ist also
exakt.

Was das Skript macht
====================

Es sucht Kandidaten, die

  * den Status ``resolved`` tragen,
  * ein ``llm_checked_at`` im Zeitfenster haben (``--seit``/``--bis``),
  * und deren Asset einem Titel zugeordnet ist,

und macht die Zuordnung rueckgaengig: ``title_id`` und
``de_us_match_key`` zurueck auf NULL, Kandidat zurueck auf ``open``.

``llm_checked_at`` wird dabei ebenfalls geleert. Das ist Absicht: sonst
gilt der Kandidat als "schon KI-geprueft" und die verbesserte Pruefung
aus #422 wuerde ihn nie wieder ansehen. Nichts wird geloescht.

Wo ausfuehren
=============

Wie beim Schwester-Skript — lokal mit der db.env genuegt:

    source ~/.creative-radar/db.env
    cd backend && python -m scripts.undo_ki_zuordnungen

Alternativ in der Railway-Shell des Backend-Service (dort ist
``DATABASE_URL`` gesetzt und ``cd backend`` entfaellt).

Drei Modi
=========

**Default (ohne Flags) — Vorschau. AENDERT NICHTS:**

    python -m scripts.undo_ki_zuordnungen

Zeigt jede Zuordnung mit Titel und der Begruendung, die das Modell
gegeben hat — daran erkennt man die falschen am schnellsten.

**Schreiben:**

    python -m scripts.undo_ki_zuordnungen --apply --yes

**Einzelne Titel behalten** (die richtig zugeordneten):

    ... --behalten Cars --behalten Pinocchio

Grenze, ehrlich benannt: Das Skript entscheidet nicht, WELCHE Zuordnung
richtig war. Es macht alle im Fenster rueckgaengig und schiebt sie in
die Pruefung zurueck — dort entscheidet der Mensch oder die
nachgeschaerfte KI-Pruefung. Konservative Richtung: lieber einmal zu
viel pruefen als falsche Daten im Report.
"""
from __future__ import annotations

import argparse
import os
import sys
import textwrap
from collections import Counter
from datetime import datetime, timezone

from sqlmodel import Session, select


def _db_bruecke() -> None:
    """Akzeptiert ``CR_DB_URL``, nicht nur ``DATABASE_URL``.

    Gleiche Bruecke wie in ``undo_autopilot_ein_wort.py``; die db.env
    setzt ``CR_DB_URL``, ``app.database`` kennt nur ``DATABASE_URL``.
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
    URL beim Import auf."""
    from app.database import engine

    return engine


# Muss VOR dem ersten ``app.*``-Import laufen: ``app.config`` baut
# ``settings`` beim Import aus der Umgebung, und ``resolve_database_url``
# liest ``settings.database_url``, nicht ``os.environ``.
_db_bruecke()

from app.models.entities import (  # noqa: E402
    Asset,
    CandidateStatus,
    Title,
    TitleCandidate,
)

# Die KI-Pruef-Laeufe vom 24.08.2026 lagen zwischen 09:49 und 09:59 UTC.
# Grosszuegiges Fenster um den Vormittag.
DEFAULT_SEIT = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)
DEFAULT_BIS = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


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
) -> list[tuple[TitleCandidate, Asset, Title]]:
    treffer: list[tuple[TitleCandidate, Asset, Title]] = []
    for candidate in session.exec(
        select(TitleCandidate).where(
            TitleCandidate.status == CandidateStatus.RESOLVED
        )
    ).all():
        geprueft = _as_utc(candidate.llm_checked_at)
        if geprueft is None or not (seit <= geprueft <= bis):
            continue
        asset = session.get(Asset, candidate.asset_id)
        if asset is None or asset.title_id is None:
            continue
        titel = session.get(Title, asset.title_id)
        if titel is None:
            continue
        if (titel.title_original or "").strip().lower() in behalten:
            continue
        treffer.append((candidate, asset, titel))
    return treffer


def _liste(treffer: list[tuple[TitleCandidate, Asset, Title]]) -> None:
    """Nur die Aufstellung — ohne Aussage darueber, was danach passiert."""
    zaehler = Counter(t.title_original for _c, _a, t in treffer)
    print(f"{len(treffer)} KI-Zuordnungen an {len(zaehler)} Titel im Fenster:\n")
    for name, anzahl in zaehler.most_common():
        print(f"  {anzahl:4d} x  {name}")
        for candidate, _asset, titel in treffer:
            if titel.title_original != name:
                continue
            notiz = (candidate.llm_note or "").strip()
            if notiz:
                # Ungekuerzt. ``llm_note`` ist per Modell auf 300 Zeichen
                # begrenzt, passt also in wenige Zeilen. Eine Kuerzung auf
                # 150 schnitt am 24.08.2026 genau im entscheidenden Satz
                # ab ("... der Kandidat ist ein Film, der beworbene Titel
                # ist 'M") — an der einen Stelle also, an der die Vorschau
                # ihren Zweck hat: die Entscheidung zu ermoeglichen.
                for zeile in textwrap.wrap(notiz, width=100):
                    print(f"          {zeile}")


def _bestaetigen() -> bool:
    try:
        antwort = input("Fortfahren? ('ja' eingeben): ").strip().lower()
    except EOFError:
        return False
    return antwort == "ja"


def _anwenden(
    session: Session,
    treffer: list[tuple[TitleCandidate, Asset, Title]],
    *,
    ohne_rueckfrage: bool,
) -> int:
    if not treffer:
        print("Nichts zu tun.")
        return 0
    # Bewusst ``_liste`` statt ``_vorschau``: die Vorschau-Fusszeile sagt
    # "es wurde nichts geaendert" — mitten im Apply-Lauf gedruckt liest
    # sie sich als Gegenteil dessen, was gerade geschieht (Wolf,
    # 24.08.2026, genau so passiert).
    _liste(treffer)
    print(
        "\nDiese Zuordnungen werden jetzt geloest; die Vorschlaege gehen "
        "zurueck in die Pruef-Queue und werden erneut KI-geprueft."
    )
    if not ohne_rueckfrage and not _bestaetigen():
        print("Abgebrochen — nichts geaendert.")
        return 1

    geloest = 0
    for candidate, asset, _titel in treffer:
        asset.title_id = None
        asset.de_us_match_key = None
        asset.updated_at = datetime.now(timezone.utc)
        session.add(asset)
        candidate.status = CandidateStatus.OPEN
        # Marker leeren, sonst gilt der Kandidat als "schon geprueft" und
        # die nachgeschaerfte Pruefung aus #422 sieht ihn nie wieder an.
        candidate.llm_checked_at = None
        candidate.llm_note = "Frueheres KI-Urteil verworfen (Vorfall 24.08.2026)."
        candidate.updated_at = datetime.now(timezone.utc)
        session.add(candidate)
        geloest += 1
    session.commit()
    print(
        f"\nFertig: {geloest} KI-Zuordnungen geloest, "
        f"{geloest} Vorschlaege wieder in der Pruef-Queue."
    )
    return 0


def _vorschau(treffer: list[tuple[TitleCandidate, Asset, Title]]) -> int:
    if not treffer:
        print("Keine KI-Zuordnungen im Fenster gefunden — nichts zu tun.")
        return 0
    _liste(treffer)
    print(
        "\nVORSCHAU — es wurde nichts geaendert."
        "\nZum Ausfuehren: --apply --yes"
        "\nEinzelne Titel behalten: --behalten <Titel> (mehrfach moeglich)"
    )
    return 0


def _zeit(raw: str) -> datetime:
    wert = datetime.fromisoformat(raw)
    return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Macht Zuordnungen der KI-Pruefung rueckgaengig "
            "(Vorfall 24.08.2026). Default: Vorschau."
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
