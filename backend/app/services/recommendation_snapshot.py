"""Empfehlungs-Snapshots (22.08.2026) — was hat das System wann empfohlen?

Das Wir-Segment vergleicht heute "empfohlen" und "gemacht" im SELBEN
Fenster — der naechste Schritt (echtes Vorher/Nachher: hat die
Empfehlung von KW X das Verhalten in KW X+1 veraendert?) braucht
eingefrorene Empfehlungs-Zeitpunkte. Bislang rechnete das System die
Empfehlungen bei jedem Abruf frisch und vergass sie wieder; jede Woche
ohne Snapshot ist eine verlorene Messwoche.

Dieser Service schreibt einmal pro Cron-Lauf die belastbaren Zellen des
Muster-Berichts weg. **Empfehlung** heisst weiterhin ausschliesslich
``breakout_verdict == "over"`` — EXAKT dieselbe MACHEN-Auswahl wie
``pattern_playbook`` und ``wir_segment``, keine Zweitdefinition. Seit
dem 26.08.2026 stehen zusaetzlich die ``under``-Zellen mit in der Row
(Feld ``breakout_verdict`` je Eintrag), damit auch "funktioniert gerade
nicht"-Befunde eine Wochen-Historie bekommen. Leser, die Empfehlungen
meinen, filtern auf over — Eintraege OHNE verdict-Feld stammen aus der
Zeit davor und sind per Konstruktion over. Eine Row pro ISO-Woche,
Last-Write-Wins beim Force-Re-Run derselben Woche.

Auf dieser Historie stehen zwei Ehrlichkeits-Auswertungen (26.08.2026):

- ``annotiere_bestaendigkeit`` haengt jeder belastbaren Zelle des
  Live-Berichts an, die wievielte Woche in Folge sie ihr Verdikt
  traegt. Der Bericht prueft ~40 Zellen pro Woche gegen |z| >= 2 —
  ein bis zwei Zufallstreffer sind zu ERWARTEN. Wiederkehr ist das
  Kriterium, das Rauschen von Signal trennt, und sie war bisher
  unsichtbar.
- ``compute_bewaehrung`` misst die Trefferquote des Systems selbst:
  wie viele Empfehlungen einer Woche standen in der Folgewoche noch?
  Reiner Snapshot-Vergleich, jederzeit wiederholbar.

Bewusst deterministisch und LLM-frei: nur DB-Lesen plus eine
JSON-Zeile schreiben — die Stage kann den Lauf weder verteuern noch
nennenswert verlaengern.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlmodel import Session, select

from app.models.entities import RecommendationSnapshot
from app.services.trailer_patterns import DEFAULT_WINDOW_DAYS, compute_trailer_patterns

logger = logging.getLogger(__name__)

# Hoechstens so viele Wochen zurueck fuer Bestaendigkeit und Bewaehrung —
# gleiche Grenze wie der Beweis-Loop (MAX_WOCHEN dort).
MAX_HISTORIE_WOCHEN = 12


def persist_recommendation_snapshot(
    session: Session,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: Optional[datetime] = None,
) -> dict:
    moment = now or datetime.now(timezone.utc)
    report = compute_trailer_patterns(session, window_days=window_days, now=now)

    zellen: list[dict] = []
    empfohlen = 0
    for dimension, cells in report.dimensions.items():
        for cell in cells:
            if cell.breakout_verdict not in ("over", "under"):
                continue
            if cell.breakout_verdict == "over":
                empfohlen += 1
            zellen.append({
                "dimension": dimension,
                "value": cell.value,
                "breakout_verdict": cell.breakout_verdict,
                "median_lift": round(cell.median_lift, 3),
                "breakout_z": (
                    round(cell.breakout_z, 2) if cell.breakout_z is not None else None
                ),
                "sample_size": cell.sample_size,
            })

    iso = moment.isocalendar()
    existing = session.get(RecommendationSnapshot, (iso.year, iso.week))
    ersetzt = existing is not None
    if existing is not None:
        # Force-Re-Run derselben Woche: Last-Write-Wins, wie bei den
        # Briefs. Loeschen+Neuanlegen statt Feld-Update haelt die Row
        # atomar konsistent (cells UND created_at aus demselben Lauf).
        session.delete(existing)
        session.commit()

    session.add(
        RecommendationSnapshot(
            iso_year=iso.year,
            iso_week=iso.week,
            window_days=window_days,
            cells=zellen,
        )
    )
    session.commit()

    ergebnis = {
        "week": f"{iso.year}-W{iso.week:02d}",
        "zellen": len(zellen),
        "empfohlen": empfohlen,
        "ersetzt": ersetzt,
    }
    logger.info("recommendation_snapshot.persisted %s", ergebnis)
    return ergebnis


# ---------- Wochen-Historie lesen ---------------------------------------


def _zellen_index(row: RecommendationSnapshot) -> tuple[set, set, bool]:
    """(over-Menge, under-Menge, kennt_verdict) einer Snapshot-Row.

    ``kennt_verdict`` unterscheidet Alt- von Neuformat: Rows vor dem
    26.08.2026 tragen kein ``breakout_verdict``-Feld und enthalten per
    Konstruktion nur over-Zellen. Fuer over-Fragen sind sie voll
    auswertbar; fuer under-Fragen sagen sie NICHTS (die under-Zellen
    jener Woche wurden nie aufgezeichnet) — das darf nicht als "war
    damals nicht under" gelesen werden.
    """
    over: set = set()
    under: set = set()
    kennt_verdict = False
    for z in row.cells:
        dimension = z.get("dimension")
        value = z.get("value")
        if not dimension or value is None:
            continue
        verdict = z.get("breakout_verdict")
        if verdict is not None:
            kennt_verdict = True
        if verdict in (None, "over"):
            over.add((dimension, value))
        elif verdict == "under":
            under.add((dimension, value))
    return over, under, kennt_verdict


def _snapshot_index(session: Session) -> dict[tuple[int, int], tuple[set, set, bool]]:
    rows = session.exec(select(RecommendationSnapshot)).all()
    return {(r.iso_year, r.iso_week): _zellen_index(r) for r in rows}


def _wochen_zurueck(iso_year: int, iso_week: int, k: int) -> tuple[int, int]:
    """ISO-Woche k Wochen vor (iso_year, iso_week) — ueber die
    Kalender-Arithmetik statt ``week - k``, wegen Jahreswechseln und
    Jahren mit 53 Wochen."""
    d = date.fromisocalendar(iso_year, iso_week, 1) - timedelta(weeks=k)
    iso = d.isocalendar()
    return iso.year, iso.week


def annotiere_bestaendigkeit(
    session: Session,
    data: dict,
    *,
    now: Optional[datetime] = None,
    max_wochen: int = MAX_HISTORIE_WOCHEN,
) -> dict:
    """Ergaenzt jede belastbare Zelle des Berichts-Payloads um
    ``wochen_in_folge`` — die wievielte Woche in Folge sie ihr Verdikt
    traegt (1 = neu diese Woche, Vorwochen-Snapshot vorhanden und ohne
    die Zelle). ``None``, wenn die Vorwoche nicht auswertbar ist: kein
    Snapshot, oder eine under-Frage an eine Altformat-Row.

    Gezaehlt wird nur, was die Snapshots BELEGEN — eine fehlende
    Snapshot-Woche beendet die Zaehlung, sie wird nicht uebersprungen.
    "3. Woche in Folge" heisst also: diese Woche plus zwei nachgewiesene
    Vorwochen mit demselben Verdikt.

    Mutiert ``data`` in place und gibt es zurueck (gleiche Konvention
    wie ``apply_weekly_trend``).
    """
    moment = now or datetime.now(timezone.utc)
    index = _snapshot_index(session)
    iso = moment.isocalendar()

    for dimension, cells in data.get("dimensions", {}).items():
        for cell in cells:
            verdict = cell.get("breakout_verdict")
            if verdict not in ("over", "under"):
                continue
            key = (dimension, cell.get("value"))
            vorwoche = index.get(_wochen_zurueck(iso.year, iso.week, 1))
            auswertbar = vorwoche is not None and (
                verdict == "over" or vorwoche[2]
            )
            if not auswertbar:
                cell["wochen_in_folge"] = None
                continue
            streak = 1
            for k in range(1, max_wochen + 1):
                eintrag = index.get(_wochen_zurueck(iso.year, iso.week, k))
                if eintrag is None:
                    break
                over_menge, under_menge, kennt_verdict = eintrag
                if verdict == "under" and not kennt_verdict:
                    break
                menge = over_menge if verdict == "over" else under_menge
                if key not in menge:
                    break
                streak += 1
            cell["wochen_in_folge"] = streak
    return data


def compute_bewaehrung(
    session: Session,
    *,
    max_wochen: int = MAX_HISTORIE_WOCHEN,
) -> dict:
    """Trefferquote der eigenen Empfehlungen: wie viele over-Zellen
    einer Snapshot-Woche standen im Snapshot der Folgewoche noch?

    Reiner Vergleich persistierter Rows — kein Neu-Rechnen, kein LLM,
    jederzeit wiederholbar. Gewertet werden nur Wochen-PAARE: eine
    Woche ohne Folgewochen-Snapshot (typisch die aktuelle) faellt raus
    statt die Quote zu verzerren. Bewertet werden nur Empfehlungen
    (over) — under-Zellen sind keine Empfehlung, ihre Wiederkehr zeigt
    ``annotiere_bestaendigkeit`` an der Karte.
    """
    index = _snapshot_index(session)
    wochen: list[dict] = []
    gesamt_empfohlen = 0
    gesamt_bestaetigt = 0
    for (jahr, woche) in sorted(index, reverse=True):
        if len(wochen) >= max_wochen:
            break
        folge_key = _wochen_zurueck(jahr, woche, -1)
        folge = index.get(folge_key)
        if folge is None:
            continue
        empfohlen, _, _ = index[(jahr, woche)]
        if not empfohlen:
            continue
        bestaetigt = empfohlen & folge[0]
        gesamt_empfohlen += len(empfohlen)
        gesamt_bestaetigt += len(bestaetigt)
        wochen.append({
            "week": f"{jahr}-W{woche:02d}",
            "folgewoche": f"{folge_key[0]}-W{folge_key[1]:02d}",
            "empfohlen": len(empfohlen),
            "bestaetigt": len(bestaetigt),
            "quote": round(len(bestaetigt) / len(empfohlen), 4),
        })

    ergebnis: dict[str, Any] = {
        "wochen": wochen,
        "gesamt": {
            "wochen_paare": len(wochen),
            "empfohlen": gesamt_empfohlen,
            "bestaetigt": gesamt_bestaetigt,
            "quote": (
                round(gesamt_bestaetigt / gesamt_empfohlen, 4)
                if gesamt_empfohlen
                else None
            ),
        },
    }
    if not wochen:
        ergebnis["note"] = (
            "Noch keine zwei aufeinanderfolgenden Snapshot-Wochen — die "
            "erste Messung entsteht, sobald der Montags-Cron zwei Wochen "
            "in Folge gelaufen ist."
        )
    return ergebnis
