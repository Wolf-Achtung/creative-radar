"""Sprint 5.3.6 — sync-path screenshot persistence.

Each of the four asset-creating sync paths (Apify IG/TikTok monitor,
manual frontend import, Instagram-link analyzer, Sprint-5.3.1 analyzer)
calls ``persist_asset_screenshot`` after constructing the Asset and
before ``session.commit()`` so the storage write and the DB row land
atomically.

Failure policy (PC-1, skip-and-log): any failure — capture returns a
non-captured status, or the call raises unexpectedly — leaves
``asset.visual_evidence_url`` as None and logs a WARN. The caller still
commits the Asset row; the sync stats still count it as ``created``.
"""
from __future__ import annotations

import logging

from app.models.entities import Asset
from app.services.screenshot_capture import capture_asset_screenshot

logger = logging.getLogger(__name__)


def persist_asset_screenshot(asset: Asset) -> None:
    try:
        result = capture_asset_screenshot(asset)
    except Exception as exc:  # noqa: BLE001 — we want PC-1 skip-and-log here
        logger.warning("capture failed for asset %s: %s", asset.id, exc)
        return

    asset.visual_evidence_status = result.status
    if result.evidence_url:
        asset.visual_evidence_url = result.evidence_url
        if result.source_url:
            asset.visual_source_url = result.source_url
        return

    logger.warning(
        "capture failed for asset %s: status=%s",
        asset.id,
        result.status,
    )
