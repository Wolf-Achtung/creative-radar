"""Projekt-Start-Brief (22.08.2026) — das Radar VOR die Arbeit schalten.

Bisher sagt das System montags, was letzte Woche funktioniert hat.
Dieser Service dreht die Blickrichtung: ein neues Wir-Projekt (Titel,
Genre) kommt herein, und der Brief sagt VORAB, welche Muster in dessen
Umfeld gerade ueberperformen — mit den staerksten Referenz-Posts als
Moodboard fuer Schnitt und Design, und dem Standort des eigenen Genres
im aktuellen Bericht.

Bewusst deterministisch und LLM-frei: dieselbe Statistik wie der
Muster-Bericht (``compute_trailer_patterns``), dieselbe MACHEN-Auswahl
(``breakout_verdict == "over"``), dieselbe Zell-Zugehoerigkeit
(``posts_for_cell``) — der Brief ist eine gezielte SICHT auf den
Bericht, keine Zweitrechnung. Ein Klick kostet nichts und ist sofort da.

Bild-Vorrang je Beispiel-Post: gespeicherte Evidence vor CDN-Thumbnail
(Wolf-Befund 21.08.2026 — alte CDN-Links sind tot, ein totes Bild
verdraengt sonst ein ladbares gespeichertes).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.entities import Asset, Channel, Title
from app.services.trailer_patterns import (
    DEFAULT_WINDOW_DAYS,
    build_lift_context,
    compute_trailer_patterns,
    posts_for_cell,
)

logger = logging.getLogger(__name__)

EXAMPLES_PER_CELL = 3


def _beispiele_fuer_zelle(
    session: Session, ctx, dimension: str, value: str, *, limit: int
) -> list[dict]:
    members = posts_for_cell(session, ctx, dimension, value)
    members.sort(key=lambda p: ctx.lift_by_post.get(p.id, 0.0), reverse=True)
    top = members[:limit]
    if not top:
        return []

    handle_by_channel: dict = {}
    for ch in session.exec(
        select(Channel).where(Channel.id.in_({p.channel_id for p in top}))
    ).all():
        handle_by_channel[ch.id] = ch.handle or ch.name

    gespeichert: dict = {}
    cdn: dict = {}
    for a in session.exec(
        select(Asset)
        .where(Asset.post_id.in_([p.id for p in top]))
        .order_by(Asset.created_at.asc())
    ).all():
        if a.visual_evidence_url:
            gespeichert.setdefault(a.post_id, str(a.id))
        elif a.thumbnail_url:
            cdn.setdefault(a.post_id, str(a.id))
    asset_by_post = {**cdn, **gespeichert}

    return [
        {
            "post_url": p.post_url,
            "handle": handle_by_channel.get(p.channel_id),
            "lift": round(ctx.lift_by_post.get(p.id, 0.0), 2),
            "caption": (p.caption or "")[:160] or None,
            "asset_id": asset_by_post.get(p.id),
        }
        for p in top
    ]


def _zell_dict(cell) -> dict:
    return {
        "value": cell.value,
        "verdict": cell.breakout_verdict,
        "median_lift": round(cell.median_lift, 3),
        "breakout_z": (
            round(cell.breakout_z, 2) if cell.breakout_z is not None else None
        ),
        "sample_size": cell.sample_size,
    }


def compute_projekt_start_brief(
    session: Session,
    title_id: UUID,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    examples_per_cell: int = EXAMPLES_PER_CELL,
    now: Optional[datetime] = None,
) -> dict:
    title = session.get(Title, title_id)
    if title is None:
        raise ValueError("Titel nicht gefunden")

    ctx = build_lift_context(session, window_days=window_days, now=now)
    report = compute_trailer_patterns(session, window_days=window_days, now=now)

    # Standort des eigenen Genres: wo steht die Zelle, in der dieses
    # Projekt spielt, im aktuellen Bericht? Kein Genre am Titel ->
    # ehrlicher Hinweis statt geratener Zelle.
    genre = (title.genres or [None])[0]
    genre_standort = None
    if genre:
        zelle = next(
            (c for c in report.dimensions.get("genre", []) if c.value == genre),
            None,
        )
        if zelle is not None:
            genre_standort = _zell_dict(zelle)

    empfehlungen: list[dict] = []
    for dimension, cells in report.dimensions.items():
        for cell in cells:
            if cell.breakout_verdict != "over":
                continue
            empfehlungen.append({
                "dimension": dimension,
                **_zell_dict(cell),
                "beispiele": _beispiele_fuer_zelle(
                    session, ctx, dimension, cell.value, limit=examples_per_cell
                ),
            })
    empfehlungen.sort(
        key=lambda e: e["breakout_z"] if e["breakout_z"] is not None else float("-inf"),
        reverse=True,
    )

    ergebnis = {
        "title": {
            "id": str(title.id),
            "title_original": title.title_original,
            "genre": genre,
            "genres": list(title.genres or []),
        },
        "window_days": window_days,
        "posts_im_fenster": len(ctx.usable),
        "genre_standort": genre_standort,
        "empfehlungen": empfehlungen,
    }
    logger.info(
        "projekt_start_brief.computed title=%s empfehlungen=%s genre=%s",
        title.id, len(empfehlungen), genre,
    )
    return ergebnis
