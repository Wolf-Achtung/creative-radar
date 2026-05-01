"""Temporary admin endpoints. WIRD WIEDER ENTFERNT NACH NUTZUNG (Phase 4 W3
Task 3.4 / Cleanup-Commit nach abgeschlossener Vision-Diagnose)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models.entities import Asset

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _verify_token(authorization: str | None) -> None:
    expected = settings.admin_sample_token
    if not expected:
        raise HTTPException(status_code=503, detail="Sample endpoint disabled (ADMIN_SAMPLE_TOKEN not set)")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    if authorization.removeprefix("Bearer ") != expected:
        raise HTTPException(status_code=403, detail="Invalid token")


def _truncate(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    return value[:max_len] + ("..." if len(value) > max_len else "")


@router.get("/sample-vision-outputs")
def sample_vision_outputs(
    limit: int = Query(10, ge=1, le=20),
    authorization: str | None = Header(None),
    session: Session = Depends(get_session),
) -> dict:
    """Liefert eine Stichprobe von Assets mit Vision-Outputs. Read-only.
    Diversifiziert über `visual_analysis_status`, sodass alle Status-Werte
    sichtbar werden (Production: text_fallback / analyzed / no_source / error)."""
    _verify_token(authorization)

    statement = (
        select(Asset)
        .where(Asset.ai_summary_de.is_not(None))
        .order_by(Asset.visual_analysis_status, Asset.id)
        .limit(limit)
    )
    assets = list(session.exec(statement).all())

    return {
        "count": len(assets),
        "samples": [
            {
                "id": str(asset.id),
                "visual_analysis_status": asset.visual_analysis_status,
                "language": asset.language,
                "ai_summary_de": _truncate(asset.ai_summary_de, 500),
                "ocr_text": _truncate(asset.ocr_text, 300),
                "visual_evidence_url": asset.visual_evidence_url,
                "screenshot_url": asset.screenshot_url,
                "thumbnail_url": asset.thumbnail_url,
            }
            for asset in assets
        ],
    }
