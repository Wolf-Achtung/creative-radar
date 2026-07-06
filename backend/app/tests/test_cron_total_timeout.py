"""Global safety-net timeout around the cron background job (2026-07-06
diagnose: title-sync alone took ~28 min this run, no stage besides
title-sync has any wall-clock ceiling). Verifies the whole run terminates
and marks the CronRun as errored instead of hanging forever."""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.api import cron as cron_module
from app.models.entities import CronRun


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_cron_timeout_", suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        try:
            os.unlink(path)
        except OSError:
            pass


def _seed_run(db) -> UUID:
    with Session(db) as session:
        run = CronRun()
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id


def test_marks_run_as_error_when_impl_exceeds_total_timeout(monkeypatch, db):
    monkeypatch.setattr(cron_module, "engine", db)
    monkeypatch.setenv("CRON_TOTAL_RUN_TIMEOUT_SECONDS", "1")

    async def _hangs_forever(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(cron_module, "_run_cron_sync_background_impl", _hangs_forever)

    run_id = _seed_run(db)
    asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    with Session(db) as session:
        run = session.get(CronRun, run_id)
        assert run.status == "error"
        assert "total_run_timeout" in run.error_message
        assert run.completed_at is not None


def test_does_not_touch_run_when_impl_finishes_in_time(monkeypatch, db):
    monkeypatch.setattr(cron_module, "engine", db)
    monkeypatch.setenv("CRON_TOTAL_RUN_TIMEOUT_SECONDS", "5")

    async def _finishes_fast(run_id, run_index, target_week="completed", force=False, brief_pairs=None):
        with Session(db) as session:
            run = session.get(CronRun, run_id)
            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)
            session.add(run)
            session.commit()

    monkeypatch.setattr(cron_module, "_run_cron_sync_background_impl", _finishes_fast)

    run_id = _seed_run(db)
    asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    with Session(db) as session:
        run = session.get(CronRun, run_id)
        assert run.status == "completed"
        assert run.error_message is None
