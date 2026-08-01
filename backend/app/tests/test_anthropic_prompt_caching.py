"""Request-Body-Assertions fuer den zentralen Cache-Breakpoint.

Der Breakpoint sitzt in ``services/anthropic_client.py`` in den beiden
Wrappern ``messages_create_text`` (deckt Roundup, Cutter-Weekly,
Designer-Weekly ueber ``call_with_json_retry``) und
``messages_create_strict_json`` (deckt Pair-Brief und Title-Brief), jeweils
am Ende des System-Prompts.

Genau EIN Marker je Request ist die Vorgabe. Ein zweiter Breakpoint auf dem
letzten User-Block wurde nach Auswertung von 30 Tagen costlog verworfen: bei
~8 % Retry-Rate gegen eine Rentabilitaetsschwelle von ~22 % Read-Anteil waere
der Write-Aufschlag auf die ~117k-Token-Payload teurer als die eingesparten
Retries. Die Tests fixieren das, damit der Marker nicht versehentlich
zurueckkommt.

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


# ---------- Flag AN: genau ein Breakpoint ----------


@pytest.mark.parametrize("strict", [False, True])
def test_single_breakpoint_when_enabled(captured_body, strict: bool) -> None:
    """Genau ein Marker, am Ende des System-Prompts."""
    body = captured_body(caching=True, strict=strict)

    assert _count_cache_markers(body) == 1

    # system ist eine Block-Liste, Marker auf dem letzten Block.
    assert isinstance(body["system"], list)
    assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert body["system"][-1]["text"] == "SYSTEM-PROMPT"


@pytest.mark.parametrize("strict", [False, True])
def test_user_message_carries_no_marker(captured_body, strict: bool) -> None:
    """Der User-Block bleibt der schlichte String — kein zweiter Breakpoint.

    Regression gegen ein versehentliches Wiedereinfuehren von BP2: bei der
    gemessenen Retry-Rate waere der Write-Aufschlag auf die Payload teurer
    als die eingesparten Retries.
    """
    body = captured_body(caching=True, strict=strict)

    assert body["messages"] == [{"role": "user", "content": "USER-PAYLOAD"}]
    assert _count_cache_markers(body["messages"]) == 0


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


def test_empty_system_yields_no_breakpoint(captured_body) -> None:
    """Leere Textbloecke sind nicht cachebar — der Marker entfaellt, der
    Prompt geht unveraendert als String raus. Kein Fehler."""
    body = captured_body(caching=True, system="   ")

    assert _count_cache_markers(body) == 0
    assert body["system"] == "   "  # unveraendert durchgereicht
    assert body["messages"] == [{"role": "user", "content": "USER-PAYLOAD"}]


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
