import re
from html import unescape
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


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
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
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
