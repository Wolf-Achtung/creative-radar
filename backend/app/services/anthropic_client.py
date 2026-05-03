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

import logging
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


# ---------- High-level call shapes ------------------------------------


def messages_create_text(
    *,
    model: str,
    system: str,
    user_message: str,
    max_tokens: int = 256,
) -> Any:
    """Single-text-message Messages API call. Returns the raw Message
    so the caller can read both ``content[0].text`` and ``usage``."""
    client = _client()

    def _do() -> Any:
        return client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )

    return call_with_retry(_do)


def messages_create_vision(
    *,
    model: str,
    system: str,
    user_message: str,
    image_url: str,
    max_tokens: int = 400,
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
        )

    return call_with_retry(_do)
