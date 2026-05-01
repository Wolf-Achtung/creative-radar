from __future__ import annotations

from datetime import date, datetime, time
from statistics import median
from typing import Any

from sqlmodel import Session, select

from app.config import settings
from app.models.entities import Asset, Channel, Market, Post
from app.services.storage import is_legacy_storage_path, is_object_key, resolve_url

REPORT_TYPES = {"weekly_overview", "de_us_comparison", "visual_kinetics"}

EVIDENCE_LABELS = {
    "secure": "Bild intern gesichert",
    "external": "Externe Bildquelle",
    "source_only": "Bildquelle vorhanden",
    "missing": "Keine Bildquelle",
}

EVIDENCE_WARNINGS = {
    "external": "Externe Bildquelle, nicht dauerhaft gesichert",
    "source_only": "Bildquelle vorhanden, aber nicht intern gesichert",
    "missing": "Kein gesichertes Bild",
}

ANALYSIS_DONE_STATES = {"done", "analyzed", "text_fallback"}
ANALYSIS_FAILURE_STATES = {
    "error", "fetch_failed", "no_source",
    # W3 honest-status set (Task 3.2). All four are terminal failures the
    # selector should treat as "not eligible for the report".
    "vision_empty", "vision_timeout", "vision_error",
    "image_unreachable", "image_invalid",
}


def _is_analysis_done(asset: Asset) -> bool:
    return str(getattr(asset, "visual_analysis_status", "") or "") in ANALYSIS_DONE_STATES


def _is_analysis_failed(asset: Asset) -> bool:
    return str(getattr(asset, "visual_analysis_status", "") or "") in ANALYSIS_FAILURE_STATES


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


def _has_internal_storage_path(asset: Asset) -> bool:
    """Hat das Asset eine interne Storage-Referenz? Erkennt parallel:
    - Legacy: '/storage/evidence/<file>.jpg' (pre-F0.1, Backing-File ggf. weg)
    - Object-Key: 'evidence/<asset_id>_<uuid>.jpg' (post-F0.1, in R2)

    Beide gelten als 'internal'. Erreichbarkeit wird hier nicht geprüft — das
    macht das Frontend, indem es die resolved URL aus _displayable_image_candidates
    sequentiell durchprobiert. Parallel-Match bleibt aktiv, bis Backfill 100%
    + 14 Tage Stabilbetrieb nachgewiesen sind (Wolf-Vorgabe W3)."""
    value = getattr(asset, "visual_evidence_url", None)
    return is_legacy_storage_path(value) or is_object_key(value)


def _has_secure_evidence(asset: Asset) -> bool:
    """Echte secure-Evidence: interner Storage-Pfad UND Storage-Mount produktiv aktiviert."""
    return _has_internal_storage_path(asset) and settings.secure_storage_enabled


def _evidence_quality(asset: Asset) -> str:
    """
    Klassifiziert Evidence-Qualität.

    secure       interner Storage-Pfad UND SECURE_STORAGE_ENABLED=True. Solange kein
                 persistentes Volume + Static-Mount existieren, bleibt das deaktiviert
                 und kein Asset wird je als secure klassifiziert (Sprint 8.2b, Pfad C).
    external     externe http(s)-URL als visual_evidence_url (CDN, Instagram).
    source_only  nur thumbnail/screenshot/visual_source vorhanden, ODER interner
                 Storage-Pfad bei deaktiviertem Storage (faktisch nicht erreichbar).
    missing      keine Bildquelle.
    """
    if _has_secure_evidence(asset):
        return "secure"

    visual_evidence = str(getattr(asset, "visual_evidence_url", "") or "")
    if visual_evidence.startswith(("http://", "https://")):
        return "external"

    if _has_internal_storage_path(asset):
        return "source_only"

    if (
        getattr(asset, "screenshot_url", None)
        or getattr(asset, "thumbnail_url", None)
        or getattr(asset, "visual_source_url", None)
    ):
        return "source_only"

    return "missing"


