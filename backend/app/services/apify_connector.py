from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from app.config import settings


BASE_URL = "https://api.apify.com/v2"


def is_apify_configured() -> bool:
    return bool(settings.apify_api_token and settings.apify_instagram_actor_id)


def is_tiktok_configured() -> bool:
    return bool(settings.apify_api_token and settings.apify_tiktok_actor_id)


async def _run_actor(actor_id: str, actor_input: dict[str, Any], wait_seconds: int | None = None) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=(wait_seconds or settings.apify_wait_seconds) + 60) as client:
        run_response = await client.post(
            f"{BASE_URL}/acts/{actor_id}/runs",
            params={"token": settings.apify_api_token, "waitForFinish": wait_seconds or settings.apify_wait_seconds},
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
    return await _run_actor(settings.apify_instagram_actor_id, actor_input)


async def run_tiktok_profile_monitor(usernames: list[str], results_limit: int | None = None) -> list[dict[str, Any]]:
    if not is_tiktok_configured():
        raise RuntimeError("APIFY_API_TOKEN oder APIFY_TIKTOK_ACTOR_ID fehlt.")

    clean_usernames = []
    for username in usernames:
        clean = (username or "").strip().rstrip("/")
        if not clean:
            continue
        if "tiktok.com/@" in clean:
            clean = clean.split("tiktok.com/@", 1)[1].split("/", 1)[0]
        clean = clean.lstrip("@")
        if clean:
            clean_usernames.append(clean)
    if not clean_usernames:
        return []

    limit = results_limit or settings.apify_results_limit_per_channel
    actor_input_candidates = [
        {
            "profiles": clean_usernames,
            "resultsPerPage": limit,
            "profileScrapeSections": ["videos"],
            "profileSorting": "latest",
            "excludePinnedPosts": True,
        },
        {
            "usernames": clean_usernames,
            "maxItems": limit,
            "profileScrapeSections": ["videos"],
            "profileSorting": "latest",
            "excludePinnedPosts": True,
        },
    ]

    last_error: Exception | None = None
    for actor_input in actor_input_candidates:
        try:
            items = await _run_actor(settings.apify_tiktok_actor_id, actor_input)
            if items:
                return items
        except Exception as exc:  # fallback for actor input schema variants
            last_error = exc
    if last_error:
        raise last_error
    return []


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
        item.get("coverUrl"),
        item.get("cover_url"),
    )
    if direct:
        return direct

    for key in ("images", "imageUrls", "displayUrls", "childPosts", "latestPosts", "media", "videoMeta", "authorMeta"):
        value = item.get(key)
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, str) and entry.strip():
                    return entry.strip()
                if isinstance(entry, dict):
                    candidate = _image_from_item(entry)
                    if candidate:
                        return candidate
        if isinstance(value, dict):
            candidate = _image_from_item(value)
            if candidate:
                return candidate
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def normalize_public_item(item: dict[str, Any]) -> dict[str, Any]:
    url = item.get("url") or item.get("postUrl") or item.get("post_url") or ""
    short_code = item.get("shortCode") or item.get("shortcode")
    if not url and short_code:
        url = f"https://www.instagram.com/p/{short_code}/"

    timestamp = item.get("timestamp") or item.get("takenAt") or item.get("date")
    caption = item.get("caption") or item.get("text") or item.get("description") or ""
    owner = item.get("ownerUsername") or item.get("username") or item.get("owner", {}).get("username")

    return {
        "platform": "instagram",
        "post_url": str(url).rstrip("/"),
        "caption": caption,
        "image_url": _image_from_item(item),
        "published_at": _parse_datetime(timestamp),
        "owner_username": owner,
        "visible_likes": item.get("likesCount") or item.get("likes"),
        "visible_comments": item.get("commentsCount") or item.get("comments"),
        "visible_views": item.get("videoViewCount") or item.get("videoPlayCount") or item.get("views"),
        "visible_shares": item.get("shareCount") or item.get("shares"),
        "visible_bookmarks": item.get("collectCount") or item.get("bookmarks"),
        "duration_seconds": item.get("duration") or item.get("videoDuration"),
        "raw": item,
    }


def normalize_tiktok_item(item: dict[str, Any]) -> dict[str, Any]:
    author_meta = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}
    video_meta = item.get("videoMeta") if isinstance(item.get("videoMeta"), dict) else {}
    music_meta = item.get("musicMeta") if isinstance(item.get("musicMeta"), dict) else {}
    post_url = item.get("webVideoUrl") or item.get("url") or item.get("videoUrl") or item.get("shareUrl") or ""
    author = author_meta.get("name") or item.get("author") or item.get("authorName") or item.get("username")
    caption = item.get("text") or item.get("description") or item.get("caption") or ""
    timestamp = item.get("createTimeISO") or item.get("createTime") or item.get("createdAt")
    raw_payload = dict(item)
    raw_payload["_creative_radar_music"] = music_meta

    return {
        "platform": "tiktok",
        "post_url": str(post_url).rstrip("/"),
        "caption": caption,
        "image_url": _image_from_item(item),
        "published_at": _parse_datetime(timestamp),
        "owner_username": author,
        "visible_likes": item.get("diggCount") or item.get("heartCount") or item.get("likes"),
        "visible_comments": item.get("commentCount") or item.get("comments"),
        "visible_views": item.get("playCount") or item.get("views"),
        "visible_shares": item.get("shareCount") or item.get("shares"),
        "visible_bookmarks": item.get("collectCount") or item.get("bookmarks"),
        "duration_seconds": video_meta.get("duration") or item.get("duration"),
        "external_id": item.get("id") or item.get("videoId"),
        "raw": raw_payload,
    }
