from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from app.config import settings


BASE_URL = "https://api.apify.com/v2"


def is_apify_configured() -> bool:
    return bool(settings.apify_api_token and settings.apify_instagram_actor_id)


async def run_public_channel_monitor(channel_urls: list[str], results_limit: int | None = None) -> list[dict[str, Any]]:
    if not is_apify_configured():
        raise RuntimeError("APIFY_API_TOKEN oder APIFY_INSTAGRAM_ACTOR_ID fehlt.")

    urls = [url.rstrip("/") for url in channel_urls if url]
    if not urls:
        return []

    actor_input = {
        "directUrls": urls,
        "resultsLimit": results_limit or settings.apify_results_limit_per_channel,
        "resultsType": "posts",
        "addParentData": True,
    }

    async with httpx.AsyncClient(timeout=settings.apify_wait_seconds + 30) as client:
        run_response = await client.post(
            f"{BASE_URL}/acts/{settings.apify_instagram_actor_id}/runs",
            params={"token": settings.apify_api_token, "waitForFinish": settings.apify_wait_seconds},
            json=actor_input,
        )
        run_response.raise_for_status()
        run_data = run_response.json().get("data", {})
        dataset_id = run_data.get("defaultDatasetId")
        if not dataset_id:
            return []
        items_response = await client.get(
            f"{BASE_URL}/datasets/{dataset_id}/items",
            params={"token": settings.apify_api_token, "clean": "true", "format": "json"},
        )
        items_response.raise_for_status()
        items = items_response.json()
        return items if isinstance(items, list) else []


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _image_from_item(item: dict[str, Any]) -> str | None:
    direct = _first_string(
        item.get("displayUrl"),
        item.get("display_url"),
        item.get("imageUrl"),
        item.get("image_url"),
        item.get("thumbnailUrl"),
        item.get("thumbnail_url"),
        item.get("previewUrl"),
        item.get("preview_url"),
    )
    if direct:
        return direct

    for key in ("images", "imageUrls", "displayUrls", "childPosts", "latestPosts", "media"):
        value = item.get(key)
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, str) and entry.strip():
                    return entry.strip()
                if isinstance(entry, dict):
                    candidate = _image_from_item(entry)
                    if candidate:
                        return candidate
    return None


def normalize_public_item(item: dict[str, Any]) -> dict[str, Any]:
    url = item.get("url") or item.get("postUrl") or item.get("post_url") or ""
    short_code = item.get("shortCode") or item.get("shortcode")
    if not url and short_code:
        url = f"https://www.instagram.com/p/{short_code}/"

    timestamp = item.get("timestamp") or item.get("takenAt") or item.get("date")
    published_at = None
    if timestamp:
        try:
            published_at = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        except Exception:
            published_at = None

    caption = item.get("caption") or item.get("text") or item.get("description") or ""
    owner = item.get("ownerUsername") or item.get("username") or item.get("owner", {}).get("username")

    return {
        "post_url": str(url).rstrip("/"),
        "caption": caption,
        "image_url": _image_from_item(item),
        "published_at": published_at,
        "owner_username": owner,
        "visible_likes": item.get("likesCount") or item.get("likes"),
        "visible_comments": item.get("commentsCount") or item.get("comments"),
        "visible_views": item.get("videoViewCount") or item.get("videoPlayCount") or item.get("views"),
        "raw": item,
    }
