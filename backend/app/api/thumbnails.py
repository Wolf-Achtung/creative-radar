"""Thumbnail-Proxy für CDN-Hotlink-Protection (Sprint 5c).

Hintergrund: Sprint 5b liefert ``thumbnail_url`` als rohe CDN-URL an die
Frontend-Card. In der Browser-DevTools-Diagnose vom 09.05.2026 hat sich
gezeigt, dass nur YouTube-Thumbnails (``i.ytimg.com``) so geladen werden
— TikTok-CDN antwortet 403 (Hotlink-Protection via Referer-Check),
Instagram-CDN blockt mit ``ERR_BLOCKED_BY_RESPONSE.NotSameOrigin`` (CORP-
Header). Der bestehende ``/api/img?url=…``-Proxy sendet einen leeren
Referer und wird ebenso blockiert.

Sprint 5c löst das per asset-ID-keyed Proxy:

  * Endpoint nimmt eine Asset-UUID, lädt die Source-URL aus der DB
    (kein Open-Proxy, keine URL-Whitelist nötig).
  * Plattform-Detection per Hostname → setzt einen plausiblen
    ``Referer`` (TikTok / Instagram) plus Browser-User-Agent.
  * Lazy Filesystem-Cache unter ``/tmp/thumbnail_cache/`` mit SHA-256-
    Keys und 7-Tage-TTL. ``/tmp`` ist auf Railway flüchtig — der Cache
    wird also nach jedem Deploy zurückgesetzt; das ist akzeptabel für
    Sprint 5c. Persistenter Storage ist Backlog.
  * Stale-while-error: schlägt der Re-Fetch nach Ablauf der TTL fehl
    (403, Timeout, Network), wird die alte Cache-Datei weiter
    ausgeliefert — die Source-URLs der CDNs laufen nach Tagen ab und
    der Frontend-Fallback (Plattform-Akronym) ist immer da, falls auch
    der Cache leer ist.

Out-of-scope (Backlog): S3/R2-Storage, WebP-Konvertierung, Resizing,
Cache-Cleanup-Job, Apify-Re-Fetch bei abgelaufenen Source-URLs,
plattform-spezifischer Content-Type aus der Source-Response (Sprint 5c
liefert pauschal ``image/jpeg``; das deckt > 99 % der Posts).
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Final, Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session, select

from app.database import get_session
from app.models.entities import Asset

router = APIRouter(prefix="/api/thumbnails", tags=["thumbnails"])
logger = logging.getLogger(__name__)


CACHE_DIR: Final[Path] = Path("/tmp/thumbnail_cache")
CACHE_TTL_SECONDS: Final[int] = 7 * 24 * 60 * 60  # 7 Tage
FETCH_TIMEOUT_SECONDS: Final[float] = 5.0
BROWSER_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Hostname-Substring → Header-Set für den Source-Fetch. Substring statt
# strikter Hostname-Vergleich, weil TikTok regional unterschiedliche
# CDN-Hosts ausliefert (``p19-common-sign.tiktokcdn-us.com``,
# ``p77-sign-va.tiktokcdn.com``, …) und Instagram pro Region/Edge wechselt
# (``scontent-lax3-1.cdninstagram.com``, ``scontent-fra3-2.cdninstagram.com``).
PLATFORM_HEADERS: Final[dict[str, dict[str, str]]] = {
    "tiktokcdn": {
        "Referer": "https://www.tiktok.com/",
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5",
    },
    "cdninstagram": {
        "Referer": "https://www.instagram.com/",
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5",
    },
    "ytimg": {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5",
    },
}


def _detect_platform_key(url: str) -> Optional[str]:
    """Hostname-Substring-Match auf bekannte CDN-Patterns."""
    lowered = url.lower()
    for key in PLATFORM_HEADERS:
        if key in lowered:
            return key
    return None


def _cache_path_for_url(url: str) -> Path:
    """SHA-256-Digest als Cache-Key — verhindert Path-Traversal und
    macht den Pfad aus jeder beliebigen URL deterministisch ableitbar."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.bin"


def _is_cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < CACHE_TTL_SECONDS


async def _fetch_and_cache(source_url: str, cache_path: Path) -> Optional[bytes]:
    """Source-Fetch mit plattform-spezifischen Headern. Bei Fehler wird
    eine ggf. vorhandene (stale) Cache-Datei zurückgegeben — der Caller
    entscheidet, ob er sie ausliefert oder 404 schickt."""
    platform_key = _detect_platform_key(source_url)
    headers = PLATFORM_HEADERS.get(platform_key, {"User-Agent": BROWSER_USER_AGENT})

    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS) as client:
            response = await client.get(source_url, headers=headers, follow_redirects=True)
            response.raise_for_status()
            data = response.content
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning("thumbnail fetch failed for %s: %s", source_url, exc)
        if cache_path.exists():
            return cache_path.read_bytes()
        return None

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)
    except OSError as exc:
        # Cache-Write darf den Request nicht kippen — wir liefern das
        # frisch gefetchte Bild aus, der nächste Request wird ohne Cache
        # einfach erneut fetchen.
        logger.warning("thumbnail cache write failed for %s: %s", cache_path, exc)
    return data


def _image_response(data: bytes) -> Response:
    """``image/jpeg`` deckt > 99 % der Live-Thumbnails ab — Browser
    rendern WebP/PNG genauso, weil sie auf Magic-Bytes sniffen statt
    auf den Content-Type zu vertrauen. Plattform-spezifischer
    Content-Type aus der Source-Response wäre sauberer; das ist aber
    Backlog."""
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=604800"},
    )


@router.get("/{asset_id}")
async def get_thumbnail(
    asset_id: str,
    session: Session = Depends(get_session),
) -> Response:
    """Liefert das Thumbnail eines Assets über den Proxy.

    Pfad-Parameter ist die Asset-UUID; Source-URL wird aus der DB
    gelesen (kein Open-Proxy, keine URL-Whitelist). Antworten:

      * 200 mit ``image/jpeg``-Body bei Cache-Hit oder erfolgreichem
        Source-Fetch (oder Stale-Hit).
      * 404 wenn das Asset nicht existiert, kein ``thumbnail_url``
        trägt, oder der Source-Fetch fehlschlägt UND kein (stale)
        Cache-Eintrag verfügbar ist. Der Frontend-``onError`` schaltet
        in dem Fall auf den Plattform-Fallback um.
      * 400 für offensichtlich invalide UUIDs — kein DB-Roundtrip.
    """
    try:
        asset_uuid = UUID(asset_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="invalid asset id")

    asset = session.exec(select(Asset).where(Asset.id == asset_uuid)).first()
    if asset is None or not asset.thumbnail_url:
        raise HTTPException(status_code=404, detail="thumbnail not available")

    source_url = asset.thumbnail_url
    cache_path = _cache_path_for_url(source_url)

    if _is_cache_fresh(cache_path):
        try:
            return _image_response(cache_path.read_bytes())
        except OSError as exc:
            # Cache-Datei wurde zwischen Stat und Read gelöscht (z. B.
            # /tmp-Cleanup); fall through auf den Source-Fetch-Pfad.
            logger.warning("cache read failed for %s: %s", cache_path, exc)

    data = await _fetch_and_cache(source_url, cache_path)
    if data is None:
        raise HTTPException(status_code=404, detail="thumbnail fetch failed")

    return _image_response(data)
