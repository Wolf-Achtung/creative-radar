"""E-Mail-Versand fuer Login-Codes — Resend-API oder SMTP-Fallback.

Sprint User-Login 2026-07, portiert aus dem Referenzprojekt
api-ki-backend-neu (``services/mailer.py``), angepasst an die
creative-radar-Settings-Konventionen (typed ``app.config.Settings``
statt os.getenv-Streuung, ein zentraler Kill-Switch).

Provider-Weiche:
- ``EMAIL_PROVIDER=resend`` (Default): HTTPS-POST an api.resend.com.
  Braucht ``RESEND_API_KEY`` und ``MAIL_FROM`` (Absender-Adresse auf
  einer in Resend verifizierten Domain). Fehlt einer der beiden Werte,
  faellt der Versand auf SMTP zurueck (Referenz-Verhalten) — und
  schlaegt dort mit einer klaren Meldung fehl, wenn auch SMTP nicht
  konfiguriert ist.
- ``EMAIL_PROVIDER=smtp``: klassisches SMTP mit STARTTLS.

``DISABLE_EMAILS=true`` ist der globale Kill-Switch (Tests, lokale
Devs): der Versand wird geloggt und uebersprungen, der Aufrufer sieht
Erfolg. Der Login-Code steht dann nur im DB-Hash — fuer lokale Tests
den Code aus dem Log der Route ziehen oder den Schalter aus lassen.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
from email.mime.text import MIMEText
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


class MailerError(Exception):
    """Versand fehlgeschlagen — Aufrufer uebersetzt das in ein 503."""


async def send_mail(to: str, subject: str, text: str, html: Optional[str] = None) -> None:
    """Sendet eine E-Mail ueber den konfigurierten Provider.

    Raises ``MailerError`` bei jedem Versand-/Konfigurationsfehler —
    bewusst EINE Exception-Klasse nach aussen, damit die Auth-Route
    nicht Provider-Interna unterscheiden muss.
    """
    if settings.disable_emails:
        logger.info("mailer.disabled skipping email to=%s subject=%s", _mask(to), subject)
        # Dev/Staging-Rettungsleine (Staging-Briefing 2026-08-06): ohne
        # Mail-Provider waere der Login-Code unerreichbar — ausserhalb von
        # production landet der Mail-Text (inkl. Code) deshalb im Log.
        # In production bleibt der Kill-Switch ein reiner Kill-Switch:
        # Klartext-Codes gehoeren nicht in Prod-Logs (Drittanbieter-Sinks).
        if settings.app_env != "production":
            # Einzeilig loggen: Railways Log-Shipper schneidet Records an
            # der ersten Newline ab — der Login-Code stand sonst genau
            # HINTER dem Schnitt und war unerreichbar (21.08.2026).
            logger.info(
                "mailer.disabled.body to=%s text=%s",
                _mask(to),
                " | ".join(line for line in text.splitlines() if line.strip()),
            )
        return

    provider = (settings.email_provider or "resend").strip().lower()
    if provider == "resend" and settings.resend_api_key and settings.mail_from:
        await _send_resend(to=to, subject=subject, text=text, html=html)
        return
    if provider == "resend":
        # Referenz-Verhalten: unvollstaendige Resend-Config -> SMTP-Fallback.
        logger.warning(
            "mailer.resend-config-incomplete falling back to SMTP (resend_api_key set=%s, mail_from set=%s)",
            bool(settings.resend_api_key),
            bool(settings.mail_from),
        )
    await _send_smtp(to=to, subject=subject, text=text, html=html)


def _mask(email: str) -> str:
    """``wolf.hohl@web.de`` -> ``w***@web.de`` — Logs ohne Klartext-PII."""
    local, _, domain = (email or "").partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


async def _send_resend(to: str, subject: str, text: str, html: Optional[str]) -> None:
    from_addr = f"{settings.mail_from_name} <{settings.mail_from}>"
    body: dict = {"from": from_addr, "to": [to], "subject": subject, "text": text}
    if html:
        body["html"] = html

    logger.info("mailer.resend sending to=%s subject=%s", _mask(to), subject)
    try:
        async with httpx.AsyncClient(timeout=settings.mail_timeout_seconds) as client:
            response = await client.post(
                RESEND_API_URL,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json=body,
            )
    except httpx.HTTPError as exc:
        logger.error("mailer.resend transport error to=%s: %s", _mask(to), exc)
        raise MailerError(f"Resend transport error: {exc}") from exc

    if response.status_code >= 400:
        # Response-Body mitloggen (enthaelt Resend-Fehlercode, z. B.
        # "domain is not verified"), aber gekappt — kein Log-Flooding.
        logger.error(
            "mailer.resend api error to=%s status=%s body=%s",
            _mask(to), response.status_code, response.text[:300],
        )
        raise MailerError(f"Resend API error {response.status_code}")
    logger.info("mailer.resend sent to=%s status=%s", _mask(to), response.status_code)


async def _send_smtp(to: str, subject: str, text: str, html: Optional[str]) -> None:
    if not settings.smtp_host:
        raise MailerError("SMTP not configured: missing SMTP_HOST")
    from_email = settings.mail_from or settings.smtp_user
    if not from_email:
        raise MailerError("SMTP not configured: missing MAIL_FROM/SMTP_USER")

    message = MIMEText(html or text, "html" if html else "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = f"{settings.mail_from_name} <{from_email}>"
    message["To"] = to

    logger.info(
        "mailer.smtp sending to=%s via %s:%s", _mask(to), settings.smtp_host, settings.smtp_port,
    )

    def _sync_send() -> None:
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.mail_timeout_seconds) as smtp:
                if settings.smtp_starttls:
                    smtp.starttls()
                if settings.smtp_user and settings.smtp_password:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.sendmail(from_email, [to], message.as_string())
        except (smtplib.SMTPException, OSError) as exc:
            raise MailerError(f"SMTP error: {exc}") from exc

    # smtplib ist blocking — in den Default-Executor, damit der
    # Event-Loop des async Endpoints nicht fuer Sekunden haengt.
    await asyncio.get_event_loop().run_in_executor(None, _sync_send)
    logger.info("mailer.smtp sent to=%s", _mask(to))
