from __future__ import annotations

from datetime import date, datetime, time
from statistics import median
from typing import Any

from sqlmodel import Session, select

from app.models.entities import Asset, Channel, Market, Post

REPORT_TYPES = {"weekly_overview", "de_us_comparison", "visual_kinetics"}


def _to_datetime_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    return datetime.combine(date_from, time.min), datetime.combine(date_to, time.max)


def _interaction_signal(post: Post) -> float:
    values = [post.visible_views, post.visible_likes, post.visible_shares, post.visible_comments, post.visible_bookmarks]
    return float(sum(v or 0 for v in values))


def _asset_title_label(asset: Asset) -> str:
    """Return the safest available user-facing title label without assuming DTO-only fields."""
    title = getattr(asset, "title", None)
    if title and getattr(title, "title_original", None):
        return title.title_original
    for field in ("placement_title_text", "kinetic_text"):
        value = getattr(asset, field, None)
        if value:
            return str(value)
    return "Filmtitel offen"


def _asset_title_key(asset: Asset) -> str:
    """Stable grouping key for report selection; works for ORM models without title_name."""
    if getattr(asset, "title_id", None):
        return str(asset.title_id)
    title = getattr(asset, "title", None)
    if title and getattr(title, "title_original", None):
        return str(title.title_original)
    for field in ("placement_title_text", "kinetic_text"):
        value = getattr(asset, field, None)
        if value:
            return str(value)
    return ""


def _score_asset(asset: Asset, post: Post, channel: Channel, baseline: float, report_type: str) -> tuple[float, list[str], list[str]]:
    score = 0.0
    tags: list[str] = []
    warnings: list[str] = []

    if asset.title_id:
        score += 0.14
        tags.append("Titel erkannt")
    else:
        score -= 0.22
        warnings.append("Titel nicht eindeutig")

    if asset.visual_analysis_status == "done":
        score += 0.1
        tags.append("Bildanalyse geprüft")
    elif asset.visual_analysis_status in {"error", "fetch_failed", "no_source"}:
        score -= 0.2
        warnings.append("keine Bildanalyse")

    if asset.visual_evidence_url or asset.screenshot_url or asset.thumbnail_url:
        score += 0.1
        tags.append("Evidence vorhanden")
    else:
        score -= 0.15
        warnings.append("kein Screenshot")

    if asset.ocr_text:
        score += 0.08
        tags.append("OCR sichtbar")
    if asset.has_title_placement:
        score += 0.08
    if (asset.placement_strength or "").lower() in {"clear", "dominant"}:
        score += 0.06
    if asset.has_kinetic:
        score += 0.09
        tags.append("Kinetic erkannt")
    if asset.kinetic_text:
        score += 0.03

    asset_type_value = getattr(getattr(asset, "asset_type", None), "value", str(getattr(asset, "asset_type", "")))
    has_cta = asset_type_value in {"CTA Post", "Ticket CTA"} or "cta" in (asset.placement_title_text or "").lower()
    if has_cta:
        score += 0.07
        tags.append("CTA erkannt")

    if post.post_url:
        score += 0.05
    else:
        score -= 0.15
        warnings.append("kein Original-Link")

    signal = _interaction_signal(post)
    if signal >= baseline * 1.25 and signal > 0:
        score += 0.12
        tags.append("Performance auffällig")
    elif signal == 0:
        score -= 0.04

    if report_type == "de_us_comparison":
        if channel.market in {Market.DE, Market.US, Market.INT}:
            score += 0.04
        if channel.market == Market.UNKNOWN:
            score -= 0.08

    return max(0.0, min(1.0, score + 0.5)), tags, warnings


