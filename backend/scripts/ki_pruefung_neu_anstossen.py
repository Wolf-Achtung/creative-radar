"""Setzt den KI-Prüf-Marker offener Kandidaten zurück (24.08.2026).

Anlass: #426 hat der KI-Prüfung beigebracht, den wirklich beworbenen
Titel zu verwerten ("der Post bewirbt 'Lanterns', nicht 'Driven'").
Nach dem Deploy meldete der Knopf trotzdem nur "1 neu geprüft" — und
die 50 Karten in der Prüf-Queue blieben unverändert stehen.

Der Grund ist ein Mechanismus, der für sich genommen richtig ist:
``llm_checked_at`` verhindert, dass jeder Klick dieselben ersten zwölf
Kandidaten prüft (Wolfs Befund vom 21.08.). Er unterscheidet aber nicht
zwischen "schon geprüft" und "von einer ÄLTEREN, schwächeren Prüfung
geprüft". Ein verbesserter Prüfer sieht den Bestand deshalb nie.

Dasselbe Muster wie bei #421: Ein Fix wirkt nur auf Neues und lässt
liegen, was schon da ist.

Was das Skript macht
====================

Es sucht Kandidaten mit Status ``open`` und gesetztem
``llm_checked_at`` und löscht diesen Marker. Beim nächsten Klick auf
"KI-Prüfung" laufen sie erneut durch — diesmal durch den Prüfer aus
#426, der den beworbenen Titel nennen darf.

Es fasst NICHTS an, was zugeordnet ist: ``resolved``-Kandidaten bleiben
unberührt, kein Asset wird verändert. Die Kosten sind ein Haiku-Call je
zurückgesetztem Kandidaten, deutlich unter einem Cent.

Wo ausführen
============

    source ~/.creative-radar/db.env
    cd backend && python -m scripts.ki_pruefung_neu_anstossen

Alternativ in der Railway-Shell des Backend-Service.

Zwei Modi
=========

**Default — Vorschau. ÄNDERT NICHTS:**

    python -m scripts.ki_pruefung_neu_anstossen

**Schreiben:**

    python -m scripts.ki_pruefung_neu_anstossen --apply --yes
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone

from sqlmodel import Session, select


def _db_bruecke() -> None:
    """Akzeptiert ``CR_DB_URL``, nicht nur ``DATABASE_URL`` — gleiche
    Brücke wie in den Schwester-Skripten (die db.env setzt CR_DB_URL)."""
    if any(
        os.environ.get(v)
        for v in ("DATABASE_URL", "DATABASE_PRIVATE_URL", "DATABASE_PUBLIC_URL")
    ):
        return
    cr_db_url = (os.environ.get("CR_DB_URL") or "").strip()
    if cr_db_url:
        os.environ["DATABASE_URL"] = cr_db_url


def _engine():
    from app.database import engine

    return engine


# Muss VOR dem ersten ``app.*``-Import laufen: ``app.config`` baut
# ``settings`` beim Import, und ``resolve_database_url`` liest
# ``settings.database_url``, nicht die Umgebung.
_db_bruecke()

from app.models.entities import CandidateStatus, TitleCandidate  # noqa: E402


def _betroffene(session: Session) -> list[TitleCandidate]:
    return [
        c
        for c in session.exec(
            select(TitleCandidate).where(
                TitleCandidate.status == CandidateStatus.OPEN
            )
        ).all()
        if c.llm_checked_at is not None
    ]


def _liste(treffer: list[TitleCandidate]) -> None:
    print(f"{len(treffer)} offene Kandidaten tragen einen KI-Marker.\n")
    # Nach Notiz-Anfang gruppiert: zeigt auf einen Blick, welche Urteile
    # die alte Prüfung gefällt hat und wie viele davon neu drankommen.
    zaehler = Counter(
        (c.llm_note or "ohne Notiz").split(":")[0].strip() for c in treffer
    )
    for art, anzahl in zaehler.most_common():
        print(f"  {anzahl:4d} x  {art}")


def _bestaetigen() -> bool:
    try:
        return input("Fortfahren? ('ja' eingeben): ").strip().lower() == "ja"
    except EOFError:
        return False


def _anwenden(
    session: Session, treffer: list[TitleCandidate], *, ohne_rueckfrage: bool
) -> int:
    if not treffer:
        print("Nichts zu tun.")
        return 0
    _liste(treffer)
    print(
        "\nDiese Marker werden jetzt geloescht. Der naechste Klick auf "
        "\"KI-Pruefung\" prueft die Kandidaten erneut — mit dem Pruefer "
        "aus #426."
    )
    if not ohne_rueckfrage and not _bestaetigen():
        print("Abgebrochen — nichts geaendert.")
        return 1

    for candidate in treffer:
        candidate.llm_checked_at = None
        candidate.llm_note = None
        candidate.updated_at = datetime.now(timezone.utc)
        session.add(candidate)
    session.commit()
    print(
        f"\nFertig: {len(treffer)} Marker geloescht. Jetzt im Dashboard "
        "\"KI-Pruefung\" klicken (12 je Klick)."
    )
    return 0


def _vorschau(treffer: list[TitleCandidate]) -> int:
    if not treffer:
        print("Kein offener Kandidat traegt einen KI-Marker — nichts zu tun.")
        return 0
    _liste(treffer)
    print(
        "\nVORSCHAU — es wurde nichts geaendert."
        "\nZum Ausfuehren: --apply --yes"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Loescht den KI-Pruef-Marker offener Kandidaten, damit der "
            "verbesserte Pruefer sie erneut ansieht. Default: Vorschau."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Aenderungen schreiben")
    parser.add_argument("--yes", action="store_true", help="ohne Rueckfrage")
    args = parser.parse_args(argv)

    with Session(_engine()) as session:
        treffer = _betroffene(session)
        if args.apply:
            return _anwenden(session, treffer, ohne_rueckfrage=args.yes)
        return _vorschau(treffer)


if __name__ == "__main__":
    sys.exit(main())
