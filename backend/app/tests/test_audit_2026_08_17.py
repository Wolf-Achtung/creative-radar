"""Tests fuer die Audit-Fixes vom 17.08.2026.

Abgedeckt:
- require_cron_trigger_auth: sync-all akzeptiert Admin-Session oder
  CRON_API_TOKEN, aber NICHT mehr das allgemeine API_TOKEN allein.
- allowed_origins: "*" wird nie mehr durchgereicht (credentials-Falle).
- _normalize_pg_driver: alle Postgres-URL-Formen landen auf +psycopg (v3).
- link_preview: nur Allowlist-Hosts werden gefetcht (SSRF-Schutz).
- _guard_production_auth: Production ohne Auth-Flags bricht den Boot ab.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.admin_session import create_session_token
from app.api.cron import require_cron_trigger_auth
from app.config import settings
from app.database import _normalize_pg_driver
from app.main import _guard_production_auth
from app.services.link_preview import _url_is_fetchable, fetch_public_preview


def _request(bearer: str | None = None) -> Request:
    headers = []
    if bearer is not None:
        headers.append((b"authorization", f"Bearer {bearer}".encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/admin/cron/sync-all",
            "headers": headers,
            "query_string": b"",
        }
    )


# --- require_cron_trigger_auth -------------------------------------------


def test_cron_trigger_open_when_no_auth_layer_active(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "admin_auth_enabled", False)
    assert require_cron_trigger_auth(_request(), None, None) is None


def test_cron_trigger_rejects_main_api_token(monkeypatch):
    """Das Bundle-Token (API_TOKEN) reicht nicht mehr fuer sync-all."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "main-token")
    monkeypatch.setattr(settings, "cron_api_token", "cron-token")
    monkeypatch.setattr(settings, "admin_auth_enabled", False)
    with pytest.raises(HTTPException) as exc:
        require_cron_trigger_auth(_request("main-token"), None, None)
    assert exc.value.status_code == 403


def test_cron_trigger_accepts_cron_token(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "main-token")
    monkeypatch.setattr(settings, "cron_api_token", "cron-token")
    monkeypatch.setattr(settings, "admin_auth_enabled", False)
    assert require_cron_trigger_auth(_request("cron-token"), None, None) is None


def test_cron_trigger_rejects_when_no_cron_token_configured(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "main-token")
    monkeypatch.setattr(settings, "cron_api_token", None)
    monkeypatch.setattr(settings, "admin_auth_enabled", False)
    with pytest.raises(HTTPException):
        require_cron_trigger_auth(_request("main-token"), None, None)


def test_cron_trigger_accepts_admin_session_cookie(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "main-token")
    monkeypatch.setattr(settings, "cron_api_token", None)
    monkeypatch.setattr(settings, "admin_auth_enabled", True)
    monkeypatch.setattr(settings, "admin_session_secret", "s3cret-s3cret-s3cret")
    token, _expires = create_session_token("s3cret-s3cret-s3cret", 3600)
    assert require_cron_trigger_auth(_request("main-token"), token, None) is None


def test_cron_trigger_rejects_invalid_admin_session(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "main-token")
    monkeypatch.setattr(settings, "cron_api_token", None)
    monkeypatch.setattr(settings, "admin_auth_enabled", True)
    monkeypatch.setattr(settings, "admin_session_secret", "s3cret-s3cret-s3cret")
    with pytest.raises(HTTPException):
        require_cron_trigger_auth(_request("main-token"), "kaputt.token", None)


# --- allowed_origins ------------------------------------------------------


def test_allowed_origins_never_returns_wildcard(monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", "*")
    assert "*" not in settings.allowed_origins
    assert settings.allowed_origins  # fester Fallback, nie leer


def test_allowed_origins_filters_wildcard_from_list(monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", "https://a.example, *, https://b.example/")
    assert settings.allowed_origins == ["https://a.example", "https://b.example"]


# --- _normalize_pg_driver -------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgresql+psycopg2://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("sqlite:///:memory:", "sqlite:///:memory:"),
    ],
)
def test_normalize_pg_driver(raw, expected):
    assert _normalize_pg_driver(raw) == expected


# --- link_preview SSRF-Schutz --------------------------------------------


@pytest.mark.parametrize(
    ("url", "fetchable"),
    [
        ("https://www.instagram.com/p/AAA/", True),
        ("https://instagram.com/reel/BBB/", True),
        ("https://evil.example/p/AAA/", False),
        ("http://postgres.railway.internal:5432/", False),
        ("file:///etc/passwd", False),
        ("https://not-instagram.com.evil.example/", False),
    ],
)
def test_link_preview_url_allowlist(url, fetchable):
    assert _url_is_fetchable(url) is fetchable


async def test_link_preview_does_not_fetch_disallowed_hosts(monkeypatch):
    """Fuer nicht erlaubte Hosts darf gar kein HTTP-Client entstehen."""
    import app.services.link_preview as lp

    def _boom(*args, **kwargs):  # pragma: no cover - Assertion-Helfer
        raise AssertionError("httpx.AsyncClient fuer verbotenen Host instanziiert")

    monkeypatch.setattr(lp.httpx, "AsyncClient", _boom)
    result = await fetch_public_preview("http://postgres.railway.internal:5432/")
    assert result["source"] == "link-only"
    assert result["caption"] is None


# --- _guard_production_auth ----------------------------------------------


def test_guard_production_auth_raises_without_auth(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "allow_auth_disabled_in_production", False)
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "admin_auth_enabled", True)
    with pytest.raises(RuntimeError, match="AUTH_ENABLED"):
        _guard_production_auth()


def test_guard_production_auth_passes_with_both_flags(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_auth_enabled", True)
    _guard_production_auth()


def test_guard_production_auth_override(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "allow_auth_disabled_in_production", True)
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "admin_auth_enabled", False)
    _guard_production_auth()


def test_guard_ignores_non_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "staging")
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "admin_auth_enabled", False)
    _guard_production_auth()
