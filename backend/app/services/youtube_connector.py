"""YouTube Data API v3 connector (Sprint 5.2.3).

Sister of ``apify_connector`` — same shape, different upstream:

- ``is_youtube_configured()`` → bool
- ``fetch_channel_videos(handle_or_id, results_limit)`` → list[dict] of
  raw YouTube ``videos.list`` items, with ``_creative_radar_channel``
  meta attached for the downstream normalizer.
- ``normalize_youtube_video(item)`` → platform-agnostic dict matching
  the Apify-connector output (``post_url``, ``caption``, ``image_url``,
  ``published_at``, ``visible_*``, ``duration_seconds``, ``external_id``,
  ``raw``) so the same Post-write path works for all three platforms.

Quota cost per channel sync: 3 units total (channels.list +
playlistItems.list + videos.list, regardless of how many videos N).
Free tier is 10k/day. Cost-log via ``record_youtube_api_call``.

Errors are typed: ``YouTubeAuthError`` (401/403 keyInvalid),
``YouTubeQuotaExceededError`` (403 quotaExceeded), ``YouTubeNotFoundError``
(404 channelNotFound or empty channels.list response). Generic transport
failures bubble up as ``YouTubeAPIError``. Caller decides how to map
those to HTTP responses (the admin sync endpoint maps to 401/429/404/503).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx

from app.config import settings
from app.services.cost_log import record_youtube_api_call


BASE_URL = "https://www.googleapis.com/youtube/v3"
DEFAULT_TIMEOUT = 12.0


# ---------- Errors -----------------------------------------------------


class YouTubeAPIError(RuntimeError):
    """Generic YouTube API failure (network, 5xx, unexpected payload)."""


class YouTubeAuthError(YouTubeAPIError):
    """401/403 with reason ``keyInvalid`` / ``forbidden`` — the API key
    is wrong, missing, or restricted."""


class YouTubeQuotaExceededError(YouTubeAPIError):
    """403 with reason ``quotaExceeded`` — we hit the daily 10k quota.
    Caller should map to 429 and stop retrying for the day."""


class YouTubeNotFoundError(YouTubeAPIError):
    """404, or an empty ``items`` list on a channels.list lookup —
    handle/id does not resolve to a public channel."""


# ---------- Configuration probe ---------------------------------------


def is_youtube_configured() -> bool:
    return bool(settings.youtube_api_key)


# ---------- HTTP plumbing ---------------------------------------------


def _classify_error(status_code: int, payload: dict[str, Any]) -> YouTubeAPIError:
    """Translate a Google API JSON-error envelope into one of our typed
    exceptions. Google wraps errors in ``{"error": {"code": ..., "errors":
    [{"reason": ...}]}}``; the ``reason`` field is what we key off."""
    error_obj = payload.get("error") if isinstance(payload, dict) else None
    reason = ""
    message = ""
    if isinstance(error_obj, dict):
        message = str(error_obj.get("message") or "")
        errors_list = error_obj.get("errors") or []
        if isinstance(errors_list, list) and errors_list:
            first = errors_list[0]
            if isinstance(first, dict):
                reason = str(first.get("reason") or "")
    label = f"{status_code} {reason or 'unknown'}: {message}".strip()

    if status_code == 404 or reason in {"channelNotFound", "notFound"}:
        return YouTubeNotFoundError(label)
    if reason == "quotaExceeded":
        return YouTubeQuotaExceededError(label)
    if status_code in (401, 403) or reason in {"keyInvalid", "forbidden", "ipRefererBlocked"}:
        return YouTubeAuthError(label)
    return YouTubeAPIError(label)


def _get(client: httpx.Client, path: str, params: dict[str, Any]) -> dict[str, Any]:
    """One GET against the YouTube API. Adds ``key=...``, raises a typed
    error on non-2xx, returns the parsed JSON body on success."""
    full_params = {**params, "key": settings.youtube_api_key}
    try:
        response = client.get(f"{BASE_URL}/{path}", params=full_params)
    except httpx.HTTPError as exc:
        raise YouTubeAPIError(f"transport error: {exc}") from exc
    if response.status_code >= 400:
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            payload = {}
        raise _classify_error(response.status_code, payload)
    try:
        return response.json()
    except Exception as exc:  # noqa: BLE001
        raise YouTubeAPIError(f"non-json response: {exc}") from exc


# ---------- Channel resolution ----------------------------------------


def _channel_lookup_param(handle_or_id: str) -> dict[str, str]:
    """YouTube channel lookups take one of three params: ``id`` (UCxxx),
    ``forHandle`` (@netflix without the @), ``forUsername`` (legacy
    pre-handles names). We pick based on the input shape."""
    value = (handle_or_id or "").strip()
    if not value:
        raise YouTubeAPIError("empty channel handle/id")
    if value.startswith("UC") and len(value) >= 20:
        return {"id": value}
    if value.startswith("@"):
        return {"forHandle": value[1:]}
    return {"forHandle": value}


def _resolve_channel(client: httpx.Client, handle_or_id: str) -> dict[str, Any]:
    """channels.list (1 quota unit). Returns the full channel dict from
    items[0] — caller pulls uploads-playlist-id, snippet, statistics out
    of it as needed. Raises YouTubeNotFoundError if items is empty."""
    params = {
        "part": "snippet,contentDetails,statistics",
        **_channel_lookup_param(handle_or_id),
    }
    body = _get(client, "channels", params)
    record_youtube_api_call(
        quota_units=1,
        operation="channels.list",
        meta={"handle_or_id": handle_or_id},
    )
    items = body.get("items") or []
    if not items:
        raise YouTubeNotFoundError(f"no channel for {handle_or_id!r}")
    first = items[0]
    if not isinstance(first, dict):
        raise YouTubeAPIError("channels.list returned non-dict item")
    return first


def _list_recent_video_ids(
    client: httpx.Client, uploads_playlist_id: str, max_results: int
) -> list[str]:
    """playlistItems.list (1 quota unit). Returns up to ``max_results``
    video IDs, newest first. We don't paginate — N is small (default 10),
    capped at 50 by the API anyway."""
    params = {
        "part": "contentDetails",
        "playlistId": uploads_playlist_id,
        "maxResults": max(1, min(int(max_results or 10), 50)),
    }
    body = _get(client, "playlistItems", params)
    record_youtube_api_call(
        quota_units=1,
        operation="playlistItems.list",
        meta={"playlist_id": uploads_playlist_id, "max_results": params["maxResults"]},
    )
    ids: list[str] = []
    for item in body.get("items") or []:
        if not isinstance(item, dict):
            continue
        content = item.get("contentDetails") or {}
        video_id = content.get("videoId")
        if isinstance(video_id, str) and video_id:
            ids.append(video_id)
    return ids


def _hydrate_videos(client: httpx.Client, video_ids: list[str]) -> list[dict[str, Any]]:
    """videos.list (1 quota unit, regardless of how many IDs). Returns
    the raw YouTube items in API-response order. Empty input returns []
    without spending a quota unit."""
    if not video_ids:
        return []
    params = {
        "part": "snippet,statistics,contentDetails",
        "id": ",".join(video_ids),
        "maxResults": len(video_ids),
    }
    body = _get(client, "videos", params)
    record_youtube_api_call(
        quota_units=1,
        operation="videos.list",
        meta={"video_count": len(video_ids)},
    )
    items = body.get("items") or []
    return [item for item in items if isinstance(item, dict)]


# ---------- Public API ------------------------------------------------


def fetch_channel_videos(
    handle_or_id: str, results_limit: int | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Sync entry point. Returns ``(channel_meta, raw_video_items)``.

    ``channel_meta`` is the full channels.list item (snippet + statistics
    + contentDetails). Each ``raw_video_item`` is the videos.list dict,
    with ``_creative_radar_channel_id`` added so the normalizer can
    cross-reference. Three quota units total.
    """
    if not is_youtube_configured():
        raise YouTubeAuthError("YOUTUBE_API_KEY is not configured")
    limit = results_limit or settings.youtube_results_limit_per_channel
    with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
        channel = _resolve_channel(client, handle_or_id)
        uploads = (
            (channel.get("contentDetails") or {})
            .get("relatedPlaylists", {})
            .get("uploads")
        )
        if not uploads:
            raise YouTubeNotFoundError(
                f"channel {handle_or_id!r} has no uploads playlist"
            )
        video_ids = _list_recent_video_ids(client, uploads, limit)
        videos = _hydrate_videos(client, video_ids)
    yt_channel_id = channel.get("id")
    for video in videos:
        video["_creative_radar_channel_id"] = yt_channel_id
    return channel, videos


