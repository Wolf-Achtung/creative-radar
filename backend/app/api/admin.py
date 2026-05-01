"""Temporary admin endpoints. WIRD WIEDER ENTFERNT NACH NUTZUNG (Phase 4 W3
follow-up: one-shot backfill of legacy evidence URLs into R2 object keys.
Cleanup commit follows after Wolf confirms the run was successful)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlmodel import Session

from app.config import settings
from app.database import get_session
from scripts import backfill_evidence

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _verify_token(authorization: str | None) -> None:
    expected = settings.admin_backfill_token
    if not expected:
        raise HTTPException(status_code=503, detail="Backfill endpoint disabled (ADMIN_BACKFILL_TOKEN not set)")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    if authorization.removeprefix("Bearer ") != expected:
        raise HTTPException(status_code=403, detail="Invalid token")


def _summary_line(stats: dict) -> str:
    """One-liner for the daily report. Phrasing tuned to be quotable."""
    total = stats["total"]
    migrated = stats["migrated"]
    skipped = stats["skipped"]
    failed = stats["failed"]
    if total == 0:
        return "No backfill candidates found (no assets with screenshot or thumbnail URL)."
    parts = [f"{migrated} of {total} assets migrated successfully."]
    if failed:
        parts.append(
            f"{failed} failed (likely expired/blocked external URLs — TikTok/Instagram CDN hotlink protection)."
        )
    if skipped:
        parts.append(f"{skipped} skipped (already migrated).")
    return " ".join(parts)


@router.post("/run-backfill")
def run_backfill(
    authorization: str | None = Header(None),
    session: Session = Depends(get_session),
) -> dict:
    """One-shot evidence backfill. Synchronous: caller waits for completion.
    Realistic runtime for ~20 production assets is 30-90s. The reverse-proxy
    timeout is 5 min — well above the expected upper bound for the W3
    follow-up run."""
    _verify_token(authorization)
    stats = backfill_evidence.run(session)
    return {
        "total": stats["total"],
        "migrated": stats["migrated"],
        "skipped": stats["skipped"],
        "failed": stats["failed"],
        "failed_ids": stats["failed_ids"],
        "failed_reasons": stats["failed_reasons"],
        "summary": _summary_line(stats),
    }
