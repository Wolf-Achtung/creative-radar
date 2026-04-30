"""Image-Proxy für externe CDN-Bildquellen.

Hintergrund: Instagram-CDN (`*.cdninstagram.com`) und TikTok-CDN (`*.tiktokcdn-*.com`)
blockieren Hotlinking aus Browsern mit fremdem Referer praktisch immer. Der Browser
sieht das Bild nie, das `<img>`-Tag triggert `onError`. Pfad-A-Light: das Backend
holt das Bild stellvertretend, streamt es 1:1 zurück, der Browser sieht eine same-origin-
Response — kein Hotlink mehr aus seiner Sicht.

Dieser Endpunkt ist KEIN allgemeiner Open-Proxy. Hosts werden gegen
`settings.image_proxy_host_suffixes` validiert (Suffix-Match auf den Hostnamen).
Größenlimit (`image_proxy_max_bytes`) gegen DoS, Timeout (`image_proxy_timeout_seconds`)
gegen langsame Quellen.
"""
from __future__ import annotations

from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.config import settings

router = APIRouter(prefix="/api/img", tags=["proxy"])


def _host_is_allowed(host: str) -> bool:
    host_lower = host.lower()
    for suffix in settings.image_proxy_host_suffixes:
        if host_lower == suffix or host_lower.endswith("." + suffix):
            return True
    return False


def _proxy_headers(target_host: str) -> dict[str, str]:
    """Spoof-frei: leerer Referer + neutraler User-Agent. CDNs reagieren idR auf
    Referer-Mismatch; ein leerer Referer wird oft akzeptiert, wo ein fremder Referer blockt."""
    return {
        "User-Agent": "Mozilla/5.0 (compatible; creative-radar-image-proxy/1.0)",
        "Accept": "image/*,*/*;q=0.8",
    }


@router.get("")
async def proxy_image(url: str = Query(..., min_length=8, max_length=2048)):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail="invalid url")
    if not _host_is_allowed(parsed.netloc):
        raise HTTPException(status_code=403, detail="host not allowed")

    timeout = httpx.Timeout(settings.image_proxy_timeout_seconds)
    client = httpx.AsyncClient(follow_redirects=True, timeout=timeout)

    try:
        upstream = await client.get(url, headers=_proxy_headers(parsed.netloc))
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"upstream error: {type(exc).__name__}")

    if upstream.status_code >= 400:
        body_size = len(upstream.content)
        await client.aclose()
        raise HTTPException(
            status_code=502,
            detail=f"upstream returned {upstream.status_code} ({body_size} bytes)",
        )

    content_type = upstream.headers.get("content-type", "application/octet-stream")
    content_length_header = upstream.headers.get("content-length")
    if content_length_header:
        try:
            if int(content_length_header) > settings.image_proxy_max_bytes:
                await client.aclose()
                raise HTTPException(status_code=502, detail="upstream payload too large")
        except ValueError:
            pass

    body = upstream.content
    await client.aclose()

    if len(body) > settings.image_proxy_max_bytes:
        raise HTTPException(status_code=502, detail="upstream payload too large")

    return StreamingResponse(
        iter([body]),
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=3600, stale-while-revalidate=86400",
            "X-Proxy-Source-Host": parsed.netloc,
        },
    )
