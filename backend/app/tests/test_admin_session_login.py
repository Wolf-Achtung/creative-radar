"""Sprint 28.05.2026 (Admin-Login) — Login-Flow + Endpoint-Schutz.

Drei Test-Cluster:

1. ``test_create_verify_token_*`` — Roundtrip + Tamper-/Ablauf-Cases auf
   der reinen Token-Lib (kein HTTP-Stack noetig).
2. ``test_verify_admin_password_*`` — konstant-zeitiger Vergleich.
3. ``test_admin_login_*`` und ``test_admin_protected_*`` — End-to-end
   ueber den FastAPI-TestClient: Passwort falsch/richtig, Cookie wird
   gesetzt, geschuetzte Routes antworten 401 ohne Cookie / 200 mit.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.admin_session import (
    ADMIN_SESSION_COOKIE,
    create_session_token,
    verify_admin_password,
    verify_session_token,
)
from app.config import settings
from app.database import get_session
from app.main import app


# ---------- Token-Lib (kein HTTP) ----------------------------------------


def test_create_verify_token_roundtrip():
    token, expires_unix = create_session_token("test-secret", ttl_seconds=60)
    assert verify_session_token(token, "test-secret") is True
    # Ablauf liegt klar in der Zukunft
    assert expires_unix > int(time.time())


def test_verify_token_rejects_wrong_secret():
    token, _ = create_session_token("test-secret", ttl_seconds=60)
    assert verify_session_token(token, "other-secret") is False


def test_verify_token_rejects_tampered_signature():
    token, _ = create_session_token("test-secret", ttl_seconds=60)
    # Erste Stelle der Signatur kippen — Bit-Flip, gueltige Base64-
    # Zeichen aber falscher Hash.
    expires_part, sig_part = token.split(".", 1)
    tampered = f"{expires_part}.{'a' + sig_part[1:] if sig_part[0] != 'a' else 'b' + sig_part[1:]}"
    assert verify_session_token(tampered, "test-secret") is False


def test_verify_token_rejects_expired():
    token, _ = create_session_token("test-secret", ttl_seconds=-1)
    assert verify_session_token(token, "test-secret") is False


def test_verify_token_rejects_malformed():
    assert verify_session_token("", "secret") is False
    assert verify_session_token("no-dot", "secret") is False
    assert verify_session_token("nondigit.sig", "secret") is False


# ---------- Passwort-Vergleich -------------------------------------------


def test_verify_admin_password_correct(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "wolf-secret", raising=False)
    assert verify_admin_password("wolf-secret") is True


def test_verify_admin_password_wrong(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "wolf-secret", raising=False)
    assert verify_admin_password("falsch") is False


def test_verify_admin_password_unconfigured(monkeypatch):
    """Wenn der Server kein Passwort hat (ENV fehlt), schlaegt JEDER
    Vergleich fehl — kein versehentliches "leeres Passwort akzeptiert"."""
    monkeypatch.setattr(settings, "admin_password", None, raising=False)
    assert verify_admin_password("anything") is False
    assert verify_admin_password("") is False


# ---------- End-to-End ueber TestClient ----------------------------------


def _shared_test_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def db():
    engine = _shared_test_engine()
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client_with_admin_auth(db, monkeypatch: pytest.MonkeyPatch):
    """TestClient mit eingeschaltetem Admin-Auth + frischen Credentials."""
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "admin_auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "admin_password", "wolf-test-pwd", raising=False)
    monkeypatch.setattr(
        settings, "admin_session_secret", "test-session-secret-32-bytes-XXXX", raising=False,
    )
    monkeypatch.setattr(settings, "app_env", "test", raising=False)

    def _override_session():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_admin_login_correct_password_sets_cookie(client_with_admin_auth):
    resp = client_with_admin_auth.post(
        "/api/admin/login", json={"password": "wolf-test-pwd"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["expires_unix"] > int(time.time())
    # Cookie wurde gesetzt
    assert ADMIN_SESSION_COOKIE in resp.cookies


def test_admin_login_wrong_password_401(client_with_admin_auth):
    resp = client_with_admin_auth.post(
        "/api/admin/login", json={"password": "falsch"},
    )
    assert resp.status_code == 401
    # Kein Cookie gesetzt
    assert ADMIN_SESSION_COOKIE not in resp.cookies


def test_admin_login_503_when_disabled(client_with_admin_auth, monkeypatch):
    """Wenn admin_auth_enabled=False, antwortet der Endpoint 503 — kein
    stilles "alles in Ordnung" bei deaktiviertem Mechanismus."""
    monkeypatch.setattr(settings, "admin_auth_enabled", False, raising=False)
    resp = client_with_admin_auth.post(
        "/api/admin/login", json={"password": "wolf-test-pwd"},
    )
    assert resp.status_code == 503


def test_admin_login_503_when_session_secret_missing(
    client_with_admin_auth, monkeypatch,
):
    monkeypatch.setattr(settings, "admin_session_secret", None, raising=False)
    resp = client_with_admin_auth.post(
        "/api/admin/login", json={"password": "wolf-test-pwd"},
    )
    assert resp.status_code == 503


def test_admin_me_unauthenticated(client_with_admin_auth):
    resp = client_with_admin_auth.get("/api/admin/me")
    assert resp.status_code == 200
    assert resp.json() == {"authenticated": False, "auth_enabled": True}


def test_admin_me_after_login(client_with_admin_auth):
    login = client_with_admin_auth.post(
        "/api/admin/login", json={"password": "wolf-test-pwd"},
    )
    assert login.status_code == 200
    # TestClient persistiert Cookies zwischen Calls automatisch
    me = client_with_admin_auth.get("/api/admin/me")
    assert me.status_code == 200
    assert me.json() == {"authenticated": True, "auth_enabled": True}


def test_admin_me_when_auth_disabled(client_with_admin_auth, monkeypatch):
    """``admin_auth_enabled=False`` → /me sagt ``authenticated=True``
    (dev-Setup-Konvention: deaktivierte Auth = immer eingeloggt)."""
    monkeypatch.setattr(settings, "admin_auth_enabled", False, raising=False)
    resp = client_with_admin_auth.get("/api/admin/me")
    assert resp.status_code == 200
    assert resp.json() == {"authenticated": True, "auth_enabled": False}


def test_admin_logout_clears_cookie(client_with_admin_auth):
    client_with_admin_auth.post(
        "/api/admin/login", json={"password": "wolf-test-pwd"},
    )
    resp = client_with_admin_auth.post("/api/admin/logout")
    assert resp.status_code == 200
    # /me nach Logout: unauthenticated. Der TestClient sieht das Clear-
    # Cookie und entfernt es aus seinem Cookie-Jar.
    me = client_with_admin_auth.get("/api/admin/me")
    assert me.status_code == 200
    assert me.json()["authenticated"] is False


# ---------- Endpoint-Schutz: blockiert ohne Login ------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        # Stichprobe pro geschuetzter Router:
        ("GET", "/api/assets"),
        ("GET", "/api/titles"),
        ("GET", "/api/channels"),
        ("GET", "/api/posts"),
        ("GET", "/api/reports"),
        ("POST", "/api/reports/suggest"),
        ("POST", "/api/monitor/apify-tiktok"),
        # Existierender /api/admin/*-Bereich
        ("GET", "/api/admin/budget-status"),
    ],
)
def test_admin_protected_routes_return_401_without_session(
    client_with_admin_auth, method, path,
):
    resp = client_with_admin_auth.request(method, path)
    assert resp.status_code == 401, (
        f"{method} {path} sollte ohne Session 401 liefern, war {resp.status_code}"
    )


def test_public_paths_pass_through_even_when_admin_auth_on(client_with_admin_auth):
    """Public-Whitelist-Pfade (health, pairs, roundups) bleiben offen,
    auch wenn admin_auth_enabled=True. Sonst waere die Startseite
    unaufrufbar."""
    for path in ("/api/health", "/api/pairs", "/api/roundups/latest"):
        resp = client_with_admin_auth.get(path)
        # Kein 401 — entweder 200 oder 503 (DB nicht erreichbar etc),
        # aber NICHT auth-blockiert.
        assert resp.status_code != 401, f"{path} wurde faelschlich blockiert"


def test_admin_protected_route_passes_with_session(client_with_admin_auth):
    login = client_with_admin_auth.post(
        "/api/admin/login", json={"password": "wolf-test-pwd"},
    )
    assert login.status_code == 200
    # Mit Cookie: geschuetzte Route antwortet nicht mehr 401. Wir pruefen
    # nur den 401-Block — der eigentliche 200/422-Antwort-Inhalt der
    # Routes ist in deren eigenen Tests abgedeckt.
    resp = client_with_admin_auth.get("/api/assets")
    assert resp.status_code != 401
