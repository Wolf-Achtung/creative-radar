from app.models.entities import Asset, Post, Channel, Title, AssetType, ReviewStatus


def create_placeholder_ai_summary(asset: Asset, post: Post, channel: Channel, title: Title | None) -> dict:
    """Safe v1 placeholder. Replace with OpenAI call after API key and prompts are configured."""
    title_name = title.title_original if title else "nicht eindeutig zugeordnet"
    caption_excerpt = (post.caption or "").strip()[:220]
    summary = (
        f"Treffer auf {channel.name} ({channel.market}). Zugeordneter Titel/Franchise: {title_name}. "
        f"Asset-Typ: {asset.asset_type}. Caption-Auszug: {caption_excerpt or 'keine Caption erfasst'}."
    )
    trend = (
        "Vorläufige Beobachtung: Creative-Material sollte im Review auf Titelplatzierung, "
        "Kinetic-Einsatz, CTA und DE/US-Unterschiede geprüft werden."
    )
    return {
        "ai_summary_de": summary,
        "ai_summary_en": summary,
        "ai_trend_notes": trend,
        "confidence_score": 0.5 if title else 0.25,
        "review_status": ReviewStatus.NEEDS_REVIEW if not title else ReviewStatus.NEW,
    }
