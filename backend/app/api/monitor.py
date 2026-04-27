from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models.entities import Asset, Channel, Post
from app.schemas.dto import ApifyMonitorRequest
from app.services.apify_connector import is_apify_configured, normalize_public_item, run_public_channel_monitor
from app.services.creative_ai import analyze_creative_text
from app.services.whitelist_matcher import find_title_matches

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


@router.post("/apify-instagram")
async def apify_instagram_monitor(payload: ApifyMonitorRequest, session: Session = Depends(get_session)):
    if not is_apify_configured():
        raise HTTPException(status_code=400, detail="Apify ist nicht konfiguriert. Bitte APIFY_API_TOKEN und APIFY_INSTAGRAM_ACTOR_ID in Railway setzen.")

    statement = select(Channel).where(Channel.active == True, Channel.mvp == True)  # noqa: E712
    if payload.channel_ids:
        statement = statement.where(Channel.id.in_(payload.channel_ids))
    channels = list(session.exec(statement).all())[: max(1, payload.max_channels)]
    if not channels:
        raise HTTPException(status_code=400, detail="Keine aktiven Channels gefunden. Bitte erst Kanalliste importieren.")

    channel_by_handle = {channel.handle: channel for channel in channels if channel.handle}
    channel_urls = [channel.url for channel in channels]
    raw_items = await run_public_channel_monitor(channel_urls, payload.results_limit_per_channel)

    created = skipped_existing = skipped_no_match = 0
    assets = []

    for raw_item in raw_items:
        item = normalize_public_item(raw_item)
        post_url = item.get("post_url")
        if not post_url:
            continue
        existing = session.exec(select(Post).where(Post.post_url == post_url)).first()
        if existing:
            skipped_existing += 1
            continue

        owner = item.get("owner_username")
        channel = channel_by_handle.get(owner) if owner else None
        if not channel:
            channel = channels[0]

        caption = item.get("caption") or ""
        matches = find_title_matches(session, caption)
        title = matches[0] if matches else None
        if payload.only_whitelist_matches and not title:
            skipped_no_match += 1
            continue

        post = Post(
            channel_id=channel.id,
            post_url=post_url,
            caption=caption,
            published_at=item.get("published_at"),
            media_type="instagram",
            raw_payload=item.get("raw") or {},
            visible_likes=item.get("visible_likes"),
            visible_comments=item.get("visible_comments"),
            visible_views=item.get("visible_views"),
        )
        session.add(post)
        session.commit()
        session.refresh(post)

        ai = analyze_creative_text(
            post_url=post_url,
            channel_name=channel.name,
            market=str(channel.market),
            title_name=title.title_original if title else None,
            caption=caption,
            ocr_text=None,
        )

        asset = Asset(
            post_id=post.id,
            title_id=title.id if title else None,
            asset_type=ai.get("asset_type"),
            screenshot_url=item.get("image_url"),
            thumbnail_url=item.get("image_url"),
        )
        for key, value in ai.items():
            if hasattr(asset, key):
                setattr(asset, key, value)
        session.add(asset)
        session.commit()
        session.refresh(asset)
        assets.append(asset)
        created += 1

    return {
        "channels_checked": len(channels),
        "raw_items": len(raw_items),
        "created_assets": created,
        "skipped_existing": skipped_existing,
        "skipped_no_whitelist_match": skipped_no_match,
        "apify_actor_id": settings.apify_instagram_actor_id,
        "assets": assets,
    }
