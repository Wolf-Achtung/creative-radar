from uuid import UUID
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.database import get_session
from app.models.entities import Asset, Channel, Post, ReviewStatus, Title
from app.schemas.dto import AssetReviewUpdate
from app.services.storage import resolve_url
from app.services.visual_analysis import analyze_asset_visual

router = APIRouter(prefix="/api/assets", tags=["assets"])
logger = logging.getLogger(__name__)


def _asset_card(asset: Asset, post: Post | None, channel: Channel | None, title: Title | None) -> dict:
    return {
        "id": asset.id,
        "post_id": asset.post_id,
        "title_id": asset.title_id,
        "title_name": title.title_original if title else None,
        "title_local": title.title_local if title else None,
        "franchise": title.franchise if title else None,
        "is_discovery": title is None,
        "asset_type": asset.asset_type,
        "language": asset.language,
        "screenshot_url": asset.screenshot_url,
        "thumbnail_url": asset.thumbnail_url,
        "ocr_text": asset.ocr_text,
        "ai_summary_de": asset.ai_summary_de,
        "ai_summary_en": asset.ai_summary_en,
        "ai_trend_notes": asset.ai_trend_notes,
        "confidence_score": asset.confidence_score,
        "review_status": asset.review_status,
        "curator_note": asset.curator_note,
        "include_in_report": asset.include_in_report,
        "is_highlight": asset.is_highlight,
        "created_at": asset.created_at,
        "visual_analysis_status": asset.visual_analysis_status,
        "visual_source_url": asset.visual_source_url,
        "visual_notes": asset.visual_notes,
        "placement_title_text": asset.placement_title_text,
        "placement_position": asset.placement_position,
        "placement_strength": asset.placement_strength,
        "has_title_placement": asset.has_title_placement,
        "has_kinetic": asset.has_kinetic,
        "kinetic_type": asset.kinetic_type,
        "kinetic_text": asset.kinetic_text,
        "de_us_match_key": asset.de_us_match_key,
        "visual_confidence_score": asset.visual_confidence_score,
        "visual_evidence_url": resolve_url(asset.visual_evidence_url),
        "visual_evidence_key": asset.visual_evidence_url,
        "visual_crop_title_url": asset.visual_crop_title_url,
        "visual_crop_cta_url": asset.visual_crop_cta_url,
        "visual_crop_kinetic_url": asset.visual_crop_kinetic_url,
        "visual_evidence_status": asset.visual_evidence_status,
        "visual_evidence_pack": asset.visual_evidence_pack,
        "post_url": post.post_url if post else None,
        "caption": post.caption if post else None,
        "published_at": post.published_at if post else None,
        "detected_at": post.detected_at if post else None,
        "visible_likes": post.visible_likes if post else None,
        "visible_comments": post.visible_comments if post else None,
        "visible_views": post.visible_views if post else None,
        "visible_shares": post.visible_shares if post else None,
        "visible_bookmarks": post.visible_bookmarks if post else None,
        "duration_seconds": post.duration_seconds if post else None,
        "platform": post.platform if post else None,
        "media_type": post.media_type if post else None,
        "channel_name": channel.name if channel else None,
        "channel_handle": channel.handle if channel else None,
        "channel_market": channel.market if channel else None,
        "channel_platform": channel.platform if channel else None,
        "channel_type": channel.channel_type if channel else None,
        "channel_priority": channel.priority if channel else None,
    }


def _card_for(session: Session, asset: Asset) -> dict:
    post = session.get(Post, asset.post_id)
    channel = session.get(Channel, post.channel_id) if post else None
    title = session.get(Title, asset.title_id) if asset.title_id else None
    return _asset_card(asset, post, channel, title)


@router.get("")
def list_assets(review_status: ReviewStatus | None = None, session: Session = Depends(get_session)):
    statement = select(Asset).order_by(Asset.created_at.desc())
    if review_status is not None:
        statement = statement.where(Asset.review_status == review_status)
    assets = session.exec(statement).all()
    return [_card_for(session, asset) for asset in assets]


@router.get("/{asset_id}")
def get_asset(asset_id: UUID, session: Session = Depends(get_session)):
    asset = session.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return _card_for(session, asset)


@router.patch("/{asset_id}/review")
def update_asset_review(asset_id: UUID, payload: AssetReviewUpdate, session: Session = Depends(get_session)):
    asset = session.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    if payload.title_id is not None:
        title = session.get(Title, payload.title_id)
        if not title:
            raise HTTPException(status_code=404, detail="Title not found")
        asset.title_id = payload.title_id
        asset.de_us_match_key = title.franchise or title.title_original
    asset.review_status = payload.review_status
    asset.include_in_report = payload.include_in_report
    asset.is_highlight = payload.is_highlight or payload.review_status == ReviewStatus.HIGHLIGHT
    asset.curator_note = payload.curator_note
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return _card_for(session, asset)


@router.post("/{asset_id}/analyze-visual")
def analyze_visual(asset_id: UUID, session: Session = Depends(get_session)):
    asset = session.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset = analyze_asset_visual(session, asset)
    return {"status": asset.visual_analysis_status, "asset": _card_for(session, asset), "analysis": {
        "ocr_text": asset.ocr_text,
        "visual_summary_de": asset.visual_notes,
        "title_placement": {"has_title_placement": asset.has_title_placement, "text": asset.placement_title_text, "position": asset.placement_position, "strength": asset.placement_strength},
        "kinetics": {"has_kinetic": asset.has_kinetic, "type": asset.kinetic_type, "text": asset.kinetic_text},
        "confidence": asset.visual_confidence_score,
    }}


@router.post("/analyze-visual-batch")
def analyze_visual_batch(limit: int = 10, only_pending: bool = True, session: Session = Depends(get_session)):
    statuses = ["pending"] if only_pending else ["pending", "error", "no_source", "text_fallback", "fetch_failed"]
    statement = select(Asset).where(Asset.visual_analysis_status.in_(statuses)).order_by(Asset.created_at.desc()).limit(max(1, min(limit, 50)))
    assets = session.exec(statement).all()
    checked = len(assets)
    done = 0
    no_source = 0
    fetch_failed = 0
    text_fallback = 0
    provider_error = 0
    failed = 0
    for asset in assets:
        updated = analyze_asset_visual(session, asset)
        if updated.visual_analysis_status == "done":
            done += 1
        elif updated.visual_analysis_status == "no_source":
            no_source += 1
        elif updated.visual_analysis_status == "fetch_failed":
            fetch_failed += 1
            failed += 1
        elif updated.visual_analysis_status == "text_fallback":
            text_fallback += 1
            failed += 1
        elif updated.visual_analysis_status in {"error", "provider_error"}:
            provider_error += 1
            failed += 1
        elif updated.visual_analysis_status not in {"running", "pending"}:
            failed += 1
    logger.info(
        "visual-batch-summary",
        extra={
            "checked": checked,
            "analyzed": done,
            "no_source": no_source,
            "fetch_failed": fetch_failed,
            "text_fallback": text_fallback,
            "provider_error": provider_error,
            "failed": failed,
        },
    )
    return {"checked": checked, "analyzed": done, "no_source": no_source, "fetch_failed": fetch_failed, "text_fallback": text_fallback, "provider_error": provider_error, "failed": failed}
