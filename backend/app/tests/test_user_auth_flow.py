"""Sprint User-Login 2026-07 — E-Mail+Code-Login, Middleware-Gate,
User-Verwaltung und Nutzungs-Log.

Vier Test-Cluster:

1. ``test_user_token_*`` — Roundtrip + Tamper-/Ablauf-/Format-Cases auf
   der reinen Token-Lib (kein HTTP-Stack noetig).
2. ``test_request_code_*`` / ``test_login_*`` — End-to-end ueber den
   FastAPI-TestClient: Allowlist-403, Code-Versand (Mailer gemockt, Code
   aus dem Mail-Text extrahiert), falscher Code + attempts-Deckel,
   Einmal-Nutzung, Cookie + /me.
3. ``test_gate_*`` — die User-Session-Middleware: geschuetzte Pfade 401
   ohne Cookie, 200 mit; Public-Pfade offen; Admin-Session-Bypass;
   Deaktivierung wirkt trotz gueltigem Cookie sofort.
4. ``test_admin_users_*`` / ``test_usage_*`` — Admin-CRUD auf der
   Allowlist + Nutzungs-Auswertung (Events via Login/landing_view).

Isolation: shared In-Memory-SQLite via StaticPool (Muster
test_api_pairs.py). Middleware und usage_log oeffnen eigene Sessions
ueber ``app.database.engine`` — der Fixture patcht das Engine-Objekt
darum zusaetzlich zum ``get_session``-Override.
"""
from __future__ import annotations

import re
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.database as database_module
from app.admin_session import create_session_token
from app.config import settings
from app.database import get_session
from app.main import app
from app.models import AppUser, LoginCode, UsageEvent
from app.user_session import (
    USER_SESSION_COOKIE,
    create_user_session_token,
    verify_user_session_token,
)

USER_SECRET = "user-test-secret"


# ---------- Token-Lib (kein HTTP) ----------------------------------------


def test_user_token_roundtrip():
    token, expires_unix = create_user_session_token("wolf@example.com", USER_SECRET, 60)
    assert verify_user_session_token(token, USER_SECRET) == "wolf@example.com"
    assert expires_unix > int(time.time())


def test_user_token_normalizes_email_case():
    token, _ = create_user_session_token("  Wolf@Example.COM ", USER_SECRET, 60)
    assert verify_user_session_token(token, USER_SECRET) == "wolf@example.com"


def test_user_token_rejects_wrong_secret():
    token, _ = create_user_session_token("wolf@example.com", USER_SECRET, 60)
    assert verify_user_session_token(token, "other-secret") is None


def test_user_token_rejects_tampered_email():
    token, _ = create_user_session_token("wolf@example.com", USER_SECRET, 60)
    email_b64, rest = token.split(".", 1)
    flipped = ("a" if email_b64[0] != "a" else "b") + email_b64[1:]
    assert verify_user_session_token(f"{flipped}.{rest}", USER_SECRET) is None


def test_user_token_rejects_expired():
    token, _ = create_user_session_token("wolf@example.com", USER_SECRET, -1)
    assert verify_user_session_token(token, USER_SECRET) is None


@pytest.mark.parametrize("garbage", ["", "abc", "a.b", "a.b.c.d", "x.NaN.y"])
def test_user_token_rejects_garbage(garbage):
    assert verify_user_session_token(garbage, USER_SECRET) is None


# ---------- HTTP-Fixtures --------------------------------------------------


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def sent_mails(monkeypatch: pytest.MonkeyPatch):
    """Faengt send_mail-Aufrufe der Auth-Route ab (kein echter Versand)."""
    mails: list[dict] = []

    async def _fake_send(to: str, subject: str, text: str, html=None):
        mails.append({"to": to, "subject": subject, "text": text})

    monkeypatch.setattr("app.api.user_auth.send_mail", _fake_send)
    return mails


