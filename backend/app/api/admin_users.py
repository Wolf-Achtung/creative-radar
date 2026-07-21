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

import csv
import html
import io
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.admin_session import require_admin_session, verify_session_token
from app.config import settings
from app.database import get_session
from app.models import AppUser, UsageEvent
from app.models.entities import utc_now
from app.user_session import USER_SESSION_COOKIE, verify_user_session_token

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin-users"],
    dependencies=[Depends(require_admin_session)],
)


def require_usage_access(
    session: Session = Depends(get_session),
    cr_admin_session: str | None = Cookie(default=None),
    cr_user_session: str | None = Cookie(default=None),
) -> None:
    """Zugriffs-Check fuer die Nutzungs-Auswertung (Wolf-Festlegung
    2026-07-20): zusaetzlich zur Voll-Admin-Session duerfen einzelne,
    im Admin-Bereich freigeschaltete Login-User (``can_view_usage``)
    die Auswertung sehen — NUR die; alle uebrigen Admin-Endpoints
    bleiben bei ``require_admin_session``.

    Pruef-Reihenfolge:
    1. ``admin_auth_enabled=False`` -> No-Op (dieselbe dev-Konvention
       wie require_admin_session).
    2. Gueltige Admin-Session -> durch.
    3. Gueltige User-Session UND User aktiv UND ``can_view_usage`` ->
       durch. Der Flag wird pro Request live aus der DB gelesen —
       Entzug wirkt sofort, trotz 30-Tage-Cookie.
    4. Sonst 401.
    """
    if not settings.admin_auth_enabled:
        return None
    if (
        cr_admin_session
        and settings.admin_session_secret
        and verify_session_token(cr_admin_session, settings.admin_session_secret)
    ):
        return None
    if cr_user_session and settings.user_session_secret:
        email = verify_user_session_token(cr_user_session, settings.user_session_secret)
        if email:
            user = session.exec(
                select(AppUser).where(AppUser.email == email.strip().lower())
            ).first()
            if user is not None and user.active and user.can_view_usage:
                return None
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Keine Berechtigung für die Nutzungs-Auswertung.",
    )


# Eigener Router fuer die Nutzungs-Endpoints: gleicher /api/admin-Prefix,
# aber require_usage_access statt require_admin_session (siehe oben).
usage_router = APIRouter(
    prefix="/api/admin",
    tags=["admin-usage"],
    dependencies=[Depends(require_usage_access)],
)


class UserCreateRequest(BaseModel):
    email: str
    display_name: str | None = None


class UserPatchRequest(BaseModel):
    active: bool | None = None
    display_name: str | None = None
    can_view_usage: bool | None = None


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
        "can_view_usage": user.can_view_usage,
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
    if payload.can_view_usage is not None:
        user.can_view_usage = payload.can_view_usage
    session.add(user)
    session.commit()
    session.refresh(user)
    logger.info(
        "admin.users patched email=%s active=%s can_view_usage=%s",
        user.email, user.active, user.can_view_usage,
    )
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


def _period_axes(cutoff: datetime, now: datetime) -> tuple[list[dict], list[dict]]:
    """Lueckenlose Wochen- und Monats-Achsen zwischen ``cutoff`` und
    ``now`` (Report-Zeitzone) — Wochen ohne Nutzung erscheinen als
    0-Spalte statt still zu fehlen ("wer nutzt es NICHT" sichtbar).

    Wochen-Keys: ``2026-W29`` (ISO-Kalender), Label ``KW 29/26``.
    Monats-Keys: ``2026-07``, Label ``Jul 26``.
    """
    tz = ZoneInfo(settings.report_timezone)
    local_cutoff = cutoff.astimezone(tz)
    local_now = now.astimezone(tz)

    weeks: list[dict] = []
    seen_weeks: set[str] = set()
    cursor = local_cutoff
    while cursor <= local_now + timedelta(days=6):
        iso = cursor.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        if key not in seen_weeks:
            seen_weeks.add(key)
            weeks.append({"key": key, "label": f"KW {iso.week}/{str(iso.year)[2:]}"})
        cursor += timedelta(days=7)
        if len(weeks) > 60:  # Schutz gegen Endlosschleife bei kaputten Uhren
            break

    _MONTH_LABELS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
                     "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
    months: list[dict] = []
    year, month = local_cutoff.year, local_cutoff.month
    while (year, month) <= (local_now.year, local_now.month):
        months.append({
            "key": f"{year}-{month:02d}",
            "label": f"{_MONTH_LABELS[month - 1]} {str(year)[2:]}",
        })
        month += 1
        if month > 12:
            month, year = 1, year + 1
        if len(months) > 24:
            break

    return weeks, months


