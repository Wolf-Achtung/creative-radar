"""Oeffnet geschlossene Katalog-Luecken wieder — mit repariertem Namen.

Befund vom 25.08.2026 (``diag_geschlossene_luecken`` gegen Production):
74 geschlossene Kandidaten, deren Asset keinen Titel hat; 47 davon
tragen den Marker "(nicht im Katalog)". Das sind Posts, die
nachweislich ein Werk bewerben, das der Katalog nicht kennt — sie
zaehlen heute in keiner Auswertung mit, und das Katalog-Nachladen
sieht sie nicht, weil es nur OFFENE Kandidaten liest.

Die Falle
=========

Einfach wieder oeffnen waere gefaehrlich. Bei einem Teil der Faelle
traegt ``suggested_title`` noch den FEHLGRIFF DES MATCHERS, nicht den
Namen aus dem KI-Urteil:

    suggested_title='partners'    llm_note="... bewirbt 'Steckerlfisch Fiasko' ..."
    suggested_title='driven'      llm_note="... bewirbt 'Desperate Housewives' ..."
    suggested_title='classified'  llm_note="... bewirbt 'Lanterns' ..."

Das Nachladen liest ``suggested_title``. Es wuerde also bei TMDb nach
"partners" suchen — und sein Text-Beleg-Waechter winkt das durch, denn
"partners" STEHT in der Caption; genau deshalb hat der Matcher es ja
gefunden. Der Waechter schuetzt gegen erfundene Namen, nicht gegen
einen veralteten Vorschlag, der echt im Text vorkommt. Ergebnis waere
ein falscher Titel im Katalog, fest zugeordnet — dieselbe Fehlerklasse
wie die 83 Fehlzuordnungen vom 24.08.

Ursache: die Korrektur von ``suggested_title`` kam erst mit #428. Wer
zwischen #426 und #428 geprueft wurde, hat die Notiz, aber nicht den
korrigierten Vorschlag. Dazu kommen Faelle, in denen der Name damals
den Text-Beleg nicht bestand ("Léon – Der Profi", wenn die Caption nur
"Léon" schreibt).

Was das Skript macht
====================

Fuer jeden geschlossenen Kandidaten mit Luecken-Marker, dessen Asset
KEINEN Titel hat:

1. Namen aus der Notiz lesen (``bewirbt '<name>' (nicht im Katalog)``).
2. ``suggested_title`` auf diesen Namen setzen.
3. Status auf ``open``.

Danach sieht das Katalog-Nachladen sie — und entscheidet mit seinen
drei Waechtern selbst, was wirklich angelegt wird. Faelle, deren Name
den Text-Beleg nicht besteht, bleiben offen und sichtbar in der
Pruef-Queue; das ist der richtige Ort fuer sie.

Ehrliche Nebenwirkung: die Pruef-Queue fuellt sich wieder. Das ist
Absicht — leer war sie nur, weil diese Faelle unsichtbar gemacht
wurden, nicht weil sie erledigt waren.

Was es NICHT anfasst: Kandidaten ohne Marker, und alles, dessen Asset
inzwischen einen Titel hat. Kein Asset wird veraendert, kein Titel
angelegt, kein LLM-Aufruf.

Wo ausfuehren
=============

    source ~/.creative-radar/db.env
    cd backend && python3 -m scripts.luecken_wieder_oeffnen

Vorschau ist die Vorgabe. Schreiben:

    python3 -m scripts.luecken_wieder_oeffnen --apply --yes
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone


def _db_bruecke() -> None:
    """Akzeptiert ``CR_DB_URL``, nicht nur ``DATABASE_URL`` — gleiche
    Bruecke wie in den Schwester-Skripten (die db.env setzt CR_DB_URL)."""
    if any(
        os.environ.get(v)
        for v in ("DATABASE_URL", "DATABASE_PRIVATE_URL", "DATABASE_PUBLIC_URL")
    ):
        return
    cr_db_url = (os.environ.get("CR_DB_URL") or "").strip()
    if cr_db_url:
        os.environ["DATABASE_URL"] = cr_db_url


# Muss VOR dem ersten ``app.*``-Import laufen: ``app.config`` baut
# ``settings`` beim Import.
_db_bruecke()

from sqlmodel import Session, select  # noqa: E402

from app.models.entities import Asset, CandidateStatus, TitleCandidate  # noqa: E402
# ``KI: bewirbt '<name>' (nicht im Katalog) — <begruendung>``
# Nicht-gierig bis zum Marker, damit Namen mit Apostroph nicht abschneiden.
_NAME_AUS_NOTIZ = re.compile(r"bewirbt '(.+?)' \(nicht im Katalog\)")


def _engine():
    from app.database import engine

    return engine


def _name_aus_notiz(notiz: str | None) -> str | None:
    """Der Name, den die KI genannt hat — oder None, wenn die Notiz ihn
    nicht hergibt (z. B. weil sie bei 300 Zeichen abgeschnitten wurde).
    Ohne Namen wird nichts angefasst: raten waere hier schlimmer als
    liegen lassen."""
    treffer = _NAME_AUS_NOTIZ.search(notiz or "")
    if not treffer:
        return None
    name = treffer.group(1).strip()
    return name or None


def _faelle(session: Session, tage: int) -> list[tuple]:
    """(Kandidat, alter Vorschlag, Name aus der Notiz) je Fall."""
    grenze = datetime.now(timezone.utc) - timedelta(days=tage)
    treffer = []
    for c in session.exec(
        select(TitleCandidate).where(TitleCandidate.status != CandidateStatus.OPEN)
    ).all():
        # Kein eigener Marker-Filter: das Muster unten VERLANGT bereits
        # "(nicht im Katalog)". Ein zweiter Test daneben liesse sich
        # durch keine Mutation toeten — ein Waechter, der nichts
        # bewacht. Das Muster ist das einzige Tor.
        #
        # Naive und aware Zeitstempel kommen gemischt vor.
        stand = c.updated_at
        if stand is not None and stand.tzinfo is None:
            stand = stand.replace(tzinfo=timezone.utc)
        if stand is None or stand < grenze:
            continue
        asset = session.get(Asset, c.asset_id)
        if asset is None or asset.title_id is not None:
            continue
        name = _name_aus_notiz(c.llm_note)
        if name is None:
            continue
        treffer.append((c, c.suggested_title or "", name))
    return treffer


def _bericht(faelle: list[tuple]) -> None:
    if not faelle:
        print("Nichts wieder zu oeffnen.")
        return
    korrigiert = [f for f in faelle if _abweichend(f[1], f[2])]
    print(f"{len(faelle)} Faelle wuerden wieder geoeffnet.\n")
    print(
        f"Davon mit falschem Vorschlag, der repariert wird: {len(korrigiert)}\n"
        "  → ohne diese Reparatur wuerde das Nachladen den Matcher-Fehlgriff\n"
        "    bei TMDb suchen und einen falschen Titel anlegen.\n"
    )
    for _c, alt, neu in korrigiert:
        print(f"  {alt!r:32} → {neu!r}")
    rest = len(faelle) - len(korrigiert)
    if rest:
        print(f"\n{rest} Faelle tragen bereits den richtigen Namen.")


def _abweichend(alt: str, neu: str) -> bool:
    """Vergleich wie im Beleg-Waechter: nur Buchstaben und Ziffern.
    "#Lanterns" und "Lanterns" sind derselbe Vorschlag."""
    def kompakt(v: str) -> str:
        return "".join(ch for ch in (v or "").lower() if ch.isalnum())

    return kompakt(alt) != kompakt(neu)


def _bestaetigen() -> bool:
    try:
        return input("\nWirklich wieder oeffnen? [ja/NEIN] ").strip().lower() == "ja"
    except EOFError:
        return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tage", type=int, default=7)
    p.add_argument("--apply", action="store_true", help="schreiben statt zeigen")
    p.add_argument("--yes", action="store_true", help="ohne Rueckfrage")
    args = p.parse_args()

    with Session(_engine()) as session:
        faelle = _faelle(session, args.tage)
        _bericht(faelle)

        if not args.apply:
            print("\nVORSCHAU — es wurde nichts geaendert.")
            return 0
        if not faelle:
            return 0
        if not args.yes and not _bestaetigen():
            print("Abgebrochen.")
            return 1

        for cand, _alt, neu in faelle:
            cand.suggested_title = neu[:200]
            cand.status = CandidateStatus.OPEN
            session.add(cand)
        session.commit()
        print(f"\nFertig: {len(faelle)} Faelle wieder offen.")
        print(
            "Naechster Schritt: im Admin-Bereich 'Fehlende Titel pruefen\n"
            "(Vorschau)' — sie entscheidet mit ihren drei Waechtern, was\n"
            "wirklich in den Katalog kommt."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
