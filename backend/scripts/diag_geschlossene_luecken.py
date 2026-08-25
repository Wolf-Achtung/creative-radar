"""Zeigt Kandidaten, die geschlossen wurden, ohne dass ihr Asset einen
Titel bekam (25.08.2026). Read-only.

Anlass
======

Wolfs Korrektur: Die Prüf-Queue in Production steht auf 0 — aber nicht,
weil der Montags-Cron sie abgearbeitet hätte, sondern weil er sie am
25.08. von Hand geleert hat. Kurz zuvor war das Katalog-Nachladen
fertig geworden (#429), das genau die Fälle auflöst, an denen die
KI-Prüfung endet: "der Post bewirbt X, aber X steht nicht im Katalog".

Damit besteht ein Verdacht, der sich nachrechnen lässt. Das Nachladen
sieht ausschliesslich **offene** Kandidaten mit dem Marker
``(nicht im Katalog)``. Wurde so ein Fall von Hand auf ``ignored``
gesetzt, ist er für das Feature unsichtbar — dauerhaft. Das Asset
bleibt ohne Titel, der Post zählt in keiner Auswertung mit, und kein
späterer Lauf holt ihn zurück.

Das ist nicht dasselbe wie ein von Hand angelegter Titel: dann trägt
das Asset eine ``title_id`` und alles ist gut. Der Unterschied ist
genau das, was dieses Skript sichtbar macht.

Was es zeigt
============

Kandidaten mit Status ``ignored`` oder ``resolved``, deren Asset
**keine** ``title_id`` hat — gruppiert danach, ob sie einen Lücken-
Marker tragen. Nur diese Gruppe wäre ein Fall für das Nachladen.

Es ändert NICHTS. Kein Schreibzugriff, kein LLM-Aufruf, beliebig oft
wiederholbar.

Wo ausführen
============

    source ~/.creative-radar/db.env
    cd backend && python3 -m scripts.diag_geschlossene_luecken

Fenster einstellen (Vorgabe: 2 Tage):

    python3 -m scripts.diag_geschlossene_luecken --tage 7
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone


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


# Muss VOR dem ersten ``app.*``-Import laufen: ``app.config`` baut
# ``settings`` beim Import, und ``resolve_database_url`` liest
# ``settings.database_url``, nicht die Umgebung.
_db_bruecke()

from sqlmodel import Session, select  # noqa: E402

from app.models.entities import Asset, CandidateStatus, TitleCandidate  # noqa: E402
from app.services.katalog_nachladen import NICHT_IM_KATALOG  # noqa: E402


def _engine():
    from app.database import engine

    return engine


def _geschlossen_ohne_titel(session: Session, tage: int) -> list[tuple]:
    """(Kandidat, Asset) für alles, was zu ist und trotzdem titellos."""
    grenze = datetime.now(timezone.utc) - timedelta(days=tage)
    kandidaten = session.exec(
        select(TitleCandidate).where(TitleCandidate.status != CandidateStatus.OPEN)
    ).all()

    treffer = []
    for c in kandidaten:
        # Naive und aware Zeitstempel kommen in diesem Bestand gemischt
        # vor (aeltere Zeilen ohne tzinfo) — sonst wirft der Vergleich.
        stand = c.updated_at
        if stand is not None and stand.tzinfo is None:
            stand = stand.replace(tzinfo=timezone.utc)
        if stand is None or stand < grenze:
            continue
        asset = session.get(Asset, c.asset_id)
        if asset is None or asset.title_id is not None:
            continue
        treffer.append((c, asset))
    return treffer


def _bericht(treffer: list[tuple], tage: int) -> None:
    print(f"Fenster: die letzten {tage} Tage.\n")
    if not treffer:
        print(
            "Nichts gefunden: jeder geschlossene Kandidat in diesem Fenster\n"
            "hat seinem Asset auch einen Titel verschafft. Es liegt nichts\n"
            "begraben, das dem Katalog-Nachladen entgangen waere."
        )
        return

    mit_marker = [(c, a) for c, a in treffer if NICHT_IM_KATALOG in (c.llm_note or "")]
    ohne = [t for t in treffer if t not in mit_marker]

    print(
        f"{len(treffer)} geschlossene Kandidaten, deren Asset KEINEN Titel hat.\n"
    )
    nach_status = Counter(str(c.status.value) for c, _ in treffer)
    for status, anzahl in nach_status.most_common():
        print(f"  {anzahl:4d} x  status={status}")
    print()

    print(
        f"Davon mit Katalog-Luecken-Marker: {len(mit_marker)}\n"
        "  → genau die Faelle, die das Nachladen aufloesen koennte, wenn\n"
        "    sie wieder offen waeren. Heute sieht es sie nicht.\n"
    )
    for c, _a in mit_marker[:40]:
        print(f"  - {c.suggested_title!r}  [{c.status.value}]  {(c.llm_note or '')[:90]}")
    if len(mit_marker) > 40:
        print(f"  ... und {len(mit_marker) - 40} weitere")

    print(
        f"\nOhne Marker: {len(ohne)}\n"
        "  → zu, ohne Titel, ohne KI-Urteil zur Katalog-Luecke. Das kann\n"
        "    richtig sein (Post bewirbt gar kein Werk) oder eine Handablage\n"
        "    ohne Begruendung. Hier hilft nur der Blick auf den Post."
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--tage", type=int, default=2,
        help="Wie weit zurueck geschaut wird (Vorgabe: 2).",
    )
    args = p.parse_args()

    with Session(_engine()) as session:
        _bericht(_geschlossen_ohne_titel(session, args.tage), args.tage)
    print("\nREAD-ONLY — es wurde nichts geaendert.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
