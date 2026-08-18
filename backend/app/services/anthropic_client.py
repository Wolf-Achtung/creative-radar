"""Thin wrapper around ``anthropic.Anthropic``.

Sprint 5.3.1 Mini-Run 2. The wrapper exists for three reasons:

1. **Single point for typed errors.** The SDK raises a handful of
   exception types (AuthenticationError, RateLimitError, APIError);
   we re-raise as ``AnthropicAuthError`` / ``AnthropicRateLimitError``
   / ``AnthropicAPIError`` so the analyze endpoint maps them to
   401/429/500 without importing the SDK directly.

2. **Lazy SDK import.** ``import anthropic`` lives inside the wrapper
   functions so the rest of the app boots even if the package is
   missing (e.g. on a dev machine that hasn't run pip install). The
   admin endpoint already uses this lazy pattern for the YouTube
   connector — same shape here.

3. **Retry-with-backoff for rate limits.** ``call_with_retry`` adds
   3 retries with exponential backoff (1s, 2s, 4s) on rate-limit
   responses; auth/other errors raise immediately. Per Wolf's spec
   in Sprint 5.3.1: rate limit -> retry, fail-then-skip-and-log
   for the calling row; never crash the whole batch.

Cost-logging is NOT done here — the caller (post_analyzer) owns the
operation-name / post-id context, so it calls ``record_anthropic_call``
with the message.usage object after each successful call. The wrapper
returns the raw Message so the caller has access to the usage.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional
import time
from typing import Any, Callable, TypeVar

from app.config import settings

logger = logging.getLogger(__name__)


# ---------- Errors -----------------------------------------------------


class AnthropicAPIError(RuntimeError):
    """Generic Anthropic failure (network, 5xx, unexpected payload)."""


class AnthropicAuthError(AnthropicAPIError):
    """401 — API key missing or invalid."""


class AnthropicRateLimitError(AnthropicAPIError):
    """429 — rate limit exhausted after the wrapper's retries."""


# ---------- Configuration probe ---------------------------------------


def is_anthropic_configured() -> bool:
    return bool(settings.anthropic_api_key)


# ---------- Client construction ---------------------------------------


def _client() -> Any:
    """Build a fresh ``anthropic.Anthropic`` instance. Cheap — keep
    the SDK import inside this function so a missing package only
    breaks the analyze endpoint, not the whole admin router."""
    if not settings.anthropic_api_key:
        raise AnthropicAuthError(
            "ANTHROPIC_API_KEY ist nicht gesetzt. Bitte in Railway konfigurieren."
        )
    import anthropic  # local import — lazy by design

    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


# ---------- Retry plumbing --------------------------------------------


T = TypeVar("T")


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> T:
    """Run ``fn``; on rate-limit, sleep with exponential backoff and
    retry up to ``max_retries`` times. Auth errors and other API
    errors raise immediately — they aren't transient.

    The wrapper imports the SDK exception classes inside the loop so
    a missing package surfaces a clean ImportError rather than a
    NameError at module load time.
    """
    import anthropic

    attempt = 0
    while True:
        try:
            return fn()
        except anthropic.AuthenticationError as exc:
            raise AnthropicAuthError(str(exc)) from exc
        except anthropic.RateLimitError as exc:
            attempt += 1
            if attempt > max_retries:
                raise AnthropicRateLimitError(
                    f"rate limit after {max_retries} retries: {exc}"
                ) from exc
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "anthropic-rate-limit-retry",
                extra={"attempt": attempt, "delay_seconds": delay},
            )
            time.sleep(delay)
        except anthropic.APIError as exc:
            # Includes APIConnectionError, APIStatusError 5xx, etc.
            # Bubble up as our generic error type — the caller decides
            # whether to skip the row or fail the request.
            raise AnthropicAPIError(str(exc)) from exc


# ---------- Prompt-Caching --------------------------------------------

