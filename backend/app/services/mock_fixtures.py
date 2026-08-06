"""Fixture-Antworten fuer die kostenpflichtigen Scrape-Quellen (Staging-
Briefing 2026-08-06, Abschnitt 2 Schritt 3).

``MOCK_EXTERNAL_APIS=true`` laesst die drei Scrape-Connectors (Apify
Instagram, Apify TikTok, YouTube Data API) deterministische Kunst-Payloads
liefern statt echter HTTP-Calls: kein Token auf der Dev-Maschine, kein
Rate-Limit, keine Kosten, laeuft offline. Die Payload-Form entspricht exakt
dem, was die ``normalize_*``-Funktionen der Connectors erwarten (Apify-
Actor-Items bzw. videos.list-Items) — der gesamte Ingest-Pfad ab
Normalisierung laeuft also ungemockt und wird mitgetestet.

Die LLM-Dienste (OpenAI, Anthropic) werden bewusst NICHT gemockt: ihre
Call-Sites degradieren ohne API-Key sauber (``_unconfigured_response`` bzw.
Skip mit Log), und fabrizierte LLM-Antworten wuerden an der Zitat-
Validierung der Brief-Generierung scheitern. Briefs fuer die Dev-Umgebung
kommen stattdessen aus ``scripts/seed_dev.py`` als schema-valide Rows.

Determinismus: jeder Post wird aus ``random.Random(hash(handle, index))``
erzeugt — gleiche Handles ergeben immer dieselben Zahlen. Timestamps sind
relativ zu ``utcnow`` (letzte ~21 Tage), damit die Posts in jedem
Aggregations-Fenster landen; die MUSTER sind dadurch stabil, die exakten
Datums-Werte nicht (dokumentiert, bewusst).

Eingebaute Muster (damit die Muster-Aggregation aus Fahrplan-Step 2 etwas
zu finden hat):
- Kurze TikToks (<=20s, "Teaser") holen ~3x so viele Views wie lange Cuts.
- US-Handles performen ~2x ueber DE-Handles (Follower-Basis-Simulation).
- Jeder 3. Instagram-Post ist ein statisches Bild (keine Views, nur Likes).
- Captions zitieren die synthetischen Titel aus ``seed_dev`` (Matching
  und Whitelist haben dadurch echte Treffer).
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Any

# Synthetische Titel — MUSS mit scripts/seed_dev.py uebereinstimmen
# (seed_dev importiert diese Liste, single source of truth).
SYNTHETIC_TITLES: list[tuple[str, str]] = [
    # (title_original, keyword fuer Captions/Hashtags)
    ("Nordlicht", "nordlicht"),
    ("Schattenjaeger 2", "schattenjaeger"),
    ("Herz aus Stahl: Reload", "herzausstahl"),
    ("Midnight Harbor", "midnightharbor"),
    ("Die letzte Schicht", "letzteschicht"),
]

_CAPTION_TEMPLATES = [
    "{title} — ab jetzt nur im Kino. #trailer #{tag}",
    "Der neue Trailer zu {title} ist da! #{tag} #film",
    "{title}: Hinter den Kulissen. #makingof #{tag}",
    "3 Gruende, warum du {title} sehen musst 🎬 #{tag}",
    "{title} | Official Teaser #{tag} #kino",
]


def _rng(*parts: Any) -> random.Random:
    """Deterministischer RNG pro (handle, index, ...) — hashlib statt
    ``hash()``, weil Pythons String-Hash pro Prozess randomisiert ist."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _market_factor(handle: str) -> float:
    """US-/UK-Handles simulieren die groessere Follower-Basis."""
    lowered = handle.lower()
    if lowered.endswith(("de", "deutschland")):
        return 1.0
    if lowered.endswith("uk") or "_uk" in lowered:
        return 1.4
    return 2.0


def _pick_title(rng: random.Random) -> tuple[str, str]:
    return SYNTHETIC_TITLES[rng.randrange(len(SYNTHETIC_TITLES))]


def _published_at(rng: random.Random, index: int) -> datetime:
    """Posts der letzten ~21 Tage, neueste zuerst, mit Streuung."""
    days_back = index * 2 + rng.uniform(0, 1.5)
    return datetime.now(timezone.utc) - timedelta(days=days_back, hours=rng.randrange(6, 22))


