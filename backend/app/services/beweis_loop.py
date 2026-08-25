"""Beweis-Loop (Roadmap Schritt 3, 25.08.2026) — hat das Radar Recht gehabt?

Das echte Vorher/Nachher, fuer das die Empfehlungs-Snapshots (#40)
gebaut wurden: was in KW X als MACHEN-Empfehlung eingefroren wurde,
gegen die eigenen Posts der **Folgewoche** KW X+1 — je Empfehlung:
wurde sie umgesetzt (mindestens ein Wir-Post in der Zelle), und hat
sie gewirkt (Median-Lift dieser Posts ueber dem eigenen
Kanal-Schnitt)? Aus "das Radar behauptet" wird "das Radar hat Recht
gehabt" — der Satz fuer den Kunden-Pitch.

Verbindungs-Entscheidungen, ehrlich benannt:

- **"Wir"** ist dieselbe Definition wie im Wir-Segment: Kanaele mit
  ``channel.is_own`` plus Posts, deren Asset auf einen Titel mit
  ``title.is_own_project`` zeigt. Keine Zweitdefinition.
- **"Umgesetzt"** zaehlt NUR die Folgewoche. Ein Post in derselben
  Woche wie die Empfehlung kann nicht auf sie reagiert haben — das
  waere wieder der Gleichzeitig-Vergleich, den das Wir-Segment schon
  liefert.
- **Zell-Zugehoerigkeit und Lift** kommen aus ``trailer_patterns``
  (``facetten_werte_je_post`` + ``build_lift_context`` mit dem Fenster
  der jeweiligen Folgewoche) — dieselben Regeln wie ueberall,
  inklusive Konfidenz-Gates.
- **"Gewirkt"** heisst Median-Lift >= 1 (ueber dem eigenen
  Kanal-Schnitt). ``null``, solange nichts umgesetzt wurde — keine
  Wirkung ohne Versuch. Korrelation, keine Kausalitaet: die Auswertung
  zeigt, ob befolgte Empfehlungen funktioniert haben, nicht, DASS sie
  wegen der Empfehlung entstanden.

Die Auswertung waechst von selbst: jeder Montags-Cron friert eine
weitere Woche ein, jede Markierung liefert mehr Wir-Posts. Rein
lesend, LLM-frei. Gate: ``FEATURE_BEWEIS_LOOP_ENABLED``.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Optional

from sqlmodel import Session, select

from app.models.entities import Channel, RecommendationSnapshot, Title
from app.services.trailer_patterns import (
    _title_by_post,
    build_lift_context,
    facetten_werte_je_post,
)

logger = logging.getLogger(__name__)

# Hoechstens so viele juengste Snapshots je Abruf — jede Woche baut
# einen eigenen Lift-Kontext, das darf nicht unbegrenzt wachsen.
MAX_WOCHEN = 12


def _montag(iso_year: int, iso_week: int) -> date:
    return date.fromisocalendar(iso_year, iso_week, 1)


def compute_beweis_loop(
    session: Session, *, now: Optional[datetime] = None
) -> dict:
    now = now or datetime.now(timezone.utc)
    heute = now.date()

    snapshots = session.exec(
        select(RecommendationSnapshot).order_by(
            RecommendationSnapshot.iso_year.desc(),
            RecommendationSnapshot.iso_week.desc(),
        )
    ).all()[:MAX_WOCHEN]
    if not snapshots:
        return {
            "wochen": [],
            "summe": {"empfehlungen": 0, "umgesetzt": 0, "gewirkt": 0},
            "note": (
                "Noch kein Empfehlungs-Snapshot persistiert — der "
                "Montags-Cron friert jede Woche eine ein, die erste "
                "Messwoche kommt von selbst."
            ),
        }

    eigene_kanal_ids = {
        c.id
        for c in session.exec(
            select(Channel).where(Channel.is_own == True)  # noqa: E712
        ).all()
    }
    projekt_titel_ids = {
        t.id
        for t in session.exec(
            select(Title).where(Title.is_own_project == True)  # noqa: E712
        ).all()
    }
    if not eigene_kanal_ids and not projekt_titel_ids:
        return {
            "wochen": [],
            "summe": {"empfehlungen": 0, "umgesetzt": 0, "gewirkt": 0},
            "note": (
                "Noch nichts als „Wir“ markiert — ohne Wir-Kanaele oder "
                "Wir-Projekte gibt es kein „gemacht“ zu messen."
            ),
        }

    wochen: list[dict] = []
    summe_empfehlungen = 0
    summe_umgesetzt = 0
    summe_gewirkt = 0
    for snapshot in snapshots:
        folgewoche_start = _montag(snapshot.iso_year, snapshot.iso_week) + timedelta(days=7)
        folgewoche_ende = folgewoche_start + timedelta(days=7)
        abgeschlossen = folgewoche_ende <= heute

        # Lift-Kontext mit dem Fenster der Folgewoche: Ende der Woche
        # als "now", damit ihre Posts drinliegen und die Baselines aus
        # den 90 Tagen davor kommen — dieselbe Rechnung, die der
        # Bericht in jener Woche gemacht haette.
        ctx = build_lift_context(
            session,
            window_days=snapshot.window_days,
            now=datetime.combine(
                folgewoche_ende, datetime.min.time(), tzinfo=timezone.utc
            ),
        )
        wir_posts = []
        titel_by_post = (
            _title_by_post(session, list(ctx.usable)) if projekt_titel_ids else {}
        )
        for post in ctx.usable:
            moment = post.detected_at
            if moment is None:
                continue
            if not (folgewoche_start <= moment.date() < folgewoche_ende):
                continue
            ist_wir = post.channel_id in eigene_kanal_ids
            if not ist_wir:
                titel = titel_by_post.get(post.id)
                ist_wir = titel is not None and titel.id in projekt_titel_ids
            if ist_wir:
                wir_posts.append(post)

        karten = facetten_werte_je_post(session, wir_posts)
        zellen: list[dict] = []
        for empfehlung in snapshot.cells:
            dimension = empfehlung.get("dimension")
            wert = empfehlung.get("value")
            if not dimension or wert is None:
                continue
            karte = karten.get(dimension, {})
            getroffen = [p for p in wir_posts if karte.get(p.id) == wert]
            lifts = [ctx.lift_by_post[p.id] for p in getroffen]
            gewirkt: Optional[bool] = None
            if lifts:
                gewirkt = median(lifts) >= 1.0
            zellen.append({
                "dimension": dimension,
                "value": wert,
                "markt_median_lift": empfehlung.get("median_lift"),
                "umgesetzt": len(getroffen),
                "median_lift_wir": round(median(lifts), 2) if lifts else None,
                "gewirkt": gewirkt,
            })
        summe_empfehlungen += len(zellen)
        summe_umgesetzt += sum(1 for z in zellen if z["umgesetzt"] > 0)
        summe_gewirkt += sum(1 for z in zellen if z["gewirkt"] is True)
        wochen.append({
            "week": f"{snapshot.iso_year}-W{snapshot.iso_week:02d}",
            "folgewoche_start": folgewoche_start.isoformat(),
            "folgewoche_abgeschlossen": abgeschlossen,
            "wir_posts_folgewoche": len(wir_posts),
            "zellen": zellen,
        })

    ergebnis = {
        "wochen": wochen,
        "summe": {
            "empfehlungen": summe_empfehlungen,
            "umgesetzt": summe_umgesetzt,
            "gewirkt": summe_gewirkt,
        },
        "note": None,
    }
    logger.info(
        "beweis_loop.computed wochen=%s empfehlungen=%s umgesetzt=%s gewirkt=%s",
        len(wochen), summe_empfehlungen, summe_umgesetzt, summe_gewirkt,
    )
    return ergebnis
