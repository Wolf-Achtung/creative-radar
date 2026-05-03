"""Unit tests for the YouTube Data API v3 connector (Sprint 5.2.3).

Covers:

- Lookup-param routing (UCxxx id, @handle, bare username) so the right
  ``channels.list`` query goes out for each input shape.
- Error classification: 401/403 keyInvalid → YouTubeAuthError, 403
  quotaExceeded → YouTubeQuotaExceededError, 404 / empty items →
  YouTubeNotFoundError, others → YouTubeAPIError.
- Quota-unit accounting: a full sync logs exactly three entries
  (channels.list, playlistItems.list, videos.list), each 1 unit.
  An empty videos.list call is skipped (no quota burn).
- ``normalize_youtube_video`` mapping from a real-shape API payload to
  the shared Post-input dict (post_url, caption, thumbnails, view/like
  counts, ISO-8601 duration parse).

HTTP transport is stubbed via ``httpx.MockTransport`` — no network, no
mocking of internal methods. That keeps the test honest about the actual
URL/path/param contract the connector forms.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest

import app.services.youtube_connector as yt_mod
from app.services.youtube_connector import (
    YouTubeAPIError,
    YouTubeAuthError,
    YouTubeNotFoundError,
    YouTubeQuotaExceededError,
    _channel_lookup_param,
    _parse_iso8601_duration,
    fetch_channel_videos,
    normalize_youtube_video,
)


# ---------- Lookup-param routing -------------------------------------


def test_channel_lookup_param_uses_id_for_uc_prefix():
    assert _channel_lookup_param("UCWOA1ZGywLbqmigxE4Qlvuw") == {
        "id": "UCWOA1ZGywLbqmigxE4Qlvuw"
    }


def test_channel_lookup_param_strips_at_for_handle():
    assert _channel_lookup_param("@netflix") == {"forHandle": "netflix"}


def test_channel_lookup_param_falls_back_to_handle_for_bare_name():
    assert _channel_lookup_param("netflix") == {"forHandle": "netflix"}


def test_channel_lookup_param_rejects_empty():
    with pytest.raises(YouTubeAPIError):
        _channel_lookup_param("")


# ---------- ISO 8601 duration parser ---------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("PT30S", 30),
        ("PT1M30S", 90),
        ("PT2H", 7200),
        ("PT1H30M45S", 5445),
        ("P1DT2H", 93600),
        ("", None),
        (None, None),
        ("not-a-duration", None),
    ],
)
def test_parse_iso8601_duration(value, expected):
    assert _parse_iso8601_duration(value) == expected


# ---------- Normalizer mapping ---------------------------------------


_SAMPLE_VIDEO_ITEM: dict[str, Any] = {
    "id": "abc123",
    "snippet": {
        "publishedAt": "2026-04-15T10:30:00Z",
        "channelTitle": "Netflix",
        "title": "Stranger Things 5 — Official Trailer",
        "description": "Watch July 4.",
        "thumbnails": {
            "default": {"url": "https://i.ytimg.com/vi/abc123/default.jpg"},
            "high": {"url": "https://i.ytimg.com/vi/abc123/hqdefault.jpg"},
            "maxres": {"url": "https://i.ytimg.com/vi/abc123/maxresdefault.jpg"},
        },
    },
    "statistics": {
        "viewCount": "1234567",
        "likeCount": "98765",
        "commentCount": "4321",
    },
    "contentDetails": {"duration": "PT2M15S"},
}


def test_normalize_youtube_video_full_mapping():
    out = normalize_youtube_video(_SAMPLE_VIDEO_ITEM)

    assert out["platform"] == "youtube"
    assert out["post_url"] == "https://www.youtube.com/watch?v=abc123"
    assert out["external_id"] == "abc123"
    # Title + description are concatenated into caption — title first so
    # whitelist/keyword matchers see the canonical name up top.
    assert out["caption"].startswith("Stranger Things 5")
    assert "Watch July 4." in out["caption"]
    # Best available thumbnail (maxres > standard > high > medium > default).
    assert out["image_url"] == "https://i.ytimg.com/vi/abc123/maxresdefault.jpg"
    assert out["published_at"] is not None
    assert out["owner_username"] == "Netflix"
    assert out["visible_views"] == 1234567
    assert out["visible_likes"] == 98765
    assert out["visible_comments"] == 4321
    # YouTube has no native shares/bookmarks/saves on the API.
    assert out["visible_shares"] is None
    assert out["visible_bookmarks"] is None
    assert out["duration_seconds"] == 135
    assert out["raw"] is _SAMPLE_VIDEO_ITEM


def test_normalize_youtube_video_handles_missing_fields():
    out = normalize_youtube_video({"id": "x", "snippet": {"title": "Solo"}})
    assert out["post_url"] == "https://www.youtube.com/watch?v=x"
    assert out["caption"] == "Solo"
    assert out["image_url"] is None
    assert out["visible_views"] is None
    assert out["duration_seconds"] is None


# ---------- Transport-level integration via MockTransport ------------


def _patched_client_factory(handler):
    """Build the mock-transport client with the *real* httpx.Client BEFORE
    we patch the connector's reference, then return a zero-arg factory the
    connector can call as ``httpx.Client(timeout=DEFAULT_TIMEOUT)``. Once
    the patch is active, the real Client constructor is shadowed; if we
    built the client inside the patched scope we'd hit the lambda
    instead, which doesn't accept ``transport``."""
    real_client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    return lambda timeout=None: real_client


@pytest.fixture
def yt_api_key(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "youtube_api_key", "TEST-KEY")
    monkeypatch.setattr(settings, "youtube_results_limit_per_channel", 10)


