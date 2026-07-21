"""User-Login-Routen: Code anfordern, Code einloesen, Logout, Me.

Sprint User-Login 2026-07. Flow-Parameter identisch zum Referenzprojekt
api-ki-backend-neu (``routes/auth.py``), damit sich beide Produkte fuer
die (teils identischen) Nutzer gleich anfuehlen:

- 6-stelliger numerischer Code, ``secrets.randbelow`` (kein Modulo-Bias),
- 10 Minuten gueltig (``login_code_ttl_seconds``),
- Rate-Limits 10x Code-Anfordern / 5x Login pro 5 Minuten — hier
  doppelt gefuehrt: pro Client-IP (bestehende Dependency) UND pro
  E-Mail-Adresse (``rate_limit.hit`` — gegen Angreifer mit IP-Rotation),
- nicht freigeschaltete Adresse -> 403 mit deutscher Meldung.

Bewusste Abweichungen von der Referenz (Begruendung im jeweiligen Code):

- Code liegt gehasht in der DB (nicht Klartext in Redis/In-Memory),
  Einmal-Nutzung via ``used_at``, Fehlversuchs-Deckel via ``attempts``.
- Session: 30-Tage-HttpOnly-Cookie (SameSite=Lax, same-site Subdomains
  app./api.creative-radar.de) statt 1h-JWT — Wochen-Brief-Leser sollen
  nicht woechentlich neu einloggen ("Angemeldet bleiben" eingebaut).
- ``/me`` antwortet IMMER 200 mit ``{authenticated, email, auth_enabled}``
  (Konvention der Admin-Session — kein Error-Toast beim Page-Load).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlmodel import Session, delete, select

from app.config import settings
from app.database import get_session
from app.models import AppUser, LoginCode
from app.models.entities import utc_now
from app.services import rate_limit
from app.services.mailer import MailerError, send_mail
from app.services.usage_log import log_usage
from app.user_session import (
    USER_SESSION_COOKIE,
    create_user_session_token,
    verify_user_session_token,
)

router = APIRouter(prefix="/api/auth", tags=["user-auth"])
logger = logging.getLogger(__name__)

LOGIN_CODE_MAX_ATTEMPTS = 5

# Bewusst locker (kein RFC-Parser): die eigentliche Autoritaet ist die
# app_user-Allowlist — die Regex faengt nur Tippfehler-Muell frueh ab,
# bevor er in Rate-Limit-Buckets und DB-Lookups laeuft.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RequestCodeIn(BaseModel):
    email: str


class LoginIn(BaseModel):
    email: str
    code: str


def _normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Bitte eine gültige E-Mail-Adresse angeben.",
        )
    return email


def _require_enabled() -> None:
    if not settings.user_auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User auth disabled on server",
        )


def _cookie_kwargs() -> dict:
    """Identisch zur Admin-Session (api/admin.py): Secure nur ausserhalb
    lokaler Umgebungen, SameSite=Lax reicht — app.* und api.* sind
    same-site (gleiche registrierbare Domain creative-radar.de)."""
    secure = settings.app_env not in ("dev", "test", "local")
    return {"httponly": True, "samesite": "lax", "secure": secure, "path": "/"}


def _hash_code(email: str, code: str) -> str:
    """SHA-256 ueber ``email:code`` — die E-Mail im Hash verhindert, dass
    ein Code-Hash-Treffer einer anderen Adresse wiederverwendbar waere."""
    return hashlib.sha256(f"{email}:{code}".encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    """SQLite (Tests) liefert naive Datetimes zurueck, Postgres aware —
    fuer Vergleiche beide auf aware UTC ziehen."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _mask(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


