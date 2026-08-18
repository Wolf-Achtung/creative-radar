"""Post analyzer — cross-platform AI analysis pipeline (Sprint 5.3.1).

Per post, in order:

1. ``extract_asset_url(post)`` -> str | None
   Dispatches by ``post.platform`` and reuses the existing image-URL
   helpers from the YouTube and Apify connectors. Returns None if the
   raw_payload doesn't carry a recognisable image (very old posts).

2. Vision call (Sonnet, ``vision_describe``)
   Only if (a) extract_asset_url returned a URL and (b) no Asset row
   with (post_id, asset_url, vision_description IS NOT NULL) already
   exists. Returns vision_description text + records cost.

3. Haiku call (``classify_format_tone``)
   Returns format + tone JSON. One Haiku call per post for both
   mechanical fields together.

4. Sonnet call (``classify_purpose_lifecycle``)
   Returns purpose + lifecycle_stage JSON. One Sonnet text call per
   post for both contextual fields together.

5. Merge into PostAnalysis dict, write to Post.analysis +
   Post.last_analyzed_at, upsert Asset row with vision fields.

Failure isolation:
- Auth error -> raise (caller maps to 401)
- Rate limit after retries -> skip this post, append to errors list
- Vision URL unreachable / 404 -> vision_description=None, continue
- Invalid JSON from classifier -> 1 retry with stricter instruction,
  then skip-and-log
- Any other per-post error -> append to errors list, do NOT crash
  the whole batch.

The orchestrator is sync today — Anthropic SDK supports async but
the analyze endpoint is a single FastAPI request handler that runs
sequentially through the channel's posts (low N, ~10-50 per channel).
Going async buys nothing without batching across channels.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlmodel import Session, select

from app.config import settings
from app.models.entities import Asset, AssetType, Post
from app.prompts import (
    analyze_format_tone,
    analyze_purpose_lifecycle,
    analyze_vision,
)
from app.services.anthropic_client import (
    AnthropicAPIError,
    AnthropicAuthError,
    AnthropicRateLimitError,
    messages_create_text,
    messages_create_vision,
)
from app.services.apify_connector import _image_from_item
from app.services.asset_screenshot_persistence import persist_asset_screenshot
from app.services.cost_log import record_anthropic_call
from app.services.youtube_connector import _best_thumbnail_url

logger = logging.getLogger(__name__)


# ---------- Result shapes ---------------------------------------------


class AnalyzePostResult:
    """Lightweight result struct for the per-post outcome. Plain class
    instead of a dataclass so the analyze endpoint can introspect both
    success and skip paths uniformly. Mutated in-place by the helpers
    below to keep the orchestration loop readable."""

    __slots__ = ("post_id", "status", "asset_created", "errors", "calls")

    def __init__(self, post_id: UUID) -> None:
        self.post_id: UUID = post_id
        self.status: str = "pending"  # 'analyzed' | 'skipped' | 'error'
        self.asset_created: bool = False
        self.errors: list[str] = []
        # Per-post call accounting — the endpoint aggregates these.
        self.calls: dict[str, int] = {"haiku": 0, "sonnet": 0, "sonnet_vision": 0}


# ---------- Asset URL extraction (DIAG-5 dispatch) --------------------


def extract_asset_url(post: Post) -> Optional[str]:
    """Pick the post's primary image URL from raw_payload, dispatched
    by platform. Reuses the existing helpers in the connectors so the
    extraction logic stays single-source-of-truth.

    Returns None for unrecognised platforms or payloads without a
    discoverable image — caller treats that as "skip vision, still do
    classification".
    """
    payload = post.raw_payload if isinstance(post.raw_payload, dict) else {}
    platform = (post.platform or "").lower()

    if platform == "youtube":
        snippet = payload.get("snippet") if isinstance(payload.get("snippet"), dict) else {}
        return _best_thumbnail_url(snippet.get("thumbnails"))

    if platform in ("instagram", "tiktok"):
        return _image_from_item(payload)

    return None


# ---------- JSON parsing helpers --------------------------------------


def _parse_json_object(raw_text: str) -> dict:
    """Tolerant JSON parser. The prompts instruct the model to output
    pure JSON, but in practice the occasional ```json fence still
    sneaks in — strip it before json.loads."""
    text = (raw_text or "").strip()
    if text.startswith("```"):
        # Strip the opening fence (```json or ```) and the closing one.
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[: -3]
        text = text.strip()
    return json.loads(text)


def _confidence_or_none(parsed: dict) -> Optional[float]:
    """Read ``confidence`` from a classifier response, clamped to
    [0.0, 1.0]. Returns None if missing or unparseable so the mean
    helper can fall back gracefully."""
    raw = parsed.get("confidence") if isinstance(parsed, dict) else None
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _mean_confidence(ft: dict, ps: dict) -> float:
    """Mean of the two model-self-reported confidence scores. If only
    one is present, return that; if neither, return 0.0 (transparent
    'we don't know' rather than a fake mid-range constant)."""
    values = [v for v in (_confidence_or_none(ft), _confidence_or_none(ps)) if v is not None]
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _block_field(block: Any, name: str) -> Any:
    """Feldzugriff, der Objekt- und Dict-Bloecke gleich behandelt —
    das SDK liefert Objekte, die Tests duck-typed Dicts."""
    value = getattr(block, name, None)
    if value is None and isinstance(block, dict):
        value = block.get(name)
    return value


def _message_text(message: Any) -> str:
    """Alle Text-Bloecke einer Messages-API-Antwort aneinanderhaengen.

    Frueher stand hier ``content[0]``. Das ging so lange gut, wie kein
    Modell vor der Antwort nachdachte: Denkt eines, steht ein
    ``thinking``-Block an erster Stelle und der blinde Zugriff liefert
    einen leeren String — die Antwort dahinter faellt unter den Tisch.
    Sonnet 5 denkt in der Voreinstellung, also ist das kein
    theoretischer Fall mehr.

    ``insight_engine`` und ``forecast`` iterieren aus demselben Grund
    ueber alle Bloecke; hier war die Stelle uebersehen worden.
    """
    if message is None:
        return ""
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        # Bloecke ohne Typ sind die Test-Dicts der Altbestaende: die
        # tragen ihren Text direkt und sollen weiter durchgehen.
        block_type = _block_field(block, "type")
        if block_type is not None and block_type != "text":
            continue
        text = _block_field(block, "text")
        if text:
            parts.append(text)
    return "".join(parts)


def _was_truncated(message: Any) -> bool:
    """Hat das Modell mitten im Satz aufgehoert, weil ``max_tokens``
    erreicht war? Denk-Tokens zaehlen gegen dasselbe Limit, deshalb ist
    das kein reines Prompt-Problem."""
    return _block_field(message, "stop_reason") == "max_tokens"


# ---------- Denk-Aufwand und Limits -----------------------------------
#
# Beide Sonnet-Aufrufe sind reine Extraktion: lies eine Bildunterschrift
# beziehungsweise ein Bild, gib ein paar Felder zurueck. Dafuer ist
# ``low`` der passende Aufwand — und vor allem ist er *hingeschrieben*.
# Ohne Angabe entscheidet das Modell, und die Voreinstellung wechselt
# mit dem Modell: Sonnet 4.6 hat nicht gedacht, Sonnet 5 denkt. Der
# Wechsel hat unser Verhalten geaendert, ohne dass eine Zeile Code sich
# geaendert haette.
SONNET_EFFORT = "low"

# ``max_tokens`` ist ein Deckel, keine Reservierung — ungenutzter Raum
# kostet nichts. Die alten 256 beziehungsweise 400 liessen den
# Denk-Tokens nur wenig Luft vor der eigentlichen Antwort. Die neuen
# Werte nehmen dem Abschneiden die Grundlage, ohne die Rechnung zu
# beruehren: die Prompts geben die Laenge vor, nicht dieses Limit.
CLASSIFY_MAX_TOKENS = 2000
VISION_MAX_TOKENS = 2000


# ---------- Per-call helpers ------------------------------------------


def _classify_format_tone(post: Post, result: AnalyzePostResult) -> Optional[dict]:
    """Returns a dict with keys 'format' and 'tone', or None on
    unrecoverable failure (logged into result.errors)."""
    try:
        message = messages_create_text(
            model=settings.anthropic_haiku_model,
            system=analyze_format_tone.SYSTEM_PROMPT,
            user_message=analyze_format_tone.build_user_message(
                post.caption or "", post.platform or ""
            ),
        )
    except AnthropicRateLimitError as exc:
        result.errors.append(f"haiku-rate-limit: {exc}")
        return None

    record_anthropic_call(
        usage=getattr(message, "usage", None),
        model=settings.anthropic_haiku_model,
        operation="classify_format_tone",
        meta={"post_id": str(post.id)},
    )
    result.calls["haiku"] += 1

    text = _message_text(message)
    try:
        return _parse_json_object(text)
    except (json.JSONDecodeError, ValueError):
        # One retry with a stricter instruction. The prompt already says
        # "valid JSON only", but on the rare miss a re-ask works.
        try:
            message = messages_create_text(
                model=settings.anthropic_haiku_model,
                system=analyze_format_tone.SYSTEM_PROMPT,
                user_message=(
                    analyze_format_tone.build_user_message(
                        post.caption or "", post.platform or ""
                    )
                    + "\n\nRespond with valid JSON only — no prose, no fences."
                ),
            )
        except AnthropicRateLimitError as exc:
            result.errors.append(f"haiku-retry-rate-limit: {exc}")
            return None
        record_anthropic_call(
            usage=getattr(message, "usage", None),
            model=settings.anthropic_haiku_model,
            operation="classify_format_tone_retry",
            meta={"post_id": str(post.id)},
        )
        result.calls["haiku"] += 1
        try:
            return _parse_json_object(_message_text(message))
        except (json.JSONDecodeError, ValueError) as exc:
            result.errors.append(f"haiku-invalid-json: {exc}")
            return None


def _classify_purpose_lifecycle(post: Post, result: AnalyzePostResult) -> Optional[dict]:
    try:
        message = messages_create_text(
            model=settings.anthropic_sonnet_model,
            system=analyze_purpose_lifecycle.SYSTEM_PROMPT,
            user_message=analyze_purpose_lifecycle.build_user_message(
                post.caption or "",
                post.platform or "",
                post.published_at,
            ),
            max_tokens=CLASSIFY_MAX_TOKENS,
            effort=SONNET_EFFORT,
        )
    except AnthropicRateLimitError as exc:
        result.errors.append(f"sonnet-rate-limit: {exc}")
        return None

    record_anthropic_call(
        usage=getattr(message, "usage", None),
        model=settings.anthropic_sonnet_model,
        operation="classify_purpose_lifecycle",
        meta={"post_id": str(post.id)},
    )
    result.calls["sonnet"] += 1

    text = _message_text(message)
    try:
        return _parse_json_object(text)
    except (json.JSONDecodeError, ValueError):
        try:
            message = messages_create_text(
                model=settings.anthropic_sonnet_model,
                system=analyze_purpose_lifecycle.SYSTEM_PROMPT,
                user_message=(
                    analyze_purpose_lifecycle.build_user_message(
                        post.caption or "",
                        post.platform or "",
                        post.published_at,
                    )
                    + "\n\nRespond with valid JSON only — no prose, no fences."
                ),
                max_tokens=CLASSIFY_MAX_TOKENS,
                effort=SONNET_EFFORT,
            )
        except AnthropicRateLimitError as exc:
            result.errors.append(f"sonnet-retry-rate-limit: {exc}")
            return None
        record_anthropic_call(
            usage=getattr(message, "usage", None),
            model=settings.anthropic_sonnet_model,
            operation="classify_purpose_lifecycle_retry",
            meta={"post_id": str(post.id)},
        )
        result.calls["sonnet"] += 1
        try:
            return _parse_json_object(_message_text(message))
        except (json.JSONDecodeError, ValueError) as exc:
            # Abgeschnittenes JSON und echtes Schrott-JSON sehen fuer den
            # Parser gleich aus. Der Unterschied entscheidet aber, was zu
            # tun ist: Limit anheben oder Prompt schaerfen.
            if _was_truncated(message):
                result.errors.append(
                    "sonnet-truncated: stop_reason=max_tokens "
                    f"max_tokens={CLASSIFY_MAX_TOKENS}"
                )
            else:
                result.errors.append(f"sonnet-invalid-json: {exc}")
            return None


def _describe_vision(post: Post, asset_url: str, result: AnalyzePostResult) -> Optional[str]:
    """Returns description text or None on failure (rate limit / 404 /
    auth — for vision-only failures we degrade gracefully so the rest
    of the analysis still happens)."""
    try:
        message = messages_create_vision(
            model=settings.anthropic_sonnet_model,
            system=analyze_vision.SYSTEM_PROMPT,
            user_message=analyze_vision.build_user_message(
                post.caption or "", post.platform or ""
            ),
            image_url=asset_url,
            max_tokens=VISION_MAX_TOKENS,
            effort=SONNET_EFFORT,
        )
    except AnthropicRateLimitError as exc:
        result.errors.append(f"vision-rate-limit: {exc}")
        return None
    except AnthropicAPIError as exc:
        # Image fetch failures (Anthropic-side 400 "could not fetch
        # image") and other non-auth API errors — graceful skip.
        result.errors.append(f"vision-api-error: {exc}")
        return None

    record_anthropic_call(
        usage=getattr(message, "usage", None),
        model=settings.anthropic_sonnet_model,
        operation="vision_describe",
        meta={"post_id": str(post.id), "asset_url": asset_url},
    )
    result.calls["sonnet_vision"] += 1

    # Abgeschnittenes verwerfen statt speichern. Eine mitten im Satz
    # endende Beschreibung sieht aus wie eine kurze — sie wandert
    # unbemerkt in die Auswertung und der Idempotenz-Sprung in
    # ``analyze_post`` haelt sie fuer erledigt. Lieber nichts: der
    # naechste Lauf versucht es erneut, und die uebrige Analyse laeuft
    # ohnehin ohne Bildbeschreibung weiter.
    if _was_truncated(message):
        logger.warning(
            "vision description truncated at max_tokens — discarded "
            "(post_id=%s max_tokens=%s)",
            post.id,
            VISION_MAX_TOKENS,
        )
        result.errors.append(
            f"vision-truncated: stop_reason=max_tokens max_tokens={VISION_MAX_TOKENS}"
        )
        return None

    return (_message_text(message) or "").strip() or None


# ---------- Per-post orchestrator -------------------------------------


def _existing_vision_asset(session: Session, post_id: UUID, asset_url: str) -> Optional[Asset]:
    """Pre-check: do we already have a non-null vision_description for
    this exact (post_id, asset_url)? Drives the inner-asset idempotency
    skip — separate from the outer post-level skip via
    last_analyzed_at."""
    statement = (
        select(Asset)
        .where(Asset.post_id == post_id)
        .where(Asset.asset_url == asset_url)
    )
    return session.exec(statement).first()


def analyze_post(
    session: Session, post: Post, *, skip_vision: bool = False
) -> AnalyzePostResult:
    """Run the full analysis pipeline for one post. Caller owns the
    Session and the loop; this function does NOT commit on its own —
    it stages changes and lets the caller commit per-post (so a crash
    halfway through a batch doesn't roll back already-completed
    posts).

    ``skip_vision=True`` drops step 2 (the Sonnet vision call) and runs
    the two text classifiers only. The four PostAnalysis fields —
    format / tone / purpose / lifecycle_stage — are derived from the
    caption alone, so they are unaffected; only ``vision_description``
    and the Asset upsert that carries it are skipped.

    Why that matters: the vision call dominates the per-post cost
    (~$0.0073 of ~$0.0101, i.e. ~72%) because the image alone is
    ~1.6k input tokens. The downstream consumer that needs coverage
    today — the insight-engine recommendation builder — reads only
    ``analysis['format']`` and ``analysis['lifecycle_stage']``, never
    ``vision_description``. So the cron backlog-drain runs text-only
    while the manual admin endpoint keeps the full pipeline.
    """
    result = AnalyzePostResult(post_id=post.id)

    # ---- Vision -------------------------------------------------------
    asset_url = None if skip_vision else extract_asset_url(post)
    vision_description: Optional[str] = None
    if asset_url:
        existing = _existing_vision_asset(session, post.id, asset_url)
        if existing and existing.vision_description:
            # Idempotent skip — already analyzed this exact image.
            vision_description = existing.vision_description
        else:
            try:
                vision_description = _describe_vision(post, asset_url, result)
            except AnthropicAuthError:
                # Auth failures are non-recoverable; let the endpoint
                # map to 401. Do NOT mask them per-post.
                raise

            asset = existing or Asset(
                post_id=post.id,
                asset_type=AssetType.UNKNOWN,
                asset_url=asset_url,
            )
            asset.asset_url = asset_url
            asset.vision_description = vision_description
            asset.vision_model = settings.anthropic_sonnet_model
            asset.analyzed_at = datetime.now(timezone.utc)
            # capture_asset_screenshot reads from screenshot_url / thumbnail_url /
            # visual_source_url — this analyzer path only has asset_url, so bridge
            # it onto visual_source_url before the capture call.
            if not asset.visual_source_url:
                asset.visual_source_url = asset_url
            persist_asset_screenshot(asset)
            session.add(asset)
            result.asset_created = existing is None

    # ---- Classification (Haiku then Sonnet) ---------------------------
    # Short-circuit on Haiku failure — no point spending the Sonnet
    # call if we already can't form a complete PostAnalysis dict.
    try:
        ft = _classify_format_tone(post, result)
        if not ft:
            result.status = "error"
            return result
        ps = _classify_purpose_lifecycle(post, result)
    except AnthropicAuthError:
        raise

    if not ps:
        result.status = "error"
        return result

    # ---- Merge into PostAnalysis -------------------------------------
    # Confidence is the mean of the two model-self-reported scores. Both
    # prompts make ``confidence`` a required JSON key, so the typical
    # path is two valid floats; on the rare case where a model omits the
    # key (despite the schema), _confidence_or_none returns None and we
    # fall back to the single available value, or 0.0 if neither
    # reported. This keeps the field honest — pseudo-constants like a
    # hardcoded 0.65 hide model uncertainty downstream.
    confidence = _mean_confidence(ft, ps)

    analysis = {
        "format": ft.get("format"),
        "purpose": ps.get("purpose"),
        "tone": ft.get("tone"),
        "lifecycle_stage": ps.get("lifecycle_stage"),
        "confidence": confidence,
        "classified_at": datetime.now(timezone.utc).isoformat(),
        "haiku_model": settings.anthropic_haiku_model,
        "sonnet_model": settings.anthropic_sonnet_model,
    }

    post.analysis = analysis
    post.last_analyzed_at = datetime.now(timezone.utc)
    session.add(post)
    result.status = "analyzed"
    return result
