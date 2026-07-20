"""Nutzungs-Event-Log (Sprint User-Login 2026-07).

Ein Helper, zwei Garantien:

1. **Nie den Request kippen.** Ein fehlgeschriebenes Event darf keinen
   Brief-Abruf zu einem 500 machen — jeder Fehler wird geloggt und
   geschluckt. Das Log ist Telemetrie, kein Geschaeftspfad.
2. **Nur eingeloggte Nutzung zaehlt.** Aufrufer reichen die E-Mail aus
   ``request.state.user_email`` (via ``user_session.request_user_email``)
   durch; ist sie None (Auth aus, Admin-Session, public Pfad), passiert
   nichts. So bleiben Wolfs Admin-Klicks und die Rollout-Phase vor dem
   Auth-Flip aus der Team-Statistik heraus.

Eigene, kurze DB-Session pro Event statt der Request-Session des
Aufrufers: ein Commit hier darf niemals halbfertige Aenderungen der
aufrufenden Route mit-committen (und umgekehrt kein Rollback der Route
das Event verlieren — Events sind append-only Fakten: "wurde geoeffnet"
stimmt auch, wenn die Antwort danach scheitert).
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session

from app.models import UsageEvent

logger = logging.getLogger(__name__)


def log_usage(email: Optional[str], action: str, context: Optional[dict] = None) -> None:
    """Schreibt ein Usage-Event, wenn ``email`` gesetzt ist. Fehler
    werden geloggt, nie geraist."""
    if not email:
        return
    try:
        # Lazy import (Muster user_session.py): koppelt Modul-Importe
        # nicht an die DB-Engine.
        from app.database import engine

        with Session(engine) as db:
            db.add(UsageEvent(email=email, action=action, context=context or {}))
            db.commit()
    except Exception:  # noqa: BLE001 — Telemetrie darf nie den Request kippen
        logger.exception("usage-log-write-failed action=%s", action)
