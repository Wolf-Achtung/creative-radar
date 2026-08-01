"""Request-Body-Assertions fuer die zentralen Cache-Breakpoints.

Die Breakpoints sitzen in ``services/anthropic_client.py`` in den beiden
Wrappern ``messages_create_text`` (deckt Roundup, Cutter-Weekly,
Designer-Weekly ueber ``call_with_json_retry``) und
``messages_create_strict_json`` (deckt Pair-Brief und Title-Brief).

Getestet wird der tatsaechlich an die API gehende Request-Body, nicht das
Wrapper-Interface — genau dort entscheidet sich, ob der Prefix cachebar ist.
Der Kill-Switch ``ANTHROPIC_PROMPT_CACHING`` muss byte-identisch das
vorherige Format erzeugen, sonst ist er als Rollback wertlos.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services import anthropic_client as ac


def _count_cache_markers(obj: Any) -> int:
    """Zaehlt ``cache_control``-Schluessel rekursiv im gesamten Body."""
    if isinstance(obj, dict):
        here = 1 if "cache_control" in obj else 0
        return here + sum(_count_cache_markers(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_count_cache_markers(v) for v in obj)
    return 0


@pytest.fixture()
def captured_body(monkeypatch: pytest.MonkeyPatch):
    """Faengt die kwargs von ``client.messages.create`` ab."""
    calls: list[dict[str, Any]] = []

    fake_client = MagicMock()

    def _create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return MagicMock()

    fake_client.messages.create.side_effect = _create
    monkeypatch.setattr(ac, "_client", lambda: fake_client)

    def _run(
        *,
        caching: bool,
        strict: bool = False,
        system: str = "SYSTEM-PROMPT",
        user_message: str = "USER-PAYLOAD",
    ) -> dict[str, Any]:
        monkeypatch.setattr(ac.settings, "anthropic_prompt_caching", caching, raising=False)
        calls.clear()
        if strict:
            ac.messages_create_strict_json(
                model="claude-opus-4-8",
                system=system,
                user_message=user_message,
                tool_name="submit_x",
                tool_description="desc",
                input_schema={"type": "object", "properties": {}},
            )
        else:
            ac.messages_create_text(
                model="claude-opus-4-8",
                system=system,
                user_message=user_message,
            )
        return calls[0]

    return _run


# ---------- Flag AN: genau zwei Breakpoints ----------


@pytest.mark.parametrize("strict", [False, True])
def test_two_breakpoints_when_enabled(captured_body, strict: bool) -> None:
    """BP1 am Ende des System-Prompts, BP2 auf dem letzten User-Block."""
    body = captured_body(caching=True, strict=strict)

    assert _count_cache_markers(body) == 2

    # BP1 — system ist eine Block-Liste, Marker auf dem letzten Block.
    assert isinstance(body["system"], list)
    assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert body["system"][-1]["text"] == "SYSTEM-PROMPT"

    # BP2 — User-Content ist eine Block-Liste, Marker auf dem letzten Block.
    content = body["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[-1]["cache_control"] == {"type": "ephemeral"}
    assert content[-1]["text"] == "USER-PAYLOAD"


# ---------- Flag AUS: exakt das vorherige Format ----------


@pytest.mark.parametrize("strict", [False, True])
def test_no_breakpoints_when_disabled(captured_body, strict: bool) -> None:
    """Kill-Switch muss das Vor-Caching-Format byte-identisch erzeugen:
    ``system`` als String, User-Content als String, kein ``cache_control``."""
    body = captured_body(caching=False, strict=strict)

    assert _count_cache_markers(body) == 0
    assert body["system"] == "SYSTEM-PROMPT"
    assert body["messages"] == [{"role": "user", "content": "USER-PAYLOAD"}]


# ---------- Guards ----------


def test_empty_system_yields_only_user_breakpoint(captured_body) -> None:
    """Leere Textbloecke sind nicht cachebar — BP1 entfaellt, BP2 bleibt."""
    body = captured_body(caching=True, system="   ")

    assert _count_cache_markers(body) == 1
    assert body["system"] == "   "  # unveraendert durchgereicht
    assert body["messages"][0]["content"][-1]["cache_control"] == {"type": "ephemeral"}


def test_empty_user_message_yields_only_system_breakpoint(captured_body) -> None:
    """Ohne User-Content-Block gibt es nichts, worauf BP2 gehoeren koennte —
    kein Fehler, der Marker entfaellt einfach."""
    body = captured_body(caching=True, user_message="")

    assert _count_cache_markers(body) == 1
    assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"] == [{"role": "user", "content": ""}]


# ---------- tools/tool_choice bleiben unangetastet ----------


@pytest.mark.parametrize("caching", [True, False])
def test_tools_and_tool_choice_untouched(captured_body, caching: bool) -> None:
    """Caching darf ``tools``/``tool_choice`` nicht beruehren — ein
    abweichendes tools-Array wuerde den gesamten Prefix unique machen."""
    body = captured_body(caching=caching, strict=True)

    assert body["tools"] == [
        {
            "name": "submit_x",
            "description": "desc",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    assert body["tool_choice"] == {
        "type": "tool",
        "name": "submit_x",
        "disable_parallel_tool_use": True,
    }
    # Kein Marker im tools-Teilbaum.
    assert _count_cache_markers(body["tools"]) == 0
    assert _count_cache_markers(body["tool_choice"]) == 0


def test_model_and_max_tokens_unchanged(captured_body) -> None:
    """Regression: Caching fasst sonst nichts am Body an."""
    body = captured_body(caching=True)
    assert body["model"] == "claude-opus-4-8"
    assert body["max_tokens"] == 256