# Cache-Prefix-Reihenfolge der API ist ``tools -> system -> messages``.
# Der Breakpoint wird hier zentral gesetzt, damit die Call-Sites unveraendert
# bleiben (Diagnose 2026-08-01):
#
#   Ende des System-Prompts. Deckt den grossen statischen Anteil ab
#   (Pair-Brief ~13k Token, Roundup ~6.7k, Title-Brief ~5.6k).
#
# Bewusst NICHT gesetzt: ein zweiter Breakpoint auf dem letzten User-Block.
# Er wuerde die Payload ueber Parse-Recalls hinweg cachen, rechnet sich bei
# der gemessenen Last aber nicht. 30 Tage costlog: weekly_brief kam auf 39
# Aufrufe bei 9 Pairs x 4 Laeufen, also ~8 % Retry-Rate. Die Schwelle liegt
# bei ~22 % Read-Anteil (1.25x Write gegen 0.1x Read bei 1.0x Baseline) —
# bei ~117k Token Payload je Call stuenden ~5,30 USD/30d Write-Aufschlag
# nur ~1,60 USD Retry-Ersparnis gegenueber. Steigt die Retry-Rate deutlich
# oder faellt die Payload, lohnt eine Neubewertung.
#
# Ebenfalls bewusst NICHT gemacht: ein Split am Ende von ``BRIEF_VOICE``.
# ``tools`` rendert vor ``system``, und die Call-Sites haben unterschiedliche
# tools-Zustaende (Pair-Brief und Title-Brief je ein eigenes Schema, Roundup/
# Cutter/Designer gar keins) — die Prefixe divergieren also bereits an
# Position 0, Cross-Call-Site-Sharing ist damit ohnehin ausgeschlossen.
#
# TTL: 5 Minuten (Default). Die Calls laufen sequenziell mit Abstaenden
# darunter, und jede Nutzung frischt den Eintrag kostenlos auf.
_CACHE_CONTROL: dict[str, str] = {"type": "ephemeral"}


def _prompt_caching_enabled() -> bool:
    return bool(getattr(settings, "anthropic_prompt_caching", True))


def _cacheable_system(system: str) -> Any:
    """``system`` als Content-Block-Liste mit Breakpoint am Ende.

    Faellt auf den unveraenderten String zurueck, wenn Caching aus ist oder
    der Prompt leer/whitespace-only waere — leere Textbloecke sind nicht
    cachebar.
    """
    if not _prompt_caching_enabled() or not (system or "").strip():
        return system
    return [{"type": "text", "text": system, "cache_control": _CACHE_CONTROL}]


# ---------- High-level call shapes ------------------------------------


def _effort_kwargs(effort: Optional[str]) -> dict[str, Any]:
    """``output_config`` nur mitschicken, wenn ein Aufrufer es will.

    ``effort`` steuert, wie tief ein Modell vor der Antwort nachdenkt.
    Denk-Tokens zaehlen gegen ``max_tokens`` und werden wie Ausgabe
    abgerechnet — bei knappen Limits draengen sie die eigentliche
    Antwort heraus.

    Bewusst opt-in statt Default, aus zwei Gruenden:

    * Haiku 4.5 kennt den Parameter nicht und quittiert ihn mit einem
      400er. Ein globaler Default wuerde die Format-/Tonalitaets-
      Klassifikation sofort zerlegen.
    * Ob ein Modell ohne Angabe ueberhaupt denkt, haengt am Modell:
      Sonnet 5 denkt per Voreinstellung, Opus 4.8 und Haiku 4.5 nicht.
      Wer das Verhalten festnageln will, schreibt es hin.
    """
    return {"output_config": {"effort": effort}} if effort else {}


def messages_create_text(
    *,
    model: str,
    system: str,
    user_message: str,
    max_tokens: int = 256,
    effort: Optional[str] = None,
) -> Any:
    """Single-text-message Messages API call. Returns the raw Message
    so the caller can read the text blocks and ``usage``.

    PR #123 wired an ``Idempotency-Key`` header as defense-in-depth
    against retry-echo; the 2026-05-12 smoke-test proved Anthropic does
    not honor the header (two billed calls 5s apart with identical key).
    The Postgres advisory-lock in ``generate_and_persist_report`` is now
    the single source of concurrency protection. Drop the dead code
    here — if Anthropic later publishes an idempotency contract we can
    re-introduce it under their official header name.
    """
    client = _client()

    def _do() -> Any:
        return client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_cacheable_system(system),
            messages=[{"role": "user", "content": user_message}],
            **_effort_kwargs(effort),
        )

    return call_with_retry(_do)


