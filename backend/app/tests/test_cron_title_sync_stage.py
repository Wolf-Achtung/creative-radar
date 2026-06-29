"""Tests for the cron title-sync stage (_run_title_sync_after_scrape):
kill-switch, result pass-through, error isolation. sync_titles_from_tmdb is
patched — no TMDb/network."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.api import cron as cron_module


def test_title_sync_stage_disabled_by_env(monkeypatch):
    monkeypatch.setenv("ENABLE_TITLE_SYNC_IN_CRON", "false")
    monkeypatch.setattr(
        cron_module, "sync_titles_from_tmdb",
        AsyncMock(side_effect=AssertionError("must not be called when disabled")),
    )

    result = asyncio.run(cron_module._run_title_sync_after_scrape(MagicMock()))

    assert result == {"enabled": False}


def test_title_sync_stage_runs_and_passes_result(monkeypatch):
    monkeypatch.setenv("ENABLE_TITLE_SYNC_IN_CRON", "true")
    fake = AsyncMock(return_value={"upserted_count": 5, "fetched_count": 12})
    monkeypatch.setattr(cron_module, "sync_titles_from_tmdb", fake)

    result = asyncio.run(cron_module._run_title_sync_after_scrape(MagicMock()))

    assert result["enabled"] is True
    assert result["upserted_count"] == 5
    assert result["fetched_count"] == 12
    # Stage-Dauer landet im Summary, damit der Timeout-Default nach dem ersten
    # sauberen Lauf datengetrieben statt geschaetzt nachjustiert werden kann.
    assert isinstance(result["duration_seconds"], float)
    fake.assert_awaited_once()


def test_title_sync_stage_times_out_and_marks_run_error(monkeypatch):
    """Stage-Timeout: ein haengender Pass wird nach ``wait_for`` als
    ``timed_out`` verbucht (Cron laeuft weiter) und die zurueckgelassene
    ``TitleSyncRun``-Row best-effort auf ``error`` gesetzt."""
    monkeypatch.setenv("ENABLE_TITLE_SYNC_IN_CRON", "true")
    monkeypatch.setattr(cron_module, "_title_sync_stage_timeout_seconds", lambda: 0.05)

    async def _hang(_session):
        await asyncio.sleep(1)
        return {"upserted_count": 0}

    monkeypatch.setattr(cron_module, "sync_titles_from_tmdb", _hang)

    session = MagicMock()
    result = asyncio.run(cron_module._run_title_sync_after_scrape(session))

    assert result["enabled"] is True
    assert result["timed_out"] is True
    assert "stage_timeout" in result["error"]
    assert isinstance(result["duration_seconds"], float)
    # Audit-Cleanup lief: rollback + commit der auf error gesetzten Row.
    session.rollback.assert_called()
    session.commit.assert_called()


def test_title_sync_stage_absorbs_errors(monkeypatch):
    monkeypatch.setenv("ENABLE_TITLE_SYNC_IN_CRON", "true")
    monkeypatch.setattr(
        cron_module, "sync_titles_from_tmdb",
        AsyncMock(side_effect=RuntimeError("TMDb down")),
    )

    result = asyncio.run(cron_module._run_title_sync_after_scrape(MagicMock()))

    assert result["enabled"] is True
    assert "TMDb down" in result["error"]
