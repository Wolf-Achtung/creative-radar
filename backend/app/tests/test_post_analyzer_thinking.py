"""Denkende Modelle im Post-Analyzer.

Hintergrund: Denk-Tokens zaehlen gegen ``max_tokens`` und werden wie
Ausgabe abgerechnet. Ob ein Modell ohne ``thinking``-Parameter denkt,
haengt am Modell — Sonnet 5 denkt, Opus 4.8 und Haiku 4.5 nicht. Der
Wechsel von Sonnet 4.6 auf Sonnet 5 hat das Verhalten also geaendert,
ohne dass eine Zeile Code sich geaendert haette.

Daraus folgen drei Dinge, die hier festgenagelt werden:

1. Denkt ein Modell, steht ein ``thinking``-Block vor dem Text. Der
   frueher blinde Zugriff ``content[0]`` lieferte dann einen leeren
   String — die Antwort dahinter fiel unter den Tisch.
2. Der Denk-Aufwand steht auf den Sonnet-Pfaden hingeschrieben, statt
   aus einer Modell-Voreinstellung zu stammen. Haiku bekommt ihn
   nicht: es kennt den Parameter nicht und quittiert ihn mit 400.
3. Abgeschnittene Antworten werden erkannt statt gespeichert.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services import anthropic_client, post_analyzer
from app.services.post_analyzer import AnalyzePostResult


# ---------- Bausteine --------------------------------------------------


def _thinking_block(text: str = "") -> SimpleNamespace:
    """Wie das SDK einen Denk-Block liefert: eigener Typ, kein ``text``.

    Unter der Voreinstellung ``display="omitted"`` ist ``thinking`` leer;
    mit ``display="summarized"`` steht dort eine Zusammenfassung. Beide
    Faelle duerfen die Textextraktion nicht stoeren.
    """
    return SimpleNamespace(type="thinking", thinking=text, signature="sig")


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _message(*blocks, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=100, output_tokens=30),
    )


def _post() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(), caption="Ein Trailer", platform="youtube", published_at=None
    )


# ---------- 1. Textextraktion ueberspringt Denk-Bloecke ----------------


def test_leading_thinking_block_does_not_swallow_the_answer():
    """Der eigentliche Fund: ``content[0]`` war der Denk-Block."""
    msg = _message(_thinking_block(), _text_block('{"purpose": "awareness"}'))
    assert post_analyzer._message_text(msg) == '{"purpose": "awareness"}'


def test_summarized_thinking_text_is_not_mixed_into_the_answer():
    """Mit ``display="summarized"`` traegt der Denk-Block echten Text —
    der darf nicht in der Antwort landen."""
    msg = _message(
        _thinking_block("Ueberlege: das ist ein Teaser..."),
        _text_block('{"purpose": "awareness"}'),
    )
    assert post_analyzer._message_text(msg) == '{"purpose": "awareness"}'


def test_multiple_text_blocks_are_joined():
    msg = _message(_text_block('{"a": 1,'), _text_block(' "b": 2}'))
    assert post_analyzer._message_text(msg) == '{"a": 1, "b": 2}'


def test_legacy_blocks_without_type_still_work():
    """Bestandsaufrufe und aeltere Tests liefern Bloecke ohne ``type``.
    Die sollen weiter durchgehen, sonst bricht die halbe Suite."""
    assert post_analyzer._message_text(_message(SimpleNamespace(text="hallo"))) == "hallo"
    assert post_analyzer._message_text({"content": [{"text": "hallo"}]}) == "hallo"


def test_empty_and_missing_content_stay_empty():
    assert post_analyzer._message_text(None) == ""
    assert post_analyzer._message_text(_message()) == ""
    assert post_analyzer._message_text(_message(_thinking_block())) == ""


# ---------- 2. Denk-Aufwand steht hingeschrieben -----------------------


def test_sonnet_classification_declares_effort_and_a_real_ceiling():
    result = AnalyzePostResult(post_id=uuid4())
    fake = MagicMock(return_value=_message(_text_block('{"purpose": "awareness"}')))

    with patch.object(post_analyzer, "messages_create_text", fake), \
         patch.object(post_analyzer, "record_anthropic_call"):
        post_analyzer._classify_purpose_lifecycle(_post(), result)

    kwargs = fake.call_args.kwargs
    assert kwargs["effort"] == "low"
    assert kwargs["max_tokens"] == post_analyzer.CLASSIFY_MAX_TOKENS
    # Der alte Wert liess den Denk-Tokens kaum Luft vor der Antwort.
    assert kwargs["max_tokens"] > 256


def test_sonnet_vision_declares_effort_and_a_real_ceiling():
    result = AnalyzePostResult(post_id=uuid4())
    fake = MagicMock(return_value=_message(_text_block("Ein dunkles Bild.")))

    with patch.object(post_analyzer, "messages_create_vision", fake), \
         patch.object(post_analyzer, "record_anthropic_call"):
        post_analyzer._describe_vision(_post(), "https://example.test/a.jpg", result)

    kwargs = fake.call_args.kwargs
    assert kwargs["effort"] == "low"
    assert kwargs["max_tokens"] == post_analyzer.VISION_MAX_TOKENS
    assert kwargs["max_tokens"] > 400


def test_haiku_never_gets_the_effort_parameter():
    """Haiku 4.5 kennt ``effort`` nicht — mitschicken waere ein 400er."""
    result = AnalyzePostResult(post_id=uuid4())
    fake = MagicMock(return_value=_message(_text_block('{"format": "trailer"}')))

    with patch.object(post_analyzer, "messages_create_text", fake), \
         patch.object(post_analyzer, "record_anthropic_call"):
        post_analyzer._classify_format_tone(_post(), result)

    assert "effort" not in fake.call_args.kwargs


def test_effort_reaches_the_request_body_only_when_asked():
    """Im Wrapper: ohne ``effort`` bleibt ``output_config`` weg."""
    assert anthropic_client._effort_kwargs(None) == {}
    assert anthropic_client._effort_kwargs("") == {}
    assert anthropic_client._effort_kwargs("low") == {"output_config": {"effort": "low"}}


@pytest.mark.parametrize("shape", ["text", "vision"])
def test_wrapper_passes_output_config_through(monkeypatch: pytest.MonkeyPatch, shape: str):
    captured: dict = {}

    class _FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _message(_text_block("ok"))

    monkeypatch.setattr(
        anthropic_client, "_client", lambda: SimpleNamespace(messages=_FakeMessages())
    )

    if shape == "text":
        anthropic_client.messages_create_text(
            model="claude-sonnet-5", system="s", user_message="u", effort="low"
        )
    else:
        anthropic_client.messages_create_vision(
            model="claude-sonnet-5", system="s", user_message="u",
            image_url="https://example.test/a.jpg", effort="low",
        )

    assert captured["output_config"] == {"effort": "low"}


# ---------- 3. Abgeschnittenes wird erkannt ----------------------------


def test_truncation_is_detected():
    assert post_analyzer._was_truncated(_message(stop_reason="max_tokens")) is True
    assert post_analyzer._was_truncated(_message(stop_reason="end_turn")) is False
    assert post_analyzer._was_truncated(None) is False


def test_truncated_vision_description_is_discarded_not_stored():
    """Die eigentliche stille Luecke: ein Fragment sah aus wie eine
    kurze Beschreibung und wanderte unbemerkt in die Auswertung."""
    result = AnalyzePostResult(post_id=uuid4())
    fragment = _message(
        _text_block("Ein dunkles Bild mit einem Mann, der"),
        stop_reason="max_tokens",
    )

    with patch.object(post_analyzer, "messages_create_vision", return_value=fragment), \
         patch.object(post_analyzer, "record_anthropic_call"):
        got = post_analyzer._describe_vision(
            _post(), "https://example.test/a.jpg", result
        )

    assert got is None
    assert any("vision-truncated" in e for e in result.errors)


def test_complete_vision_description_still_passes():
    result = AnalyzePostResult(post_id=uuid4())
    whole = _message(_text_block("  Ein dunkles Bild.  "), stop_reason="end_turn")

    with patch.object(post_analyzer, "messages_create_vision", return_value=whole), \
         patch.object(post_analyzer, "record_anthropic_call"):
        got = post_analyzer._describe_vision(
            _post(), "https://example.test/a.jpg", result
        )

    assert got == "Ein dunkles Bild."
    assert result.errors == []


def test_truncated_classification_names_the_cause():
    """Abgeschnittenes JSON und Schrott-JSON sehen fuer den Parser
    gleich aus — die Diagnose muss sie trennen, weil sie zu
    verschiedenen Gegenmassnahmen fuehren."""
    result = AnalyzePostResult(post_id=uuid4())
    cut = _message(_text_block('{"purpose": "aware'), stop_reason="max_tokens")

    with patch.object(post_analyzer, "messages_create_text", return_value=cut), \
         patch.object(post_analyzer, "record_anthropic_call"):
        got = post_analyzer._classify_purpose_lifecycle(_post(), result)

    assert got is None
    assert any("sonnet-truncated" in e for e in result.errors)
    assert not any("sonnet-invalid-json" in e for e in result.errors)


def test_plain_bad_json_keeps_its_own_diagnosis():
    result = AnalyzePostResult(post_id=uuid4())
    junk = _message(_text_block("Sorry, ich kann das nicht."), stop_reason="end_turn")

    with patch.object(post_analyzer, "messages_create_text", return_value=junk), \
         patch.object(post_analyzer, "record_anthropic_call"):
        got = post_analyzer._classify_purpose_lifecycle(_post(), result)

    assert got is None
    assert any("sonnet-invalid-json" in e for e in result.errors)
