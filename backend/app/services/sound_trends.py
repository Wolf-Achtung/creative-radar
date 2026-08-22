"""TikTok-Sound-Trends (22.08.2026) — welche Sounds tragen die Treffer?

Tellerrand-Idee aus dem Stufen-Fahrplan: der Apify-TikTok-Scrape
liefert ``musicMeta`` seit jeher mit und der Connector spiegelt es
komplett in ``raw_payload`` (``normalize_tiktok_item``) — ausgewertet
hat es nur nie jemand. Auf TikTok ist der Sound ein eigener Ranking-
und Trend-Faktor; fuer Cutter ist "die Top-Posts der Woche laufen auf
diesem Sound" direkt umsetzbares Material.

Deterministisch, LLM-frei, rein lesend: Sounds werden aus dem
``raw_payload`` der TikTok-Posts im Fenster extrahiert (neuer
Spiegel-Schluessel ``_creative_radar_music`` MIT Fallback auf das rohe
``musicMeta`` — der Altbestand wurde vor dem Spiegel gescrapt),
gruppiert nach (Name, Autor), und mit dem kanal-normierten Median-Lift
der zugehoerigen Posts versehen — derselbe Lift wie im Muster-Bericht,
keine Zweitdefinition von "laeuft gut".
"""
from __future__ import annotations

import logging
from statistics import median
from typing import Optional

from sqlmodel import Session, select

from app.models.entities import Channel, Post
from app.services.trailer_patterns import DEFAULT_WINDOW_DAYS, build_lift_context

logger = logging.getLogger(__name__)

MIN_POSTS_PER_SOUND = 2
TOP_SOUNDS = 15


def _music_meta(post: Post) -> Optional[dict]:
    raw = post.raw_payload or {}
    meta = raw.get("_creative_radar_music") or raw.get("musicMeta")
    if not isinstance(meta, dict) or not meta:
        return None
    name = (meta.get("musicName") or "").strip()
    if not name:
        return None
    return {
        "name": name,
        "author": (meta.get("musicAuthor") or "").strip() or None,
        "original": bool(meta.get("musicOriginal")),
    }


def compute_sound_trends(
    session: Session,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_posts: int = MIN_POSTS_PER_SOUND,
    top: int = TOP_SOUNDS,
) -> dict:
    # Derselbe Kontext wie der Muster-Bericht: nur Posts mit belastbarer
    # Kanal-Baseline tragen einen Lift — und nur die zaehlen hier, damit
    # "Median-Lift je Sound" dieselbe Waehrung ist wie ueberall sonst.
    ctx = build_lift_context(session, window_days=window_days)
    tiktok_posts = [p for p in ctx.usable if p.platform == "tiktok"]

    sounds: dict = {}
    posts_mit_sound = 0
    for post in tiktok_posts:
        meta = _music_meta(post)
        if meta is None:
            continue
        posts_mit_sound += 1
        key = (meta["name"].lower(), (meta["author"] or "").lower())
        eintrag = sounds.setdefault(key, {
            "name": meta["name"],
            "author": meta["author"],
            "original": meta["original"],
            "posts": 0,
            "channel_ids": set(),
            "lifts": [],
            "beispiel_post_url": None,
            "beispiel_lift": None,
        })
        eintrag["posts"] += 1
        eintrag["channel_ids"].add(post.channel_id)
        lift = ctx.lift_by_post.get(post.id)
        if lift is not None:
            eintrag["lifts"].append(lift)
            if eintrag["beispiel_lift"] is None or lift > eintrag["beispiel_lift"]:
                eintrag["beispiel_lift"] = lift
                eintrag["beispiel_post_url"] = post.post_url

    rows = [e for e in sounds.values() if e["posts"] >= min_posts]
    # Staerkstes Signal zuerst: erst Wirkung (Median-Lift), dann Menge.
    rows.sort(
        key=lambda e: (
            median(e["lifts"]) if e["lifts"] else 0.0,
            e["posts"],
        ),
        reverse=True,
    )

    handle_by_channel: dict = {}
    channel_ids = {cid for e in rows[:top] for cid in e["channel_ids"]}
    if channel_ids:
        for ch in session.exec(
            select(Channel).where(Channel.id.in_(channel_ids))
        ).all():
            handle_by_channel[ch.id] = ch.handle or ch.name

    ergebnis = {
        "window_days": window_days,
        "tiktok_posts_im_fenster": len(tiktok_posts),
        "posts_mit_sound": posts_mit_sound,
        "sounds": [
            {
                "name": e["name"],
                "author": e["author"],
                "original": e["original"],
                "posts": e["posts"],
                "kanaele": sorted(
                    handle_by_channel.get(cid, "?") for cid in e["channel_ids"]
                ),
                "median_lift": (
                    round(median(e["lifts"]), 2) if e["lifts"] else None
                ),
                "beispiel_post_url": e["beispiel_post_url"],
            }
            for e in rows[:top]
        ],
        "note": (
            None
            if posts_mit_sound
            else (
                "Keine Sound-Metadaten im Fenster — TikTok-Posts kommen "
                "mit dem naechsten Scrape-Lauf, aeltere Bestaende tragen "
                "teils kein musicMeta."
            )
        ),
    }
    logger.info(
        "sound_trends.computed tiktok_posts=%s mit_sound=%s sounds=%s",
        len(tiktok_posts), posts_mit_sound, len(ergebnis["sounds"]),
    )
    return ergebnis
