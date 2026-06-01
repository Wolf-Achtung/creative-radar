"""Cadence-Sprint 2026-05-17 — Brief-Generation als neue Cron-Stage.

Verifiziert das Verhalten von ``_run_brief_generation_after_sync`` und seiner
Integration in ``_run_cron_sync_background``:

1. Happy-Path: jeder ``enabled=True``-Pair bekommt einen Brief-Gen-Call,
   ``now`` liegt einen Tag in der Vergangenheit, Counter und Cost stimmen.
2. ENV-Toggle ``ENABLE_BRIEF_GEN_IN_CRON=false`` skippt die Stage komplett.
3. Per-Pair-Try/Except: ein failender Pair killt nicht die anderen.
4. ``now - 1 day``-Anker (H4-Mitigation gegen ISO-Wochen-Off-by-One am
   Montag-Cron, der sonst die noch leere neue KW erwischt hätte).
5. Cost-Floor-Alert: bei 0 generierten Briefs + <$5 Anthropic-Cost feuert
   ``logger.critical('cron_brief_gen.silent_failure')`` — das Frühwarn-
   signal #2 aus dem Premortem (PR #147 Failure-Mode #2).
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.api import cron as cron_module
from app.models.entities import CronRun
from app.services.insight_engine import PAIRS


def _engine_for_path(path: str):
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_cron_briefs_", suffix=".db")
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


def _seed_run(db) -> UUID:
    with Session(db) as session:
        run = CronRun()
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id


def _patch_cron_neighbors(monkeypatch, db, *, brief_gen_mock=None, anthropic_cost_usd: float = 0.0):
    """Stubbt alle Cron-Pipeline-Nachbarn ausserhalb der Brief-Gen-Stage
    konstant, damit jeder Test nur die Brief-Gen-Semantik beobachtet."""
    monkeypatch.setattr("app.api.cron.engine", db)

    monkeypatch.setattr(
        cron_module, "compute_apify_monthly_spend",
        lambda session: SimpleNamespace(
            hard_cap_exceeded=False,
            enforced=False,
            soft_warn_exceeded=False,
            to_dict=lambda: {"spent_usd_cents": 0, "budget_usd_cents": 50000},
            pct_used=0.0,
            spent_usd_cents=0,
            budget_usd_cents=50000,
        ),
    )
    monkeypatch.setattr(
        cron_module, "_execute_platform_sync",
        AsyncMock(return_value=({}, [])),
    )
    monkeypatch.setattr(
        cron_module, "_run_rematch_after_sync",
        lambda session: {},
    )
    monkeypatch.setattr(
        cron_module, "aggregate_apify_costs_since",
        lambda session, since: {"estimated_cost_usd": 0.0, "calls_total": 0},
    )
    monkeypatch.setattr(
        cron_module, "aggregate_anthropic_costs_since",
        lambda session, since: {"estimated_cost_usd": anthropic_cost_usd, "calls_total": 0},
    )
    monkeypatch.setattr(
        cron_module, "aggregate_openai_costs_since",
        lambda session, since: {"estimated_cost_usd": 0.0, "calls_total": 0},
    )
    if brief_gen_mock is not None:
        monkeypatch.setattr(
            cron_module, "generate_and_persist_report",
            brief_gen_mock,
        )


def _enabled_pair_count() -> int:
    return sum(1 for v in PAIRS.values() if v.get("enabled", False))


def test_cron_triggers_brief_gen_for_all_enabled_pairs(db, monkeypatch):
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    # llm_output non-None marks a real, persisted brief — the cron now
    # counts ``report.llm_output is None`` as failed (parse/schema/citation
    # fail → _persist_report skip), so success-path mocks must carry it.
    brief_mock = MagicMock(
        return_value=SimpleNamespace(cost_usd_estimate=1.50, llm_output=object())
    )
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    run_id = _seed_run(db)

    asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    n_pairs = _enabled_pair_count()
    assert brief_mock.call_count == n_pairs

    today_utc = datetime.now(timezone.utc).date()
    for call in brief_mock.call_args_list:
        passed_now = call.kwargs["now"]
        assert isinstance(passed_now, datetime)
        assert (today_utc - passed_now.date()) == timedelta(days=1)
        assert call.kwargs["window_days"] == 30

    with Session(db) as session:
        run = session.get(CronRun, run_id)
        assert run.status == "completed"
        briefs = run.summary_json["briefs"]
        assert briefs["enabled"] is True
        assert briefs["generated"] == n_pairs
        assert briefs["skipped_cache_hit"] == 0
        assert briefs["failed"] == 0
        # 1.50 USD * 100 cents/USD = 150 cents per fresh brief.
        assert briefs["cost_usd_cents"] == n_pairs * 150
        assert briefs["errors"] == []


def test_cron_brief_gen_skipped_when_disabled(db, monkeypatch):
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "false")
    brief_mock = MagicMock()
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    run_id = _seed_run(db)

    asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    assert brief_mock.call_count == 0
    with Session(db) as session:
        run = session.get(CronRun, run_id)
        briefs = run.summary_json["briefs"]
        assert briefs["enabled"] is False
        assert briefs["generated"] == 0
        assert briefs["skipped_cache_hit"] == 0
        assert briefs["failed"] == 0
        assert briefs["cost_usd_cents"] == 0


def test_cron_brief_gen_handles_per_pair_failure(db, monkeypatch):
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    failing_pair = "disney"

    def side_effect(session, pair_key, **kwargs):
        if pair_key == failing_pair:
            raise RuntimeError("simulated LLM blowup")
        return SimpleNamespace(cost_usd_estimate=0.50, llm_output=object())

    brief_mock = MagicMock(side_effect=side_effect)
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    run_id = _seed_run(db)

    asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    n_pairs = _enabled_pair_count()
    assert brief_mock.call_count == n_pairs

    with Session(db) as session:
        run = session.get(CronRun, run_id)
        briefs = run.summary_json["briefs"]
        assert briefs["failed"] == 1
        assert briefs["generated"] == n_pairs - 1
        assert len(briefs["errors"]) == 1
        err = briefs["errors"][0]
        assert err["pair"] == failing_pair
        assert err["error_class"] == "RuntimeError"
        assert "simulated LLM blowup" in err["error_message"]
        # Run selbst bleibt completed — Per-Pair-Failure ist KEIN Cron-Crash.
        assert run.status == "completed"


def test_cron_brief_gen_counts_missing_llm_output_as_failed(db, monkeypatch):
    """Regression 2026-06-01: generate_and_persist_report returns normally
    with ``llm_output is None`` when the brief fails parse/schema/citation
    checks (and _persist_report skips the write). Such a report must count
    as ``failed``, not ``generated`` — otherwise the cron reports phantom
    successes (that run logged generated=8 / failed=0 while nothing
    persisted)."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    brief_mock = MagicMock(
        return_value=SimpleNamespace(cost_usd_estimate=1.50, llm_output=None)
    )
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    run_id = _seed_run(db)

    asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    n_pairs = _enabled_pair_count()
    with Session(db) as session:
        run = session.get(CronRun, run_id)
        assert run.status == "completed"
        briefs = run.summary_json["briefs"]
        assert briefs["generated"] == 0
        assert briefs["failed"] == n_pairs
        # Cost is still accumulated for the paid-but-unpersisted calls.
        assert briefs["cost_usd_cents"] == n_pairs * 150
        assert len(briefs["errors"]) == n_pairs
        assert all(e["error_class"] == "no_llm_output" for e in briefs["errors"])


