"""Post-Check — einen Entwurf pruefen, BEVOR er rausgeht.

Die Blickrichtungs-Umkehr (Roadmap-Ausbau, 25.08.2026): der
Muster-Bericht sagt montags, was letzte Woche gefehlt hat. Der
Post-Check sagt es VOR dem Posten: Caption-Entwurf, Laenge und
Cover-Angaben hereingeben, und jede Angabe wird gegen die aktuellen
Befunde gehalten — "Kurze Caption: funktioniert gerade" oder "Ohne
Call-to-Action: funktioniert gerade nicht, mit CTA schneiden Posts
besser ab".

Bewusst KEINE Zweitrechnung und kein LLM-Call:

- Die Werte des Entwurfs kommen aus EXAKT den Extraktoren der
  ``DIMENSIONS``-Registry (``trailer_patterns``) — dieselbe Funktion,
  die auch echte Posts einsortiert. Ein Entwurf mit 85 Zeichen landet
  im selben Laengen-Bucket wie ein echter Post mit 85 Zeichen.
- Die Befunde kommen aus ``compute_trailer_patterns`` — dieselben
  Zellen, Schwellen und z-Tests wie Panel und Playbook.
- Format und Tonfall kann kein Extraktor aus einem Entwurf lesen
  (das macht sonst die Post-Analyse per Modell) — sie kommen als
  optionale Selbstauskunft herein und laufen ueber den normalen
  ``analysis``-Pfad mit Konfidenz 1.0.

Was der Bericht nicht belastbar beurteilen kann (``insufficient``
oder Zelle fehlt), erscheint als "kein Befund" — kein geratenes
Urteil. Korrelation, keine Kausalitaet, wie ueberall.
"""
from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Optional

from sqlmodel import Session

from app.services.pattern_playbook import WERT_LABEL
from app.services.trailer_patterns import (
    DEFAULT_WINDOW_DAYS,
    DIMENSIONS,
    compute_trailer_patterns,
)

logger = logging.getLogger(__name__)

# Selbstauskunfts-Vokabulare — dieselben Werte wie die Post-Analyse
# (prompts/analyze_format_tone.py). Der Endpoint validiert dagegen.
FORMAT_WERTE = (
    "teaser", "trailer", "clip", "behind_the_scenes", "interview",
    "short", "compilation", "promo", "other",
)
TON_WERTE = (
    "energetic", "emotional", "humorous", "suspenseful", "informative",
    "inspirational", "edgy", "neutral",
)


def _wert_label(wert: str) -> str:
    return WERT_LABEL.get(wert, wert)


def _zeile(dimension: str, wert: str, cell) -> dict:
    """Eine Check-Zeile aus Zelle + Entwurfswert — mit dem staerksten
    besser laufenden Geschwister-Wert als konkretem Hinweis."""
    label = _wert_label(wert)
    if cell is None or cell.breakout_verdict == "insufficient":
        return {
            "dimension": dimension,
            "wert": wert,
            "befund": "kein_befund",
            "satz": f"{label}: dazu gibt es gerade keinen belastbaren Befund.",
            "tipp": None,
        }
    if cell.breakout_verdict == "over":
        return {
            "dimension": dimension,
            "wert": wert,
            "befund": "gut",
            "satz": f"{label}: funktioniert gerade.",
            "tipp": None,
        }
    if cell.breakout_verdict == "under":
        return {
            "dimension": dimension,
            "wert": wert,
            "befund": "achtung",
            "satz": f"{label}: funktioniert gerade nicht.",
            "tipp": None,  # der Endpunkt haengt unten den Geschwister-Tipp an
        }
    return {
        "dimension": dimension,
        "wert": wert,
        "befund": "neutral",
        "satz": f"{label}: unauffällig — weder stark noch schwach.",
        "tipp": None,
    }


def pruefe_post(
    session: Session,
    *,
    caption: str,
    duration_seconds: Optional[int] = None,
    titel_im_bild: Optional[bool] = None,
    format_wert: Optional[str] = None,
    ton_wert: Optional[str] = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: Optional[datetime] = None,
) -> dict:
    report = compute_trailer_patterns(session, window_days=window_days, now=now)
    cell_by_key = {
        (dimension, cell.value): cell
        for dimension, cells in report.dimensions.items()
        for cell in cells
    }

    # Der Entwurf als Stellvertreter-Post: die Extraktoren lesen nur
    # caption / duration_seconds / raw_payload / analysis — genau die
    # Felder, die ein Entwurf hat. Selbstauskunft (Format, Tonfall)
    # laeuft mit Konfidenz 1.0 ueber den normalen analysis-Pfad.
    analysis: dict[str, Any] = {"confidence": 1.0}
    if format_wert:
        analysis["format"] = format_wert
    if ton_wert:
        analysis["tone"] = ton_wert
    entwurf = SimpleNamespace(
        caption=caption,
        duration_seconds=duration_seconds,
        raw_payload={},
        analysis=analysis if len(analysis) > 1 else None,
    )

    checks: list[dict] = []
    for dim in DIMENSIONS:
        if dim.name == "music_kind":
            continue  # ein Entwurf traegt keine Apify-Musikdaten
        wert = dim.extract(entwurf)
        if wert is None:
            continue
        checks.append(_zeile(dim.name, wert, cell_by_key.get((dim.name, wert))))

    if titel_im_bild is not None:
        wert = "mit_titel" if titel_im_bild else "ohne_titel"
        checks.append(_zeile("cover_titel", wert, cell_by_key.get(("cover_titel", wert))))

    # Konkreter Hinweis bei "funktioniert gerade nicht": der staerkste
    # over-Wert derselben Dimension, nach breakout_z.
    for zeile in checks:
        if zeile["befund"] != "achtung":
            continue
        geschwister = [
            cell
            for cell in report.dimensions.get(zeile["dimension"], [])
            if cell.value != zeile["wert"] and cell.breakout_verdict == "over"
        ]
        if geschwister:
            beste = max(geschwister, key=lambda c: c.breakout_z or 0.0)
            zeile["tipp"] = f"Gerade besser: {_wert_label(beste.value)}."

    zusammenfassung = {
        "gut": sum(1 for z in checks if z["befund"] == "gut"),
        "achtung": sum(1 for z in checks if z["befund"] == "achtung"),
        "neutral": sum(1 for z in checks if z["befund"] == "neutral"),
        "kein_befund": sum(1 for z in checks if z["befund"] == "kein_befund"),
    }
    ergebnis = {
        "checks": checks,
        "zusammenfassung": zusammenfassung,
        "window_days": window_days,
        "basis": {
            "posts": report.posts_with_baseline,
            "kanaele": report.channels_covered,
        },
    }
    logger.info(
        "post_check.computed checks=%s achtung=%s gut=%s",
        len(checks), zusammenfassung["achtung"], zusammenfassung["gut"],
    )
    return ergebnis
