"""Bearer-token auth middleware for the Creative-Radar API (Phase 4 W4 / Task 4.3).

Two configuration switches in app.config.Settings:

- ``auth_enabled``: master on/off. When False, every request flows through
  unchanged. Lets the rollout land Frontend changes first; Wolf flips the
  flag once tokens are in place on both sides.
- ``api_token``: the expected Bearer value. When ``auth_enabled`` is on but
  this is empty, the middleware returns 503 with a clear message — fail
  closed, never silently accept any token.

Public paths (always pass through, regardless of auth flag):

- ``/api/health`` and ``/api/health/db`` — readiness/liveness probes.
- ``/api/img`` and any sub-path — the image proxy is hit by ``<img src>``
  tags from the browser, which cannot send an Authorization header (HTML
  limitation). Security stays via host-whitelist + size limit.
- ``/storage`` and any sub-path — internal evidence files served by the
  ``StaticFiles`` mount, also fetched by ``<img src>`` (same header
  limitation). Read-only mount, files written only by the storage adapter.
- ``/api/reports/latest/download.html`` and ``download.md`` — clicked via
  ``<a href download>``, same Authorization-header limitation.
- ``/docs``, ``/redoc``, ``/openapi.json`` — FastAPI's own documentation
  surface stays open in pilot.

OPTIONS requests pass through unconditionally so the CORS preflight can
complete without a token (browsers strip the Authorization header from
preflight by design).

Token contract:

- ``Authorization: Bearer <token>`` header, exact match against
  ``settings.api_token``. Anything else returns 401 (missing) or 403
  (wrong).
- Path-based public whitelist is the only override. There is no
  query-parameter token, no cookie auth, no IP allowlist. Phase-5 backlog:
  signed URLs for the image-proxy and report-download paths.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import settings

PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/api/health",
    "/api/img",
    # Sprint 5c — Thumbnail-Proxy. Wird vom Browser direkt aus
    # ``<img src="/api/thumbnails/{asset_id}">`` gehit, kein Token-
    # Forwarding möglich. Endpunkt ist nicht offen wie ``/api/img``,
    # sondern asset-ID-keyed: nur in der DB existierende UUIDs liefern
    # Daten, alles andere → 404.
    "/api/thumbnails",
    "/storage",
    "/docs",
    "/redoc",
    "/openapi.json",
)

PUBLIC_PATH_EXACT: frozenset[str] = frozenset(
    {
        "/api/reports/latest/download.html",
        "/api/reports/latest/download.md",
        # Landing-page pair-registry. Drives the public card grid on
        # ``app.creative-radar.de`` and must render before any auth flip
        # is in effect (Frontend may load the page before the bearer
        # token has been hydrated). Read-only, list of enabled pairs
        # only — no PII, no DB query.
        "/api/pairs",
        # Landing-page Roundup-Block (Master-Plan-Schritt-3b).
        # Spiegelt /api/pairs: Read-only, public, listet den jeweils
        # neuesten Segment-Roundup je Segment. Der teure Generier-
        # Endpoint /api/admin/roundups/generate bleibt bewusst hinter
        # Bearer-Auth (Wolf-Festlegung 26.05.).
        "/api/roundups/latest",
    }
)


def _path_is_public(path: str) -> bool:
    if path in PUBLIC_PATH_EXACT:
        return True
    for prefix in PUBLIC_PATH_PREFIXES:
        # Either an exact match (``/api/health`` itself) or a sub-path
        # (``/api/health/db``). Plain ``startswith(prefix)`` would also let
        # ``/api/healthbeat`` slip through, hence the slash boundary.
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


async def auth_middleware(
    request: Request, call_next: Callable[[Request], Awaitable]
):
    if request.method == "OPTIONS":
        return await call_next(request)
    if not settings.auth_enabled:
        return await call_next(request)
    if _path_is_public(request.url.path):
        return await call_next(request)

    expected = settings.api_token
    if not expected:
        # Fail closed: AUTH_ENABLED was flipped without API_TOKEN being set.
        # Better to return a clear 503 than silently authenticate every
        # caller (which is what comparing against an empty string would do).
        return JSONResponse(
            {"detail": "API token not configured on server"},
            status_code=503,
        )

    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse(
            {"detail": "Missing Bearer token"}, status_code=401
        )

    presented = auth_header.removeprefix("Bearer ").strip()
    if presented != expected:
        return JSONResponse({"detail": "Invalid token"}, status_code=403)

    return await call_next(request)
