"""Rematch-Stage-Timeout (Diagnose-Folge 2026-07-06): der 06.07.-Lauf hat mit
dem stark gewachsenen Titel-Katalog (title_sync-Fix ohne Seiten-Cap pro
Studio) beim Rematch das komplette 2h-Gesamtbudget verbraucht, ohne dass
Briefs/Roundups/Cutter-Weekly noch liefen. Verifiziert den eigenen Timeout um
die Rematch-Stage, analog zu ``_run_title_sync_after_scrape``."""
from __future__ import annotations

import os
import tempfile
import time

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.api import cron as cron_module


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_rematch_timeout_", suffix=".db")
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


@pytest.mark.asyncio
async def test_rematch_stage_times_out_and_reports_error(monkeypatch, db):
    monkeypatch.setenv("REMATCH_STAGE_TIMEOUT_SECONDS", "1")

    def _hangs_forever(session):
        time.sleep(10)

    monkeypatch.setattr(cron_module, "rematch_unassigned_assets", _hangs_forever)

    with Session(db) as session:
        result = await cron_module._run_rematch_after_sync(session)

    assert result["timed_out"] is True
    assert "stage_timeout after 1s" in result["error"]
    assert isinstance(result["duration_seconds"], float)


@pytest.mark.asyncio
async def test_rematch_stage_returns_summary_when_fast(monkeypatch, db):
    monkeypatch.setenv("REMATCH_STAGE_TIMEOUT_SECONDS", "5")

    class _FakeSummary:
        def to_dict(self):
            return {
                "checked": 3,
                "auto_matched": 2,
                "candidates_created": 1,
                "still_unmatched": 0,
            }

    monkeypatch.setattr(
        cron_module, "rematch_unassigned_assets", lambda session: _FakeSummary()
    )

    with Session(db) as session:
        result = await cron_module._run_rematch_after_sync(session)

    assert result["checked"] == 3
    assert result["auto_matched"] == 2
    assert "duration_seconds" in result
    assert "error" not in result
