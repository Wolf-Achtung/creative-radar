from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import requests

from app.models.entities import Asset


@dataclass
class VisualEvidenceResult:
    status: str
    evidence_url: str | None = None
    source_url: str | None = None
    thumbnail_url: str | None = None
    captured_at: str | None = None


def _candidate_sources(asset: Asset) -> list[str]:
    return [url for url in [asset.screenshot_url, asset.thumbnail_url, asset.visual_source_url] if url]


def capture_asset_screenshot(asset: Asset) -> VisualEvidenceResult:
    sources = _candidate_sources(asset)
    if not sources:
        return VisualEvidenceResult(status="no_source")

    evidence_dir = Path("backend/storage/evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)

    for source in sources:
        try:
            response = requests.get(source, timeout=12)
            if response.status_code >= 400:
                continue
            content_type = (response.headers.get("content-type") or "").lower()
            if not content_type.startswith("image/"):
                continue
            payload = response.content or b""
            if len(payload) < 1024:
                continue
            extension = content_type.split("/")[-1].split(";")[0] or "jpg"
            filename = f"{asset.id}_{uuid4().hex}.{extension}"
            target = evidence_dir / filename
            target.write_bytes(payload)
            captured_at = datetime.now(timezone.utc).isoformat()
            return VisualEvidenceResult(
                status="captured",
                evidence_url=f"/storage/evidence/{filename}",
                source_url=source,
                thumbnail_url=asset.thumbnail_url,
                captured_at=captured_at,
            )
        except Exception:
            continue

    return VisualEvidenceResult(status="fetch_failed")
