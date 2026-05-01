"""Idempotent evidence backfill: re-capture each asset's screenshot/thumbnail
into the configured storage backend (R2 in production), populate the
visual_evidence_url field with the resulting object key.

Not run by pytest — touches the real DB. Wolf executes manually after
F0.1 deploy:

    cd backend && python -m scripts.backfill_evidence

The script:
- iterates all assets that have a screenshot_url OR thumbnail_url
- skips assets whose visual_evidence_url is already a non-http value
  (object key or legacy /storage/ path -> nothing to do)
- captures via screenshot_capture.capture_asset_screenshot, which uses
  the active storage adapter (LocalFileStorage / S3Storage)
- per-asset failures are logged and counted; they never abort the run
- processes assets in batches of 50 with progress output
- prints a final summary line

Exit code: 0 always (per-asset errors are reported, not raised).
"""

from __future__ import annotations

import sys
from typing import Iterable, Iterator, TypeVar

from sqlmodel import Session, select

from app.database import engine
from app.models.entities import Asset
from app.services.screenshot_capture import capture_asset_screenshot

BATCH_SIZE = 50

T = TypeVar("T")


def _batched(items: Iterable[T], size: int) -> Iterator[list[T]]:
    batch: list[T] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _is_already_migrated(asset: Asset) -> bool:
    """Wolf's rule: any non-http value in visual_evidence_url is treated as
    already-migrated. Covers both new object keys ('evidence/...') and legacy
    '/storage/...' paths whose backing files won't survive redeploy anyway —
    those will be re-captured by the next visual_analysis run."""
    value = asset.visual_evidence_url
    return bool(value) and not value.startswith(("http://", "https://"))


def run(session: Session) -> dict:
    statement = select(Asset).where(
        (Asset.screenshot_url.is_not(None)) | (Asset.thumbnail_url.is_not(None))
    ).order_by(Asset.created_at.desc())
    assets = list(session.exec(statement).all())

    total = len(assets)
    migrated = 0
    skipped = 0
    failed = 0
    failed_ids: list[str] = []
    failed_reasons: dict[str, str] = {}

    print(f"Backfill candidates: {total} assets (screenshot or thumbnail present)")
    if not total:
        print("Nothing to do.")
        return {
            "total": 0, "migrated": 0, "skipped": 0, "failed": 0,
            "failed_ids": [], "failed_reasons": {},
        }

    batch_index = 0
    for batch in _batched(assets, BATCH_SIZE):
        batch_index += 1
        print(f"\n--- Batch {batch_index} ({len(batch)} assets) ---")
        for asset in batch:
            asset_id = str(asset.id)
            if _is_already_migrated(asset):
                print(f"SKIP {asset_id}: already migrated ({asset.visual_evidence_url})")
                skipped += 1
                continue
            try:
                result = capture_asset_screenshot(asset)
            except Exception as exc:  # noqa: BLE001
                reason = f"{type(exc).__name__}: {exc}"
                print(f"FAIL {asset_id}: {reason}")
                failed += 1
                failed_ids.append(asset_id)
                failed_reasons[asset_id] = reason
                continue

            if result.status == "captured" and result.evidence_url:
                asset.visual_evidence_url = result.evidence_url
                if result.source_url:
                    asset.visual_source_url = result.source_url
                asset.visual_evidence_status = "captured"
                try:
                    session.add(asset)
                    session.commit()
                    session.refresh(asset)
                    print(f"OK   {asset_id}: {result.evidence_url}")
                    migrated += 1
                except Exception as exc:  # noqa: BLE001
                    session.rollback()
                    reason = f"db commit failed: {type(exc).__name__}: {exc}"
                    print(f"FAIL {asset_id}: {reason}")
                    failed += 1
                    failed_ids.append(asset_id)
                    failed_reasons[asset_id] = reason
            else:
                reason = f"capture status={result.status}"
                print(f"FAIL {asset_id}: {reason}")
                failed += 1
                failed_ids.append(asset_id)
                failed_reasons[asset_id] = reason

    summary = {
        "total": total,
        "migrated": migrated,
        "skipped": skipped,
        "failed": failed,
        "failed_ids": failed_ids,
        "failed_reasons": failed_reasons,
    }
    print(
        f"\n=== Backfill summary ===\n"
        f"  total considered: {summary['total']}\n"
        f"  migrated:         {summary['migrated']}\n"
        f"  skipped:          {summary['skipped']}\n"
        f"  failed:           {summary['failed']}"
    )
    return summary


def main() -> int:
    with Session(engine) as session:
        run(session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
