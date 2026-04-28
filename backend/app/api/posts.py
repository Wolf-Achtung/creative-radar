from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.database import get_session
from app.models.entities import Asset, Channel, Post, Title, Market, Priority
from app.services.title_candidates import create_candidate_from_asset
from app.schemas.dto import ManualPostImport, AnalyzeInstagramLinkRequest
from app.services.ai_asset_analyzer import create_placeholder_ai_summary
from app.services.creative_ai import analyze_creative_text
from app.services.link_preview import fetch_public_preview, infer_instagram_handle
from app.services.whitelist_matcher import find_best_title_match

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


def _get_or_create_auto_channel(session: Session) -> Channel:
    channel = session.exec(select(Channel).where(Channel.handle == "auto_import_instagram")).first()
    if channel:
        return channel
    channel = Channel(
        name="Auto Import · Instagram",
        platform="instagram",
        url="https://www.instagram.com/",
        handle="auto_import_instagram",
        market=Market.UNKNOWN,
        channel_type="Auto Import",
        priority=Priority.C,
        active=True,
        mvp=True,
        notes="Automatisch angelegt für Link-Analysen ohne vorausgewählten Kanal.",
    )
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


def _find_channel_for_url(session: Session, post_url: str, channel_id: UUID | None) -> Channel:
    if channel_id:
        selected = session.get(Channel, channel_id)
        if selected:
            return selected
    handle = infer_instagram_handle(post_url)
    if handle:
        matched = session.exec(select(Channel).where(Channel.handle == handle)).first()
        if matched:
            return matched
    return _get_or_create_auto_channel(session)


def _match_title(session: Session, title_id: UUID | None, caption: str | None) -> tuple[Title | None, float]:
    if title_id:
        title = session.get(Title, title_id)
        return title, 1.0 if title else 0.0
    match = find_best_title_match(session, caption or "")
    if match.title and match.source == "exact":
        return match.title, match.confidence
    return None, match.confidence


@router.post("/manual-import")
def manual_import(payload: ManualPostImport, session: Session = Depends(get_session)):
    channel = session.get(Channel, payload.channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    existing = session.exec(select(Post).where(Post.post_url == payload.post_url)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Post already exists")

    title, confidence = _match_title(session, payload.title_id, payload.caption)

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
        title_id=title.id if title else None,
        asset_type=payload.asset_type,
        screenshot_url=payload.screenshot_url,
        ocr_text=payload.ocr_text,
    )
    ai = create_placeholder_ai_summary(asset, post, channel, title)
    for key, value in ai.items():
        setattr(asset, key, value)
    session.add(asset)
    session.commit()
    session.refresh(asset)
    if not title and confidence < 0.95:
        create_candidate_from_asset(session, asset.id)
    return {"post": post, "asset": asset}


@router.post("/analyze-instagram-link")
async def analyze_instagram_link(payload: AnalyzeInstagramLinkRequest, session: Session = Depends(get_session)):
    existing = session.exec(select(Post).where(Post.post_url == payload.post_url)).first()
    if existing:
        asset = session.exec(select(Asset).where(Asset.post_id == existing.id)).first()
        return {"post": existing, "asset": asset, "already_exists": True}

    preview = await fetch_public_preview(payload.post_url)
    caption = payload.caption_hint or preview.get("caption") or preview.get("title") or ""
    channel = _find_channel_for_url(session, payload.post_url, payload.channel_id)
    title, confidence = _match_title(session, payload.title_id, caption)

    post = Post(
        channel_id=channel.id,
        post_url=payload.post_url,
        caption=caption,
        media_type="instagram",
        raw_payload=preview,
    )
    session.add(post)
    session.commit()
    session.refresh(post)

    ai = analyze_creative_text(
        post_url=payload.post_url,
        channel_name=channel.name,
        market=str(channel.market),
        title_name=title.title_original if title else None,
        caption=caption,
        ocr_text=None,
        asset_type_hint=payload.asset_type_hint,
    )

    asset = Asset(
        post_id=post.id,
        title_id=title.id if title else None,
        asset_type=ai.get("asset_type", payload.asset_type_hint),
        screenshot_url=preview.get("image_url"),
        thumbnail_url=preview.get("image_url"),
        ocr_text=None,
    )
    for key, value in ai.items():
        if hasattr(asset, key):
            setattr(asset, key, value)
    session.add(asset)
    session.commit()
    session.refresh(asset)
    if not title and confidence < 0.95:
        create_candidate_from_asset(session, asset.id)
    return {"post": post, "asset": asset, "preview": preview, "already_exists": False}
