"""User-Session-Auth (Sprint User-Login 2026-07).

Dritte Auth-Schicht neben Bearer-Token (``app/auth.py`` — "richtiges
Frontend?") und Admin-Session (``app/admin_session.py`` — "richtiger
Admin?"): die User-Session beantwortet "welcher der ~15 freigeschalteten
Nutzer?" und stellt die GESAMTE Website (Startseite + alle Wochen-Briefs)
hinter den E-Mail+Code-Login (Wolf-Festlegung 2026-07-20).

Token-Format — Erweiterung des bewaehrten Admin-Session-Musters um die
User-Identitaet:

    <base64url(email)>.<expires_unix>.<base64url(hmac_sha256(payload, secret))>

Der HMAC laeuft ueber ``<email_b64>.<expires_unix>`` — E-Mail und Ablauf
sind beide signiert, keines von beiden manipulierbar. Stateless wie die
Admin-Session (kein Session-Speicher in der DB — das Datenmodell der
Referenz api-ki-backend-neu kennt ebenfalls keine Session-Tabelle, dort
JWT). Revocation laeuft ueber die ``app_user``-Tabelle: die Middleware
prueft pro Request, ob der User noch existiert UND aktiv ist —
Deaktivieren im Admin-Bereich wirkt sofort, trotz 30-Tage-Cookie.

Public-Pfade der User-Schicht (bewusst EIGENE Liste, nicht die aus
``app/auth.py`` — dort sind ``/api/pairs`` und ``/api/roundups/latest``
public, genau die stehen jetzt hinter dem Login):

- ``/api/health*`` — Probes.
- ``/api/auth/*`` — der Login-Flow selbst (Henne-Ei).
- ``/api/img``, ``/api/thumbnails``, ``/storage`` — Bild-Subresourcen;
  bleiben aus denselben Gruenden public wie in den anderen beiden
  Schichten (``<img src>``-Limitierungen, siehe app/auth.py-Docstring).
- ``/docs``, ``/redoc``, ``/openapi.json`` — wie bisher offen (Pilot).
- ``/api/admin/*`` — der Admin-Bereich hat seine eigene, staerkere
  Passwort-Session; ein doppelter Login waere reine Reibung.

Zusaetzlich laesst die Middleware Requests mit GUELTIGER Admin-Session
(Cookie ``cr_admin_session``) auf allen Pfaden durch: die Admin-UI
(/admin) nutzt auch Nicht-Admin-Endpoints wie ``/api/channels`` oder
``/api/assets`` — Wolf soll sich nicht zweimal anmelden muessen.
Admin-Requests bekommen KEIN ``request.state.user_email`` und erzeugen
damit auch keine Usage-Events (die Team-Statistik bleibt sauber).

ENV-Erinnerung (Wolf-Schritt, NICHT im Code):
- ``USER_SESSION_SECRET`` — 32+ Bytes Random (``openssl rand -hex 32``),
  eigener Wert, nicht das Admin-Secret.
- ``USER_AUTH_ENABLED=true`` — Master-Schalter, erst flippen wenn
  Backend + Frontend deployt sind und die User angelegt wurden.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import secrets
import time
from typing import Awaitable, Callable, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from app.admin_session import ADMIN_SESSION_COOKIE, verify_session_token as verify_admin_token
from app.config import settings
from app.models import AppUser

logger = logging.getLogger(__name__)

USER_SESSION_COOKIE = "cr_user_session"

USER_PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/api/health",
    "/api/auth",
    "/api/img",
    "/api/thumbnails",
    "/storage",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/admin",
)


def _user_path_is_public(path: str) -> bool:
    for prefix in USER_PUBLIC_PATH_PREFIXES:
        # Slash-Boundary wie in app/auth.py — plain startswith liesse
        # z. B. ``/api/authx`` durchrutschen.
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> Optional[bytes]:
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return None


def _signature(payload: str, secret: str) -> str:
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return _b64url_encode(sig)


def create_user_session_token(email: str, secret: str, ttl_seconds: int) -> tuple[str, int]:
    """Signiertes Session-Token fuer ``email`` mit Ablauf jetzt+ttl.
    Returns ``(token, expires_unix)`` — der Caller setzt das Cookie mit
    ``max_age=ttl_seconds``."""
    expires_unix = int(time.time()) + ttl_seconds
    email_b64 = _b64url_encode(email.strip().lower().encode("utf-8"))
    payload = f"{email_b64}.{expires_unix}"
    return f"{payload}.{_signature(payload, secret)}", expires_unix


def verify_user_session_token(token: str, secret: str) -> Optional[str]:
    """E-Mail aus einem gueltigen, nicht abgelaufenen Token — sonst None.

    Signatur-Vergleich via ``secrets.compare_digest`` (konstant-zeitig),
    Ablauf-Check NACH dem Signatur-Check — gleiche Reihenfolge wie
    ``admin_session.verify_session_token``.
    """
    if not token or not secret:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    email_b64, expires_str, presented_sig = parts
    try:
        expires_unix = int(expires_str)
    except ValueError:
        return None
    expected_sig = _signature(f"{email_b64}.{expires_unix}", secret)
    if not secrets.compare_digest(presented_sig, expected_sig):
        return None
    if int(time.time()) >= expires_unix:
        return None
    raw_email = _b64url_decode(email_b64)
    if raw_email is None:
        return None
    try:
        return raw_email.decode("utf-8")
    except UnicodeDecodeError:
        return None


def resolve_active_user_email(db: Session, email: str) -> Optional[str]:
    """Normalisierte E-Mail, wenn der User existiert UND aktiv ist —
    der Pro-Request-Revocation-Check der Middleware."""
    normalized = (email or "").strip().lower()
    if not normalized:
        return None
    user = db.exec(select(AppUser).where(AppUser.email == normalized)).first()
    if user is None or not user.active:
        return None
    return normalized


async def user_auth_middleware(
    request: Request, call_next: Callable[[Request], Awaitable]
):
    """Stellt alle Nicht-Public-API-Pfade hinter die User-Session.

    Registrierungs-Reihenfolge in main.py: VOR ``auth_middleware``
    addiert -> laeuft INNERHALB der Bearer-Schicht (Bearer zuerst,
    dann User-Session), CORS-Preflight bleibt aussen unberuehrt.
    """
    if request.method == "OPTIONS":
        return await call_next(request)
    if not settings.user_auth_enabled:
        return await call_next(request)
    if _user_path_is_public(request.url.path):
        return await call_next(request)

    secret = settings.user_session_secret
    if not secret:
        # Fail closed — USER_AUTH_ENABLED ohne USER_SESSION_SECRET ist
        # eine Misconfig, kein Freifahrtschein (Muster app/auth.py).
        logger.error("user-auth-session-secret-missing")
        return JSONResponse(
            {"detail": "User session secret not configured on server"},
            status_code=503,
        )

    token = request.cookies.get(USER_SESSION_COOKIE)
    email = verify_user_session_token(token, secret) if token else None
    if email:
        # Lazy import — database zieht beim Import die Engine hoch;
        # Modul-Level wuerde jeden Import von user_session (z. B. in
        # Token-Lib-Tests ohne DB) an die Engine koppeln.
        from app.database import engine

        with Session(engine) as db:
            active_email = resolve_active_user_email(db, email)
        if active_email:
            request.state.user_email = active_email
            return await call_next(request)
        logger.warning("user-auth-rejected-inactive email=%s", email[:1] + "***")

    # Kein (gueltiges) User-Cookie: eine gueltige Admin-Session darf
    # trotzdem passieren — die Admin-UI ruft auch Nicht-Admin-Endpoints.
    admin_token = request.cookies.get(ADMIN_SESSION_COOKIE)
    if (
        admin_token
        and settings.admin_session_secret
        and verify_admin_token(admin_token, settings.admin_session_secret)
    ):
        return await call_next(request)

    return JSONResponse({"detail": "Login required"}, status_code=401)


def request_user_email(request: Request) -> Optional[str]:
    """E-Mail des eingeloggten Users, oder None (Auth aus / Admin /
    public Pfad). Der eine Accessor fuer alle Usage-Log-Aufrufer."""
    return getattr(request.state, "user_email", None)