# 200 + JSON-Body statt 204 wie in der Referenz: der Frontend-Client
# (api/client.js parseJsonResponse) wirft bei Antworten ohne JSON-
# Content-Type — ein leerer 204 waere dort ein kuenstlicher Fehlerpfad.
@router.post("/request-code")
async def request_code(
    payload: RequestCodeIn,
    db: Session = Depends(get_session),
    _rl: None = Depends(
        rate_limit.rate_limit(
            "auth-request-code",
            max_calls=settings.auth_rate_max_request_code,
            window_seconds=settings.auth_rate_window_sec,
        )
    ),
):
    """Login-Code anfordern: prueft die Allowlist, erzeugt einen
    6-stelligen Code (10 Min. gueltig) und mailt ihn.

    403 fuer nicht freigeschaltete Adressen — gleiche Semantik wie die
    Referenz (die Allowlist ist bei ~15 bekannten Nutzern kein Geheimnis,
    und die Meldung erspart dem vertippten Kollegen das vergebliche
    Postfach-Warten). 503 wenn der Mail-Versand scheitert.
    """
    _require_enabled()
    email = _normalize_email(payload.email)
    rate_limit.hit(
        "auth-request-code-email",
        email,
        max_calls=settings.auth_rate_max_request_code,
        window_seconds=settings.auth_rate_window_sec,
    )

    user = db.exec(select(AppUser).where(AppUser.email == email)).first()
    if user is None or not user.active:
        logger.warning("auth.request-code rejected (not allowlisted) email=%s", _mask(email))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Diese E-Mail-Adresse ist nicht freigeschaltet.",
        )

    code = f"{secrets.randbelow(1_000_000):06d}"
    ttl = settings.login_code_ttl_seconds
    # Latest-code-wins: alte offene Codes der Adresse raus, genau eine
    # aktive Row pro E-Mail — haelt auch die Tabelle von selbst klein.
    db.exec(delete(LoginCode).where(LoginCode.email == email))
    db.add(
        LoginCode(
            email=email,
            code_hash=_hash_code(email, code),
            expires_at=utc_now() + timedelta(seconds=ttl),
        )
    )
    db.commit()

    minutes = max(1, ttl // 60)
    text = (
        "Ihr persönlicher Anmeldecode für Creative Radar lautet:\n\n"
        f"{code}\n\n"
        f"Der Code ist {minutes} Minuten gültig.\n\n"
        "Falls Sie diese Anmeldung nicht angefordert haben, können Sie diese E-Mail ignorieren.\n\n"
        "Kein Code angekommen?\n"
        "• Spam- oder Junk-Ordner prüfen\n"
        "• Code einfach erneut anfordern\n\n"
        "Diese E-Mail gehört zum Login von Creative Radar (app.creative-radar.de).\n"
        "Es handelt sich nicht um Werbung.\n\n"
        "– Creative Radar\n"
    )
    if settings.disable_emails:
        # Lokaler Dev-Pfad ohne Mail-Provider: Code im Log statt Postfach.
        # In Production ist disable_emails aus — dort landet NIE ein
        # Klartext-Code im Log.
        logger.info("auth.request-code (emails disabled) email=%s code=%s", _mask(email), code)
    try:
        await send_mail(to=email, subject="Ihr Anmeldecode für Creative Radar", text=text)
    except MailerError:
        logger.exception("auth.request-code mail send failed email=%s", _mask(email))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="E-Mail-Versand fehlgeschlagen. Bitte später erneut versuchen.",
        )
    logger.info("auth.request-code sent email=%s", _mask(email))
    return {"ok": True}


