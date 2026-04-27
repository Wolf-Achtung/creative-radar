import json
from typing import Any

from openai import OpenAI

from app.config import settings
from app.models.entities import AssetType, ReviewStatus


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


def _asset_type(value: Any) -> AssetType:
    if isinstance(value, AssetType):
        return value
    if not value:
        return AssetType.UNKNOWN
    clean = str(value).strip().lower()
    for item in AssetType:
        if clean in {item.value.lower(), item.name.lower()}:
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
        return {
            'asset_type': asset_type_hint,
            'language': 'Unknown',
            'ai_summary_de': 'OpenAI ist noch nicht konfiguriert. Der Treffer wurde angelegt und sollte manuell geprüft werden.',
            'ai_summary_en': 'OpenAI is not configured yet. Manual review required.',
            'ai_trend_notes': 'Nach Setzen von OPENAI_API_KEY in Railway wird diese Zusammenfassung automatisch erzeugt.',
            'confidence_score': 0.2,
            'review_status': ReviewStatus.NEEDS_REVIEW,
        }

    client = OpenAI(api_key=settings.openai_api_key)
    prompt = f"""
Analysiere diesen Social-Media-Creative-Treffer aus Film-, Serien- oder Game-Marketing.

Post-Link: {post_url}
Kanal: {channel_name}
Markt: {market}
Titel/Franchise: {title_name or 'nicht vorausgewählt'}
Asset-Typ-Hinweis: {asset_type_hint.value}
Caption/Seitentext: {caption or 'nicht verfügbar'}
Sichtbarer Text/OCR: {ocr_text or 'nicht verfügbar'}

Liefere eine sachliche, kurze Analyse für ein internes Kreativteam.
Keine Klickzahlen, keine Erfolgsbehauptungen, keine harten Bewertungen.

Antworte nur als JSON mit exakt diesen Feldern. Alle Textfelder müssen Strings sein, keine Arrays:
asset_type, language, ai_summary_de, ai_summary_en, ai_trend_notes, confidence_score
"""
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {'role': 'system', 'content': 'Du bist ein präziser Creative-Analyst für Film-, Serien- und Game-Marketing. Du gibst valides JSON zurück. Textfelder sind immer Strings.'},
            {'role': 'user', 'content': prompt},
        ],
        temperature=0.2,
    )
    raw = response.choices[0].message.content or '{}'
    data = _safe_json(raw)
    return {
        'asset_type': _asset_type(data.get('asset_type')),
        'language': _as_text(data.get('language'), 'Unknown')[:64],
        'ai_summary_de': _as_text(data.get('ai_summary_de'), 'Keine belastbare Zusammenfassung erzeugt.'),
        'ai_summary_en': _as_text(data.get('ai_summary_en'), ''),
        'ai_trend_notes': _as_text(data.get('ai_trend_notes'), ''),
        'confidence_score': _confidence(data.get('confidence_score')),
        'review_status': ReviewStatus.NEW,
    }