def _displayable_image_candidates(asset: Asset) -> list[str]:
    """
    Liefert ALLE potenziell anzeigbaren Bild-URLs in priorisierter Reihenfolge.
    Das Frontend probiert sie sequentiell durch und nimmt die erste, die lädt.

    Begründung: externe CDN-URLs (Instagram-Story-Thumbnails etc.) sind kurzlebig
    und unterschiedlich stabil pro Feld. Wenn _displayable_image_url nur die erste
    Treffer-URL zurückgibt und die expired ist, wird der zweite Kandidat nie
    versucht — Vorschlagskarten zeigen den Load-Failed-Placeholder, obwohl
    thumbnail_url oder screenshot_url noch laden würden.

    Reihenfolge: secure-Evidence (falls Flag an) → visual_evidence_url →
    visual_source_url → screenshot_url → thumbnail_url. Duplikate entfernt.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def add(url: str | None) -> None:
        if url and isinstance(url, str) and url not in seen:
            candidates.append(url)
            seen.add(url)

    if settings.secure_storage_enabled:
        ev = getattr(asset, "visual_evidence_url", None)
        if is_legacy_storage_path(ev):
            # Legacy substring path stays as-is; FastAPI's /storage mount
            # serves it if the backing file survived the redeploy.
            add(ev)
        elif is_object_key(ev):
            # Object-key resolves to a presigned GET (R2) or /storage/<key>
            # (local). Frontend never sees the bare key — it would 404.
            add(resolve_url(ev))

    for field in ("visual_evidence_url", "visual_source_url", "screenshot_url", "thumbnail_url"):
        url = getattr(asset, field, None)
        if url and isinstance(url, str) and url.startswith(("http://", "https://")):
            add(url)

    return candidates


def _displayable_image_url(asset: Asset) -> str | None:
    """Erste Wahl-URL — Backwards-Compat für Pfad-B-Übergang und Tests.

    Identisch zur ersten Position aus _displayable_image_candidates(). Wird
    weiterhin im Selector-Output mitgeliefert, damit ein altes Frontend, das
    nur display_image_url kennt, nicht bricht.
    """
    candidates = _displayable_image_candidates(asset)
    return candidates[0] if candidates else None


def _suitability_label(score: float, report_type: str, evidence_quality: str, warnings: list[str], has_title: bool) -> str:
    base = "hoch" if score >= 0.75 else "mittel" if score >= 0.5 else "eingeschränkt"

    # Cap 1: visual_kinetics ohne secure-Evidence kann nie „hoch" sein.
    if evidence_quality != "secure" and report_type == "visual_kinetics" and base == "hoch":
        base = "eingeschränkt"

    # Cap 2: Generell — ohne secure-Evidence darf kein Report-Typ „hoch" zeigen.
    # Solange SECURE_STORAGE_ENABLED=False, ist „hoch" gar nicht erreichbar.
    if evidence_quality != "secure" and base == "hoch":
        base = "mittel"

    # Cap 3: Komplett fehlende Bildquelle → maximal „eingeschränkt".
    if evidence_quality == "missing":
        base = "eingeschränkt"

    # Cap 4: Kein Titel UND keine secure-Evidence → eingeschränkt.
    if not has_title and evidence_quality != "secure":
        base = "eingeschränkt"

    # Cap 5: Kein Titel allein cappt mindestens auf „mittel".
    if not has_title and base == "hoch":
        base = "mittel"

    return base


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

    if _is_analysis_done(asset):
        score += 0.1
        tags.append("Bildanalyse geprüft")
    elif _is_analysis_failed(asset):
        score -= 0.2
        warnings.append("keine Bildanalyse")

    evidence_quality = _evidence_quality(asset)
    if evidence_quality == "secure":
        score += 0.18
        tags.append("Bild gesichert")
    elif evidence_quality == "external":
        score += 0.05
        warnings.append(EVIDENCE_WARNINGS["external"])
    elif evidence_quality == "source_only":
        score += 0.02
        warnings.append(EVIDENCE_WARNINGS["source_only"])
    else:
        score -= 0.18
        warnings.append(EVIDENCE_WARNINGS["missing"])

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
    if "Titel erkannt" in tag_set and "Bild gesichert" in tag_set and "CTA erkannt" in tag_set:
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
    excluded = {
        "missing_title": 0,
        "missing_visual": 0,
        "missing_secure_evidence": 0,
        "external_visual_only": 0,
        "source_only_visual": 0,
        "analysis_error": 0,
        "low_signal": 0,
        "missing_market_pair": 0,
    }
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
        if not (asset.visual_evidence_url or asset.screenshot_url or asset.thumbnail_url or asset.visual_source_url):
            excluded["missing_visual"] += 1
            if report_type == "visual_kinetics":
                continue
        evidence_quality = _evidence_quality(asset)
        if evidence_quality != "secure":
            excluded["missing_secure_evidence"] += 1
            if evidence_quality == "external":
                excluded["external_visual_only"] += 1
            elif evidence_quality == "source_only":
                excluded["source_only_visual"] += 1
        if _is_analysis_failed(asset):
            excluded["analysis_error"] += 1
            continue
        if report_type == "visual_kinetics" and not (_has_secure_evidence(asset) or asset.ocr_text or asset.has_title_placement or asset.has_kinetic or asset.kinetic_text):
            excluded["low_signal"] += 1
            continue

        score, tags, warnings = _score_asset(asset, post, channel, baseline, report_type)
        title_key = _asset_title_key(asset)
        markets_for_title = title_market_map.get(title_key, set())
        has_pair = Market.DE in markets_for_title and (Market.US in markets_for_title or Market.INT in markets_for_title)
        if report_type == "de_us_comparison":
            if has_pair:
                tags.append("DE/US Paarung erkannt")
            else:
                excluded["missing_market_pair"] += 1
                excluded["low_signal"] += 1
                continue
        if score < 0.45:
            excluded["low_signal"] += 1
            continue
        eligible += 1
        title = _asset_title_label(asset)
        evidence_quality = _evidence_quality(asset)
        expected_warning = EVIDENCE_WARNINGS.get(evidence_quality)
        if expected_warning and expected_warning not in warnings:
            warnings.append(expected_warning)
        reason = _build_reason(report_type, tags, warnings, signal=_interaction_signal(post), baseline=baseline)
        suitability = _suitability_label(score, report_type, evidence_quality, warnings, has_title=bool(asset.title_id))
        display_candidates = _displayable_image_candidates(asset)
        display_url = display_candidates[0] if display_candidates else None
        item: dict[str, Any] = {
            "asset_id": str(asset.id),
            "title": title,
            "channel": channel.name,
            "market": channel.market.value,
            "display_image_url": display_url,
            "display_image_candidates": display_candidates,
            "evidence_quality": evidence_quality,
            "has_secure_evidence": evidence_quality == "secure",
            "evidence_label": EVIDENCE_LABELS[evidence_quality],
            "is_evidence_displayable": display_url is not None,
            "score": round(score, 2),
            "suitability": suitability,
            "reason": reason,
            "tags": tags,
            "recommended_for": [report_type],
            "warnings": warnings,
        }
        if report_type == "de_us_comparison":
            item["pair_group_key"] = title_key
            item["pair_market"] = channel.market.value
        selected.append(item)

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