@router.post("/login")
def login(
    payload: LoginIn,
    response: Response,
    db: Session = Depends(get_session),
    _rl: None = Depends(
        rate_limit.rate_limit(
            "auth-login",
            max_calls=settings.auth_rate_max_login,
            window_seconds=settings.auth_rate_window_sec,
        )
    ),
) -> dict:
    """Code einloesen: Einmal-Nutzung, Ablauf- und Fehlversuchs-Check,
    Erfolg setzt das 30-Tage-HttpOnly-Session-Cookie.

    Alle Fehlerpfade antworten einheitlich 401 "Code ist ungültig oder
    abgelaufen." — ob die Adresse unbekannt, der Code falsch, verbraucht
    oder abgelaufen ist, unterscheidet die Antwort bewusst nicht.
    """
    _require_enabled()
    if not settings.user_session_secret:
        logger.error("auth.login user-session-secret-missing")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User session secret not configured on server",
        )
    email = _normalize_email(payload.email)
    rate_limit.hit(
        "auth-login-email",
        email,
        max_calls=settings.auth_rate_max_login,
        window_seconds=settings.auth_rate_window_sec,
    )

    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Code ist ungültig oder abgelaufen.",
    )

    user = db.exec(select(AppUser).where(AppUser.email == email)).first()
    code_row = db.exec(
        select(LoginCode)
        .where(LoginCode.email == email)
        .order_by(LoginCode.created_at.desc())  # type: ignore[attr-defined]
    ).first()

    if user is None or not user.active or code_row is None:
        logger.warning("auth.login failed (no user/code) email=%s", _mask(email))
        raise invalid
    if code_row.used_at is not None:
        logger.warning("auth.login failed (code already used) email=%s", _mask(email))
        raise invalid
    if code_row.attempts >= LOGIN_CODE_MAX_ATTEMPTS:
        logger.warning("auth.login failed (too many attempts) email=%s", _mask(email))
        raise invalid
    if _as_utc(code_row.expires_at) <= utc_now():
        logger.warning("auth.login failed (code expired) email=%s", _mask(email))
        raise invalid

    presented_hash = _hash_code(email, (payload.code or "").strip())
    if not hmac.compare_digest(presented_hash, code_row.code_hash):
        # Fehlversuch zaehlen, DANN ablehnen — nach 5 Fehlversuchen ist
        # der Code verbrannt, selbst innerhalb der 10 Minuten.
        code_row.attempts += 1
        db.add(code_row)
        db.commit()
        logger.warning(
            "auth.login failed (wrong code, attempt %s) email=%s",
            code_row.attempts, _mask(email),
        )
        raise invalid

    now = utc_now()
    code_row.used_at = now
    user.last_login_at = now
    db.add(code_row)
    db.add(user)
    db.commit()

    token, expires_unix = create_user_session_token(
        email, settings.user_session_secret, settings.user_session_ttl_seconds
    )
    response.set_cookie(
        key=USER_SESSION_COOKIE,
        value=token,
        max_age=settings.user_session_ttl_seconds,
        **_cookie_kwargs(),
    )
    log_usage(email, "login", {})
    logger.info("auth.login success email=%s", _mask(email))
    return {
        "ok": True,
        "email": email,
        "expires_unix": expires_unix,
        # Monitoring-Freischaltung — Frontend zeigt damit direkt nach dem
        # Login den "Nutzung"-Link, ohne /me nachzufragen.
        "can_view_usage": bool(user.can_view_usage),
        # Admin-per-User-Login (Sprint 2026-07-21): steuert den
        # "Admin"-Link in der User-Leiste.
        "is_admin": email in settings.admin_user_email_set,
    }


@router.post("/logout")
def logout(response: Response) -> dict:
    """Logout = Cookie clearen (stateless Token, Muster Admin-Session)."""
    kwargs = _cookie_kwargs()
    response.delete_cookie(
        key=USER_SESSION_COOKIE,
        path=kwargs["path"],
        samesite=kwargs["samesite"],
        secure=kwargs["secure"],
        httponly=kwargs["httponly"],
    )
    return {"ok": True}


@router.get("/me")
def me(request: Request, db: Session = Depends(get_session)) -> dict:
    """Antwortet IMMER 200 — das Frontend entscheidet damit beim
    Page-Load zwischen Login-Formular und App, ohne Error-Toast.
    ``auth_enabled=false`` (Rollout-Phase / lokales Dev) gilt als
    eingeloggt (Konvention der Admin-Session)."""
    if not settings.user_auth_enabled:
        return {"authenticated": True, "auth_enabled": False, "email": None, "can_view_usage": False, "is_admin": False}
    secret = settings.user_session_secret
    token = request.cookies.get(USER_SESSION_COOKIE)
    email: Optional[str] = verify_user_session_token(token, secret) if (token and secret) else None
    can_view_usage = False
    if email:
        user = db.exec(select(AppUser).where(AppUser.email == email.strip().lower())).first()
        if user is None or not user.active:
            email = None
        else:
            # Monitoring-Freischaltung (Wolf 2026-07-20): steuert im
            # Frontend den "Nutzung"-Link in der User-Leiste.
            can_view_usage = bool(user.can_view_usage)
    return {
        "authenticated": bool(email),
        "auth_enabled": True,
        "email": email,
        "can_view_usage": can_view_usage,
        "is_admin": bool(email) and email in settings.admin_user_email_set,
    }