@pytest.fixture
def client(db, sent_mails, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "admin_auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "user_auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "user_session_secret", USER_SECRET, raising=False)
    # Secure-Cookie wuerde ueber http://testserver nicht mitgesendet.
    monkeypatch.setattr(settings, "app_env", "test", raising=False)
    # Middleware + usage_log oeffnen eigene Sessions ueber
    # app.database.engine (lazy import im Funktions-Body) — auf die
    # Test-Engine umbiegen.
    monkeypatch.setattr(database_module, "engine", db, raising=False)

    def _override_session():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _add_user(db, email: str, active: bool = True) -> AppUser:
    with Session(db) as session:
        user = AppUser(email=email, active=active)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def _extract_code(sent_mails) -> str:
    match = re.search(r"\b(\d{6})\b", sent_mails[-1]["text"])
    assert match, f"Kein 6-stelliger Code im Mail-Text: {sent_mails[-1]['text']!r}"
    return match.group(1)


def _login(client, db, sent_mails, email: str) -> dict:
    assert client.post("/api/auth/request-code", json={"email": email}).status_code == 200
    response = client.post(
        "/api/auth/login", json={"email": email, "code": _extract_code(sent_mails)}
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------- request-code ---------------------------------------------------


def test_request_code_rejects_unknown_email(client, db, sent_mails):
    response = client.post("/api/auth/request-code", json={"email": "fremd@example.com"})
    assert response.status_code == 403
    assert "nicht freigeschaltet" in response.json()["detail"]
    assert sent_mails == []


def test_request_code_rejects_inactive_user(client, db, sent_mails):
    _add_user(db, "inaktiv@example.com", active=False)
    response = client.post("/api/auth/request-code", json={"email": "inaktiv@example.com"})
    assert response.status_code == 403
    assert sent_mails == []


def test_request_code_rejects_invalid_email_format(client, db):
    assert client.post("/api/auth/request-code", json={"email": "kein-at-zeichen"}).status_code == 422


def test_request_code_sends_mail_and_stores_hash(client, db, sent_mails):
    _add_user(db, "wolf@example.com")
    response = client.post("/api/auth/request-code", json={"email": "Wolf@Example.com"})
    assert response.status_code == 200
    assert sent_mails[-1]["to"] == "wolf@example.com"
    code = _extract_code(sent_mails)
    with Session(db) as session:
        rows = session.exec(select(LoginCode).where(LoginCode.email == "wolf@example.com")).all()
    assert len(rows) == 1
    # Nur der Hash liegt in der DB, nie der Klartext-Code.
    assert code not in (rows[0].code_hash or "")
    assert rows[0].used_at is None


def test_request_code_latest_code_wins(client, db, sent_mails):
    _add_user(db, "wolf@example.com")
    client.post("/api/auth/request-code", json={"email": "wolf@example.com"})
    first_code = _extract_code(sent_mails)
    client.post("/api/auth/request-code", json={"email": "wolf@example.com"})
    with Session(db) as session:
        rows = session.exec(select(LoginCode).where(LoginCode.email == "wolf@example.com")).all()
    assert len(rows) == 1  # alte Row geloescht
    # Der erste Code ist damit wertlos.
    response = client.post("/api/auth/login", json={"email": "wolf@example.com", "code": first_code})
    # 401 ausser die beiden Zufalls-Codes kollidieren (1:1e6) — dann
    # waere der Login legitim erfolgreich.
    second_code = _extract_code(sent_mails)
    assert response.status_code == (200 if first_code == second_code else 401)


def test_request_code_disabled_returns_503(client, db, monkeypatch):
    monkeypatch.setattr(settings, "user_auth_enabled", False, raising=False)
    _add_user(db, "wolf@example.com")
    assert client.post("/api/auth/request-code", json={"email": "wolf@example.com"}).status_code == 503


# ---------- login ------------------------------------------------------------


def test_login_success_sets_cookie_and_me(client, db, sent_mails):
    _add_user(db, "wolf@example.com")
    result = _login(client, db, sent_mails, "wolf@example.com")
    assert result["ok"] is True
    assert result["email"] == "wolf@example.com"
    assert USER_SESSION_COOKIE in client.cookies

    me = client.get("/api/auth/me").json()
    assert me == {"authenticated": True, "auth_enabled": True, "email": "wolf@example.com"}

    with Session(db) as session:
        user = session.exec(select(AppUser).where(AppUser.email == "wolf@example.com")).first()
        code_row = session.exec(select(LoginCode).where(LoginCode.email == "wolf@example.com")).first()
    assert user.last_login_at is not None
    assert code_row.used_at is not None  # Einmal-Nutzung markiert


def test_login_rejects_wrong_code_and_counts_attempts(client, db, sent_mails):
    _add_user(db, "wolf@example.com")
    client.post("/api/auth/request-code", json={"email": "wolf@example.com"})
    code = _extract_code(sent_mails)
    wrong = "000000" if code != "000000" else "000001"
    response = client.post("/api/auth/login", json={"email": "wolf@example.com", "code": wrong})
    assert response.status_code == 401
    with Session(db) as session:
        row = session.exec(select(LoginCode).where(LoginCode.email == "wolf@example.com")).first()
    assert row.attempts == 1


def test_login_code_burns_after_max_attempts(client, db, sent_mails, monkeypatch):
    # Rate-Limit aus, um die attempts-Grenze isoliert zu treffen (5
    # Fehlversuche + 1 korrekter Versuch > max_login=5 pro 5 Min.).
    monkeypatch.setattr(settings, "rate_limit_enabled", False, raising=False)
    _add_user(db, "wolf@example.com")
    client.post("/api/auth/request-code", json={"email": "wolf@example.com"})
    code = _extract_code(sent_mails)
    wrong = "000000" if code != "000000" else "000001"
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "wolf@example.com", "code": wrong})
    # Selbst der RICHTIGE Code ist jetzt verbrannt.
    response = client.post("/api/auth/login", json={"email": "wolf@example.com", "code": code})
    assert response.status_code == 401


