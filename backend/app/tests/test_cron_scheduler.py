"""In-process Wochen-Trigger (Incident 2026-07-13) — ersetzt den bislang
1,5-4,5h verzoegerten GitHub-Actions-Schedule. Deckt ab: Zeitfenster-Logik,
Wochen-Dedup (kein Doppel-Trigger), "laeuft bereits"-Guard, Env-Kill-Switch.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import CronRun
from app.services import cron_scheduler


def _engine_for_path(path: str):
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_cronsched_", suffix=".db")
    os.close(fd)
    engine = _engine_for_path(path)
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        try:
            os.unlink(path)
        except OSError:
            pass


# Montag, 03:02 UTC — mitten im Trigger-Fenster (03:00-03:04 UTC).
_IN_WINDOW = datetime(2026, 7, 20, 3, 2, tzinfo=timezone.utc)
# Montag, 06:13 UTC — genau der beobachtete GH-Actions-Verzoegerungsfall,
# aber ausserhalb unseres eigenen Fensters.
_OUT_OF_WINDOW = datetime(2026, 7, 20, 6, 13, tzinfo=timezone.utc)


def test_week_start_utc_is_monday_midnight():
    # Sonntag 23:59 UTC liegt noch in der Vorwoche.
    sunday = datetime(2026, 7, 19, 23, 59, tzinfo=timezone.utc)
    assert cron_scheduler.week_start_utc(sunday) == datetime(2026, 7, 13, tzinfo=timezone.utc)
    # Montag 03:02 UTC liegt in der neuen Woche.
    assert cron_scheduler.week_start_utc(_IN_WINDOW) == datetime(2026, 7, 20, tzinfo=timezone.utc)


def test_is_trigger_window():
    assert cron_scheduler.is_trigger_window(_IN_WINDOW) is True
    assert cron_scheduler.is_trigger_window(_OUT_OF_WINDOW) is False
    # Dienstag, gleiche Uhrzeit wie das Fenster — falscher Wochentag.
    tuesday = _IN_WINDOW + timedelta(days=1)
    assert cron_scheduler.is_trigger_window(tuesday) is False


@pytest.mark.asyncio
async def test_fires_once_when_no_run_this_week(monkeypatch, db):
    monkeypatch.setattr(cron_scheduler, "engine", db)
    monkeypatch.setattr("app.api.cron._reap_stale_runs", lambda session: None)
    with patch(
        "app.api.cron._run_cron_sync_background", new=AsyncMock(return_value=None)
    ) as mocked:
        fired = await cron_scheduler.maybe_trigger_scheduled_run(now=_IN_WINDOW)
        await asyncio.sleep(0)  # let the asyncio.create_task()'d coroutine run

    assert fired is True
    mocked.assert_called_once()
    args = mocked.call_args.args
    assert args[2:] == ("completed", False, None)  # target_week, force, pairs
    with Session(db) as session:
        runs = session.exec(select(CronRun)).all()
    assert len(runs) == 1


@pytest.mark.asyncio
async def test_skips_outside_trigger_window(monkeypatch, db):
    monkeypatch.setattr(cron_scheduler, "engine", db)
    fired = await cron_scheduler.maybe_trigger_scheduled_run(now=_OUT_OF_WINDOW)
    assert fired is False
    with Session(db) as session:
        runs = session.exec(select(CronRun)).all()
    assert len(runs) == 0


@pytest.mark.asyncio
async def test_dedups_against_a_run_already_started_this_week(monkeypatch, db):
    monkeypatch.setattr(cron_scheduler, "engine", db)
    monkeypatch.setattr("app.api.cron._reap_stale_runs", lambda session: None)
    # Simuliert einen frueheren manuellen Klick auf "Jetzt komplett
    # aktualisieren" am selben Montag, bevor das Fenster erreicht wurde.
    with Session(db) as session:
        earlier = CronRun(
            started_at=_IN_WINDOW - timedelta(hours=1),
            completed_at=_IN_WINDOW - timedelta(minutes=30),
            status="completed",
        )
        session.add(earlier)
        session.commit()

    fired = await cron_scheduler.maybe_trigger_scheduled_run(now=_IN_WINDOW)

    assert fired is False
    with Session(db) as session:
        runs = session.exec(select(CronRun)).all()
    assert len(runs) == 1  # nur der simulierte frühere Run, kein zweiter


@pytest.mark.asyncio
async def test_skips_when_a_run_from_last_week_is_still_running(monkeypatch, db):
    # Distinct from the weekly-dedup case: started_at is BEFORE this week's
    # start (so already_triggered_this_week() alone wouldn't catch it) and
    # not yet reaped as stale — the separate "running" guard is what
    # prevents a double-fire here.
    monkeypatch.setattr(cron_scheduler, "engine", db)
    monkeypatch.setattr("app.api.cron._reap_stale_runs", lambda session: None)
    with Session(db) as session:
        running = CronRun(
            started_at=cron_scheduler.week_start_utc(_IN_WINDOW) - timedelta(minutes=5),
            status="running",
        )
        session.add(running)
        session.commit()

    fired = await cron_scheduler.maybe_trigger_scheduled_run(now=_IN_WINDOW)
    assert fired is False


@pytest.mark.asyncio
async def test_disabled_via_env_short_circuits_the_loop(monkeypatch):
    monkeypatch.setenv("ENABLE_INTERNAL_CRON_SCHEDULER", "false")
    assert cron_scheduler.is_scheduler_enabled() is False
    # run_scheduler_loop() must return immediately instead of looping forever.
    await asyncio.wait_for(cron_scheduler.run_scheduler_loop(), timeout=2)
