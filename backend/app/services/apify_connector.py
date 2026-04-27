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


def normalize_public_item(item: dict[str, Any]) -> dict[str, Any]:
    url = item.get("url") or item.get("postUrl") or ""
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

    return {
        "post_url": str(url).rstrip("/"),
        "caption": item.get("caption") or item.get("text") or item.get("description") or "",
        "image_url": item.get("displayUrl") or item.get("imageUrl") or item.get("thumbnailUrl"),
        "published_at": published_at,
        "owner_username": item.get("ownerUsername") or item.get("username"),
        "visible_likes": item.get("likesCount") or item.get("likes"),
        "visible_comments": item.get("commentsCount") or item.get("comments"),
        "visible_views": item.get("videoViewCount") or item.get("videoPlayCount") or item.get("views"),
        "raw": item,
    }
