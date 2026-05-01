from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any

from sqlmodel import Session, select

from app.models.entities import Asset, Channel, Post, ReviewStatus, Title


def _score(asset: Asset, post: Post | None) -> int:
    visible = 0
    if post:
        visible += int(post.visible_views or 0)
        visible += int(post.visible_likes or 0) * 5
        visible += int(post.visible_comments or 0) * 10
    review_bonus = 0
    if asset.review_status == ReviewStatus.HIGHLIGHT or asset.is_highlight:
        review_bonus += 1000
    if asset.review_status == ReviewStatus.APPROVED or asset.include_in_report:
        review_bonus += 350
    confidence = int(((asset.confidence_score or 0) + (asset.visual_confidence_score or 0)) * 50)
    placement_bonus = 120 if asset.has_title_placement else 0
    kinetic_bonus = 90 if asset.has_kinetic else 0
    return visible + review_bonus + confidence + placement_bonus + kinetic_bonus


def _asset_payload(session: Session, asset: Asset) -> dict[str, Any]:
    post = session.get(Post, asset.post_id)
    channel = session.get(Channel, post.channel_id) if post else None
    title = session.get(Title, asset.title_id) if asset.title_id else None
    return {
        "id": str(asset.id),
        "score": _score(asset, post),
        "title": title.title_original if title else asset.placement_title_text or "Discovery / kein Whitelist-Match",
        "channel": channel.name if channel else "Unbekannt",
        "market": channel.market if channel else "UNKNOWN",
        "asset_type": asset.asset_type,
        "review_status": asset.review_status,
        "is_highlight": asset.is_highlight,
        "post_url": post.post_url if post else None,
        "summary": asset.ai_summary_de,
        "trend": asset.ai_trend_notes,
        "visible_views": post.visible_views if post else None,
        "visible_likes": post.visible_likes if post else None,
        "visible_comments": post.visible_comments if post else None,
        "has_title_placement": asset.has_title_placement,
        "placement_title_text": asset.placement_title_text,
        "placement_position": asset.placement_position,
        "placement_strength": asset.placement_strength,
        "has_kinetic": asset.has_kinetic,
        "kinetic_type": asset.kinetic_type,
        "de_us_match_key": asset.de_us_match_key,
        "visual_analysis_status": asset.visual_analysis_status,
    }


