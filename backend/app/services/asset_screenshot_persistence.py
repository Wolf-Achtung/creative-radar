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
import time
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.models.entities import Asset
from app.services.screenshot_capture import (
    capture_asset_screenshot,
    capture_asset_screenshot_async,
)

logger = logging.getLogger(__name__)


def _apply_result(asset: Asset, result) -> None:
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


def persist_asset_screenshot(asset: Asset) -> None:
    try:
        result = capture_asset_screenshot(asset)
    except Exception as exc:  # noqa: BLE001 — we want PC-1 skip-and-log here
        logger.warning("capture failed for asset %s: %s", asset.id, exc)
        return
    _apply_result(asset, result)


async def persist_asset_screenshot_async(asset: Asset) -> None:
    """Async sibling — same skip-and-log policy, awaits the AsyncClient."""
    try:
        result = await capture_asset_screenshot_async(asset)
    except Exception as exc:  # noqa: BLE001
        logger.warning("capture failed for asset %s: %s", asset.id, exc)
        return
    _apply_result(asset, result)


# --- Evidence-Backfill (22.08.2026) ------------------------------------------
# Der Scrape-Pfad captured jedes neue Asset sofort (Sprint 5.3.6) — aber
# ein transienter Fehler (CDN-Timeout, kurzer Storage-Schluckauf) liess
# das Asset dauerhaft ohne gespeichertes Bild zurueck, und Instagram-
# CDN-Links verfallen nach 24-48 h. Dieser Backfill holt Captures fuer
# JUNGE Assets ohne Evidence nach, solange die Quelle noch lebt
# (YouTube-Thumbnails verfallen nie, Instagram nur kurz nach Scrape).
#
# Zeit-Budget VOR jedem Asset geprueft — die Lektion aus dem Vision-
# Vorfall vom 10./20.08.: ein Stueckzahl-Deckel allein sagt nichts
# ueber Laufzeit. Was nicht mehr passt, steht ehrlich im Ergebnis.
async def backfill_missing_evidence(
    session: Session,
    *,
    max_assets: int = 300,
    budget_seconds: int = 600,
    max_age_days: int = 14,
) -> dict:
    grenze = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    rows = session.exec(
        select(Asset)
        .where(
            Asset.visual_evidence_url.is_(None),
            Asset.created_at >= grenze,
        )
        .order_by(Asset.created_at.desc())
    ).all()
    start = time.monotonic()
    captured = fehlgeschlagen = skipped_budget = 0
    batch = rows[:max_assets]
    for index, asset in enumerate(batch):
        if time.monotonic() - start > budget_seconds:
            skipped_budget = len(batch) - index
            break
        await persist_asset_screenshot_async(asset)
        if asset.visual_evidence_url:
            session.add(asset)
            captured += 1
            if captured % 50 == 0:
                session.commit()
        else:
            fehlgeschlagen += 1
    session.commit()
    ergebnis = {
        "kandidaten": len(rows),
        "captured": captured,
        "fehlgeschlagen": fehlgeschlagen,
        "skipped_budget": skipped_budget,
        "uebrig": max(len(rows) - max_assets, 0),
        "duration_seconds": round(time.monotonic() - start, 1),
    }
    logger.info("evidence_backfill.complete %s", ergebnis)
    return ergebnis
