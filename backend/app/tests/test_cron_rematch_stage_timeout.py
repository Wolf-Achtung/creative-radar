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
    """Havarie-Backstop: haengt der Worker trotz Soft-Deadline (z.B. ein
    einzelner Asset-Durchlauf blockiert), feuert weiterhin das harte
    ``wait_for``-Timeout."""
    monkeypatch.setenv("REMATCH_STAGE_TIMEOUT_SECONDS", "1")

    def _hangs_forever(session, **_kwargs):
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
                "partial": False,
                "remaining": 0,
            }

    monkeypatch.setattr(
        cron_module,
        "rematch_unassigned_assets",
        lambda session, **_kwargs: _FakeSummary(),
    )

    with Session(db) as session:
        result = await cron_module._run_rematch_after_sync(session)

    assert result["checked"] == 3
    assert result["auto_matched"] == 2
    assert result["partial"] is False
    assert "duration_seconds" in result
    assert "error" not in result


@pytest.mark.asyncio
async def test_rematch_stage_passes_soft_budget_below_hard_limit(monkeypatch, db):
    """Soft-Deadline (Cron-Run 16421771): die Stage reicht dem Worker ein
    Zeitbudget 120s UNTER dem harten Stage-Limit durch, damit er selbst
    sauber abbricht statt in den nicht-abbrechbaren ``wait_for``-Zombie-
    Zustand zu laufen (Session-Sharing mit der Brief-Stage)."""
    monkeypatch.setenv("REMATCH_STAGE_TIMEOUT_SECONDS", "1800")
    captured = {}

    class _FakeSummary:
        def to_dict(self):
            return {
                "checked": 1, "auto_matched": 0, "candidates_created": 0,
                "still_unmatched": 1, "partial": False, "remaining": 0,
            }

    def _capture(session, **kwargs):
        captured.update(kwargs)
        return _FakeSummary()

    monkeypatch.setattr(cron_module, "rematch_unassigned_assets", _capture)

    with Session(db) as session:
        result = await cron_module._run_rematch_after_sync(session)

    assert captured["time_budget_seconds"] == 1680.0
    assert "error" not in result


@pytest.mark.asyncio
async def test_rematch_stage_partial_summary_is_no_error(monkeypatch, db, caplog):
    """Ein Soft-Deadline-Teilabbruch ist KEIN Fehlerfall: die Summary kommt
    mit ``partial``/``remaining`` durch, ohne ``error``/``timed_out``, und
    wird als ``rematch.partial``-WARNING geloggt."""
    import logging

    monkeypatch.setenv("REMATCH_STAGE_TIMEOUT_SECONDS", "1800")

    class _PartialSummary:
        def to_dict(self):
            return {
                "checked": 4000, "auto_matched": 120, "candidates_created": 30,
                "still_unmatched": 3880, "partial": True, "remaining": 9000,
            }

    monkeypatch.setattr(
        cron_module,
        "rematch_unassigned_assets",
        lambda session, **_kwargs: _PartialSummary(),
    )

    with caplog.at_level(logging.WARNING, logger="app.api.cron"):
        with Session(db) as session:
            result = await cron_module._run_rematch_after_sync(session)

    assert result["partial"] is True
    assert result["remaining"] == 9000
    assert "error" not in result
    assert "timed_out" not in result
    assert any("rematch.partial" in r.getMessage() for r in caplog.records)