def test_cron_brief_gen_uses_yesterday_for_iso_week(db, monkeypatch):
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    brief_mock = MagicMock(
        return_value=SimpleNamespace(cost_usd_estimate=0.0, llm_output=object())
    )
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    run_id = _seed_run(db)

    before = datetime.now(timezone.utc)
    asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))
    after = datetime.now(timezone.utc)

    assert brief_mock.call_count > 0
    # Jeder Aufruf muss now ~= utcnow - 1 day mitgeben. Wir akzeptieren
    # eine kleine Run-Drift: passed_now liegt zwischen (before-1d) und
    # (after-1d). Wenn jemand die "-1 day"-Logik versehentlich entfernt,
    # bricht diese Assertion sofort.
    expected_min = before - timedelta(days=1, seconds=2)
    expected_max = after - timedelta(days=1) + timedelta(seconds=2)
    for call in brief_mock.call_args_list:
        passed_now = call.kwargs["now"]
        assert expected_min <= passed_now <= expected_max, (
            f"now-1d-Logik kaputt: passed_now={passed_now} nicht in "
            f"[{expected_min}, {expected_max}]"
        )


def test_cron_brief_gen_cost_floor_alert_triggers(db, monkeypatch, caplog):
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    # Alle Pairs schmeissen Exception → 0 generated, n failed, $0 Anthropic.
    # Das ist exakt das Frühwarn-Szenario aus Premortem-Failure-Mode #2.
    brief_mock = MagicMock(side_effect=RuntimeError("silent regression"))
    _patch_cron_neighbors(
        monkeypatch, db, brief_gen_mock=brief_mock, anthropic_cost_usd=0.0,
    )
    run_id = _seed_run(db)

    with caplog.at_level(logging.CRITICAL, logger="app.api.cron"):
        asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    silent_failure_records = [
        r for r in caplog.records
        if r.levelname == "CRITICAL"
        and "cron_brief_gen.silent_failure" in r.getMessage()
    ]
    assert len(silent_failure_records) == 1
    rec = silent_failure_records[0]
    assert rec.briefs_enabled is True
    assert rec.briefs_generated == 0
    assert rec.briefs_failed == _enabled_pair_count()
    assert rec.anthropic_cost_usd == 0.0

    with Session(db) as session:
        run = session.get(CronRun, run_id)
        briefs = run.summary_json["briefs"]
        assert briefs["generated"] == 0
        assert briefs["failed"] == _enabled_pair_count()


def test_cron_brief_gen_cost_floor_alert_silent_when_costs_present(db, monkeypatch, caplog):
    """Gegenprobe zum Alert-Test: wenn ``anthropic_cost_usd >= 5.0``, ist
    der Pfad nicht silent (Cost beweist, dass LLM-Calls gelaufen sind),
    also feuert kein Critical — auch wenn ``generated == 0``."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    brief_mock = MagicMock(side_effect=RuntimeError("loud regression"))
    _patch_cron_neighbors(
        monkeypatch, db, brief_gen_mock=brief_mock, anthropic_cost_usd=12.34,
    )
    run_id = _seed_run(db)

    with caplog.at_level(logging.CRITICAL, logger="app.api.cron"):
        asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    silent_failure_records = [
        r for r in caplog.records
        if "cron_brief_gen.silent_failure" in r.getMessage()
    ]
    assert silent_failure_records == []
