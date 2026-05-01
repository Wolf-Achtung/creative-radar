"""Tests for the W3 Hebel-A language whitelist in services/creative_ai.py.
The whitelist collapses free-form model output into ISO codes; production
showed the model writing whole descriptive sentences into the language
column."""
from __future__ import annotations

from app.services.creative_ai import _normalize_language


def test_iso_code_passthrough() -> None:
    assert _normalize_language("de") == "de"
    assert _normalize_language("en") == "en"
    assert _normalize_language("ES") == "es"
    assert _normalize_language("UNKNOWN") == "unknown"


def test_word_form_maps_to_iso() -> None:
    assert _normalize_language("English") == "en"
    assert _normalize_language("German") == "de"
    assert _normalize_language("Deutsch") == "de"
    assert _normalize_language("Spanish") == "es"
    assert _normalize_language("Japanese") == "ja"


def test_hallucinated_sentence_collapses() -> None:
    """Real production sample: 'English (caption); likely mixed with Spanish
    context due to CDMX' must NOT land in the DB verbatim."""
    assert _normalize_language(
        "English (caption); likely mixed with Spanish context due to CDMX"
    ) == "en"


def test_meta_description_collapses_to_unknown() -> None:
    """Real production sample: 'Unknown (ohne sichtbaren Post-Text/Caption
    nicht verifizierbar)' has no useful ISO content and must collapse."""
    assert _normalize_language(
        "Unknown (ohne sichtbaren Post-Text/Caption nicht verifizierbar)"
    ) == "unknown"


def test_falsy_inputs() -> None:
    assert _normalize_language(None) == "unknown"
    assert _normalize_language("") == "unknown"
    assert _normalize_language("   ") == "unknown"


def test_unrecognised_language_falls_to_unknown() -> None:
    assert _normalize_language("klingon") == "unknown"
    assert _normalize_language("nonsense gibberish") == "unknown"


def test_iso_token_inside_punctuation() -> None:
    """An ISO code mixed in with punctuation should still be picked up."""
    assert _normalize_language("(en),(other)") == "en"
