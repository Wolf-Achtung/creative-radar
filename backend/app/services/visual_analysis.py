from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import OpenAI
from sqlmodel import Session, select

from app.config import settings
from app.models.entities import Asset, AssetType, Channel, Post, Title
from app.services.cost_log import record_openai_call
from app.services.screenshot_capture import capture_asset_screenshot
from app.services.storage import resolve_url


# Status values the model is allowed to feed back via data["visual_analysis_status"].
# Anything else (hallucinated, typos) collapses to text_fallback.
# 'analyzed' is the canonical W4 success status; 'done' stays accepted for the
# 14-day toleranz fenster so historical assets and any external pipeline path
# that still writes 'done' are not treated as bad data.
ALLOWED_TERMINAL_STATUS_FROM_DATA = {"done", "analyzed", "text_fallback"}


# Substrings in OpenAI exception messages that signal "the model could not load
# the image we passed". Conservative match — used after exception class checks.
_IMAGE_UNREACHABLE_MARKERS = (
    "could not download",
    "could not load image",
    "failed to download",
    "image_url",
    "invalid_image_url",
    "unable to fetch image",
    "image url could not be fetched",
)


def _vision_data_is_empty(data: dict[str, Any]) -> bool:
    """OpenAI returned a non-exception response, but the parsed dict has nothing
    useful in it. Triggered when raw == '{}' or _safe_json fell back to {}."""
    if not data:
        return True
    meaningful_keys = ("ocr_text", "visual_summary_de", "title_placement",
                       "kinetics", "creative_mechanic", "placement_title_text",
                       "visual_notes")
    return not any(_as_text(data.get(k)).strip() for k in meaningful_keys)


def _classify_openai_exception(exc: BaseException) -> str:
    """Map a raised exception from the OpenAI Vision call to a terminal status.
    Falls back to 'vision_error' for anything we don't recognise."""
    cls_name = type(exc).__name__
    msg = str(exc).lower()

    if "timeout" in cls_name.lower() or "timeout" in msg:
        return "vision_timeout"
    if any(marker in msg for marker in _IMAGE_UNREACHABLE_MARKERS):
        return "image_unreachable"
    return "vision_error"


def _safe_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                return {}
        return {}


def _as_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value.strip() or fallback
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if item is not None).strip() or fallback
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip() or fallback


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "ja", "1"}
    return bool(value)


def _as_float(value: Any, fallback: float = 0.35) -> float:
    try:
        number = float(value)
    except Exception:
        return fallback
    return max(0.0, min(1.0, number))


def _slug(value: str | None) -> str | None:
    if not value:
        return None
    clean = value.lower().strip()
    clean = re.sub(r"[^a-z0-9äöüß]+", "-", clean)
    return clean.strip("-") or None


def _find_title_match(session: Session, visual_text: str, caption: str) -> Title | None:
    haystack = f"{visual_text}\n{caption}".lower()
    titles = list(session.exec(select(Title).where(Title.active == True)).all())  # noqa: E712
    for title in titles:
        candidates = [title.title_original, title.title_local, title.franchise]
        for keyword in title.keywords:
            if keyword.active:
                candidates.append(keyword.keyword)
        for candidate in candidates:
            if candidate and candidate.lower() in haystack:
                return title
    return None


_NO_ANALYSIS_POSSIBLE_NOTE = (
    "Keine Inhaltsanalyse möglich — weder Bild noch Caption-Text vorhanden."
)