def test_login_code_single_use(client, db, sent_mails):
    _add_user(db, "wolf@example.com")
    client.post("/api/auth/request-code", json={"email": "wolf@example.com"})
    code = _extract_code(sent_mails)
    assert client.post("/api/auth/login", json={"email": "wolf@example.com", "code": code}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "wolf@example.com", "code": code}).status_code == 401


def test_login_rate_limit_per_email(client, db, sent_mails):
    _add_user(db, "wolf@example.com")
    client.post("/api/auth/request-code", json={"email": "wolf@example.com"})
    # max_login=5 pro Fenster: der sechste Versuch derselben Adresse
    # antwortet 429, unabhaengig vom Code.
    statuses = [
        client.post("/api/auth/login", json={"email": "wolf@example.com", "code": "999999"}).status_code
        for _ in range(6)
    ]
    assert 429 in statuses


def test_logout_clears_cookie(client, db, sent_mails):
    _add_user(db, "wolf@example.com")
    _login(client, db, sent_mails, "wolf@example.com")
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").json()["authenticated"] is False


# ---------- Middleware-Gate ---------------------------------------------------


def test_gate_blocks_protected_paths_without_login(client, db):
    assert client.get("/api/pairs").status_code == 401
    assert client.get("/api/roundups/latest").status_code == 401
    assert client.get("/api/insights/overview").status_code == 401


def test_gate_leaves_public_paths_open(client, db):
    assert client.get("/api/health").status_code == 200
    # /me antwortet 200 (Konvention), nicht 401.
    assert client.get("/api/auth/me").status_code == 200


def test_gate_opens_after_login(client, db, sent_mails):
    _add_user(db, "wolf@example.com")
    _login(client, db, sent_mails, "wolf@example.com")
    assert client.get("/api/pairs").status_code == 200


