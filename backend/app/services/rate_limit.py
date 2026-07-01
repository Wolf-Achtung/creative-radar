"""In-memory rate limiting (Sicherheits-Audit 2026-07-01).

Deckt zwei Luecken aus dem Audit: kein Brute-Force-Schutz auf
``/api/admin/login`` und kein Burst-Schutz auf den kostenpflichtigen
KI-/Scraper-Endpoints (die Monats-Budget-Caps in ``config.py`` greifen erst,
wenn das Monatsbudget real aufgebraucht ist, nicht bei einem kurzfristigen
Burst).

Nicht Multi-Instanz-sicher: der Zaehler lebt im Prozessspeicher. Fuer den
aktuellen Single-Instance-Railway-Deploy ausreichend (siehe
``database.py``-Pool-Kommentare zur selben Ein-Instanz-Annahme); bei
horizontaler Skalierung muesste das auf einen gemeinsamen Store (Redis o.
Ae.) wandern.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request, status

from app.config import settings

_buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_lock = Lock()


def client_ip(request: Request) -> str:
    """Best-effort Client-IP hinter Railway/Cloudflare — dieselben Header
    wie in ``api/insights.py`` (``brief_request_received``-Log)."""
    forwarded_for = request.headers.get("x-forwarded-for", "")
    return (
        request.headers.get("x-real-ip")
        or (forwarded_for.split(",")[0].strip() if forwarded_for else "")
        or (request.client.host if request.client else "")
        or "unknown"
    )


def reset() -> None:
    """Testing-Hook: leert alle Zaehler. Siehe app/tests/conftest.py."""
    with _lock:
        _buckets.clear()


def rate_limit(bucket: str, *, max_calls: int, window_seconds: float):
    """FastAPI-Dependency-Factory: max. ``max_calls`` Requests pro
    ``window_seconds`` und Client-IP im ``bucket``-Namensraum, sonst 429.

    ``settings.rate_limit_enabled`` ist der Kill-Switch (Default an); No-Op
    wenn deaktiviert, damit ein Incident (z. B. falsch-positive Blocks durch
    einen geteilten Firmen-NAT) ohne Code-Deploy behoben werden kann.
    """

    def _dependency(request: Request) -> None:
        if not settings.rate_limit_enabled:
            return
        key = (bucket, client_ip(request))
        now = time.monotonic()
        with _lock:
            calls = _buckets[key]
            while calls and now - calls[0] > window_seconds:
                calls.popleft()
            if len(calls) >= max_calls:
                retry_after = max(1, int(window_seconds - (now - calls[0])))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests, please try again later.",
                    headers={"Retry-After": str(retry_after)},
                )
            calls.append(now)

    return _dependency
