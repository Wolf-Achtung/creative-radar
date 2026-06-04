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

    assert result == {"enabled": True, "upserted_count": 5, "fetched_count": 12}
    fake.assert_awaited_once()


def test_title_sync_stage_absorbs_errors(monkeypatch):
    monkeypatch.setenv("ENABLE_TITLE_SYNC_IN_CRON", "true")
    monkeypatch.setattr(
        cron_module, "sync_titles_from_tmdb",
        AsyncMock(side_effect=RuntimeError("TMDb down")),
    )

    result = asyncio.run(cron_module._run_title_sync_after_scrape(MagicMock()))

    assert result["enabled"] is True
    assert "TMDb down" in result["error"]