def _heuristic_analysis(asset: Asset, post: Post | None, title: Title | None) -> dict[str, Any]:
    caption = (post.caption if post else "") or ""
    # W3 Hebel B: when there is genuinely nothing to analyse (no image url, no
    # caption, no OCR), don't let the heuristic invent vague context. Surface
    # an explicit "not possible" note so downstream consumers (and the UI)
    # can distinguish 'analysis attempted but inconclusive' from 'nothing to
    # analyse'.
    if not caption.strip() and not (asset.ocr_text or "").strip():
        return {
            "visual_analysis_status": "text_fallback",
            "ocr_text": None,
            "visual_notes": _NO_ANALYSIS_POSSIBLE_NOTE,
            "placement_title_text": title.title_original if title else None,
            "placement_position": "unknown",
            "placement_strength": "none",
            "has_title_placement": False,
            "has_kinetic": False,
            "kinetic_type": None,
            "kinetic_text": None,
            "asset_type": asset.asset_type,
            "de_us_match_key": _slug(title.franchise or title.title_original) if title else None,
            "visual_confidence_score": 0.0,
        }

    text = f"{caption}\n{asset.ocr_text or ''}"
    lower = text.lower()
    has_kinetic = any(word in lower for word in ["kinetic", "motion", "animated", "text", "typography", "schrift", "titeltafel", "title card"])
    has_title = bool(title) or any(word in lower for word in ["trailer", "teaser", "poster", "tickets", "kino", "theaters"])
    placement_strength = "strong" if has_title and (title or "trailer" in lower or "poster" in lower) else "weak"
    asset_type = asset.asset_type
    if has_kinetic and asset_type == AssetType.UNKNOWN:
        asset_type = AssetType.KINETIC
    return {
        "visual_analysis_status": "text_fallback",
        "ocr_text": asset.ocr_text or caption[:1000],
        "visual_notes": "Heuristische Analyse aus Caption/OCR, da kein belastbares Preview-Bild verfügbar ist.",
        "placement_title_text": title.title_original if title else None,
        "placement_position": "unknown",
        "placement_strength": placement_strength,
        "has_title_placement": has_title,
        "has_kinetic": has_kinetic,
        "kinetic_type": "text_or_motion_cue" if has_kinetic else None,
        "kinetic_text": asset.ocr_text if has_kinetic else None,
        "asset_type": asset_type,
        "de_us_match_key": _slug(title.franchise or title.title_original) if title else _slug(caption[:80]),
        "visual_confidence_score": 0.35,
    }


logger = logging.getLogger(__name__)

