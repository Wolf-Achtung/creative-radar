from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models.entities import Asset, Channel, Post
from app.schemas.dto import ApifyMonitorRequest, TikTokMonitorRequest
from app.services.apify_connector import (
    is_apify_configured,
    is_tiktok_configured,
    normalize_public_item,
    normalize_tiktok_item,
    run_public_channel_monitor,
    run_tiktok_profile_monitor,
)
from app.services.creative_ai import analyze_creative_text
from app.services.whitelist_matcher import find_title_matches

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


def _handle_from_url_or_value(value: str | None) -> str:
    clean = (value or "").strip().rstrip("/")
    if not clean:
        return ""
    if "tiktok.com/@" in clean:
        return clean.split("tiktok.com/@", 1)[1].split("/", 1)[0].lstrip("@")
    if "instagram.com/" in clean:
        return clean.split("instagram.com/", 1)[1].split("/", 1)[0].lstrip("@")
    return clean.lstrip("@")


def _match_channel(channels: list[Channel], owner: str | None, fallback_index: int = 0) -> Channel:
    owner_clean = _handle_from_url_or_value(owner).lower()
    for channel in channels:
        handle = _handle_from_url_or_value(channel.handle or channel.url).lower()
        if handle and owner_clean and handle == owner_clean:
            return channel
    return channels[min(fallback_index, len(channels) - 1)]


def _create_asset_from_item(
    *,
    session: Session,
    item: dict,
    channel: Channel,
    platform: str,
    only_whitelist_matches: bool,
) -> tuple[Asset | None, str]:
    post_url = item.get("post_url")
    if not post_url:
        return None, "no_url"
    existing = session.exec(select(Post).where(Post.post_url == post_url)).first()
    if existing:
        return None, "existing"

    caption = item.get("caption") or ""
    matches = find_title_matches(session, caption)
    title = matches[0] if matches else None
    if only_whitelist_matches and not title:
        return None, "no_match"

    post = Post(
        channel_id=channel.id,
        platform=platform,
        post_url=post_url,
        external_id=item.get("external_id"),
        caption=caption,
        published_at=item.get("published_at"),
        media_type=platform,
        raw_payload=item.get("raw") or {},
        visible_likes=item.get("visible_likes"),
        visible_comments=item.get("visible_comments"),
        visible_views=item.get("visible_views"),
        visible_shares=item.get("visible_shares"),
        visible_bookmarks=item.get("visible_bookmarks"),
        duration_seconds=item.get("duration_seconds"),
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
    return asset, "created"


@router.post("/apify-instagram")
async def apify_instagram_monitor(payload: ApifyMonitorRequest, session: Session = Depends(get_session)):
    if not is_apify_configured():
        raise HTTPException(status_code=400, detail="Apify ist nicht konfiguriert. Bitte APIFY_API_TOKEN und APIFY_INSTAGRAM_ACTOR_ID in Railway setzen.")

    statement = select(Channel).where(Channel.active == True, Channel.mvp == True, Channel.platform == "instagram")  # noqa: E712
    if payload.channel_ids:
        statement = statement.where(Channel.id.in_(payload.channel_ids))
    channels = list(session.exec(statement).all())[: max(1, payload.max_channels)]
    if not channels:
        raise HTTPException(status_code=400, detail="Keine aktiven Instagram-Channels gefunden. Bitte erst Kanalliste importieren.")

    channel_urls = [channel.url for channel in channels]
    raw_items = await run_public_channel_monitor(channel_urls, payload.results_limit_per_channel)

    created = skipped_existing = skipped_no_match = skipped_other = 0
    assets = []

    for index, raw_item in enumerate(raw_items):
        item = normalize_public_item(raw_item)
        channel = _match_channel(channels, item.get("owner_username"), index)
        asset, status = _create_asset_from_item(
            session=session,
            item=item,
            channel=channel,
            platform="instagram",
            only_whitelist_matches=payload.only_whitelist_matches,
        )
        if status == "created" and asset:
            created += 1
            assets.append(asset)
        elif status == "existing":
            skipped_existing += 1
        elif status == "no_match":
            skipped_no_match += 1
        else:
            skipped_other += 1

    return {
        "platform": "instagram",
        "channels_checked": len(channels),
        "raw_items": len(raw_items),
        "created_assets": created,
        "skipped_existing": skipped_existing,
        "skipped_no_whitelist_match": skipped_no_match,
        "skipped_other": skipped_other,
        "apify_actor_id": settings.apify_instagram_actor_id,
        "assets": assets,
    }


@router.post("/apify-tiktok")
async def apify_tiktok_monitor(payload: TikTokMonitorRequest, session: Session = Depends(get_session)):
    if not is_tiktok_configured():
        raise HTTPException(status_code=400, detail="TikTok-Apify ist nicht konfiguriert. Bitte APIFY_API_TOKEN und APIFY_TIKTOK_ACTOR_ID in Railway setzen.")

    statement = select(Channel).where(Channel.active == True, Channel.mvp == True, Channel.platform == "tiktok")  # noqa: E712
    if payload.channel_ids:
        statement = statement.where(Channel.id.in_(payload.channel_ids))
    channels = list(session.exec(statement).all())[: max(1, payload.max_channels)]

    usernames = [_handle_from_url_or_value(item) for item in payload.usernames if item]
    if not usernames:
        usernames = [_handle_from_url_or_value(channel.handle or channel.url) for channel in channels]
    usernames = [item for item in usernames if item]

    if not usernames:
        raise HTTPException(status_code=400, detail="Keine TikTok-Usernames gefunden. Bitte TikTok-Channels anlegen oder Usernames eingeben.")

    if not channels:
        # Temporary auto-channel for direct username test runs.
        first_username = usernames[0]
        channel = session.exec(select(Channel).where(Channel.platform == "tiktok", Channel.handle == first_username)).first()
        if not channel:
            channel = Channel(
                name=f"TikTok @{first_username}",
                platform="tiktok",
                url=f"https://www.tiktok.com/@{first_username}",
                handle=first_username,
                active=True,
                mvp=True,
            )
            session.add(channel)
            session.commit()
            session.refresh(channel)
        channels = [channel]

    raw_items = await run_tiktok_profile_monitor(usernames[: max(1, payload.max_channels)], payload.results_limit_per_channel)

    created = skipped_existing = skipped_no_match = skipped_other = 0
    assets = []

    for index, raw_item in enumerate(raw_items):
        item = normalize_tiktok_item(raw_item)
        channel = _match_channel(channels, item.get("owner_username"), index)
        asset, status = _create_asset_from_item(
            session=session,
            item=item,
            channel=channel,
            platform="tiktok",
            only_whitelist_matches=payload.only_whitelist_matches,
        )
        if status == "created" and asset:
            created += 1
            assets.append(asset)
        elif status == "existing":
            skipped_existing += 1
        elif status == "no_match":
            skipped_no_match += 1
        else:
            skipped_other += 1

    return {
        "platform": "tiktok",
        "channels_checked": len(usernames),
        "raw_items": len(raw_items),
        "created_assets": created,
        "skipped_existing": skipped_existing,
        "skipped_no_whitelist_match": skipped_no_match,
        "skipped_other": skipped_other,
        "apify_actor_id": settings.apify_tiktok_actor_id,
        "assets": assets,
    }
