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


def _asset_type(value: str | None) -> AssetType:
    if not value:
        return AssetType.UNKNOWN
    clean = value.strip().lower()
    for item in AssetType:
        if clean == item.value.lower():
            return item
    return AssetType.UNKNOWN


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

Antworte nur als JSON mit exakt diesen Feldern:
asset_type, language, ai_summary_de, ai_summary_en, ai_trend_notes, confidence_score
"""
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {'role': 'system', 'content': 'Du bist ein präziser Creative-Analyst für Film-, Serien- und Game-Marketing.'},
            {'role': 'user', 'content': prompt},
        ],
        temperature=0.2,
    )
    raw = response.choices[0].message.content or '{}'
    data = _safe_json(raw)
    return {
        'asset_type': _asset_type(data.get('asset_type')),
        'language': data.get('language') or 'Unknown',
        'ai_summary_de': data.get('ai_summary_de') or 'Keine belastbare Zusammenfassung erzeugt.',
        'ai_summary_en': data.get('ai_summary_en') or '',
        'ai_trend_notes': data.get('ai_trend_notes') or '',
        'confidence_score': float(data.get('confidence_score') or 0.5),
        'review_status': ReviewStatus.NEW,
    }