def messages_create_strict_json(
    *,
    model: str,
    system: str,
    user_message: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict[str, Any],
    max_tokens: int = 20000,
) -> Any:
    """Messages-API-Call mit API-erzwungenem JSON via Tool-Use.

    Anthropic kennt kein OpenAI-aequivalentes ``response_format`` mit
    direktem JSON-Schema in der Messages-API (das SDK-Feld
    ``output_config.format.json_schema`` ist neuer und im Feld weniger
    getestet — Wolf-Entscheid Briefing 28.05.). Stattdessen forciert
    Tool-Use mit ``tool_choice={"type": "tool", "name": ...,
    "disable_parallel_tool_use": True}`` Schema-konforme Antworten: das
    Modell MUSS das genannte Tool aufrufen, und Anthropic validiert die
    Tool-Argumente gegen ``input_schema`` vor dem Return.

    Ergebnis:
    - ``msg.content`` enthaelt einen ``tool_use``-Block mit ``.input``
      als bereits geparstes Dict.
    - Falls aus irgendeinem Grund kein Tool-Use-Block kommt (sollte mit
      ``disable_parallel_tool_use`` nicht passieren, aber API-Drift),
      bleibt der Text-Fallback-Pfad im Caller erhalten — siehe Pair-
      Pipeline-Extraktion in ``insight_engine._call_and_extract``.

    Schema-Quelle: ``LLMReport.model_json_schema()`` (Pydantic v2)
    erzeugt JSON-Schema-Draft-2020-12 mit ``$defs`` fuer geschachtelte
    Sub-Modelle — Anthropic Tool-Input-Schemas unterstuetzen das nativ.

    Cost-Logging: passiert weiterhin im Caller via
    ``record_anthropic_call(msg.usage, ...)`` analog zum Text-Pfad.
    """
    client = _client()

    def _do() -> Any:
        return client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_cacheable_system(system),
            messages=[{"role": "user", "content": user_message}],
            tools=[
                {
                    "name": tool_name,
                    "description": tool_description,
                    "input_schema": input_schema,
                }
            ],
            tool_choice={
                "type": "tool",
                "name": tool_name,
                "disable_parallel_tool_use": True,
            },
        )

    return call_with_retry(_do)


def messages_create_vision(
    *,
    model: str,
    system: str,
    user_message: str,
    image_url: str,
    max_tokens: int = 400,
    effort: Optional[str] = None,
) -> Any:
    """Messages API call with one image content block + one text block.
    The image is passed by URL — Anthropic fetches it server-side, no
    base64 round-trip on our side.
    """
    client = _client()

    def _do() -> Any:
        return client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "url", "url": image_url},
                        },
                        {"type": "text", "text": user_message},
                    ],
                }
            ],
            **_effort_kwargs(effort),
        )

    return call_with_retry(_do)


# ---------- M2 JSON-parse-retry helper (Master-Plan-Schritt-4) ---------

# Bewusste Code-Duplikation des Pair-Pfad-M2-Retry-Helpers
# (Wolf-Festlegung Ping 1, 25.05.). Die Logik existiert seit Schritt M2 in
# ``insight_engine.generate_weekly_report`` als inline-Closure + Retry-Loop;
# der Pair-Pfad bleibt davon unberuehrt (Pair-Tabu). Der Helper hier ist
# additiv, wird vom Roundup-Pfad konsumiert. Wenn die Pair-Logik spaeter
# evolviert (z.B. MAX_RECALLS-Tuning, neue Log-Felder), muss diese Kopie
# explizit nachgezogen werden — kein automatischer DRY-Sync.


def _strip_codefence(text: str) -> str:
    """Tolerate a ```json ... ``` wrap if the model adds one despite the
    "no Markdown" instruction. Duplikat von ``insight_engine._strip_codefence``;
    siehe Modul-Doku oben fuer den Pair-Tabu-Hintergrund.
    """
    t = text.strip()
    if t.startswith("```"):
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1:]
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()


