"""Referenz-Suche — die Facetten-Suche ueber die analysierten Posts.

Roadmap Schritt 1 (25.08.2026): der Montags-Bericht sagt, WELCHE Muster
funktionieren; diese Suche zeigt auf Zuruf die Posts DAHINTER. Ein
Cutter fragt "Horror, Titel im Bild, ueber 1,5x Kanal-Schnitt, letzte
90 Tage" und bekommt ein Referenz-Grid mit Bildern, Zahlen und Links —
die Moodboard-Werkbank statt des Wochen-Reports.

Bewusst KEINE Zweitrechnung: Lift und Zugehoerigkeit kommen aus
``trailer_patterns`` (``build_lift_context`` + ``facetten_werte_je_
post``) — dieselben Regeln wie Bericht und Beispiel-Endpoint, inklusive
Konfidenz-Filter auf modell-erzeugten Dimensionen. Was hier "2,3x"
heisst, heisst im Muster-Panel exakt dasselbe.

Rein lesend, kein LLM-Call. Gate: ``FEATURE_REFERENZ_SUCHE_ENABLED``
(Endpoint in ``api/insights.py``).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from app.models.entities import Asset, Channel
from app.services.trailer_patterns import (
    DEFAULT_WINDOW_DAYS,
    LiftContext,
    _title_by_post,
    build_lift_context,
    facetten_werte_je_post,
)

# Obergrenze je Facette in der Zaehlung: Genre kann ~20 Werte tragen,
# alles andere weniger — 25 schneidet nichts Reales ab, verhindert aber,
# dass eine kuenftige freie Dimension die Antwort aufblaeht.
MAX_WERTE_JE_FACETTE = 25

# Caption-Auszug in der Trefferkarte — gleiche Laenge wie im
# Beispiel-Endpoint, die Karte zeigt einen Anriss, kein Archiv.
CAPTION_AUSZUG_ZEICHEN = 240


def _asset_je_post(session: Session, post_ids: list[Any]) -> dict[Any, str]:
    """Post → Asset-ID fuer den Thumbnail-Proxy.

    Gespeicherte Bilder (``visual_evidence_url`` → R2/Storage) schlagen
    CDN-Thumbnails — Referenz-Posts sind oft Wochen alt, und alte
    Instagram-CDN-Links sind tot (Wolf-Befund 21.08.2026). Identische
    Wahl wie im Beispiel-Endpoint; Posts ohne Bildquelle fehlen im
    Mapping, die Karte zeigt dann nur Text.
    """
    if not post_ids:
        return {}
    gespeichert: dict[Any, str] = {}
    cdn: dict[Any, str] = {}
    for a in session.exec(
        select(Asset)
        .where(Asset.post_id.in_(post_ids))
        .order_by(Asset.created_at.asc())
    ).all():
        if a.visual_evidence_url:
            gespeichert.setdefault(a.post_id, str(a.id))
        elif a.thumbnail_url:
            cdn.setdefault(a.post_id, str(a.id))
    return {**cdn, **gespeichert}


def suche_referenzen(
    session: Session,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    market: Optional[str] = None,
    platform: Optional[str] = None,
    facetten: Optional[dict[str, str]] = None,
    min_lift: Optional[float] = None,
    limit: int = 24,
    now: Optional[datetime] = None,
) -> dict:
    """Facetten-Suche ueber die Posts des Fensters.

    ``facetten`` ist Dimension→Wert (z. B. ``{"genre": "Horror",
    "cover_titel": "mit_titel"}``); mehrere Facetten schneiden sich.
    Eine unbekannte Dimension wirft ``ValueError`` (der Endpoint macht
    422 daraus); ein unbekannter WERT ist kein Fehler, sondern eine
    leere Treffermenge — der Wertevorrat ist offen (Genres, Kinetik).

    Die ``facetten_zaehlung`` zaehlt ueber die GEFILTERTE Menge: sie
    beantwortet "was koennte ich von hier aus noch einengen", nicht
    "was gaebe es insgesamt".
    """
    facetten = facetten or {}
    ctx: LiftContext = build_lift_context(
        session, window_days=window_days, market=market, now=now
    )
    karten = facetten_werte_je_post(session, ctx.usable)
    unbekannt = sorted(set(facetten) - set(karten))
    if unbekannt:
        raise ValueError(
            f"Unbekannte Facette(n): {', '.join(unbekannt)}. "
            f"Bekannt: {', '.join(sorted(karten))}"
        )

    treffer = list(ctx.usable)
    if platform:
        treffer = [
            p for p in treffer
            if ctx.platform_by_channel.get(p.channel_id) == platform
        ]
    for dimension, wert in facetten.items():
        karte = karten[dimension]
        treffer = [p for p in treffer if karte.get(p.id) == wert]
    if min_lift is not None:
        treffer = [p for p in treffer if ctx.lift_by_post[p.id] >= min_lift]

    treffer.sort(key=lambda p: ctx.lift_by_post[p.id], reverse=True)
    gesamt = len(treffer)
    top = treffer[:limit]

    zaehlung: dict[str, list[dict]] = {}
    treffer_ids = {p.id for p in treffer}
    for dimension, karte in karten.items():
        counter = Counter(
            wert for post_id, wert in karte.items() if post_id in treffer_ids
        )
        zaehlung[dimension] = [
            {"wert": wert, "anzahl": anzahl}
            for wert, anzahl in sorted(
                counter.items(), key=lambda kv: (-kv[1], kv[0])
            )[:MAX_WERTE_JE_FACETTE]
        ]

    channel_by_id: dict[Any, Channel] = {}
    if top:
        channel_by_id = {
            ch.id: ch
            for ch in session.exec(
                select(Channel).where(Channel.id.in_({p.channel_id for p in top}))
            ).all()
        }
    asset_by_post = _asset_je_post(session, [p.id for p in top])
    title_by_post = _title_by_post(session, top)

    karten_ausgabe = []
    for p in top:
        channel = channel_by_id.get(p.channel_id)
        titel = title_by_post.get(p.id)
        genres = titel.genres if titel is not None else None
        karten_ausgabe.append(
            {
                "post_url": p.post_url,
                "asset_id": asset_by_post.get(p.id),
                "platform": ctx.platform_by_channel.get(p.channel_id, "unknown"),
                "channel_handle": (
                    (channel.handle or channel.name) if channel else "?"
                ),
                "market": (
                    str(getattr(channel.market, "value", channel.market))
                    if channel and channel.market is not None
                    else None
                ),
                "lift": round(ctx.lift_by_post[p.id], 2),
                "views": int(p.visible_views) if p.visible_views else None,
                "caption": (p.caption or "")[:CAPTION_AUSZUG_ZEICHEN],
                "detected_at": (
                    p.detected_at.date().isoformat() if p.detected_at else None
                ),
                "titel": (titel.title_original or None) if titel else None,
                "genre": (
                    genres[0].strip()
                    if isinstance(genres, list)
                    and genres
                    and isinstance(genres[0], str)
                    and genres[0].strip()
                    else None
                ),
            }
        )

    return {
        "window_days": window_days,
        "market": market,
        "platform": platform,
        "facetten": dict(facetten),
        "min_lift": min_lift,
        "posts_im_fenster": ctx.posts_in_window,
        "nutzbar": len(ctx.usable),
        "gesamt": gesamt,
        "treffer": karten_ausgabe,
        "facetten_zaehlung": zaehlung,
    }
