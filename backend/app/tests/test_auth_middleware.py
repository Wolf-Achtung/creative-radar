"""Tests for the Bearer-token auth middleware (Phase 4 W4 / Task 4.3).

Coverage:
- auth_enabled=False: every request passes, regardless of header
- auth_enabled=True with correct token: request passes
- auth_enabled=True with wrong token: 403
- auth_enabled=True with no Authorization header: 401
- auth_enabled=True with malformed Authorization (no 'Bearer ' prefix): 401
- auth_enabled=True with API_TOKEN unset: 503 (fail closed)
- public-path whitelist still 200 even with auth_enabled=True and no token:
  /api/health, /api/health/db, /api/img, /storage/<file>, /docs,
  /openapi.json, /api/reports/latest/download.html,
  /api/reports/latest/download.md
- OPTIONS preflight passes through (CORS handler should answer)
- Layout-probe: PUBLIC_PATH_PREFIXES references match real route prefixes
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import _path_is_public, PUBLIC_PATH_EXACT, PUBLIC_PATH_PREFIXES
from app.config import settings
from app.main import app


# Register a DB-free probe endpoint so the auth tests can exercise the
# middleware without touching the SQLite test DB (which is per-connection
# isolated under :memory: and would need a heavier fixture). The probe
# lives under a real /api prefix so the public-path whitelist correctly
# does NOT match it — this is the surface we want to assert auth on.
@app.get("/api/_auth_probe")
def _auth_probe() -> dict:
    return {"ok": True}


client = TestClient(app)


# ---------- pass-through when auth is off ----------


def test_pass_through_when_auth_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    response = client.get("/api/_auth_probe")
    # Insights returns 200 with an empty overview — the point is that it
    # was not blocked by auth.
    assert response.status_code == 200


def test_pass_through_when_auth_disabled_even_with_bogus_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    response = client.get(
        "/api/_auth_probe",
        headers={"Authorization": "Bearer absolutely-bogus"},
    )
    assert response.status_code == 200


# ---------- correct token ----------


def test_authenticated_request_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "valid-token-abc", raising=False)

    response = client.get(
        "/api/_auth_probe",
        headers={"Authorization": "Bearer valid-token-abc"},
    )
    assert response.status_code == 200


# ---------- failure modes ----------


def test_missing_authorization_header_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "valid-token", raising=False)

    response = client.get("/api/_auth_probe")
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing Bearer token"


def test_wrong_scheme_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "valid-token", raising=False)

    response = client.get(
        "/api/_auth_probe",
        headers={"Authorization": "valid-token"},  # missing 'Bearer ' prefix
    )
    assert response.status_code == 401


def test_wrong_token_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "valid-token", raising=False)

    response = client.get(
        "/api/_auth_probe",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid token"


def test_auth_enabled_without_token_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed guard: AUTH_ENABLED=true without API_TOKEN must NOT silently
    let any caller through. Returns 503 with a readable message so Wolf sees
    the misconfig immediately."""
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", None, raising=False)

    response = client.get(
        "/api/_auth_probe",
        headers={"Authorization": "Bearer anything"},
    )
    assert response.status_code == 503
    assert "API token not configured" in response.json()["detail"]


# ---------- public-path whitelist ----------


def test_health_endpoint_public_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "valid-token", raising=False)

    response = client.get("/api/health")
    assert response.status_code == 200


def test_db_health_endpoint_public_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "valid-token", raising=False)

    response = client.get("/api/health/db")
    # Whatever the DB-check response is (200 or 503 depending on driver),
    # the auth layer must not have blocked it (would be 401/403/503-token).
    assert response.status_code != 401
    assert response.status_code != 403
    assert response.json().get("detail") != "API token not configured on server"


