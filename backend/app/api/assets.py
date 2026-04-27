from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.database import get_session
from app.models.entities import Asset, ReviewStatus
from app.schemas.dto import AssetReviewUpdate

router = APIRouter(prefix="/api/assets", tags=["assets"])


@router.get("")
def list_assets(review_status: ReviewStatus | None = None, session: Session = Depends(get_session)):
    statement = select(Asset).order_by(Asset.created_at.desc())
    if review_status is not None:
        statement = statement.where(Asset.review_status == review_status)
    return session.exec(statement).all()


@router.get("/{asset_id}")
def get_asset(asset_id: UUID, session: Session = Depends(get_session)):
    asset = session.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.patch("/{asset_id}/review")
def update_asset_review(asset_id: UUID, payload: AssetReviewUpdate, session: Session = Depends(get_session)):
    asset = session.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset.review_status = payload.review_status
    asset.include_in_report = payload.include_in_report
    asset.is_highlight = payload.is_highlight or payload.review_status == ReviewStatus.HIGHLIGHT
    asset.curator_note = payload.curator_note
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset
