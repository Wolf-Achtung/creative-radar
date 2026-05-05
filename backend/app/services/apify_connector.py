from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import httpx

from app.config import settings
from app.services.cost_log import record_apify_run


BASE_URL = "https://api.apify.com/v2"

# Apify run lifecycle. Anything in TERMINAL_STATUSES ends the poll loop;
# only SUCCESS_STATUS produces a dataset read. The "TIMED-OUT" form (with
# hyphen) is what the Apify API actually returns; "TIMED_OUT" is accepted
# defensively in case Apify normalises to the underscore form in the future.
TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT", "TIMED_OUT"})
SUCCESS_STATUS = "SUCCEEDED"


def is_apify_configured() -> bool:
    return bool(settings.apify_api_token and settings.apify_instagram_actor_id)


def is_tiktok_configured() -> bool:
    return bool(settings.apify_api_token and settings.apify_tiktok_actor_id)


async def _poll_until_terminal(client: httpx.AsyncClient, run_id: str, poll_interval: float) -> dict[str, Any]:
    """Block until the Apify run reaches a terminal status, returning the
    final run-data dict. Caller wraps this in ``asyncio.wait_for`` for the
    total-timeout guarantee.
    """
    while True:
        await asyncio.sleep(poll_interval)
        response = await client.get(
            f"{BASE_URL}/actor-runs/{run_id}",
            params={"token": settings.apify_api_token},
        )
        response.raise_for_status()
        data = response.json().get("data", {})
        if data.get("status") in TERMINAL_STATUSES:
            return data


async def _run_actor(actor_id: str, actor_input: dict[str, Any], wait_seconds: int | None = None) -> list[dict[str, Any]]:
    """Start an Apify actor run, poll until terminal, and read the dataset.

    The Apify ``waitForFinish`` parameter caps at 60s server-side, so for
    runs longer than a minute the previous synchronous flow read the
    dataset before the run had finished and returned 0 items. This
    refactor decouples start from finish: POST without waitForFinish, then
    poll ``/actor-runs/{id}`` every ``apify_poll_interval_seconds`` until
    one of the TERMINAL_STATUSES, capped by ``wait_seconds`` (or the
    ``apify_wait_seconds`` setting) as a hard total-timeout via
    ``asyncio.wait_for``.

    ``wait_seconds`` keeps the original kwarg name and position for
    backwards compatibility, but its semantics changed: it is now the
    total run-completion budget in seconds, not the per-HTTP-request
    timeout.
    """
    total_timeout = float(wait_seconds if wait_seconds is not None else settings.apify_wait_seconds)
    poll_interval = float(settings.apify_poll_interval_seconds)
    # Per-request httpx timeout: enough for one poll + buffer. The total
    # run budget is enforced by asyncio.wait_for around the poll loop.
    request_timeout = poll_interval + 30.0

    async with httpx.AsyncClient(timeout=request_timeout) as client:
        # Step 1: kick off the run. No waitForFinish — server-side cap of
        # 60s would silently truncate the wait for long-running actors.
        run_response = await client.post(
            f"{BASE_URL}/acts/{actor_id}/runs",
            params={"token": settings.apify_api_token},
            json=actor_input,
        )
        run_response.raise_for_status()
        run_data = run_response.json().get("data", {})
        run_id = run_data.get("id")
        if not run_id:
            return []

        # Step 2: poll until the run hits a terminal status, bounded by
        # the total timeout. asyncio.TimeoutError → operational failure.
        try:
            run_data = await asyncio.wait_for(
                _poll_until_terminal(client, run_id, poll_interval),
                timeout=total_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"apify run {run_id} (actor={actor_id}) did not finish "
                f"within {total_timeout:.0f}s"
            ) from exc

        status = run_data.get("status")
        if status != SUCCESS_STATUS:
            raise RuntimeError(
                f"apify run {run_id} (actor={actor_id}) ended with status={status}"
            )

        # Step 3: dataset read — the run is now actually complete.
        dataset_id = run_data.get("defaultDatasetId")
        if not dataset_id:
            return []
        items_response = await client.get(
            f"{BASE_URL}/datasets/{dataset_id}/items",
            params={"token": settings.apify_api_token, "clean": "true", "format": "json"},
        )
        items_response.raise_for_status()
        items = items_response.json()
        normalized_items = items if isinstance(items, list) else []
        # Cost-log hook (W4 Task 4.4 / F0.6). Never lets a failed log break
        # the actor run — record_apify_run swallows its own errors.
        record_apify_run(
            run_data=run_data,
            items_count=len(normalized_items),
            operation=f"actor:{actor_id}",
        )
        return normalized_items


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