def _try_parse_llm_json(
    raw_text: str,
) -> tuple[Optional[Any], Optional[json.JSONDecodeError], str]:
    """Strict + lenient JSON-Parsing. Duplikat von
    ``insight_engine._try_parse_llm_json``; semantisch identisch.

    Returns ``(parsed, error, parse_path)``:
    - ``parsed``: Python-Objekt bei Erfolg, sonst ``None``.
    - ``error``: letzter ``json.JSONDecodeError`` bei Total-Fehler.
    - ``parse_path``: ``"strict"`` / ``"lenient"`` / ``""``.
    """
    cleaned = _strip_codefence(raw_text)
    try:
        return json.loads(cleaned), None, "strict"
    except json.JSONDecodeError as strict_exc:
        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first != -1 and last > first:
            substring = cleaned[first:last + 1]
            if substring != cleaned:
                try:
                    return json.loads(substring), None, "lenient"
                except json.JSONDecodeError:
                    pass
        return None, strict_exc, ""


def _unwrap_single_key(parsed: Any, *, expected_field: str) -> Any:
    """Defensive net for the ``{"<wrapper>": {<flat report>}}`` mis-
    structuring Opus emits under forced tool-use (observed 2026-06-01,
    regression from #189): the whole report arrives nested under one stray
    top-level key (e.g. ``what``) instead of flat, so ``model_validate``
    fails deterministically and the brief never persists.

    Unwraps ONLY when ALL of the following hold:
      - ``parsed`` is a ``dict`` with EXACTLY one top-level key,
      - that key's value is itself a ``dict``,
      - that inner dict contains ``expected_field`` (e.g. ``"headline"``).

    Returns ``parsed`` unchanged in every other case, so:
      - a legitimate flat report (many top-level keys) is never touched,
      - a single-key wrapper whose value is NOT a dict (the string-valued
        ``{"what": "..."}`` capture) is left alone to fail validation
        loudly rather than being silently mis-unwrapped.

    The identity of the return value (``result is parsed``) tells the
    caller whether the net fired, so it can emit a WARNING — the net
    staying hot must stay visible until the prompt-side fix (A) proves it
    redundant.
    """
    if not isinstance(parsed, dict) or len(parsed) != 1:
        return parsed
    only_value = next(iter(parsed.values()))
    if isinstance(only_value, dict) and expected_field in only_value:
        return only_value
    return parsed


@dataclass
class JsonRetryResult:
    """Ergebnis von ``call_with_json_retry``.

    - ``parsed``: ``dict``/``list`` (geparstes JSON-Objekt) oder ``None``
      wenn alle Versuche fehlschlugen.
    - ``call_attempts``: Liste von ``(message, raw_text)``-Tupeln in der
      Reihenfolge der Aufrufe. Caller iteriert hier durch, um pro Call
      ``record_anthropic_call(usage, operation=..., meta=...)`` zu fahren —
      damit landet jeder Retry im costlog, F0.7-Cap erfasst die wahre
      Spend-Summe.
    - ``parse_error``: letzter beobachteter ``JSONDecodeError`` (None,
      wenn ``parsed`` gesetzt).
    - ``parse_path``: ``"strict"`` (codefence-strip + ``json.loads``
      reichte), ``"lenient"`` (Substring-Extraktion vom ersten ``{``
      bis letzten ``}`` rettete), ``""`` (nichts ging).
    """
    parsed: Optional[Any]
    call_attempts: list[tuple[Any, str]] = field(default_factory=list)
    parse_error: Optional[json.JSONDecodeError] = None
    parse_path: str = ""


