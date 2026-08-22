"""Empfehlungs-Snapshots (22.08.2026) — was hat das System wann empfohlen?

Das Wir-Segment vergleicht heute "empfohlen" und "gemacht" im SELBEN
Fenster — der naechste Schritt (echtes Vorher/Nachher: hat die
Empfehlung von KW X das Verhalten in KW X+1 veraendert?) braucht
eingefrorene Empfehlungs-Zeitpunkte. Bislang rechnete das System die
Empfehlungen bei jedem Abruf frisch und vergass sie wieder; jede Woche
ohne Snapshot ist eine verlorene Messwoche.

Dieser Service schreibt einmal pro Cron-Lauf die ``over``-Zellen des
Muster-Berichts weg — EXAKT dieselbe MACHEN-Auswahl wie
``pattern_playbook`` und ``wir_segment`` (``breakout_verdict ==
"over"``), keine Zweitdefinition von "empfohlen". Eine Row pro
ISO-Woche, Last-Write-Wins beim Force-Re-Run derselben Woche.

Bewusst deterministisch und LLM-frei: nur DB-Lesen plus eine
JSON-Zeile schreiben — die Stage kann den Lauf weder verteuern noch
nennenswert verlaengern.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session

from app.models.entities import RecommendationSnapshot
from app.services.trailer_patterns import DEFAULT_WINDOW_DAYS, compute_trailer_patterns

logger = logging.getLogger(__name__)


def persist_recommendation_snapshot(
    session: Session,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: Optional[datetime] = None,
) -> dict:
    moment = now or datetime.now(timezone.utc)
    report = compute_trailer_patterns(session, window_days=window_days, now=now)

    zellen: list[dict] = []
    for dimension, cells in report.dimensions.items():
        for cell in cells:
            if cell.breakout_verdict != "over":
                continue
            zellen.append({
                "dimension": dimension,
                "value": cell.value,
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
        "ersetzt": ersetzt,
    }
    logger.info("recommendation_snapshot.persisted %s", ergebnis)
    return ergebnis
