"""Wochen-Plan — aus dem Wochen-Bericht wird ein Plan (Roadmap-Ausbau 2).

Der Montags-Bericht sagt, was letzte Woche war. Der Wochen-Plan sagt,
was DIESE Woche zu tun ist — je Wir-Projekt:

- **Wo die Kampagne steht**: Phase und Einordnung gegen vergleichbare
  Kampagnen (aus dem Release-Countdown).
- **Was diese Woche passt**: die Machart, die in genau dieser Phase
  gerade funktioniert — und was eher nicht (Phasen-Muster).
- **Was liegen blieb**: Empfehlungen der juengsten abgeschlossenen
  Messwoche, die niemand umgesetzt hat (Beweis-Loop).

Reine KOMPOSITION: Countdown, Phasen-Muster und Beweis-Loop rechnen,
dieses Modul setzt zusammen und formuliert. Jede Zahl hier steht
genauso in den Einzel-Sektionen. Deterministisch, LLM-frei, rein
lesend. Gate: ``FEATURE_WOCHEN_PLAN_ENABLED``.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlmodel import Session

from app.services.beweis_loop import compute_beweis_loop
from app.services.pattern_playbook import WERT_LABEL
from app.services.projekt_export import _countdown_satz
from app.services.release_countdown import compute_release_countdown

logger = logging.getLogger(__name__)

# Der Plan ist eine Auswahl: hoechstens so viele Passt-/Lieber-nicht-
# Zeilen je Projekt, hoechstens so viele Liegengeblieben-Zeilen.
MAX_PASST = 3
MAX_LIEBER_NICHT = 2
MAX_LIEGENGEBLIEBEN = 5


def _label(wert: str) -> str:
    return WERT_LABEL.get(wert, wert)


def compute_wochen_plan(
    session: Session, *, now: Optional[datetime] = None
) -> dict:
    countdown = compute_release_countdown(session, now=now)
    if countdown["note"]:
        return {
            "projekte": [],
            "liegengeblieben": [],
            "note": countdown["note"],
        }

    beweis = compute_beweis_loop(session, now=now)
    liegengeblieben: list[dict] = []
    # Die juengste ABGESCHLOSSENE Messwoche zaehlt: eine laufende
    # Folgewoche ist noch kein Versaeumnis.
    for woche in beweis.get("wochen", []):
        if not woche["folgewoche_abgeschlossen"]:
            continue
        for zelle in woche["zellen"]:
            if zelle["umgesetzt"] == 0:
                liegengeblieben.append({
                    "dimension": zelle["dimension"],
                    "wert": zelle["value"],
                    "satz": (
                        f"{_label(zelle['value'])} war in {woche['week']} "
                        "empfohlen — niemand hat es umgesetzt."
                    ),
                })
        break  # nur die juengste abgeschlossene Woche
    liegengeblieben = liegengeblieben[:MAX_LIEGENGEBLIEBEN]

    projekte: list[dict] = []
    for zeile in countdown["projekte"]:
        if zeile["phase"] is None:
            projekte.append({
                "titel": zeile["titel"],
                "title_id": zeile["title_id"],
                "phase": None,
                "wochen_bis_release": None,
                "timing": zeile["hinweis"],
                "passt": [],
                "lieber_nicht": [],
            })
            continue
        muster = countdown["phasen_muster"].get(zeile["phase"], [])
        passt = [
            f"{_label(z['value'])} — funktioniert in dieser Phase gerade."
            for z in muster if z["breakout_verdict"] == "over"
        ][:MAX_PASST]
        lieber_nicht = [
            f"{_label(z['value'])} — funktioniert in dieser Phase gerade nicht."
            for z in muster if z["breakout_verdict"] == "under"
        ][:MAX_LIEBER_NICHT]
        projekte.append({
            "titel": zeile["titel"],
            "title_id": zeile["title_id"],
            "phase": zeile["phase"],
            "wochen_bis_release": zeile["wochen_bis_release"],
            "timing": _countdown_satz(zeile, countdown["markt_kampagnenstart"]),
            "passt": passt,
            "lieber_nicht": lieber_nicht,
        })

    ergebnis = {
        "projekte": projekte,
        "liegengeblieben": liegengeblieben,
        "note": None,
    }
    logger.info(
        "wochen_plan.computed projekte=%s liegengeblieben=%s",
        len(projekte), len(liegengeblieben),
    )
    return ergebnis
