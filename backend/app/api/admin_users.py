"""Admin-Endpoints fuer User-Verwaltung + Nutzungs-Auswertung.

Sprint User-Login 2026-07. Eigenes Modul statt weiterem Anbau an das
ohnehin grosse ``api/admin.py`` — gleicher ``/api/admin``-Prefix,
gleiche Router-Level-Dependency (``require_admin_session``): Wolf
verwaltet die ~15 Login-User im Admin-Bereich, ohne je in die DB zu
muessen.

Auswertungs-Design (``GET /usage``): die Aggregation laeuft in Python
statt in SQL — bei 15 Usern und einem Wochen-Rhythmus reden wir ueber
hunderte, nicht Millionen Events pro Fenster, und der ``context``-Blob
ist ein JSON-Feld, dessen Cross-DB-Query (SQLite-Tests vs. Postgres)
mehr Komplexitaet kostet als das Nachladen spart. ``days`` ist auf 365
gedeckelt, damit ein Tippfehler nicht die ganze Tabelle zieht.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.admin_session import require_admin_session
from app.database import get_session
from app.models import AppUser, UsageEvent
from app.models.entities import utc_now

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin-users"],
    dependencies=[Depends(require_admin_session)],
)


class UserCreateRequest(BaseModel):
    email: str
    display_name: str | None = None


class UserPatchRequest(BaseModel):
    active: bool | None = None
    display_name: str | None = None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _user_dict(user: AppUser) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "active": user.active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


@router.get("/users")
def list_users(session: Session = Depends(get_session)) -> list[dict]:
    users = session.exec(select(AppUser).order_by(AppUser.email)).all()  # type: ignore[arg-type]
    return [_user_dict(user) for user in users]


@router.post("/users", status_code=201)
def create_user(payload: UserCreateRequest, session: Session = Depends(get_session)) -> dict:
    email = (payload.email or "").strip().lower()
    if "@" not in email or "." not in email.partition("@")[2]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Bitte eine gültige E-Mail-Adresse angeben.",
        )
    existing = session.exec(select(AppUser).where(AppUser.email == email)).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Diese E-Mail-Adresse ist bereits angelegt.",
        )
    user = AppUser(email=email, display_name=(payload.display_name or "").strip() or None)
    session.add(user)
    session.commit()
    session.refresh(user)
    logger.info("admin.users created email=%s", email)
    return _user_dict(user)


@router.patch("/users/{user_id}")
def patch_user(user_id: UUID, payload: UserPatchRequest, session: Session = Depends(get_session)) -> dict:
    user = session.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User nicht gefunden.")
    if payload.active is not None:
        user.active = payload.active
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip() or None
    session.add(user)
    session.commit()
    session.refresh(user)
    logger.info("admin.users patched email=%s active=%s", user.email, user.active)
    return _user_dict(user)


@router.delete("/users/{user_id}")
def delete_user(user_id: UUID, session: Session = Depends(get_session)) -> dict:
    """Loescht den User (harte Loeschung — fuer "raus aus der Liste").
    Die Usage-Events der E-Mail bleiben bewusst stehen (Audit-Charakter,
    kein FK). Zum voruebergehenden Sperren ist PATCH active=false der
    richtige Weg."""
    user = session.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User nicht gefunden.")
    session.delete(user)
    session.commit()
    logger.info("admin.users deleted email=%s", user.email)
    return {"ok": True}


@router.get("/usage")
def usage_summary(
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> dict:
    """Nutzungs-Auswertung fuers Monitoring: wer war wann zuletzt aktiv,
    welche Aktionen und welche Studios/Briefs werden am meisten genutzt.

    ``users`` enthaelt ALLE angelegten User (auch die ohne Events im
    Fenster — genau die will Wolf sehen: "wer nutzt es NICHT?"),
    angereichert um Event-Zahlen aus den letzten ``days`` Tagen.
    ``last_active`` ist das juengste Event der E-Mail im Fenster;
    fuer laenger inaktive User zeigt ``last_login_at`` aus der
    User-Row den letzten bekannten Login davor.
    """
    cutoff = utc_now() - timedelta(days=days)
    events = session.exec(
        select(UsageEvent).where(UsageEvent.created_at >= cutoff)
    ).all()

    per_user: dict[str, dict] = {}
    action_counts: dict[str, int] = {}
    brief_counts: dict[str, int] = {}
    for event in events:
        created = _as_utc(event.created_at)
        stats = per_user.setdefault(
            event.email, {"events": 0, "logins": 0, "last_active": None, "actions": {}}
        )
        stats["events"] += 1
        stats["actions"][event.action] = stats["actions"].get(event.action, 0) + 1
        if event.action == "login":
            stats["logins"] += 1
        if stats["last_active"] is None or created > stats["last_active"]:
            stats["last_active"] = created
        action_counts[event.action] = action_counts.get(event.action, 0) + 1
        if event.action == "brief_view":
            pair = (event.context or {}).get("pair")
            if pair:
                brief_counts[pair] = brief_counts.get(pair, 0) + 1

    users = session.exec(select(AppUser).order_by(AppUser.email)).all()  # type: ignore[arg-type]
    known_emails = {user.email for user in users}
    user_rows = []
    for user in users:
        stats = per_user.get(user.email, {"events": 0, "logins": 0, "last_active": None, "actions": {}})
        user_rows.append(
            {
                **_user_dict(user),
                "events": stats["events"],
                "logins": stats["logins"],
                "last_active": stats["last_active"].isoformat() if stats["last_active"] else None,
                "actions": stats["actions"],
            }
        )
    # Events geloeschter User (E-Mail nicht mehr in app_user) tauchen als
    # eigene Zeilen auf — Historie soll sichtbar bleiben, nicht raetselhaft
    # in den Summen stecken.
    for email, stats in sorted(per_user.items()):
        if email in known_emails:
            continue
        user_rows.append(
            {
                "id": None,
                "email": email,
                "display_name": None,
                "active": False,
                "created_at": None,
                "last_login_at": None,
                "events": stats["events"],
                "logins": stats["logins"],
                "last_active": stats["last_active"].isoformat() if stats["last_active"] else None,
                "actions": stats["actions"],
                "deleted": True,
            }
        )

    return {
        "days": days,
        "events_total": len(events),
        "users": user_rows,
        "actions": [
            {"action": action, "count": count}
            for action, count in sorted(action_counts.items(), key=lambda item: -item[1])
        ],
        "top_briefs": [
            {"pair": pair, "count": count}
            for pair, count in sorted(brief_counts.items(), key=lambda item: -item[1])
        ][:15],
    }
