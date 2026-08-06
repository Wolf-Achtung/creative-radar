"""In-process weekly cron trigger (Incident 2026-07-13).

Bis hierhin lief der woechentliche Sync ausschliesslich ueber die GitHub
Action ``cron-sync.yml`` (Schedule ``0 3 * * 1``). Beobachtung ueber sieben
Wochen: der Schedule-Trigger von GitHub Actions feuert konsistent 1,5-4,5h
zu spaet (GitHubs eigenes Best-effort-Scheduling fuer den geteilten
Runner-Pool, kein Bug in diesem Repo). Das verschiebt den gesamten
Cron-Lauf (bis zu 2,5h Laufzeit obendrauf) oft bis weit in den Montag
hinein, statt wie beabsichtigt fertig zu sein, wenn Cutter morgens ins
Office kommen.

Dieses Modul ersetzt den Schedule-Trigger durch einen Loop, der im selben
immer-laufenden Railway-Web-Prozess lebt: keine externe Runner-Queue, also
keine Wartezeit. ``cron-sync.yml`` bleibt als manueller Fallback
(``workflow_dispatch``) bestehen, der ``schedule:``-Trigger wurde entfernt.

Sicherheit gegen Doppel-Ausloesung:
- Wochen-Dedup ueber ``CronRun.started_at`` (irgendein Run seit Montag
  00:00 UTC zaehlt, egal ob durch diesen Loop, den Admin-Button oder einen
  manuellen ``workflow_dispatch`` gestartet — verhindert einen zweiten
  teuren Vollauf in derselben Woche).
- Der bestehende "laeuft bereits"-Check (``CronRun.status == 'running'``)
  in ``cron_sync_all`` greift zusaetzlich bei echten Gleichzeitigkeits-
  Faellen.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.database import engine
from app.models.entities import CronRun

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 60
_TRIGGER_WEEKDAY = 0  # Monday (datetime.weekday(): Monday == 0)
_TRIGGER_HOUR_UTC = 3
_TRIGGER_MINUTE_WINDOW = 5  # 03:00-03:04 UTC — five 60s-ticks of margin


def is_scheduler_enabled() -> bool:
    """Explizites ENV gewinnt immer; ohne ENV ist der Scheduler nur in
    Production an (Staging-Briefing 2026-08-06): ein Staging-/Dev-Backend
    mit gespiegelten Prod-Variablen wuerde sonst montags von allein echte
    Apify-/LLM-Laeufe starten. Late import von settings, damit das Modul
    weiter ohne DB-Konfiguration importierbar bleibt (Test-Pfad)."""
    raw = os.environ.get("ENABLE_INTERNAL_CRON_SCHEDULER")
    if raw is not None:
        return raw.lower() == "true"
    from app.config import settings  # noqa: PLC0415

    return settings.app_env == "production"


def week_start_utc(now: datetime) -> datetime:
    """Montag 00:00 UTC der Woche, in der ``now`` liegt."""
    days_since_monday = now.weekday()
    return (now - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def is_trigger_window(now: datetime) -> bool:
    return (
        now.weekday() == _TRIGGER_WEEKDAY
        and now.hour == _TRIGGER_HOUR_UTC
        and now.minute < _TRIGGER_MINUTE_WINDOW
    )


def already_triggered_this_week(session: Session, now: datetime) -> bool:
    week_start = week_start_utc(now)
    existing = session.exec(
        select(CronRun).where(CronRun.started_at >= week_start)
    ).first()
    return existing is not None


async def maybe_trigger_scheduled_run(now: datetime | None = None) -> bool:
    """Reapt hängengebliebene Runs auf JEDEM Tick (60s), unabhaengig vom
    Zeitfenster, und startet bei Bedarf den regulaeren Sync-Lauf
    (``target_week='completed'``, ``force=False`` — identisch zum
    bisherigen GitHub-Action-Trigger). Gibt ``True`` zurueck, wenn ein Lauf
    gestartet wurde (fuer Tests).

    Incident 2026-07-13 (Folgefund): ein manuell getriggerter Recovery-Lauf
    wurde durch einen Railway-Redeploy (ausgeloest von einem waehrenddessen
    gemergten PR) mitten im Lauf gekillt. Der ``CronRun``-Eintrag blieb
    ``running``, weil ``_reap_stale_runs`` bis dahin nur beim naechsten
    ``POST /sync-all`` lief — ohne neuen manuellen Trigger blieb die Zeile
    zwei Stunden lang faelschlich als "laeuft noch" sichtbar. Reap jetzt auf
    jedem 60s-Tick, damit ein gekillter Lauf spaetestens ``CRON_RUN_
    TIMEOUT_MINUTES`` (Default 30min) spaeter korrekt als ``failed``
    markiert ist, statt fuer immer auf ``running`` haengen zu bleiben.
    """
    # Lazy imports: vermeiden einen Modul-Ladezeit-Zirkelimport mit
    # ``app.api.cron`` (das dieses Modul NICHT importiert — nur ``main.py``
    # importiert beide unabhaengig voneinander).
    from app.api.cron import _reap_stale_runs, _run_cron_sync_background
    from app.services.cron_channel_selection import compute_run_index

    now = now or datetime.now(timezone.utc)

    with Session(engine) as session:
        _reap_stale_runs(session)

        if not is_trigger_window(now):
            return False
        if already_triggered_this_week(session, now):
            return False

        running = session.exec(
            select(CronRun).where(CronRun.status == "running")
        ).first()
        if running:
            return False

        run_index = compute_run_index()
        run = CronRun(run_index=run_index)
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    logger.info(
        "cron_scheduler.triggering run_id=%s run_index=%d", run_id, run_index
    )
    asyncio.create_task(
        _run_cron_sync_background(run_id, run_index, "completed", False, None)
    )
    return True


async def run_scheduler_loop() -> None:
    if not is_scheduler_enabled():
        logger.info("cron_scheduler.disabled")
        return
    logger.info("cron_scheduler.started")
    while True:
        try:
            await maybe_trigger_scheduled_run()
        except Exception:  # noqa: BLE001 — loop must survive a bad tick
            logger.exception("cron_scheduler.tick_failed")
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
