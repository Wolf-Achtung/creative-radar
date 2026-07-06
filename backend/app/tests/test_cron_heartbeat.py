"""Cron-Heartbeat / Dead-Man's-Switch ping (Diagnose-Folge 2026-07-06)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.api.cron import _ping_cron_heartbeat
from app.config import settings


@pytest.mark.asyncio
async def test_noop_when_url_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "cron_heartbeat_url", None, raising=False)
    with patch("app.api.cron.httpx.AsyncClient") as ac:
        await _ping_cron_heartbeat(success=True)
        ac.assert_not_called()


@pytest.mark.asyncio
async def test_success_pings_base_url(monkeypatch):
    monkeypatch.setattr(settings, "cron_heartbeat_url", "https://hc-ping.com/abc", raising=False)
    with patch("app.api.cron.httpx.AsyncClient") as ac:
        instance = ac.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=httpx.Response(200))
        await _ping_cron_heartbeat(success=True)
        instance.get.assert_called_once_with("https://hc-ping.com/abc")


@pytest.mark.asyncio
async def test_failure_pings_fail_suffix(monkeypatch):
    monkeypatch.setattr(settings, "cron_heartbeat_url", "https://hc-ping.com/abc", raising=False)
    with patch("app.api.cron.httpx.AsyncClient") as ac:
        instance = ac.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=httpx.Response(200))
        await _ping_cron_heartbeat(success=False)
        instance.get.assert_called_once_with("https://hc-ping.com/abc/fail")


@pytest.mark.asyncio
async def test_ping_error_is_swallowed(monkeypatch):
    monkeypatch.setattr(settings, "cron_heartbeat_url", "https://hc-ping.com/abc", raising=False)
    with patch("app.api.cron.httpx.AsyncClient") as ac:
        instance = ac.return_value.__aenter__.return_value
        instance.get = AsyncMock(side_effect=httpx.ConnectTimeout("timeout"))
        # Must not raise — a heartbeat-ping failure may not crash the cron run.
        await _ping_cron_heartbeat(success=True)