def _build_reason(report_type: str, tags: list[str], warnings: list[str], signal: float, baseline: float) -> str:
    tag_set = set(tags)
    if report_type == "de_us_comparison":
        if "DE/US Paarung erkannt" in tag_set:
            return "Vorgeschlagen, weil zum Titel passende DE-/US-Treffer vorhanden sind."
        return "Vorgeschlagen, weil der Treffer für den DE/US-Vergleich relevante Markt- und Titel-Signale hat."
    if report_type == "visual_kinetics":
        if "OCR sichtbar" in tag_set and ("Kinetic erkannt" in tag_set or "Titel erkannt" in tag_set):
            return "Vorgeschlagen, weil OCR/Text und Titel-/Claim-Platzierung erkannt wurden."
        return "Vorgeschlagen, weil sichtbare Text- und Bewegungs-Signale für Bild/Text & Kinetics vorliegen."
    if signal >= baseline * 1.25 and signal > 0:
        return "Vorgeschlagen, weil der Treffer im beobachteten Datensatz hohe sichtbare Interaktion zeigt."
    if "Titel erkannt" in tag_set and "Evidence vorhanden" in tag_set and "CTA erkannt" in tag_set:
        return "Vorgeschlagen, weil Titel erkannt, Evidence-Bild vorhanden und CTA sichtbar ist."
    return "Vorgeschlagen, weil belastbare Titel-, Visual- und Kontextsignale vorliegen."


def select_assets_for_report(
    session: Session,
    report_type: str,
    date_from: date,
    date_to: date,
    channels: list[str] | None = None,
    markets: list[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    if report_type not in REPORT_TYPES:
        raise ValueError(f"Unsupported report_type: {report_type}")

    start, end = _to_datetime_bounds(date_from, date_to)
    query = select(Asset, Post, Channel).join(Post, Asset.post_id == Post.id).join(Channel, Post.channel_id == Channel.id).where(
        Post.detected_at >= start,
        Post.detected_at <= end,
    )

    rows = list(session.exec(query).all())
    if channels:
        allow = {c.lower() for c in channels}
        rows = [r for r in rows if (r[2].platform or "").lower() in allow or (r[2].name or "").lower() in allow]
    if markets:
        allow_m = {m.upper() for m in markets}
        rows = [r for r in rows if str(r[2].market.value).upper() in allow_m]

    baseline = median([_interaction_signal(post) for _, post, _ in rows]) if rows else 0
    excluded = {"missing_title": 0, "missing_visual": 0, "analysis_error": 0, "low_signal": 0}
    selected = []
    eligible = 0

    title_market_map: dict[str, set[Market]] = {}
    for asset, post, channel in rows:
        title_key = _asset_title_key(asset)
        if title_key:
            title_market_map.setdefault(title_key, set()).add(channel.market)

    for asset, post, channel in rows:
        if not asset.title_id:
            excluded["missing_title"] += 1
            if report_type == "de_us_comparison":
                continue
        if not (asset.visual_evidence_url or asset.screenshot_url or asset.thumbnail_url):
            excluded["missing_visual"] += 1
            if report_type in {"weekly_overview", "visual_kinetics"}:
                continue
        if asset.visual_analysis_status in {"error", "fetch_failed", "no_source"}:
            excluded["analysis_error"] += 1
            continue
        if report_type == "visual_kinetics" and not (asset.ocr_text and (asset.has_kinetic or asset.has_title_placement)):
            excluded["low_signal"] += 1
            continue

        score, tags, warnings = _score_asset(asset, post, channel, baseline, report_type)
        title_key = _asset_title_key(asset)
        markets_for_title = title_market_map.get(title_key, set())
        if report_type == "de_us_comparison" and Market.DE in markets_for_title and (Market.US in markets_for_title or Market.INT in markets_for_title):
            tags.append("DE/US Paarung erkannt")
        if score < 0.45:
            excluded["low_signal"] += 1
            continue
        eligible += 1
        title = _asset_title_label(asset)
        reason = _build_reason(report_type, tags, warnings, signal=_interaction_signal(post), baseline=baseline)
        selected.append({
            "asset_id": str(asset.id),
            "title": title,
            "channel": channel.name,
            "market": channel.market.value,
            "visual_evidence_url": asset.visual_evidence_url or asset.screenshot_url or asset.thumbnail_url,
            "score": round(score, 2),
            "reason": reason,
            "tags": tags,
            "recommended_for": [report_type],
            "warnings": warnings,
        })

    selected.sort(key=lambda a: a["score"], reverse=True)
    top = selected[:limit]
    return {
        "report_type": report_type,
        "checked": len(rows),
        "eligible": eligible,
        "selected": len(top),
        "excluded": excluded,
        "assets": top,
    }
