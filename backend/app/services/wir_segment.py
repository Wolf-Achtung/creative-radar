"""Wir-Segment Schritt 1 (21.08.2026) — empfohlen → gemacht → gewirkt.

Die Muster-Auswertung sagt bislang nur, was im Gesamt-Kanalbestand
funktioniert. Dieser Service schliesst den Kreis fuer das EIGENE:

- Kanaele mit ``channel.is_own`` (Checkliste in Admin → Quellen), UND
- seit 22.08.2026: Posts, deren Asset auf einen Titel mit
  ``title.is_own_project`` gemappt ist — Trailerhaus arbeitet
  projektweise, nicht kanalweise: auf einem Verleih-Kanal ist ein Post
  von ihnen und zwanzig nicht, das Kanal-Haekchen waere dort falsch.
  Die Titel-Zuordnung aus der Pruef-Queue wird hier ein zweites Mal
  geerntet. Grenze, ehrlich benannt: das misst "unsere Filme", nicht
  "unser Asset" — fremdes Material zum selben Titel zaehlt mit.

- **empfohlen**: die ``breakout_verdict == "over"``-Zellen des
  aktuellen Muster-Berichts — exakt die MACHEN-Empfehlungen des
  Playbooks (gleiche Auswahlregel wie ``pattern_playbook``,
  keine Zweitdefinition von "empfohlen").
- **gemacht**: wie viele Posts der eigenen Kanaele im selben Fenster
  in dieser Zelle liegen — Zugehoerigkeit ueber ``posts_for_cell``,
  also mit denselben Regeln (Konfidenz-Gate, Genre-Mapping) wie die
  Zellen-Zaehlung selbst.
- **gewirkt**: der Median-Lift genau dieser eigenen Posts
  (kanal-normiert aus dem ``LiftContext``) neben dem Median-Lift der
  Zelle im Gesamtbestand — liegt "wir" darunter, ist das Muster bei
  uns noch nicht angekommen oder anders umgesetzt.

Bewusst dasselbe Fenster wie der Bericht (kein "seit Empfehlung"):
die Empfehlungen sind Fenster-Aggregate, ein Posts-danach-Vergleich
braeuchte Empfehlungs-Zeitpunkte je Zelle und ein eigenes
Vorher/Nachher-Design — Schritt 2, wenn die Markierung erst einmal
Daten liefert.

Ohne markierte Kanaele liefert die Auswertung einen Klartext-Hinweis
statt leerer Zahlen — derselbe Ehrlichkeits-Grundsatz wie ueberall.
"""
from __future__ import annotations

import logging
from datetime import datetime
from statistics import median
from typing import Optional

from sqlmodel import Session, select

from app.core.feature_flags import is_wir_projekte_enabled
from app.models.entities import Channel, Title
from app.services.trailer_patterns import (
    DEFAULT_WINDOW_DAYS,
    _title_by_post,
    build_lift_context,
    compute_trailer_patterns,
    posts_for_cell,
)

logger = logging.getLogger(__name__)


def compute_wir_segment(
    session: Session,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: Optional[datetime] = None,
) -> dict:
    eigene_kanaele = session.exec(
        select(Channel).where(Channel.is_own == True)  # noqa: E712
    ).all()
    projekt_titel = session.exec(
        select(Title).where(Title.is_own_project == True)  # noqa: E712
    ).all()
    if not eigene_kanaele and not projekt_titel:
        # Hinweistext passend zur Umgebung: der Wir-Projekte-Block ist
        # feature-geflaggt — wo er nicht sichtbar ist, darf der Text
        # nicht auf ihn zeigen (Arbeitsregel 23.08.2026).
        if is_wir_projekte_enabled():
            hinweis = (
                "Noch nichts als „Wir“ markiert — in Quellen → "
                "Wir-Projekte die eigenen Filmprojekte ankreuzen "
                "(oder Wir-Kanäle, falls ihr einen Kanal komplett betreut)."
            )
        else:
            hinweis = (
                "Noch kein Kanal als „Wir“ markiert — in Quellen → "
                "Wir-Kanäle die eigenen Kanäle ankreuzen."
            )
        return {
            "own_channels": 0,
            "own_project_titles": 0,
            "eigene_posts_im_fenster": 0,
            "window_days": window_days,
            "zeilen": [],
            "note": hinweis,
        }
    own_ids = {c.id for c in eigene_kanaele}
    projekt_titel_ids = {t.id for t in projekt_titel}

    ctx = build_lift_context(session, window_days=window_days, now=now)
    report = compute_trailer_patterns(session, window_days=window_days, now=now)

    # Projektweise Zugehoerigkeit: Post → Titel ueber dieselbe Zuordnung
    # (aeltestes Asset mit title_id) wie Genre-Dimension und Titel-Modus.
    titel_by_post = (
        _title_by_post(session, list(ctx.usable)) if projekt_titel_ids else {}
    )

    def _ist_wir(post) -> bool:
        if post.channel_id in own_ids:
            return True
        titel = titel_by_post.get(post.id)
        return titel is not None and titel.id in projekt_titel_ids

    eigene_gesamt = [p for p in ctx.usable if _ist_wir(p)]

    zeilen: list[dict] = []
    for dimension, cells in report.dimensions.items():
        for cell in cells:
            if cell.breakout_verdict != "over":
                continue
            members = posts_for_cell(session, ctx, dimension, cell.value)
            eigene = [p for p in members if _ist_wir(p)]
            lifts = [
                ctx.lift_by_post[p.id] for p in eigene if p.id in ctx.lift_by_post
            ]
            zeilen.append({
                "dimension": dimension,
                "value": cell.value,
                "empfohlen_median_lift": round(cell.median_lift, 3),
                "empfohlen_breakout_z": (
                    round(cell.breakout_z, 2) if cell.breakout_z is not None else None
                ),
                "gemacht": len(eigene),
                "gewirkt_median_lift": round(median(lifts), 3) if lifts else None,
            })
    # Am meisten Umgesetztes zuerst, dahinter die staerksten
    # Empfehlungen, die "wir" noch gar nicht spielen.
    zeilen.sort(
        key=lambda z: (-z["gemacht"], -(z["empfohlen_breakout_z"] or 0))
    )

    ergebnis = {
        "own_channels": len(own_ids),
        "own_project_titles": len(projekt_titel_ids),
        "eigene_posts_im_fenster": len(eigene_gesamt),
        "window_days": window_days,
        "zeilen": zeilen,
        "note": None,
    }
    logger.info(
        "wir_segment.computed own_channels=%s own_project_titles=%s "
        "eigene_posts=%s zeilen=%s",
        len(own_ids), len(projekt_titel_ids), len(eigene_gesamt), len(zeilen),
    )
    return ergebnis
