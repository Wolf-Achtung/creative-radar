#!/usr/bin/env python3
"""Diagnose — Falsch-Zitat-Rate der Pair-Briefs (Citation-Cutover B→A).

NUR DIAGNOSE. Read-only. Kein LLM-Aufruf, kein Cent Kosten, kein Schreiben.

Hintergrund
-----------
Der Citation-Validator laeuft seit Sprint 28.05.2026 im Soft-Modus:
``insight_citation_strict_enforce=False``. Das LLM ist im System-Prompt
zur Befuellung der ``cited_post_ids``-Felder verpflichtet, nicht belegte
IDs werden als ``insight-engine-citation-unverified`` geloggt — der Brief
geht trotzdem raus. Das Cutover-Kriterium auf Strikt steht in
``config.py``: "nach 2-3 Wochen Cron-Lauf, Falsch-Zitat-Rate < 2 % im
Log". Der Wartungsdurchgang vom 19.08.2026 hat festgestellt, dass dieser
Cutover nie stattgefunden hat — knapp drei Monate spaeter.

Warum dieses Skript nicht in Logs sucht
---------------------------------------
Die naheliegende Auswertung waere ein Griff in die Railway-Logs. Das
waere aber die schlechtere Quelle, aus drei Gruenden:

1. **Aufbewahrung.** Railway haelt Logs begrenzt vor. Die Wochen, um die
   es geht, liegen teils ausserhalb.
2. **Vollstaendigkeit.** Ein WARNING pro Sektion sagt, DASS etwas nicht
   belegt war — die Grundgesamtheit (wie viele IDs insgesamt zitiert
   wurden) steht in einem zweiten, separaten INFO-Record. Beides ueber
   Wochen zusammenzufuehren ist fehleranfaellig.
3. **Es ist gar nicht noetig.** ``_validate_citations`` ist eine reine
   Funktion von ``(LLMReport, PairAggregation)`` — und **beide liegen
   vollstaendig in der Datenbank**, als JSON-Spalten ``llm_output`` und
   ``aggregation`` je ``insight_report``-Zeile.

Dieses Skript baut die beiden Pydantic-Modelle aus den gespeicherten
Blobs zurueck und laesst denselben Validator noch einmal laufen, den der
Cron-Lauf damals ausgefuehrt hat. Ergebnis: die exakte Rate ueber den
gesamten Bestand, jederzeit reproduzierbar, unabhaengig von der
Log-Aufbewahrung.

Wichtige Einschraenkung
-----------------------
Ausgewertet wird der **heute gespeicherte** Stand. Ein Brief, der per
``force=true`` neu erzeugt wurde, hat die alte Fassung ueberschrieben
(Last-Write-Wins auf dem Composite-PK). Die Rate beschreibt also den
Bestand, nicht die Historie jedes einzelnen Generierungsversuchs. Fuer
die Cutover-Frage ist das die richtige Groesse: sie beantwortet "wie
zuverlaessig zitieren die Briefs, die wir tatsaechlich ausgeliefert
haben".

Aufruf (lokal gegen Prod, analog den anderen diag_*-Skripten):

    source ~/.creative-radar/db.env && \\
        DATABASE_URL="$CR_DB_URL" python -m scripts.diag_citation_rate

Optionen:
    --wochen N     nur die letzten N ISO-Wochen (Default: alle)
    --pro-sektion  zusaetzlich die Aufschluesselung je Brief-Sektion
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Schwelle aus dem Cutover-Kriterium in config.py.
SCHWELLE_PROZENT = 2.0


def _die(msg: str, code: int = 2):
    print(f"diag_citation_rate: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _has_db_config() -> bool:
    if any(
        os.environ.get(v)
        for v in ("DATABASE_URL", "DATABASE_PRIVATE_URL", "DATABASE_PUBLIC_URL")
    ):
        return True
    return all(
        os.environ.get(v) for v in ("PGHOST", "PGUSER", "PGPASSWORD", "PGDATABASE")
    )


def auswerten(zeilen) -> dict:
    """Rechnet die Rate ueber eine Liste von ``insight_report``-Zeilen.

    Ausgelagert und ohne DB-Zugriff, damit der Test sie mit erfundenen
    Zeilen fuettern kann — die Zahl, auf der eine Produktionsentscheidung
    beruht, darf nicht ungeprueft bleiben.
    """
    from app.schemas.insights import LLMReport, PairAggregation
    from app.services.insight_engine import (
        _build_citation_allow_set,
        _collect_cited_ids,
    )

    gesamt_zitiert = 0
    gesamt_fehlend = 0
    briefs_gesamt = 0
    briefs_mit_fehler = 0
    briefs_ohne_zitat = 0
    unlesbar: list[str] = []
    je_sektion: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [zitiert, fehlend]
    je_woche: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])

    for zeile in zeilen:
        kennung = f"{zeile.pair_key} {zeile.iso_year}-W{zeile.iso_week:02d}"
        try:
            bericht = LLMReport.model_validate(zeile.llm_output)
            agg = PairAggregation.model_validate(zeile.aggregation)
        except Exception as exc:  # noqa: BLE001 — Altzeilen sollen nicht abbrechen
            unlesbar.append(f"{kennung}: {type(exc).__name__}")
            continue

        briefs_gesamt += 1
        allow = _build_citation_allow_set(agg)
        zitiert_hier = 0
        fehlend_hier = 0

        for sektion, ids in _collect_cited_ids(bericht):
            if not ids:
                continue
            fehlend = [i for i in ids if i not in allow]
            zitiert_hier += len(ids)
            fehlend_hier += len(fehlend)
            je_sektion[sektion][0] += len(ids)
            je_sektion[sektion][1] += len(fehlend)

        if zitiert_hier == 0:
            briefs_ohne_zitat += 1
        if fehlend_hier:
            briefs_mit_fehler += 1

        gesamt_zitiert += zitiert_hier
        gesamt_fehlend += fehlend_hier
        schluessel = (zeile.iso_year, zeile.iso_week)
        je_woche[schluessel][0] += zitiert_hier
        je_woche[schluessel][1] += fehlend_hier

    rate = (gesamt_fehlend / gesamt_zitiert * 100) if gesamt_zitiert else None
    return {
        "briefs_gesamt": briefs_gesamt,
        "briefs_mit_fehler": briefs_mit_fehler,
        "briefs_ohne_zitat": briefs_ohne_zitat,
        "zitiert_gesamt": gesamt_zitiert,
        "fehlend_gesamt": gesamt_fehlend,
        "rate_prozent": rate,
        "je_sektion": {k: tuple(v) for k, v in je_sektion.items()},
        "je_woche": {k: tuple(v) for k, v in je_woche.items()},
        "unlesbar": unlesbar,
    }


def _urteil(ergebnis: dict) -> str:
    rate = ergebnis["rate_prozent"]
    if rate is None:
        return (
            "KEIN URTEIL — kein einziger Brief zitiert ueberhaupt IDs. Der "
            "Strikt-Modus haette hier nichts zu pruefen; erst klaeren, warum "
            "die cited_post_ids-Felder leer bleiben."
        )
    if ergebnis["briefs_ohne_zitat"] == ergebnis["briefs_gesamt"]:
        return "KEIN URTEIL — alle Briefs ohne Zitate."
    if rate < SCHWELLE_PROZENT:
        return (
            f"Kriterium ERFUELLT ({rate:.2f} % < {SCHWELLE_PROZENT} %). "
            f"Der Cutover auf INSIGHT_CITATION_STRICT_ENFORCE=true ist durch "
            f"die Daten gedeckt. Achtung: im Strikt-Modus loesen nicht "
            f"belegte IDs den Retry-Loop aus (MAX_RECALLS=2) und im "
            f"Worst-Case Persist-Skip — {ergebnis['briefs_mit_fehler']} der "
            f"{ergebnis['briefs_gesamt']} Briefs waeren betroffen gewesen."
        )
    return (
        f"Kriterium NICHT erfuellt ({rate:.2f} % >= {SCHWELLE_PROZENT} %). "
        f"Vor dem Cutover die Sektionen mit der hoechsten Fehlrate ansehen "
        f"(--pro-sektion); ein Flip wuerde {ergebnis['briefs_mit_fehler']} von "
        f"{ergebnis['briefs_gesamt']} Briefs in den Retry-Loop schicken."
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--wochen", type=int, default=0, help="nur die letzten N ISO-Wochen")
    p.add_argument("--pro-sektion", action="store_true", help="Aufschluesselung je Sektion")
    args = p.parse_args()

    if not _has_db_config():
        _die(
            "keine DB-Konfiguration gefunden. Erwartet DATABASE_URL (oder "
            "PGHOST/PGUSER/PGPASSWORD/PGDATABASE). Beispiel:\n"
            '    DATABASE_URL="$CR_DB_URL" python -m scripts.diag_citation_rate'
        )

    from sqlmodel import Session, select

    from app.database import engine
    from app.models.entities import InsightReport

    with Session(engine) as session:
        zeilen = list(
            session.exec(
                select(InsightReport).order_by(
                    InsightReport.iso_year.desc(), InsightReport.iso_week.desc()
                )
            )
        )

    if args.wochen:
        erlaubt = sorted({(z.iso_year, z.iso_week) for z in zeilen}, reverse=True)
        erlaubt = set(erlaubt[: args.wochen])
        zeilen = [z for z in zeilen if (z.iso_year, z.iso_week) in erlaubt]

    if not zeilen:
        _die("keine insight_report-Zeilen gefunden.", code=1)

    e = auswerten(zeilen)

    print("=" * 68)
    print("Falsch-Zitat-Rate der Pair-Briefs (Soft-Modus-Telemetrie)")
    print("=" * 68)
    print(f"Briefs ausgewertet      : {e['briefs_gesamt']}")
    print(f"  davon ohne Zitate     : {e['briefs_ohne_zitat']}")
    print(f"  davon mit Fehlzitat   : {e['briefs_mit_fehler']}")
    print(f"Zitierte IDs gesamt     : {e['zitiert_gesamt']}")
    print(f"Nicht belegte IDs       : {e['fehlend_gesamt']}")
    if e["rate_prozent"] is None:
        print("Falsch-Zitat-Rate       : n/a")
    else:
        print(f"Falsch-Zitat-Rate       : {e['rate_prozent']:.2f} %")
    if e["unlesbar"]:
        print(f"\nUebersprungen (Blob nicht lesbar): {len(e['unlesbar'])}")
        for z in e["unlesbar"][:10]:
            print(f"  - {z}")

    print("\nJe ISO-Woche (zitiert / nicht belegt / Rate):")
    for (jahr, woche), (zit, feh) in sorted(e["je_woche"].items(), reverse=True):
        r = f"{feh / zit * 100:.2f} %" if zit else "n/a"
        print(f"  {jahr}-W{woche:02d}  {zit:5d} / {feh:4d}  {r}")

    if args.pro_sektion:
        print("\nJe Sektion (zitiert / nicht belegt / Rate):")
        for sektion, (zit, feh) in sorted(
            e["je_sektion"].items(), key=lambda kv: -(kv[1][1])
        ):
            r = f"{feh / zit * 100:.2f} %" if zit else "n/a"
            print(f"  {sektion:38s} {zit:5d} / {feh:4d}  {r}")

    print("\n" + "-" * 68)
    print(_urteil(e))
    print("-" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
