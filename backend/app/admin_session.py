"""Admin-Session-Auth (Sprint 28.05.2026).

Zweite Auth-Schicht zusaetzlich zur Bearer-Token-Middleware aus
``app/auth.py``. Bearer beantwortet "richtiges Frontend?" (Build-time-
Token im Bundle), die Admin-Session beantwortet "richtiger User?"
(Login mit Passwort, ausgegeben pro Login-Vorgang).

Token-Format:

    <expires_unix>.<base64url(hmac_sha256(expires_unix, secret))>

Stateless: der Server braucht keinen Session-Speicher in der DB. Eine
gueltige Signatur + nicht-abgelaufener Timestamp = "Login war einmal
korrekt, ist noch nicht zu alt". Logout = Cookie clearen — eine
Token-Revocation-Liste ist nicht implementiert (8h-TTL ist die obere
Schranke fuer den Schadensradius eines kompromittierten Tokens).

Sicherheits-Eigenschaften:

- ``secrets.compare_digest`` fuer den Signatur-Vergleich (konstant-
  zeitig, kein Timing-Leak).
- ``secrets.compare_digest`` fuer den Passwort-Vergleich in
  ``verify_admin_password`` — dasselbe Argument.
- HTTP-Only-Cookie, ``SameSite=Lax``, ``Secure`` in Production
  (HTTPS-only). Cookie ist nicht aus JS lesbar — XSS kann das Token
  nicht extrahieren.
- Fail-closed: fehlt ``ADMIN_SESSION_SECRET`` oder ``ADMIN_PASSWORD``,
  antworten Login + Dependency mit 503 statt still alles
  durchzulassen.

ENV-Erinnerung (Wolf-Schritt, NICHT im Code):
- ``ADMIN_PASSWORD`` — Klartext-Passwort (ausreichend lang, nicht
  aus Lex-Stamm).
- ``ADMIN_SESSION_SECRET`` — 32+ Bytes Random (``openssl rand -hex 32``).
- ``ADMIN_AUTH_ENABLED=true`` — Master-Schalter, sobald die anderen
  beiden ENVs gesetzt sind.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, status

from app.auth import _path_is_public
from app.config import settings

logger = logging.getLogger(__name__)

ADMIN_SESSION_COOKIE = "cr_admin_session"


def _hmac_signature(expires_unix: int, secret: str) -> str:
    """HMAC-SHA256 ueber ``expires_unix`` (als String) mit ``secret``,
    base64url-encoded ohne Padding. Padding-frei macht das Token sicher
    in URL/Header-Kontexten (kein ``=``-Tail, das geparst werden muss)."""
    payload = str(expires_unix).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")


def create_session_token(secret: str, ttl_seconds: int) -> tuple[str, int]:
    """Erzeugt ein neues Session-Token mit Ablauf jetzt+ttl. Returns
    ``(token, expires_unix)`` — der Caller setzt das Cookie mit
    ``max_age=ttl_seconds`` und kann fuer Diagnose den Ablauf-Timestamp
    loggen.
    """
    expires_unix = int(time.time()) + ttl_seconds
    sig = _hmac_signature(expires_unix, secret)
    return f"{expires_unix}.{sig}", expires_unix


def verify_session_token(token: str, secret: str) -> bool:
    """``True`` wenn das Token eine gueltige Signatur traegt UND nicht
    abgelaufen ist. ``False`` bei jedem Format-/Signatur-/Ablauf-Fehler
    — alle Pfade sind konstant-zeitig (kein Early-Return zwischen
    Format-Check und Signatur-Vergleich, das ein Timing-Leak waere)."""
    if not token or not secret:
        return False
    parts = token.split(".", 1)
    if len(parts) != 2:
        return False
    expires_str, presented_sig = parts
    try:
        expires_unix = int(expires_str)
    except ValueError:
        return False
    expected_sig = _hmac_signature(expires_unix, secret)
    # Beide Strings sind base64url-encoded → ASCII, vergleichbare Laenge.
    # secrets.compare_digest haelt das konstant-zeitig.
    if not secrets.compare_digest(presented_sig, expected_sig):
        return False
    if int(time.time()) >= expires_unix:
        return False
    return True


def verify_admin_password(presented: str) -> bool:
    """Konstant-zeitiger Vergleich gegen ``settings.admin_password``.

    Returns ``False`` wenn das Server-Passwort nicht konfiguriert ist
    (statt eine leere Vergleichs-Falle aufzumachen). Der Login-Endpoint
    macht aus dem False ein 401 — fail-closed im Sinne des Users.
    Eine getrennte 503-Antwort waere informativ, gibt aber einem
    Angreifer den Hinweis "Server ist falsch konfiguriert, Tuer
    offen?". Lieber 401 wie bei einem normalen Fehlgang.
    """
    expected = settings.admin_password
    if not expected or not presented:
        return False
    return secrets.compare_digest(presented, expected)


def require_admin_session(
    request: Request,
    cr_admin_session: Optional[str] = Cookie(default=None),
) -> None:
    """FastAPI-Dependency: prueft das Session-Cookie. 401 wenn ungueltig,
    503 wenn der Server kein Session-Secret hat (fail-closed).

    Anwendung als Router-Level-Dependency:

        router = APIRouter(dependencies=[Depends(require_admin_session)])

    Path-Skip: dieselben Pfade, die ``app/auth.py`` als Public deklariert
    (health, img, thumbnails, storage, docs, openapi, pairs,
    roundups/latest, report-downloads) lassen wir hier auch durch — ein
    geloggter ``<a href download>``-Klick auf ``download.html`` kann
    kein Cookie aus dem CORS-untaggten Frontend-Bundle setzen, und der
    Bearer-Token aus dem Bundle deckt diese Pfade ohnehin nicht ab
    (PUBLIC_PATH_EXACT). Stillschweigend gleicher Public-Bereich auf
    beiden Auth-Schichten — wir definieren ihn an einer Stelle und
    teilen ihn.

    Wird beim Master-Schalter ``settings.admin_auth_enabled=False``
    bewusst zum No-Op — Rollout-Stufen sollen sich nacheinander
    aktivieren lassen, ohne dass das Frontend vorher die Login-Routes
    sehen muss.
    """
    if not settings.admin_auth_enabled:
        return None
    if _path_is_public(request.url.path):
        return None
    secret = settings.admin_session_secret
    if not secret:
        logger.error("admin-auth-session-secret-missing")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin session secret not configured on server",
        )
    if not cr_admin_session or not verify_session_token(
        cr_admin_session, secret
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin session required",
        )
    return None