def call_with_json_retry(
    *,
    model: str,
    system: str,
    user_message: str,
    max_tokens: int = 12000,
    max_recalls: int = 2,
    log_prefix: str = "anthropic-json",
    log_extra: Optional[dict] = None,
) -> JsonRetryResult:
    """LLM-Call mit JSON-Parse-Retry-Loop.

    Mechanik (analog ``insight_engine.generate_weekly_report``-M2-Pfad):
    1. ``messages_create_text`` mit den uebergebenen Parametern.
    2. Text aus content-Blocks extrahieren.
    3. ``_try_parse_llm_json`` (strict → lenient).
    4. Bei Parse-Fehler bis zu ``max_recalls`` frische
       ``messages_create_text``-Calls. Anthropic honoriert keine
       Idempotency-Keys (siehe ``messages_create_text``-Doc), jeder Call
       liefert eine echte neue Completion.
    5. Falls ein Re-Call selbst raised (Rate-Limit, API-Error), break
       und log ``{log_prefix}-recall-aborted``.

    Logging:
    - ``{log_prefix}-call-start`` / ``-call-done`` pro Attempt (mit
      ``log_extra`` + ``attempt`` + ``duration_ms``).
    - ``{log_prefix}-parse-retry`` pro Retry-Versuch.
    - ``{log_prefix}-parse-recovered`` wenn parse_path=lenient ODER
      recall_count > 0.

    Return ``JsonRetryResult``. Caller:
    - validiert ``parsed`` gegen sein Pydantic-Schema.
    - iteriert ``call_attempts`` fuer Cost-Tracking
      (``record_anthropic_call`` pro usage).
    - extrahiert ``raw_text`` aus dem LETZTEN ``call_attempts``-Tupel
      fuer ``raw_for_response`` bei Schema-Fail oder Total-Parse-Fail.
    - logged sein eigenes ``{log_prefix}-parse-failed``-Event mit den
      ``raw_response_*``-Diagnose-Feldern (Caller kennt seinen Kontext
      besser — pair_key, segment, etc. — und packt das passend in
      ``extra``).
    """
    extra_base: dict = dict(log_extra or {})

    def _call_and_extract(attempt_index: int) -> tuple[Any, str]:
        attempt_extra = {**extra_base, "attempt": attempt_index}
        logger.info(f"{log_prefix}-call-start", extra=attempt_extra)
        started = time.monotonic()
        try:
            msg = messages_create_text(
                model=model,
                system=system,
                user_message=user_message,
                max_tokens=max_tokens,
            )
        except Exception as call_exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.error(
                f"{log_prefix}-call-done",
                extra={
                    **attempt_extra,
                    "duration_ms": duration_ms,
                    "outcome": "error",
                    "error_type": type(call_exc).__name__,
                },
            )
            raise
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            f"{log_prefix}-call-done",
            extra={
                **attempt_extra,
                "duration_ms": duration_ms,
                "outcome": "success",
            },
        )
        text = ""
        try:
            for block in msg.content or []:
                if getattr(block, "type", None) == "text":
                    text += getattr(block, "text", "")
        except Exception as extract_exc:  # pragma: no cover — defensive
            logger.warning(
                f"{log_prefix}-content-extract-failed: %s", extract_exc,
            )
        return msg, text

    result = JsonRetryResult(parsed=None)

    message, raw_text = _call_and_extract(attempt_index=0)
    result.call_attempts.append((message, raw_text))
    parsed, parse_error, parse_path = _try_parse_llm_json(raw_text)
    result.parsed = parsed
    result.parse_error = parse_error
    result.parse_path = parse_path

    for retry_n in range(1, max_recalls + 1):
        if result.parsed is not None:
            break
        logger.warning(
            f"{log_prefix}-parse-retry",
            extra={
                **extra_base,
                "attempt": retry_n,
                "max_attempts": max_recalls,
                "error_type": (
                    type(result.parse_error).__name__
                    if result.parse_error else "Unknown"
                ),
                "error_message": (
                    str(result.parse_error)[:200] if result.parse_error else ""
                ),
            },
        )
        try:
            message, raw_text = _call_and_extract(attempt_index=retry_n)
        except Exception as call_exc:
            logger.error(
                f"{log_prefix}-parse-recall-aborted",
                extra={
                    **extra_base,
                    "attempt": retry_n,
                    "error_type": type(call_exc).__name__,
                    "error_message": str(call_exc)[:200],
                },
            )
            break
        result.call_attempts.append((message, raw_text))
        parsed, parse_error, parse_path = _try_parse_llm_json(raw_text)
        result.parsed = parsed
        result.parse_error = parse_error
        result.parse_path = parse_path

    if result.parsed is not None and (
        result.parse_path == "lenient" or len(result.call_attempts) > 1
    ):
        logger.info(
            f"{log_prefix}-parse-recovered",
            extra={
                **extra_base,
                "parse_path": result.parse_path,
                "anthropic_calls": len(result.call_attempts),
                "recall_count": len(result.call_attempts) - 1,
            },
        )

    return result
