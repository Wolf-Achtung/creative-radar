from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.database import get_session
from app.models.entities import Asset, Channel, Post, Title
from app.schemas.dto import ManualPostImport
from app.services.ai_asset_analyzer import create_placeholder_ai_summary
from app.services.whitelist_matcher import find_title_matches

router = APIRouter(prefix="/api/posts", tags=["posts"])


@router.get("")
def list_posts(session: Session = Depends(get_session)):
    return session.exec(select(Post).order_by(Post.detected_at.desc())).all()


@router.get("/{post_id}")
def get_post(post_id: UUID, session: Session = Depends(get_session)):
    post = session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@router.post("/manual-import")
def manual_import(payload: ManualPostImport, session: Session = Depends(get_session)):
    channel = session.get(Channel, payload.channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    existing = session.exec(select(Post).where(Post.post_url == payload.post_url)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Post already exists")

    title_id = payload.title_id
    if not title_id:
        matches = find_title_matches(session, payload.caption or "")
        title_id = matches[0].id if matches else None

    post = Post(
        channel_id=payload.channel_id,
        post_url=payload.post_url,
        published_at=payload.published_at,
        caption=payload.caption,
        media_type=payload.media_type,
    )
    session.add(post)
    session.commit()
    session.refresh(post)

    asset = Asset(
        post_id=post.id,
        title_id=title_id,
        asset_type=payload.asset_type,
        screenshot_url=payload.screenshot_url,
        ocr_text=payload.ocr_text,
    )
    title = session.get(Title, title_id) if title_id else None
    ai = create_placeholder_ai_summary(asset, post, channel, title)
    for key, value in ai.items():
        setattr(asset, key, value)
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return {"post": post, "asset": asset}