def build_overview(session: Session, week_start: date | None = None, week_end: date | None = None) -> dict[str, Any]:
    assets = list(session.exec(select(Asset).order_by(Asset.created_at.desc())).all())
    if week_start or week_end:
        filtered = []
        for asset in assets:
            post = session.get(Post, asset.post_id)
            stamp = post.detected_at.date() if post and post.detected_at else asset.created_at.date()
            if week_start and stamp < week_start:
                continue
            if week_end and stamp > week_end:
                continue
            filtered.append(asset)
        assets = filtered

    cards = [_asset_payload(session, asset) for asset in assets]
    ranked = sorted(cards, key=lambda item: item["score"], reverse=True)

    by_channel: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_title_market: dict[str, dict[str, int]] = defaultdict(lambda: {"DE": 0, "US": 0, "INT": 0, "UNKNOWN": 0})
    by_match_key_market: dict[str, dict[str, Any]] = defaultdict(lambda: {"DE": 0, "US": 0, "INT": 0, "UNKNOWN": 0, "titles": set()})
    type_counter = Counter()
    market_counter = Counter()
    status_counter = Counter()
    placement_counter = Counter()
    visual_status_counter = Counter()
    discovery = 0
    missing_previews = 0
    title_placements = 0
    kinetic_assets = 0
    visual_analyzed = 0

    for asset in assets:
        post = session.get(Post, asset.post_id)
        channel = session.get(Channel, post.channel_id) if post else None
        title = session.get(Title, asset.title_id) if asset.title_id else None
        key_title = title.title_original if title else asset.placement_title_text or "Discovery / kein Whitelist-Match"
        market = str(channel.market.value if channel and hasattr(channel.market, "value") else channel.market if channel else "UNKNOWN")
        card = _asset_payload(session, asset)
        by_channel[card["channel"]].append(card)
        by_title_market[key_title][market if market in by_title_market[key_title] else "UNKNOWN"] += 1
        match_key = asset.de_us_match_key or key_title
        if match_key:
            by_match_key_market[match_key][market if market in by_match_key_market[match_key] else "UNKNOWN"] += 1
            by_match_key_market[match_key]["titles"].add(key_title)
        type_counter[str(asset.asset_type.value if hasattr(asset.asset_type, "value") else asset.asset_type)] += 1
        market_counter[market] += 1
        status_counter[str(asset.review_status.value if hasattr(asset.review_status, "value") else asset.review_status)] += 1
        placement_counter[str(asset.placement_strength or "unknown")] += 1
        visual_status_counter[str(asset.visual_analysis_status or "pending")] += 1
        if not title:
            discovery += 1
        if not (asset.thumbnail_url or asset.screenshot_url):
            missing_previews += 1
        if asset.has_title_placement:
            title_placements += 1
        if asset.has_kinetic:
            kinetic_assets += 1
        if asset.visual_analysis_status in {"done", "text_fallback"}:
            visual_analyzed += 1

    channel_rankings = []
    for channel, items in by_channel.items():
        channel_rankings.append({
            "channel": channel,
            "count": len(items),
            "top_assets": sorted(items, key=lambda item: item["score"], reverse=True)[:5],
        })
    channel_rankings.sort(key=lambda item: item["count"], reverse=True)

    de_us_comparison = []
    for title, markets in by_title_market.items():
        if title == "Discovery / kein Whitelist-Match":
            continue
        de_us_comparison.append({
            "title": title,
            "de_count": markets.get("DE", 0),
            "us_count": markets.get("US", 0),
            "int_count": markets.get("INT", 0),
            "gap": abs(markets.get("DE", 0) - markets.get("US", 0)),
        })
    de_us_comparison.sort(key=lambda item: item["de_count"] + item["us_count"] + item["int_count"], reverse=True)

    placement_comparison = []
    for match_key, markets in by_match_key_market.items():
        de_count = markets.get("DE", 0)
        us_count = markets.get("US", 0)
        int_count = markets.get("INT", 0)
        if de_count or us_count or int_count:
            placement_comparison.append({
                "match_key": match_key,
                "titles": sorted(markets["titles"]),
                "de_count": de_count,
                "us_count": us_count,
                "int_count": int_count,
                "gap": abs(de_count - us_count),
            })
    placement_comparison.sort(key=lambda item: item["de_count"] + item["us_count"] + item["int_count"], reverse=True)

    recommendations = []
    if discovery:
        recommendations.append(f"{discovery} Discovery-Treffer prüfen und daraus Whitelist-Titel/Keywords ableiten.")
    if missing_previews:
        recommendations.append(f"{missing_previews} Assets haben kein Preview-Bild. Screenshot-Service/Apify-Media-Mapping weiter priorisieren.")
    if visual_analyzed < len(assets):
        recommendations.append(f"{len(assets) - visual_analyzed} Assets benötigen noch Visual/OCR-Analyse.")
    if not de_us_comparison:
        recommendations.append("Für DE/US-Vergleich müssen Whitelist-Matches oder Titelzuordnungen ergänzt werden.")
    if not any(item.get("visible_views") or item.get("visible_likes") or item.get("visible_comments") for item in cards):
        recommendations.append("Aktuell keine belastbaren sichtbaren Interaktionswerte vorhanden; Ranking basiert vorläufig auf Review-Status, Placement, Kinetic und Confidence.")

    return {
        "total_assets": len(assets),
        "discovery_assets": discovery,
        "missing_previews": missing_previews,
        "visual_analyzed": visual_analyzed,
        "title_placements": title_placements,
        "kinetic_assets": kinetic_assets,
        "type_breakdown": dict(type_counter),
        "market_breakdown": dict(market_counter),
        "status_breakdown": dict(status_counter),
        "placement_breakdown": dict(placement_counter),
        "visual_status_breakdown": dict(visual_status_counter),
        "top_assets_total": ranked[:10],
        "channel_rankings": channel_rankings[:15],
        "de_us_comparison": de_us_comparison[:20],
        "placement_comparison": placement_comparison[:20],
        "recommendations": recommendations,
    }