def _aggregate_usage(session: Session, days: int) -> dict:
    """Gemeinsamer Aggregations-Kern fuer die Monitoring-Ansicht
    (``GET /usage``) und die Export-Endpoints (HTML-Bericht, CSV) —
    eine Quelle, drei Darstellungen."""
    now = utc_now()
    cutoff = now - timedelta(days=days)
    events = session.exec(
        select(UsageEvent).where(UsageEvent.created_at >= cutoff)
    ).all()

    tz = ZoneInfo(settings.report_timezone)
    per_user: dict[str, dict] = {}
    action_counts: dict[str, int] = {}
    brief_counts: dict[str, int] = {}

    def _fresh_stats() -> dict:
        return {
            "events": 0, "logins": 0, "last_active": None,
            "actions": {}, "briefs": {}, "weeks": {}, "months": {},
        }

    for event in events:
        created = _as_utc(event.created_at)
        stats = per_user.setdefault(event.email, _fresh_stats())
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
                stats["briefs"][pair] = stats["briefs"].get(pair, 0) + 1
        # Zeit-Raster (Wolf 21.07.: "Übersicht über alle Nutzer und ihr
        # Tun pro Woche und/oder Monat") — Bucketing in der Report-
        # Zeitzone, damit ein Sonntag-23-Uhr-Klick nicht in der falschen
        # Woche landet.
        local = created.astimezone(tz)
        iso = local.isocalendar()
        week_key = f"{iso.year}-W{iso.week:02d}"
        month_key = f"{local.year}-{local.month:02d}"
        stats["weeks"][week_key] = stats["weeks"].get(week_key, 0) + 1
        stats["months"][month_key] = stats["months"].get(month_key, 0) + 1

    users = session.exec(select(AppUser).order_by(AppUser.email)).all()  # type: ignore[arg-type]
    known_emails = {user.email for user in users}
    user_rows = []
    for user in users:
        stats = per_user.get(user.email, _fresh_stats())
        user_rows.append(
            {
                **_user_dict(user),
                "events": stats["events"],
                "logins": stats["logins"],
                "last_active": stats["last_active"].isoformat() if stats["last_active"] else None,
                "actions": stats["actions"],
                "briefs": stats["briefs"],
                "weeks": stats["weeks"],
                "months": stats["months"],
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
                "can_view_usage": False,
                "created_at": None,
                "last_login_at": None,
                "events": stats["events"],
                "logins": stats["logins"],
                "last_active": stats["last_active"].isoformat() if stats["last_active"] else None,
                "actions": stats["actions"],
                "briefs": stats["briefs"],
                "weeks": stats["weeks"],
                "months": stats["months"],
                "deleted": True,
            }
        )

    week_axis, month_axis = _period_axes(cutoff, now)
    return {
        "days": days,
        "events_total": len(events),
        "week_axis": week_axis,
        "month_axis": month_axis,
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


@usage_router.get("/usage")
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
    return _aggregate_usage(session, days)


# Sprechende Labels fuer die Action-Slugs im HTML-Bericht — Spiegel der
# USAGE_ACTION_LABELS im Frontend (MonitoringPanel.jsx).
_ACTION_LABELS = {
    "login": "Anmeldung",
    "landing_view": "Startseite geöffnet",
    "brief_view": "Studio-Brief geöffnet",
    "title_view": "Film-Detailseite geöffnet",
    "forecast_view": "ER-Prognose abgerufen",
    "report_download": "Report heruntergeladen",
}


def _fmt_local(iso_or_dt) -> str:
    """ISO-String/Datetime -> ``20.07.2026 14:32`` in der Report-Zeitzone
    (Europe/Berlin via settings.report_timezone). Leere Werte -> ``—``."""
    if not iso_or_dt:
        return "—"
    value = iso_or_dt
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return str(iso_or_dt)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(ZoneInfo(settings.report_timezone))
    return local.strftime("%d.%m.%Y %H:%M")


@usage_router.get("/usage/user-events")
def usage_user_events(
    email: str = Query(..., max_length=255),
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> dict:
    """Drill-down pro Nutzer: chronologische Event-Liste (neueste zuerst)
    fuer die aufklappbare Detail-Zeile in der Nutzungs-Ansicht."""
    normalized = (email or "").strip().lower()
    cutoff = utc_now() - timedelta(days=days)
    events = session.exec(
        select(UsageEvent)
        .where(UsageEvent.email == normalized)
        .where(UsageEvent.created_at >= cutoff)
        .order_by(UsageEvent.created_at.desc())  # type: ignore[attr-defined]
        .limit(limit)
    ).all()
    return {
        "email": normalized,
        "days": days,
        "events": [
            {
                "created_at": _fmt_local(event.created_at),
                "action": event.action,
                "action_label": _ACTION_LABELS.get(event.action, event.action),
                "pair": (event.context or {}).get("pair"),
                "context": event.context or {},
            }
            for event in events
        ],
    }


@usage_router.get("/usage/export.html")
def usage_export_html(
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> Response:
    """Nutzungs-Bericht als eigenstaendiges HTML-Dokument (Download).

    Wolf-Wunsch 2026-07-20: die Monitoring-Auswertung soll als Datei an
    andere Verantwortliche weitergebbar sein, ohne dass die einen
    Admin-Zugang brauchen. Self-contained HTML (Inline-CSS, keine
    externen Ressourcen) — laesst sich per Mail weiterreichen und ueber
    den Browser-Druckdialog als PDF sichern. Der Abruf selbst bleibt
    Admin-Session-geschuetzt (Router-Dependency); das Frontend laedt die
    Datei per fetch+Blob, damit Bearer-Header und Cookie mitgehen.
    """
    data = _aggregate_usage(session, days)
    generated = _fmt_local(utc_now())

    def esc(value) -> str:
        return html.escape(str(value if value is not None else "—"))

    user_rows = "\n".join(
        "<tr>"
        f"<td>{esc(user['display_name'] or user['email'])}"
        + (f"<br><span class='muted'>{esc(user['email'])}</span>" if user["display_name"] else "")
        + "</td>"
        f"<td>{'entfernt' if user.get('deleted') else ('aktiv' if user['active'] else 'gesperrt')}</td>"
        f"<td>{esc(_fmt_local(user['last_active']))}</td>"
        f"<td class='num'>{user['logins']}</td>"
        f"<td class='num'>{user['actions'].get('brief_view', 0)}</td>"
        f"<td class='num'>{user['events']}</td>"
        "</tr>"
        for user in data["users"]
    )
    brief_rows = "\n".join(
        f"<tr><td>{esc(brief['pair'])}</td><td class='num'>{brief['count']}</td></tr>"
        for brief in data["top_briefs"]
    ) or "<tr><td colspan='2'>Keine Brief-Ansichten im Zeitraum.</td></tr>"
    action_rows = "\n".join(
        f"<tr><td>{esc(_ACTION_LABELS.get(action['action'], action['action']))}</td>"
        f"<td class='num'>{action['count']}</td></tr>"
        for action in data["actions"]
    ) or "<tr><td colspan='2'>Keine Ereignisse im Zeitraum.</td></tr>"

    # Drill-down je Nutzer (Wolf-Festlegung 2026-07-20): pro Nutzer mit
    # Aktivitaet ein eigener Abschnitt — Aktions-Aufschluesselung plus
    # die geoeffneten Studio-Briefs. Die vollstaendige Event-Liste bleibt
    # dem CSV vorbehalten (der Bericht soll lesbar bleiben).
    detail_sections = []
    for user in data["users"]:
        if not user["events"]:
            continue
        name = esc(user["display_name"] or user["email"])
        action_detail = "".join(
            f"<tr><td>{esc(_ACTION_LABELS.get(action, action))}</td><td class='num'>{count}</td></tr>"
            for action, count in sorted(user["actions"].items(), key=lambda item: -item[1])
        )
        briefs = ", ".join(
            f"{esc(pair)} ({count}×)"
            for pair, count in sorted(user["briefs"].items(), key=lambda item: -item[1])
        ) or "—"
        detail_sections.append(
            f"<h3>{name}</h3>"
            f"<p class='muted'>{esc(user['email'])} · zuletzt aktiv {esc(_fmt_local(user['last_active']))}</p>"
            f"<table><thead><tr><th>Aktion</th><th>Anzahl</th></tr></thead><tbody>{action_detail}</tbody></table>"
            f"<p>Geöffnete Studio-Briefs: {briefs}</p>"
        )
    user_details = "\n".join(detail_sections) or "<p>Keine Aktivität im Zeitraum.</p>"

    # Zeit-Matrix (Wolf 21.07.): alle Nutzer x Kalenderwochen bzw. Monate.
    # Bis ~4 Monate Fenster in Wochen-Spalten, darueber Monats-Spalten —
    # sonst wird die Tabelle breiter als eine Druckseite.
    use_months = days > 120
    axis = data["month_axis"] if use_months else data["week_axis"]
    bucket_field = "months" if use_months else "weeks"
    matrix_heading = (
        "Aktivität pro Monat" if use_months else "Aktivität pro Kalenderwoche"
    )
    matrix_head = "".join(f"<th class='num'>{esc(col['label'])}</th>" for col in axis)
    matrix_rows = "\n".join(
        "<tr>"
        f"<td>{esc(user['display_name'] or user['email'])}</td>"
        + "".join(
            f"<td class='num'>{user[bucket_field].get(col['key']) or '·'}</td>"
            for col in axis
        )
        + f"<td class='num'><strong>{user['events']}</strong></td>"
        "</tr>"
        for user in data["users"]
    ) or f"<tr><td colspan='{len(axis) + 2}'>Keine Nutzer angelegt.</td></tr>"
    matrix_table = (
        f"<h2>{matrix_heading}</h2>"
        "<p class='muted'>Ereignisse pro Nutzer und Zeitraum · „·“ = keine Nutzung.</p>"
        "<div style='overflow-x:auto'><table>"
        f"<thead><tr><th>Nutzer</th>{matrix_head}<th class='num'>Σ</th></tr></thead>"
        f"<tbody>{matrix_rows}</tbody>"
        "</table></div>"
    )

    html_doc = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Creative Radar — Nutzungsbericht</title>
<style>
  body {{ font-family: 'Helvetica Neue', Arial, sans-serif; color: #1d2330; margin: 40px auto; max-width: 860px; padding: 0 20px; }}
  h1 {{ font-size: 1.6em; margin-bottom: 0.2em; }}
  h2 {{ font-size: 1.15em; margin-top: 2em; border-bottom: 2px solid #1f4d4d; padding-bottom: 4px; }}
  h3 {{ font-size: 1em; margin: 1.6em 0 0.2em; }}
  .meta {{ color: #6f675b; margin-bottom: 1.5em; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 0.75em; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #ddd4c6; vertical-align: top; font-size: 0.95em; }}
  th {{ background: #f3efe6; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .muted {{ color: #6f675b; font-size: 0.85em; }}
  footer {{ margin-top: 3em; color: #6f675b; font-size: 0.85em; border-top: 1px solid #ddd4c6; padding-top: 10px; }}
  @media print {{ body {{ margin: 0 auto; }} }}
</style>
</head>
<body>
<h1>Creative Radar — Nutzungsbericht</h1>
<p class="meta">Zeitraum: letzte {data['days']} Tage · erstellt am {esc(generated)} Uhr ·
{data['events_total']} Ereignisse eingeloggter Nutzer. Admin-Zugriffe werden nicht gezählt.</p>

<h2>Nutzer</h2>
<table>
<thead><tr><th>Nutzer</th><th>Status</th><th>Zuletzt aktiv</th><th>Anmeldungen</th><th>Brief-Ansichten</th><th>Ereignisse gesamt</th></tr></thead>
<tbody>
{user_rows or "<tr><td colspan='6'>Keine Nutzer angelegt.</td></tr>"}
</tbody>
</table>

{matrix_table}

<h2>Meistgeöffnete Studio-Briefs</h2>
<table>
<thead><tr><th>Studio (Pair)</th><th>Ansichten</th></tr></thead>
<tbody>
{brief_rows}
</tbody>
</table>

<h2>Aktionen gesamt</h2>
<table>
<thead><tr><th>Aktion</th><th>Anzahl</th></tr></thead>
<tbody>
{action_rows}
</tbody>
</table>

<h2>Details je Nutzer</h2>
{user_details}

<footer>Creative Radar — Social-Media-Wochenanalyse für die Filmbranche.
Dieser Bericht enthält personenbezogene Nutzungsdaten und ist nur für interne Verantwortliche bestimmt.</footer>
</body>
</html>"""

    filename = f"creative-radar-nutzung-{utc_now().strftime('%Y-%m-%d')}.html"
    return Response(
        content=html_doc,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@usage_router.get("/usage/export.csv")
def usage_export_csv(
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> Response:
    """Roh-Events als CSV (Excel-tauglich, UTF-8 mit BOM): eine Zeile pro
    Ereignis mit E-Mail, Aktion, Studio-Pair (falls vorhanden) und
    Zeitpunkt in der Report-Zeitzone. Fuer eigene Auswertungen jenseits
    des fertigen HTML-Berichts."""
    cutoff = utc_now() - timedelta(days=days)
    events = session.exec(
        select(UsageEvent).where(UsageEvent.created_at >= cutoff).order_by(UsageEvent.created_at)  # type: ignore[arg-type]
    ).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["zeitpunkt", "email", "aktion", "aktion_label", "studio_pair", "kontext"])
    for event in events:
        context = event.context or {}
        writer.writerow([
            _fmt_local(event.created_at),
            event.email,
            event.action,
            _ACTION_LABELS.get(event.action, event.action),
            context.get("pair", ""),
            json.dumps(context, ensure_ascii=False) if context else "",
        ])

    filename = f"creative-radar-nutzung-{utc_now().strftime('%Y-%m-%d')}.csv"
    return Response(
        # BOM, damit Excel das UTF-8 (Umlaute in Labels) korrekt oeffnet.
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