# ---------- Normalization --------------------------------------------


_ISO8601_DURATION = re.compile(
    r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$"
)


def _parse_iso8601_duration(value: Any) -> int | None:
    """YouTube returns durations like ``PT1M30S`` (1 minute 30s = 90).
    Returns total seconds or None if unparsable / missing."""
    if not isinstance(value, str) or not value.startswith("P"):
        return None
    match = _ISO8601_DURATION.match(value)
    if not match:
        return None
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _best_thumbnail_url(thumbnails: Any) -> str | None:
    """YouTube's snippet.thumbnails has up to 5 sizes (default, medium,
    high, standard, maxres). Pick the largest available — most useful
    for downstream visual analysis."""
    if not isinstance(thumbnails, dict):
        return None
    for size in ("maxres", "standard", "high", "medium", "default"):
        entry = thumbnails.get(size)
        if isinstance(entry, dict):
            url = entry.get("url")
            if isinstance(url, str) and url:
                return url
    return None


def normalize_youtube_video(item: dict[str, Any]) -> dict[str, Any]:
    """Map a videos.list item onto the platform-agnostic Post-input dict
    shared with apify_connector's normalize_*_item helpers."""
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    content_details = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}

    video_id = item.get("id") if isinstance(item.get("id"), str) else None
    post_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
    caption = snippet.get("title") or ""
    description = snippet.get("description") or ""
    if description and caption:
        caption = f"{caption}\n\n{description}"
    elif description:
        caption = description

    return {
        "platform": "youtube",
        "post_url": post_url,
        "caption": caption,
        "image_url": _best_thumbnail_url(snippet.get("thumbnails")),
        "published_at": _parse_datetime(snippet.get("publishedAt")),
        "owner_username": snippet.get("channelTitle"),
        "visible_likes": _int_or_none(statistics.get("likeCount")),
        "visible_comments": _int_or_none(statistics.get("commentCount")),
        "visible_views": _int_or_none(statistics.get("viewCount")),
        "visible_shares": None,
        "visible_bookmarks": None,
        "duration_seconds": _parse_iso8601_duration(content_details.get("duration")),
        "external_id": video_id,
        "raw": item,
    }