def analyze_asset_visual(session: Session, asset: Asset) -> Asset:
    post = session.get(Post, asset.post_id)
    channel = session.get(Channel, post.channel_id) if post else None
    title = session.get(Title, asset.title_id) if asset.title_id else None
    asset.visual_analysis_status = "running"
    session.add(asset)
    session.commit()
    session.refresh(asset)

    evidence = capture_asset_screenshot(asset)
    asset.visual_evidence_status = evidence.status
    if evidence.evidence_url:
        asset.visual_evidence_url = evidence.evidence_url
        asset.visual_source_url = evidence.source_url
        asset.visual_evidence_pack = {"full_screenshot": evidence.evidence_url, "title_crop": asset.visual_crop_title_url, "cta_crop": asset.visual_crop_cta_url, "kinetic_crop": asset.visual_crop_kinetic_url, "thumbnail": asset.thumbnail_url, "source_url": evidence.source_url, "captured_at": evidence.captured_at}
    image_url = asset.visual_evidence_url or asset.thumbnail_url or asset.screenshot_url
    caption = (post.caption if post else "") or ""

    if not title:
        possible_title = _find_title_match(session, asset.ocr_text or "", caption)
        if possible_title:
            asset.title_id = possible_title.id
            title = possible_title

    if not image_url:
        data = _heuristic_analysis(asset, post, title)
    elif not settings.openai_api_key:
        data = _heuristic_analysis(asset, post, title)
        data["visual_analysis_status"] = "text_fallback"
        data["visual_notes"] = "Bild konnte nicht ausgewertet werden. Die Caption wurde ersatzweise analysiert."
    else:
        # image_url may be a bare object key (post-F0.1) — resolve it to a fetchable
        # URL (presigned for S3, /storage/<key> for local) before sending to OpenAI.
        openai_image_url = resolve_url(image_url) or image_url
        client = OpenAI(api_key=settings.openai_api_key)
        prompt = f"""
Analysiere das Creative-Visual für ein Film-/Serien-/Game-Marketing-Monitoring.

Kontext:
Kanal: {channel.name if channel else 'Unbekannt'}
Markt: {channel.market if channel else 'UNKNOWN'}
Titel/Franchise-Vermutung: {title.title_original if title else 'kein Match'}
Caption: {caption or 'nicht verfügbar'}

Aufgaben:
1. OCR / sichtbaren Text im Bild erfassen.
2. Filmtitel-/Serientitel-/Game-Titel-Placement erkennen.
3. Position grob bestimmen: top, center, bottom, full_frame, caption_only, unknown.
4. Placement-Stärke: strong, medium, weak, none.
5. Kinetic/Text-Motion-Hinweise erkennen. Wenn Standbild es nicht sicher zeigt, nur "possible" verwenden.
6. DE/US-Match-Key als stabilen Franchise-/Titel-Key vorschlagen.
7. Asset-Typ möglichst konkret klassifizieren.

Antworte nur als JSON mit Strings/Booleans/Zahlen:
ocr_text, visual_summary_de, title_placement, kinetics, creative_mechanic, cta_detected,
brand_or_studio_visibility, format_observation, uncertainties,
placement_title_text, placement_position, placement_strength,
has_title_placement, has_kinetic, kinetic_type, kinetic_text, asset_type,
de_us_match_key, visual_confidence_score, confidence
"""
        try:
            response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": "Du bist ein präziser Visual-Analyst für Entertainment-Marketing. Gib ausschließlich valides JSON zurück."},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": openai_image_url}},
                ]},
            ],
            temperature=0.1,
        )
            raw = response.choices[0].message.content or "{}"
            # Cost-log hook (W4 Task 4.4 / F0.6). Logs token counts and the
            # asset id so the cost summary can group per-asset later if
            # needed. Never raises.
            record_openai_call(
                getattr(response, "usage", None),
                operation="vision_call",
                meta={"asset_id": str(asset.id), "model": settings.openai_model},
            )
            data = _safe_json(raw)
            if _vision_data_is_empty(data):
                # Model returned an empty / unparseable JSON payload. Don't claim
                # 'done' — surface as vision_empty so the counter stays honest.
                data = {"visual_analysis_status": "vision_empty",
                        "visual_notes": "OpenAI Vision lieferte keine verwertbare Antwort."}
            else:
                # W4 / Mini-Run: 'analyzed' is the canonical success status,
                # converging the in-repo write path with what the external
                # pipeline (Apify webhook / out-of-repo script) already writes
                # in production. 'done' stays accepted by the selector and
                # counter for 14 days of toleranz.
                data["visual_analysis_status"] = "analyzed"
        except Exception as exc:
            classified = _classify_openai_exception(exc)
            data = _heuristic_analysis(asset, post, title)
            data["visual_analysis_status"] = classified
            data["visual_notes"] = (
                "Bild konnte nicht ausgewertet werden. Die Caption wurde ersatzweise analysiert."
            )
            logger.warning(
                "visual-analysis-failed",
                extra={
                    "asset_id": str(asset.id),
                    "visual_source_url": image_url,
                    "error_class": type(exc).__name__,
                    "classified_status": classified,
                },
            )
    
    title_placement = data.get("title_placement") if isinstance(data.get("title_placement"), dict) else {}
    kinetics = data.get("kinetics") if isinstance(data.get("kinetics"), dict) else {}

    if evidence.status == "no_source":
        asset.visual_analysis_status = "no_source"
    elif evidence.status == "fetch_failed":
        # Capture failure dominates: without a real image, any vision claim is
        # speculative. Drop the prior 'and data == "done"' guard.
        asset.visual_analysis_status = "fetch_failed"
    else:
        # Whitelist-guard: only 'done' or 'text_fallback' may come from the model
        # data. New error statuses set by our own classification path are also
        # accepted. Anything else (hallucinated, typos) collapses to text_fallback.
        raw_status = _as_text(data.get("visual_analysis_status"), "text_fallback")
        if raw_status in ALLOWED_TERMINAL_STATUS_FROM_DATA or raw_status in {
            "vision_empty", "vision_timeout", "vision_error", "image_unreachable", "image_invalid"
        }:
            asset.visual_analysis_status = raw_status
        else:
            asset.visual_analysis_status = "text_fallback"
    asset.visual_source_url = image_url
    asset.ocr_text = _as_text(data.get("ocr_text"), asset.ocr_text or "") or None
    asset.visual_notes = _as_text(data.get("visual_summary_de"), _as_text(data.get("visual_notes"), "")) or None
    asset.placement_title_text = _as_text(title_placement.get("text"), _as_text(data.get("placement_title_text"), title.title_original if title else "")) or None
    asset.placement_position = _as_text(title_placement.get("position"), _as_text(data.get("placement_position"), "unknown"))
    asset.placement_strength = _as_text(title_placement.get("strength"), _as_text(data.get("placement_strength"), "unknown"))
    asset.has_title_placement = _as_bool(title_placement.get("has_title_placement", data.get("has_title_placement")))
    asset.has_kinetic = _as_bool(kinetics.get("has_kinetic", data.get("has_kinetic")))
    asset.kinetic_type = _as_text(kinetics.get("type"), _as_text(data.get("kinetic_type"), "")) or None
    asset.kinetic_text = _as_text(kinetics.get("text"), _as_text(data.get("kinetic_text"), "")) or None
    asset.de_us_match_key = _as_text(data.get("de_us_match_key"), "") or _slug(asset.placement_title_text) or None
    asset.visual_confidence_score = _as_float(data.get("confidence", data.get("visual_confidence_score")))

    asset_type = _as_text(data.get("asset_type"), "")
    if asset_type:
        for enum_value in AssetType:
            if asset_type.lower() in {enum_value.value.lower(), enum_value.name.lower().replace("_", " ")}:
                asset.asset_type = enum_value
                break

    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset
