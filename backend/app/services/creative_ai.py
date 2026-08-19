import json
import logging
from typing import Any

from openai import AsyncOpenAI, OpenAI

from app.config import settings
from app.models.entities import AssetType, ReviewStatus
from app.services.cost_log import record_openai_call

logger = logging.getLogger(__name__)


def _safe_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                return {}
        return {}


def _as_text(value: Any, fallback: str = '') -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value.strip() or fallback
    if isinstance(value, list):
        parts = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, (dict, list)):
                parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return '\n'.join(parts).strip() or fallback
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip() or fallback


# W3 Hebel A — language whitelist. Production showed the model writing whole
# sentences ("English (caption); likely mixed with Spanish context due to
# CDMX") into the language column. _as_text + [:64] truncated those but
# didn't normalize them. ALLOWED_LANGUAGE_CODES + _normalize_language
# collapse free-form output into a small ISO 639-1 set plus 'unknown'.
ALLOWED_LANGUAGE_CODES = {"de", "en", "es", "fr", "it", "pt", "ja", "ko", "zh", "unknown"}

_LANGUAGE_WORD_MAP = {
    "german": "de", "deutsch": "de",
    "english": "en",
    "spanish": "es", "español": "es",
    "french": "fr", "français": "fr",
    "italian": "it", "italiano": "it",
    "portuguese": "pt", "português": "pt",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
}


def _normalize_language(value: Any) -> str:
    """Collapse the model's free-form language output into the whitelist.
    Lower-case + exact match first, then word-form match (English -> en),
    then ISO code as standalone word, finally 'unknown'."""
    if value is None:
        return "unknown"
    raw = str(value).strip().lower()
    if not raw:
        return "unknown"
    if raw in ALLOWED_LANGUAGE_CODES:
        return raw
    for word, iso in _LANGUAGE_WORD_MAP.items():
        if word in raw:
            return iso
    for token in raw.replace("(", " ").replace(")", " ").replace(";", " ").replace(",", " ").split():
        if token in ALLOWED_LANGUAGE_CODES and token != "unknown":
            return token
    return "unknown"


def _asset_type(value: Any) -> AssetType:
    if isinstance(value, AssetType):
        return value
    if not value:
        return AssetType.UNKNOWN
    clean = str(value).strip().lower().replace('_', ' ')
    aliases = {
        'trailer drop': AssetType.TRAILER_DROP,
        'trailer announcement': AssetType.TRAILER_DROP,
        'trailer ankündigung': AssetType.TRAILER_DROP,
        'trailer': AssetType.TRAILER,
        'teaser': AssetType.TEASER,
        'poster': AssetType.POSTER,
        'key art': AssetType.KEY_ART,
        'poster / key art': AssetType.KEY_ART,
        'character card': AssetType.CHARACTER_CARD,
        'cast post': AssetType.CAST_POST,
        'character / cast post': AssetType.CAST_POST,
        'quote / review': AssetType.REVIEW_QUOTE,
        'review quote': AssetType.REVIEW_QUOTE,
        'cta post': AssetType.CTA_POST,
        'ticket cta': AssetType.TICKET_CTA,
        'release reminder': AssetType.RELEASE_REMINDER,
        'behind the scenes': AssetType.BEHIND_THE_SCENES,
        'event / festival': AssetType.EVENT_FESTIVAL,
        'event': AssetType.EVENT_FESTIVAL,
        'festival': AssetType.EVENT_FESTIVAL,
        'series episode push': AssetType.SERIES_EPISODE_PUSH,
        'episode push': AssetType.SERIES_EPISODE_PUSH,
        'franchise / brand post': AssetType.FRANCHISE_BRAND_POST,
        'brand post': AssetType.FRANCHISE_BRAND_POST,
        'kinetic': AssetType.KINETIC,
        'story': AssetType.STORY,
        'discovery': AssetType.DISCOVERY,
        'unknown': AssetType.UNKNOWN,
    }
    if clean in aliases:
        return aliases[clean]
    for item in AssetType:
        if clean in {item.value.lower(), item.name.lower().replace('_', ' ')}:
            return item
    return AssetType.UNKNOWN


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        return 0.5
    if number < 0:
        return 0.0
    if number > 1:
        return 1.0
    return number


_SYSTEM_MSG = (
    'Du bist ein präziser Creative-Analyst für Film-, Serien- und Game-Marketing. '
    'Du gibst valides JSON zurück. Textfelder sind immer Strings.'
)


def _build_prompt(
    *, post_url: str, channel_name: str, market: str, title_name: str | None,
    caption: str | None, ocr_text: str | None, asset_type_hint: AssetType,
) -> str:
    asset_types = ', '.join(item.value for item in AssetType)
    return f"""
Analysiere diesen Social-Media-Creative-Treffer aus Film-, Serien- oder Game-Marketing.

Post-Link: {post_url}
Kanal: {channel_name}
Markt: {market}
Titel/Franchise: {title_name or 'kein Whitelist-Match / Discovery'}
Asset-Typ-Hinweis: {asset_type_hint.value}
Caption/Seitentext: {caption or 'nicht verfügbar'}
Sichtbarer Text/OCR: {ocr_text or 'nicht verfügbar'}

Klassifiziere asset_type möglichst konkret mit genau einem dieser Werte:
{asset_types}

Liefere:
- ai_summary_de: maximal 3 Sätze, direkt entscheidungsfähig für Creative Review.
- ai_summary_en: maximal 2 Sätze.
- ai_trend_notes: maximal 2 Sätze, beobachtbares Pattern, keine Erfolgsbehauptung.
- confidence_score zwischen 0 und 1.

Keine Klickzahlen, keine Erfolgsbehauptungen, keine harten Bewertungen.
Antworte nur als JSON. Alle Textfelder müssen Strings sein, keine Arrays.
"""


def _unconfigured_response(asset_type_hint: AssetType) -> dict[str, Any]:
    return {
        'asset_type': asset_type_hint,
        'language': 'unknown',
        'ai_summary_de': 'OpenAI ist noch nicht konfiguriert. Der Treffer wurde angelegt und sollte manuell geprüft werden.',
        'ai_summary_en': 'OpenAI is not configured yet. Manual review required.',
        'ai_trend_notes': 'Nach Setzen von OPENAI_API_KEY in Railway wird diese Zusammenfassung automatisch erzeugt.',
        'confidence_score': 0.2,
        'review_status': ReviewStatus.NEEDS_REVIEW,
    }


def _text_data_is_empty(data: dict[str, Any]) -> bool:
    """Der Aufruf kam ohne Exception zurueck, die geparste Antwort
    enthaelt aber nichts Verwertbares.

    Wartung 2026-08-19. Gegenstueck zu
    ``visual_analysis._vision_data_is_empty`` — dort loest derselbe
    Zustand seit W4 den Status ``vision_empty`` aus. Auf diesem Pfad
    fehlte die Erkennung ganz: ``_safe_json`` gibt bei unlesbarer
    Antwort ``{}`` zurueck, ``_shape_response`` fuellt daraufhin
    stillschweigend Platzhalter-Text ein und meldet ``ReviewStatus.NEW``
    — von einer echten Analyse nicht zu unterscheiden.

    Diese Funktion faellt kein Urteil und aendert kein Feld. Sie macht
    den Zustand nur sichtbar (WARNING im Aufrufer), damit er zaehlbar
    wird. Welchen ``review_status`` und welche ``confidence`` ein
    fehlgeschlagener Aufruf tragen soll, ist eine Produktentscheidung
    und steht im Wartungsbericht vom 19.08. — nicht hier.
    """
    if not data:
        return True
    aussagekraeftig = ('ai_summary_de', 'ai_summary_en', 'ai_trend_notes')
    return not any(_as_text(data.get(k)).strip() for k in aussagekraeftig)


def _shape_response(raw: str) -> dict[str, Any]:
    data = _safe_json(raw)
    if _text_data_is_empty(data):
        logger.warning(
            "creative-ai-empty-response",
            extra={
                'model': settings.openai_model,
                'raw_length': len(raw or ''),
                'raw_first_300': (raw or '')[:300],
            },
        )
    return {
        'asset_type': _asset_type(data.get('asset_type')),
        'language': _normalize_language(data.get('language')),
        'ai_summary_de': _as_text(data.get('ai_summary_de'), 'Keine belastbare Zusammenfassung erzeugt.'),
        'ai_summary_en': _as_text(data.get('ai_summary_en'), ''),
        'ai_trend_notes': _as_text(data.get('ai_trend_notes'), ''),
        'confidence_score': _confidence(data.get('confidence_score')),
        'review_status': ReviewStatus.NEW,
    }


def analyze_creative_text(
    *,
    post_url: str,
    channel_name: str,
    market: str,
    title_name: str | None,
    caption: str | None,
    ocr_text: str | None,
    asset_type_hint: AssetType = AssetType.UNKNOWN,
) -> dict[str, Any]:
    if not settings.openai_api_key:
        return _unconfigured_response(asset_type_hint)

    client = OpenAI(api_key=settings.openai_api_key)
    prompt = _build_prompt(
        post_url=post_url, channel_name=channel_name, market=market,
        title_name=title_name, caption=caption, ocr_text=ocr_text,
        asset_type_hint=asset_type_hint,
    )
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {'role': 'system', 'content': _SYSTEM_MSG},
            {'role': 'user', 'content': prompt},
        ],
        temperature=0.2,
    )
    raw = response.choices[0].message.content or '{}'
    # Cost-log hook (W4 Task 4.4 / F0.6). Never raises.
    record_openai_call(
        getattr(response, "usage", None),
        operation="chat_completion",
        meta={"channel": channel_name, "model": settings.openai_model},
    )
    return _shape_response(raw)


async def analyze_creative_text_async(
    *,
    post_url: str,
    channel_name: str,
    market: str,
    title_name: str | None,
    caption: str | None,
    ocr_text: str | None,
    asset_type_hint: AssetType = AssetType.UNKNOWN,
) -> dict[str, Any]:
    """Async sibling of ``analyze_creative_text`` (Block 2 / async refactor).

    Same prompt + same response shape; uses ``AsyncOpenAI`` so the call can
    be awaited without blocking the event loop. The cron items-loop runs
    these concurrently under a Semaphore — see
    ``api/monitor._create_asset_from_item_async``.

    The unconfigured-fallback path stays sync; no awaits needed.
    """
    if not settings.openai_api_key:
        return _unconfigured_response(asset_type_hint)

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    prompt = _build_prompt(
        post_url=post_url, channel_name=channel_name, market=market,
        title_name=title_name, caption=caption, ocr_text=ocr_text,
        asset_type_hint=asset_type_hint,
    )
    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {'role': 'system', 'content': _SYSTEM_MSG},
            {'role': 'user', 'content': prompt},
        ],
        temperature=0.2,
    )
    raw = response.choices[0].message.content or '{}'
    record_openai_call(
        getattr(response, "usage", None),
        operation="chat_completion",
        meta={"channel": channel_name, "model": settings.openai_model},
    )
    return _shape_response(raw)