def _channel_payload(uploads: str = "UU_uploads_123") -> dict[str, Any]:
    return {
        "items": [
            {
                "id": "UCWOA1ZGywLbqmigxE4Qlvuw",
                "snippet": {"title": "Netflix", "customUrl": "@netflix"},
                "contentDetails": {"relatedPlaylists": {"uploads": uploads}},
                "statistics": {"subscriberCount": "300000000"},
            }
        ]
    }


def _playlist_items_payload(video_ids: list[str]) -> dict[str, Any]:
    return {"items": [{"contentDetails": {"videoId": vid}} for vid in video_ids]}


def _videos_payload(video_ids: list[str]) -> dict[str, Any]:
    return {"items": [{**_SAMPLE_VIDEO_ITEM, "id": vid} for vid in video_ids]}


def test_fetch_channel_videos_happy_path(yt_api_key):
    captured_paths: list[str] = []
    captured_params: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_paths.append(request.url.path)
        captured_params.append(dict(request.url.params))
        if request.url.path.endswith("/channels"):
            return httpx.Response(200, json=_channel_payload("UU_uploads_xyz"))
        if request.url.path.endswith("/playlistItems"):
            return httpx.Response(200, json=_playlist_items_payload(["v1", "v2", "v3"]))
        if request.url.path.endswith("/videos"):
            return httpx.Response(200, json=_videos_payload(["v1", "v2", "v3"]))
        return httpx.Response(500)

    cost_log_calls: list[tuple[int, str, dict | None]] = []

    def fake_record(quota_units, operation, meta=None):
        cost_log_calls.append((quota_units, operation, meta or {}))

    with patch.object(yt_mod, "record_youtube_api_call", side_effect=fake_record), \
         patch.object(yt_mod.httpx, "Client", _patched_client_factory(handler)):
        channel, videos = fetch_channel_videos("@netflix", results_limit=3)

    assert channel["id"] == "UCWOA1ZGywLbqmigxE4Qlvuw"
    assert len(videos) == 3
    assert all(video["_creative_radar_channel_id"] == channel["id"] for video in videos)

    # Three sequential API calls, one per quota-billable endpoint.
    assert [path.split("/")[-1] for path in captured_paths] == [
        "channels",
        "playlistItems",
        "videos",
    ]
    # Key is appended to every request.
    assert all(params.get("key") == "TEST-KEY" for params in captured_params)
    # @handle becomes forHandle=netflix (no leading @).
    assert captured_params[0].get("forHandle") == "netflix"
    # Uploads playlist threads through to playlistItems.list.
    assert captured_params[1].get("playlistId") == "UU_uploads_xyz"
    assert int(captured_params[1].get("maxResults")) == 3
    # videos.list batches all IDs in one comma-list.
    assert captured_params[2].get("id") == "v1,v2,v3"

    # Cost-log records exactly the three billable calls, 1 unit each.
    assert [(units, op) for units, op, _ in cost_log_calls] == [
        (1, "channels.list"),
        (1, "playlistItems.list"),
        (1, "videos.list"),
    ]


def test_fetch_channel_videos_empty_uploads_skips_videos_list(yt_api_key):
    """If the channel has zero uploaded videos, playlistItems.list returns
    an empty items array. The connector must short-circuit before calling
    videos.list — that call would burn a quota unit for nothing."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/channels"):
            return httpx.Response(200, json=_channel_payload("UU_empty"))
        if request.url.path.endswith("/playlistItems"):
            return httpx.Response(200, json={"items": []})
        if request.url.path.endswith("/videos"):
            return httpx.Response(500, json={"error": {"message": "should-not-be-called"}})
        return httpx.Response(500)

    cost_log_calls: list[tuple[int, str]] = []

    def fake_record(quota_units, operation, meta=None):
        cost_log_calls.append((quota_units, operation))

    with patch.object(yt_mod, "record_youtube_api_call", side_effect=fake_record), \
         patch.object(yt_mod.httpx, "Client", _patched_client_factory(handler)):
        _, videos = fetch_channel_videos("@netflix")

    assert videos == []
    assert "/youtube/v3/videos" not in [p for p in paths]
    # Only two billable calls when uploads are empty.
    assert [op for _, op in cost_log_calls] == ["channels.list", "playlistItems.list"]


def test_fetch_channel_videos_quota_exceeded_raises_typed_error(yt_api_key):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": 403,
                    "message": "The request cannot be completed because you have exceeded your quota.",
                    "errors": [{"reason": "quotaExceeded"}],
                }
            },
        )

    with patch.object(yt_mod.httpx, "Client", _patched_client_factory(handler)):
        with pytest.raises(YouTubeQuotaExceededError):
            fetch_channel_videos("@netflix")


def test_fetch_channel_videos_invalid_key_raises_auth_error(yt_api_key):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"code": 400, "errors": [{"reason": "keyInvalid"}]}},
        )

    with patch.object(yt_mod.httpx, "Client", _patched_client_factory(handler)):
        with pytest.raises(YouTubeAuthError):
            fetch_channel_videos("@netflix")


def test_fetch_channel_videos_unknown_handle_raises_not_found(yt_api_key):
    def handler(request: httpx.Request) -> httpx.Response:
        # channels.list with no match returns 200 + empty items, not 404.
        # Connector treats that as YouTubeNotFoundError so the admin
        # endpoint can return a clean 404 without inspecting payloads.
        return httpx.Response(200, json={"items": []})

    with patch.object(yt_mod.httpx, "Client", _patched_client_factory(handler)):
        with pytest.raises(YouTubeNotFoundError):
            fetch_channel_videos("@does-not-exist-xyz123")


def test_fetch_channel_videos_requires_api_key(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "youtube_api_key", None)
    with pytest.raises(YouTubeAuthError):
        fetch_channel_videos("@netflix")
