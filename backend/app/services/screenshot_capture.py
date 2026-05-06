from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from app.models.entities import Asset
from app.services.storage import get_storage


@dataclass
class VisualEvidenceResult:
    status: str
    evidence_url: str | None = None  # object key, e.g. "evidence/asset_123_uuid.jpg"
    source_url: str | None = None
    thumbnail_url: str | None = None
    captured_at: str | None = None


_YOUTUBE_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/watch\?(?:[^#]*&)?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/embed/)"
    r"([A-Za-z0-9_-]{11})"
)

_YOUTUBE_THUMBNAIL_QUALITIES = ("maxresdefault", "sddefault", "hqdefault", "mqdefault")


def _youtube_thumbnail_candidates(url: str | None) -> list[str]:
    """Derive ordered i.ytimg.com thumbnail URLs from a YouTube watch/shorts/shortlink URL.

    Returns empty list if the URL is None, empty, or not a recognizable YouTube URL.
    The downstream httpx loop in capture_asset_screenshot tries them in order, so
    higher-resolution variants that don't exist (common on older or short-form videos)
    fall through to lower-resolution ones automatically.
    """
    if not url:
        return []
    match = _YOUTUBE_VIDEO_ID_RE.search(url)
    if not match:
        return []
    video_id = match.group(1)
    return [f"https://i.ytimg.com/vi/{video_id}/{q}.jpg" for q in _YOUTUBE_THUMBNAIL_QUALITIES]


def _candidate_sources(asset: Asset) -> list[str]:
    sources = [url for url in [asset.screenshot_url, asset.thumbnail_url, asset.visual_source_url] if url]
    asset_url = getattr(asset, "asset_url", None)
    if asset_url:
        sources.append(asset_url)
        sources.extend(_youtube_thumbnail_candidates(asset_url))
    return sources


def _safe_extension(content_type: str) -> str:
    extension = content_type.split("/")[-1].split(";")[0].strip().lower() or "jpg"
    if extension in {"jpeg", "pjpeg"}:
        return "jpg"
    if extension not in {"jpg", "png", "webp", "gif"}:
        return "jpg"
    return extension


def _process_response(
    asset: Asset,
    source: str,
    response: httpx.Response,
) -> VisualEvidenceResult | None:
    """Validate one HTTP response; on success upload to storage and return
    a captured result. Returns None to signal "skip this source, try next"
    (HTTP 4xx/5xx, non-image content-type, payload too small). Returns a
    ``fetch_failed`` result when storage.put raises — that one is terminal
    because the bytes were already downloaded."""
    if response.status_code >= 400:
        return None
    content_type = (response.headers.get("content-type") or "").lower().split(";")[0].strip() or "image/jpeg"
    if not content_type.startswith("image/"):
        return None
    payload = response.content or b""
    if len(payload) < 1024:
        return None
    storage = get_storage()
    key = f"evidence/{asset.id}_{uuid4().hex}.{_safe_extension(content_type)}"
    try:
        storage.put(key, payload, content_type)
    except Exception:
        return VisualEvidenceResult(status="fetch_failed")
    captured_at = datetime.now(timezone.utc).isoformat()
    return VisualEvidenceResult(
        status="captured",
        evidence_url=key,
        source_url=source,
        thumbnail_url=asset.thumbnail_url,
        captured_at=captured_at,
    )


def capture_asset_screenshot(asset: Asset) -> VisualEvidenceResult:
    sources = _candidate_sources(asset)
    if not sources:
        return VisualEvidenceResult(status="no_source")

    with httpx.Client(timeout=12, follow_redirects=True) as client:
        for source in sources:
            try:
                response = client.get(source)
            except Exception:
                continue
            result = _process_response(asset, source, response)
            if result is not None:
                return result

    return VisualEvidenceResult(status="fetch_failed")


async def capture_asset_screenshot_async(asset: Asset) -> VisualEvidenceResult:
    """Async sibling of ``capture_asset_screenshot`` (Block 2 / async refactor).

    Identical fallback ladder, identical storage write. The only behavioural
    difference is that the per-source GET runs on ``httpx.AsyncClient`` so
    the call can be awaited concurrently with siblings under a Semaphore.

    ``storage.put`` (S3 / R2) is still synchronous, but it runs after the
    GET — so under per-call concurrency=10 it doesn't bottleneck the event
    loop more than the sync version did under sequential calls."""
    sources = _candidate_sources(asset)
    if not sources:
        return VisualEvidenceResult(status="no_source")

    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        for source in sources:
            try:
                response = await client.get(source)
            except Exception:
                continue
            result = _process_response(asset, source, response)
            if result is not None:
                return result

    return VisualEvidenceResult(status="fetch_failed")
