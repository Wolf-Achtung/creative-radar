"""Tests fuer den Anthropic-JSON-Retry-Helper ``call_with_json_retry``
in ``anthropic_client.py``. Master-Plan-Schritt-4 Commit 2/N.

Bewusst additiv zu den Pair-Pfad-M2-Tests
(``test_json_parse_retry.py``) — der Helper ist Code-Duplikation des
Pair-Pfad-Retry-Loops, daher braucht er eigene Coverage. Pair-Pfad bleibt
unangetastet.

Vier Garantien:
1. Strict-Parse-Pfad: gueltiges JSON beim ersten Call → kein Retry,
   ``parse_path="strict"``, ein Eintrag in ``call_attempts``.
2. Lenient-Rettung: erstes JSON hat Preamble+Postamble, Substring-Extraktion
   rettet ohne Re-Call → ``parse_path="lenient"``, ein Eintrag.
3. Re-Call rettet: erster Call kaputtes JSON, zweiter Call sauber →
   ``parse_path="strict"``, zwei Eintraege in ``call_attempts``.
4. Total-Fail: alle Calls kaputt → ``parsed=None``, ``parse_error``
   gesetzt, ``call_attempts`` hat ``max_recalls+1`` Eintraege.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import anthropic_client as anthropic_module
from app.services.anthropic_client import JsonRetryResult, call_with_json_retry


def _msg(text: str, in_tokens: int = 1000, out_tokens: int = 200):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=in_tokens, output_tokens=out_tokens),
    )


# ---------------------------------------------------------------------------
# Test 1 — strict-parse: kein Retry, ein Call
# ---------------------------------------------------------------------------

def test_strict_parse_no_retry(monkeypatch):
    mock = MagicMock(return_value=_msg('{"ok": 1}'))
    monkeypatch.setattr(anthropic_module, "messages_create_text", mock)

    result = call_with_json_retry(
        model="claude-opus-4-7",
        system="sys",
        user_message="usr",
        max_recalls=2,
        log_prefix="test",
    )

    assert isinstance(result, JsonRetryResult)
    assert result.parsed == {"ok": 1}
    assert result.parse_path == "strict"
    assert result.parse_error is None
    assert len(result.call_attempts) == 1
    assert mock.call_count == 1


# ---------------------------------------------------------------------------
# Test 2 — lenient: Preamble/Postamble wird substring-rescued, kein Re-Call
# ---------------------------------------------------------------------------

def test_lenient_substring_no_retry(monkeypatch):
    mock = MagicMock(
        return_value=_msg('Here is the JSON:\n{"ok": 1}\nThanks.')
    )
    monkeypatch.setattr(anthropic_module, "messages_create_text", mock)

    result = call_with_json_retry(
        model="claude-opus-4-7",
        system="sys",
        user_message="usr",
        max_recalls=2,
        log_prefix="test",
    )

    assert result.parsed == {"ok": 1}
    assert result.parse_path == "lenient"
    assert len(result.call_attempts) == 1
    assert mock.call_count == 1


# ---------------------------------------------------------------------------
# Test 3 — Re-Call rettet: erste Antwort kaputt, zweite sauber
# ---------------------------------------------------------------------------

def test_recall_recovers_after_invalid_json(monkeypatch):
    mock = MagicMock(side_effect=[
        _msg('not valid json at all{{{'),
        _msg('{"ok": "second"}'),
    ])
    monkeypatch.setattr(anthropic_module, "messages_create_text", mock)

    result = call_with_json_retry(
        model="claude-opus-4-7",
        system="sys",
        user_message="usr",
        max_recalls=2,
        log_prefix="test",
        log_extra={"trace": "abc"},
    )

    assert result.parsed == {"ok": "second"}
    assert result.parse_path == "strict"
    assert len(result.call_attempts) == 2
    assert mock.call_count == 2


# ---------------------------------------------------------------------------
# Test 4 — Total-Fail nach allen Retries
# ---------------------------------------------------------------------------

def test_total_failure_returns_none_with_max_attempts(monkeypatch):
    mock = MagicMock(return_value=_msg('not json'))
    monkeypatch.setattr(anthropic_module, "messages_create_text", mock)

    result = call_with_json_retry(
        model="claude-opus-4-7",
        system="sys",
        user_message="usr",
        max_recalls=2,
        log_prefix="test",
    )

    assert result.parsed is None
    assert result.parse_path == ""
    assert result.parse_error is not None
    # max_recalls=2 → initial-Call + 2 Re-Calls = 3 Attempts gesamt
    assert len(result.call_attempts) == 3
    assert mock.call_count == 3


# ---------------------------------------------------------------------------
# Test 5 — Re-Call raised: break ohne weiteren Versuch
# ---------------------------------------------------------------------------

def test_recall_aborted_on_underlying_exception(monkeypatch):
    """Wenn der zweite ``messages_create_text``-Call selbst raised (z.B.
    Rate-Limit-Exhaustion), bricht der Loop ab — kein dritter Versuch."""
    mock = MagicMock(side_effect=[
        _msg('not json'),
        RuntimeError("rate-limit exhaustion"),
    ])
    monkeypatch.setattr(anthropic_module, "messages_create_text", mock)

    result = call_with_json_retry(
        model="claude-opus-4-7",
        system="sys",
        user_message="usr",
        max_recalls=2,
        log_prefix="test",
    )

    assert result.parsed is None
    assert len(result.call_attempts) == 1  # nur der initiale Call landet drin
    assert mock.call_count == 2  # initial + ein Re-Call (der dann raised)