def mock_instagram_items(channel_urls: list[str], results_limit: int) -> list[dict[str, Any]]:
    """Items in der Form des Apify Instagram-Scrapers (siehe
    ``normalize_public_item``: url/caption/timestamp/ownerUsername/
    likesCount/commentsCount/videoViewCount/displayUrl/videoDuration)."""
    items: list[dict[str, Any]] = []
    for url in channel_urls:
        handle = url.rstrip("/").rsplit("/", 1)[-1]
        factor = _market_factor(handle)
        for i in range(results_limit):
            rng = _rng("ig", handle, i)
            title, tag = _pick_title(rng)
            is_static = i % 3 == 2  # Muster: jeder 3. Post ist ein Foto
            base_likes = int(rng.randrange(800, 4000) * factor)
            short_code = f"MOCK{hashlib.sha256(f'{handle}{i}'.encode()).hexdigest()[:9]}"
            item: dict[str, Any] = {
                "shortCode": short_code,
                "url": f"https://www.instagram.com/p/{short_code}/",
                "caption": _CAPTION_TEMPLATES[i % len(_CAPTION_TEMPLATES)].format(title=title, tag=tag),
                "timestamp": _published_at(rng, i).isoformat(),
                "ownerUsername": handle,
                "likesCount": base_likes,
                "commentsCount": int(base_likes * rng.uniform(0.02, 0.06)),
                "type": "Image" if is_static else "Video",
                "displayUrl": f"https://mock.local/ig/{handle}/{i}.jpg",
            }
            if not is_static:
                duration = rng.choice([12, 18, 34, 61, 92])
                item["videoDuration"] = duration
                view_factor = 3.0 if duration <= 20 else 1.0  # Muster: Teaser x3
                item["videoViewCount"] = int(base_likes * rng.uniform(18, 30) * view_factor)
            items.append(item)
    return items


def mock_tiktok_items(usernames: list[str], results_limit: int) -> list[dict[str, Any]]:
    """Items in der Form des clockworks~tiktok-scraper (siehe
    ``normalize_tiktok_item``: webVideoUrl/text/createTimeISO/authorMeta/
    diggCount/commentCount/playCount/shareCount/videoMeta.duration)."""
    items: list[dict[str, Any]] = []
    for username in usernames:
        factor = _market_factor(username)
        for i in range(results_limit):
            rng = _rng("tt", username, i)
            title, tag = _pick_title(rng)
            duration = rng.choice([9, 15, 21, 45, 78])
            view_factor = 3.0 if duration <= 20 else 1.0  # Muster: Teaser x3
            views = int(rng.randrange(40_000, 220_000) * factor * view_factor)
            video_id = hashlib.sha256(f"{username}{i}".encode()).hexdigest()[:19]
            items.append({
                "id": video_id,
                "webVideoUrl": f"https://www.tiktok.com/@{username}/video/{video_id}",
                "text": _CAPTION_TEMPLATES[i % len(_CAPTION_TEMPLATES)].format(title=title, tag=tag),
                "createTimeISO": _published_at(rng, i).isoformat(),
                "authorMeta": {"name": username},
                "diggCount": int(views * rng.uniform(0.04, 0.09)),
                "commentCount": int(views * rng.uniform(0.001, 0.004)),
                "playCount": views,
                "shareCount": int(views * rng.uniform(0.002, 0.008)),
                "collectCount": int(views * rng.uniform(0.001, 0.003)),
                "videoMeta": {
                    "duration": duration,
                    "coverUrl": f"https://mock.local/tt/{username}/{i}.jpg",
                },
            })
    return items


def mock_youtube_channel_videos(
    handle_or_id: str, results_limit: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(channel_meta, video_items) in der Form von channels.list +
    videos.list (siehe ``normalize_youtube_video``)."""
    handle = handle_or_id.lstrip("@")
    channel_id = "UCMOCK" + hashlib.sha256(handle.encode()).hexdigest()[:16]
    channel_meta = {
        "id": channel_id,
        "snippet": {"title": handle, "customUrl": f"@{handle}"},
        "statistics": {"subscriberCount": "250000"},
        "contentDetails": {"relatedPlaylists": {"uploads": f"UUMOCK{handle}"}},
    }
    factor = _market_factor(handle)
    videos: list[dict[str, Any]] = []
    for i in range(results_limit):
        rng = _rng("yt", handle, i)
        title, tag = _pick_title(rng)
        video_id = "mock" + hashlib.sha256(f"{handle}{i}".encode()).hexdigest()[:7]
        duration = rng.choice([16, 31, 95, 142])
        view_factor = 3.0 if duration <= 20 else 1.0
        views = int(rng.randrange(30_000, 150_000) * factor * view_factor)
        videos.append({
            "id": video_id,
            "snippet": {
                "title": f"{title} | Official Trailer",
                "description": _CAPTION_TEMPLATES[i % len(_CAPTION_TEMPLATES)].format(title=title, tag=tag),
                "publishedAt": _published_at(rng, i).isoformat(),
                "channelTitle": handle,
                "thumbnails": {"high": {"url": f"https://mock.local/yt/{handle}/{i}.jpg"}},
            },
            "statistics": {
                "viewCount": str(views),
                "likeCount": str(int(views * rng.uniform(0.02, 0.05))),
                "commentCount": str(int(views * rng.uniform(0.001, 0.003))),
            },
            "contentDetails": {"duration": f"PT{duration}S"},
            "_creative_radar_channel_id": channel_id,
        })
    return channel_meta, videos