def test_gate_deactivation_beats_valid_cookie(client, db, sent_mails):
    user = _add_user(db, "wolf@example.com")
    _login(client, db, sent_mails, "wolf@example.com")
    assert client.get("/api/pairs").status_code == 200
    with Session(db) as session:
        row = session.get(AppUser, user.id)
        row.active = False
        session.add(row)
        session.commit()
    # Cookie ist kryptographisch noch gueltig — der Pro-Request-DB-Check
    # sperrt trotzdem sofort.
    assert client.get("/api/pairs").status_code == 401


def test_gate_admin_session_bypasses_user_login(client, db, monkeypatch):
    monkeypatch.setattr(settings, "admin_session_secret", "admin-secret", raising=False)
    token, _ = create_session_token("admin-secret", ttl_seconds=60)
    client.cookies.set("cr_admin_session", token)
    # Admin-Session darf Daten-Endpoints nutzen (Admin-UI laedt
    # /api/channels etc.), ohne User-Login.
    assert client.get("/api/pairs").status_code == 200


def test_gate_disabled_keeps_site_public(client, db, monkeypatch):
    monkeypatch.setattr(settings, "user_auth_enabled", False, raising=False)
    assert client.get("/api/pairs").status_code == 200


def test_gate_missing_secret_fails_closed(client, db, monkeypatch):
    monkeypatch.setattr(settings, "user_session_secret", None, raising=False)
    assert client.get("/api/pairs").status_code == 503


# ---------- Usage-Log + Admin-Auswertung -----------------------------------


def test_usage_events_written_for_login_and_landing(client, db, sent_mails):
    _add_user(db, "wolf@example.com")
    _login(client, db, sent_mails, "wolf@example.com")
    client.get("/api/pairs")
    with Session(db) as session:
        actions = [e.action for e in session.exec(select(UsageEvent)).all()]
    assert "login" in actions
    assert "landing_view" in actions


def test_usage_no_events_for_admin_session(client, db, monkeypatch):
    monkeypatch.setattr(settings, "admin_session_secret", "admin-secret", raising=False)
    token, _ = create_session_token("admin-secret", ttl_seconds=60)
    client.cookies.set("cr_admin_session", token)
    client.get("/api/pairs")
    with Session(db) as session:
        assert session.exec(select(UsageEvent)).all() == []


def test_usage_summary_endpoint(client, db, sent_mails):
    _add_user(db, "wolf@example.com")
    _login(client, db, sent_mails, "wolf@example.com")
    client.get("/api/pairs")
    summary = client.get("/api/admin/usage?days=30").json()
    assert summary["events_total"] >= 2
    row = next(u for u in summary["users"] if u["email"] == "wolf@example.com")
    assert row["logins"] == 1
    assert row["last_active"] is not None
    assert {a["action"] for a in summary["actions"]} >= {"login", "landing_view"}


# ---------- Admin-User-Verwaltung -------------------------------------------


def test_admin_users_crud_roundtrip(client, db):
    created = client.post(
        "/api/admin/users",
        json={"email": "Neu@Example.com", "display_name": "  Neu  "},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["email"] == "neu@example.com"  # lowercase-normalisiert
    assert body["display_name"] == "Neu"

    # Duplikat -> 409
    assert client.post("/api/admin/users", json={"email": "neu@example.com"}).status_code == 409
    # Kaputte Adresse -> 422
    assert client.post("/api/admin/users", json={"email": "kein-at"}).status_code == 422

    listed = client.get("/api/admin/users").json()
    assert [u["email"] for u in listed] == ["neu@example.com"]

    patched = client.patch(f"/api/admin/users/{body['id']}", json={"active": False}).json()
    assert patched["active"] is False

    assert client.delete(f"/api/admin/users/{body['id']}").json() == {"ok": True}
    assert client.get("/api/admin/users").json() == []
