import re
from html import unescape
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import settings

# Audit 2026-08-17 (SSRF): dieser Service hat vorher JEDE URL mit
# follow_redirects=True gefetcht und Titel/Metadaten zurueckgegeben — damit
# liess sich das Railway-interne Netz (postgres.railway.internal & Co.)
# scannen und Response-Inhalte auslesen. Jetzt gilt dasselbe Muster wie im
# Image-Proxy (app/api/proxy.py): Host-Allowlist + Redirect-Re-Validierung
# pro Hop, http/https only. Nicht erlaubte Ziele werden NICHT gefetcht; der
# Aufrufer bekommt das unveraenderte "link-only"-Resultat — kein Breaking
# Change fuer den Admin-Import-Flow.
_MAX_REDIRECTS = 5


def _host_is_allowed(host: str) -> bool:
    host_lower = (host or "").lower().rsplit(":", 1)[0]
    for suffix in settings.link_preview_host_suffixes:
        if host_lower == suffix or host_lower.endswith("." + suffix):
            return True
    return False


def _url_is_fetchable(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and _host_is_allowed(parsed.netloc)


def infer_instagram_handle(url: str) -> str | None:
    try:
        parts = [part for part in urlparse(url).path.split('/') if part]
    except Exception:
        return None
    if not parts:
        return None
    if parts[0] in {'p', 'reel', 'tv', 'stories'}:
        return None
    return parts[0]


async def fetch_public_preview(url: str) -> dict:
    result = {
        'post_url': url,
        'caption': None,
        'image_url': None,
        'title': None,
        'handle': infer_instagram_handle(url),
        'source': 'link-only',
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 CreativeRadar/1.0',
        'Accept-Language': 'de-DE,de;q=0.9,en;q=0.8',
    }
    if not _url_is_fetchable(url):
        return result
    try:
        # follow_redirects bewusst aus: jeder Redirect-Hop wird selbst gegen
        # die Allowlist geprueft, sonst waere ein Redirect von instagram.com
        # auf einen internen Host der triviale Bypass.
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, headers=headers) as client:
            current_url = url
            for _ in range(_MAX_REDIRECTS + 1):
                response = await client.get(current_url)
                if response.status_code not in (301, 302, 303, 307, 308):
                    break
                location = response.headers.get("location")
                if not location:
                    return result
                current_url = urljoin(current_url, location)
                if not _url_is_fetchable(current_url):
                    return result
            else:
                return result
            if response.status_code >= 400:
                return result
            html = response.text
    except Exception:
        return result

    soup = BeautifulSoup(html, 'html.parser')

    def meta_value(*names: str) -> str | None:
        for name in names:
            tag = soup.find('meta', attrs={'property': name}) or soup.find('meta', attrs={'name': name})
            if tag and tag.get('content'):
                return unescape(tag.get('content').strip())
        return None

    title = meta_value('og:title', 'twitter:title') or (soup.title.string.strip() if soup.title and soup.title.string else None)
    description = meta_value('og:description', 'description', 'twitter:description')
    image = meta_value('og:image', 'twitter:image')

    if description:
        description = re.sub(r'\s+', ' ', description).strip()
    if title:
        title = re.sub(r'\s+', ' ', title).strip()

    result.update({
        'caption': description,
        'image_url': image,
        'title': title,
        'source': 'public-preview' if description or image or title else 'link-only',
    })
    return result