def test_image_proxy_public_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The image proxy is hit by <img src> tags, which cannot send Bearer
    headers. It MUST stay reachable when auth is on, even without a header."""
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "valid-token", raising=False)

    # Ask for an obviously-bogus URL so the proxy returns 400/403, but the
    # auth layer must not have blocked it first.
    response = client.get("/api/img?url=https://example.invalid/foo.jpg")
    assert response.status_code != 401
    assert response.status_code != 403 or response.json().get("detail") != "Invalid token"


def test_storage_mount_public_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The /storage mount serves evidence images via <img src>, which cannot
    send Bearer headers. It MUST stay reachable when auth is on. Auth must
    not be the layer that returns 401/403; a 404 from StaticFiles for a
    missing file is the expected outcome here."""
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "valid-token", raising=False)

    response = client.get("/storage/evidence/does-not-exist.jpg")
    assert response.status_code != 401
    assert response.status_code != 403


def test_docs_endpoint_public_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "valid-token", raising=False)

    response = client.get("/openapi.json")
    assert response.status_code == 200


def test_options_preflight_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """CORS preflight (OPTIONS) must reach the CORS middleware — the browser
    strips the Authorization header on preflight, so a 401 here would break
    every cross-origin call from the SPA."""
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "valid-token", raising=False)

    response = client.options(
        "/api/_auth_probe",
        headers={
            "Origin": "https://app.creative-radar.de",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code in (200, 204), (
        f"OPTIONS preflight blocked by auth: status={response.status_code}, "
        f"body={response.text[:200]}"
    )


# ---------- path classifier unit tests ----------


def test_path_is_public_exact_match() -> None:
    assert _path_is_public("/api/reports/latest/download.html") is True
    assert _path_is_public("/api/reports/latest/download.md") is True


def test_path_is_public_prefix_match() -> None:
    assert _path_is_public("/api/health") is True
    assert _path_is_public("/api/health/db") is True
    assert _path_is_public("/api/img") is True
    assert _path_is_public("/api/img?url=foo") is False  # query string isn't part of path
    assert _path_is_public("/storage") is True
    assert _path_is_public("/storage/evidence/abc.jpg") is True
    assert _path_is_public("/docs") is True
    assert _path_is_public("/redoc") is True
    assert _path_is_public("/openapi.json") is True


def test_path_is_public_rejects_lookalikes() -> None:
    """Slash-boundary matters — /api/healthbeat must NOT slip through as
    public just because it starts with /api/health."""
    assert _path_is_public("/api/healthbeat") is False
    assert _path_is_public("/api/imgproxy") is False
    assert _path_is_public("/storagebox") is False
    assert _path_is_public("/api/_auth_probe") is False
    assert _path_is_public("/api/reports/latest") is False  # no /download suffix
    assert _path_is_public("/api/reports/latest/download.json") is False  # not in whitelist


# ---------- production-layout probe ----------


def test_public_path_prefixes_match_real_route_prefixes() -> None:
    """Layout-probe: every PUBLIC_PATH_PREFIX must correspond to at least
    one real registered route. Catches the case where a prefix in the
    whitelist gets stale (e.g. a router gets renamed) and protects from
    the W4-Hotfix-3 'paths drifted from code' regression class."""
    registered_paths = {
        getattr(route, "path", "") for route in app.router.routes
    }
    # Strip query strings and FastAPI path params for easier matching.
    registered_prefixes = {p.rstrip("/") for p in registered_paths if p}

    for prefix in PUBLIC_PATH_PREFIXES:
        # docs/redoc/openapi are FastAPI built-ins — they live on the app,
        # not on a router we registered. Skip the assertion for those.
        if prefix in ("/docs", "/redoc", "/openapi.json"):
            continue
        # At least one registered path must start with this prefix.
        match = any(
            rp == prefix or rp.startswith(prefix + "/")
            for rp in registered_prefixes
        )
        assert match, (
            f"PUBLIC_PATH_PREFIXES contains {prefix!r} but no registered "
            f"route starts with it. Registered paths sample: "
            f"{sorted(registered_prefixes)[:10]}..."
        )


def test_public_path_exact_paths_match_real_routes() -> None:
    """Same probe as above for the exact-match list."""
    registered_paths = {
        getattr(route, "path", "") for route in app.router.routes
    }
    for path in PUBLIC_PATH_EXACT:
        assert path in registered_paths, (
            f"PUBLIC_PATH_EXACT lists {path!r} but no route is registered "
            f"at that exact path. Either the whitelist or the route drifted."
        )
