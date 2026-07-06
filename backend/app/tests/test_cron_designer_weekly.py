"""Cron-Integration des Designer-Wochenbriefings.

Mirror von ``test_cron_cutter_weekly.py``. Verifiziert
``_run_designer_weekly_after_cutter_weekly`` plus Reihenfolge im
``_run_cron_sync_background`` (additiv NACH dem Cutter-Block):

1. Feature-Flag off (Default — Trockenlauf) → Block skippt mit
   ``reason="feature_flag_off"``, Cron-Verhalten wie vor dem Sprint.
2. F0.7-Cap-Re-Check: hard-cap exceeded → Block skippt, Generator wird
   nicht gerufen.
3. Kill-Switch: Cap exceeded aber enforced=False → läuft trotzdem.
4. Cache-Hit auf PK (iso_year, iso_week) → kein LLM-Call; force=True
   überspringt den Check (Last-Write-Wins-Persistenz).
5. Erfolg → Summary mit Modell, released_platforms, Kosten.
6. Block-Isolation: Exception im Generator kippt den Run nicht.
7. Reihenfolge: Cutter-Weekly VOR dem Designer-Block, Summary trägt den
   designer_weekly-Block.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.api import cron as cron_module
from app.models.entities import CronRun, DesignerWeeklyBriefing


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_cron_designer_", suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        try:
            os.unlink(path)
        except OSError:
            pass


def _make_budget_status(*, exceeded: bool, enforced: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        hard_cap_exceeded=exceeded,
        enforced=enforced,
        soft_warn_exceeded=False,
        spent_usd_cents=21_000 if exceeded else 1_000,
        budget_usd_cents=20_000,
        pct_used=1.05 if exceeded else 0.05,
        to_dict=lambda: {
            "hard_cap_exceeded": exceeded,
            "enforced": enforced,
            "spent_usd_cents": 21_000 if exceeded else 1_000,
            "budget_usd_cents": 20_000,
        },
    )


def _fake_report(*, model: str = "claude-opus-4-7", llm_output_present: bool = True):
    released = SimpleNamespace(platform="instagram", status="pattern_released")
    idle = SimpleNamespace(platform="tiktok", status="no_pattern")
    return SimpleNamespace(
        model=model,
        llm_output=SimpleNamespace() if llm_output_present else None,
        cost_usd_estimate=0.31,
        evidence=SimpleNamespace(platforms=[released, idle]),
    )


def _patch_generate(monkeypatch, fn):
    """Der Cron-Block importiert lazy — am Quellmodul patchen."""
    monkeypatch.setattr(
        "app.services.designer_weekly.generate_and_persist_designer_weekly", fn
    )


# ---------------------------------------------------------------------------
# Test 1 — Feature-Flag off (Trockenlauf-Default)
# ---------------------------------------------------------------------------


def test_designer_weekly_skipped_when_feature_flag_off(db, monkeypatch):
    monkeypatch.delenv("FEATURE_DESIGNER_WEEKLY_ENABLED", raising=False)
    gen_mock = MagicMock()
    _patch_generate(monkeypatch, gen_mock)

    with Session(db) as session:
        result = cron_module._run_designer_weekly_after_cutter_weekly(session)

    assert result["enabled"] is False
    assert result["skipped"] is True
    assert result["reason"] == "feature_flag_off"
    gen_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2 — F0.7-Cap-Re-Check
# ---------------------------------------------------------------------------


def test_designer_weekly_skipped_when_anthropic_cap_exceeded(db, monkeypatch):
    monkeypatch.setenv("FEATURE_DESIGNER_WEEKLY_ENABLED", "true")
    monkeypatch.setattr(
        cron_module, "compute_anthropic_monthly_spend",
        lambda s: _make_budget_status(exceeded=True, enforced=True),
    )
    gen_mock = MagicMock()
    _patch_generate(monkeypatch, gen_mock)

    with Session(db) as session:
        result = cron_module._run_designer_weekly_after_cutter_weekly(session)

    assert result["skipped"] is True
    assert result["reason"] == "anthropic_budget_exceeded"
    assert result["anthropic_budget"]["hard_cap_exceeded"] is True
    gen_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3 — Kill-Switch: enforced=False läuft trotzdem
# ---------------------------------------------------------------------------


def test_designer_weekly_runs_when_cap_exceeded_but_enforced_false(db, monkeypatch):
    monkeypatch.setenv("FEATURE_DESIGNER_WEEKLY_ENABLED", "true")
    monkeypatch.setattr(
        cron_module, "compute_anthropic_monthly_spend",
        lambda s: _make_budget_status(exceeded=True, enforced=False),
    )
    _patch_generate(monkeypatch, MagicMock(return_value=_fake_report()))

    with Session(db) as session:
        result = cron_module._run_designer_weekly_after_cutter_weekly(session)

    assert result["skipped"] is False
    assert result["generated"] == 1


# ---------------------------------------------------------------------------
# Test 4 — Cache-Hit + force
# ---------------------------------------------------------------------------


def _seed_existing_row(db, brief_now: datetime) -> None:
    iso_cal = brief_now.isocalendar()
    with Session(db) as session:
        session.add(
            DesignerWeeklyBriefing(
                iso_year=iso_cal.year,
                iso_week=iso_cal.week,
                evidence={"existing": True},
                llm_output=None,
                model="none",
            )
        )
        session.commit()


def test_designer_weekly_cache_hit_skips_llm_call(db, monkeypatch):
    monkeypatch.setenv("FEATURE_DESIGNER_WEEKLY_ENABLED", "true")
    monkeypatch.setattr(
        cron_module, "compute_anthropic_monthly_spend",
        lambda s: _make_budget_status(exceeded=False),
    )
    brief_now = datetime.now(timezone.utc) - timedelta(days=1)
    _seed_existing_row(db, brief_now)
    gen_mock = MagicMock()
    _patch_generate(monkeypatch, gen_mock)

    with Session(db) as session:
        result = cron_module._run_designer_weekly_after_cutter_weekly(
            session, brief_now=brief_now
        )

    assert result["generated"] == 0
    assert result["skipped_cache_hit"] == 1
    gen_mock.assert_not_called()


def test_designer_weekly_force_overrides_cache_hit(db, monkeypatch):
    monkeypatch.setenv("FEATURE_DESIGNER_WEEKLY_ENABLED", "true")
    monkeypatch.setattr(
        cron_module, "compute_anthropic_monthly_spend",
        lambda s: _make_budget_status(exceeded=False),
    )
    brief_now = datetime.now(timezone.utc) - timedelta(days=1)
    _seed_existing_row(db, brief_now)
    gen_mock = MagicMock(return_value=_fake_report())
    _patch_generate(monkeypatch, gen_mock)

    with Session(db) as session:
        result = cron_module._run_designer_weekly_after_cutter_weekly(
            session, brief_now=brief_now, force=True
        )

    assert result["generated"] == 1
    gen_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Test 5 — Erfolg: Summary-Felder
# ---------------------------------------------------------------------------


def test_designer_weekly_success_summary(db, monkeypatch):
    monkeypatch.setenv("FEATURE_DESIGNER_WEEKLY_ENABLED", "true")
    monkeypatch.setattr(
        cron_module, "compute_anthropic_monthly_spend",
        lambda s: _make_budget_status(exceeded=False),
    )
    brief_now = datetime.now(timezone.utc) - timedelta(days=1)
    captured_kwargs = {}

    def fake_generate(session, **kwargs):
        captured_kwargs.update(kwargs)
        return _fake_report()

    _patch_generate(monkeypatch, fake_generate)

    with Session(db) as session:
        result = cron_module._run_designer_weekly_after_cutter_weekly(
            session, brief_now=brief_now
        )

    assert result["generated"] == 1
    assert result["model"] == "claude-opus-4-7"
    assert result["llm_output_present"] is True
    assert result["released_platforms"] == ["instagram"]
    assert result["cost_usd_cents"] == 31
    iso_cal = brief_now.isocalendar()
    assert result["iso_year"] == iso_cal.year
    assert result["iso_week"] == iso_cal.week
    # Der Block reicht den Cron-Wochen-Anker durch — gleiche KW wie
    # Briefs/Roundups/Cutter-Weekly desselben Laufs.
    assert captured_kwargs["now"] == brief_now


# ---------------------------------------------------------------------------
# Test 6 — Block-Isolation: Exception kippt den Run nicht
# ---------------------------------------------------------------------------


def test_designer_weekly_failure_is_isolated(db, monkeypatch):
    monkeypatch.setenv("FEATURE_DESIGNER_WEEKLY_ENABLED", "true")
    monkeypatch.setattr(
        cron_module, "compute_anthropic_monthly_spend",
        lambda s: _make_budget_status(exceeded=False),
    )

    def boom(session, **kwargs):
        raise RuntimeError("simulated designer failure")

    _patch_generate(monkeypatch, boom)

    with Session(db) as session:
        result = cron_module._run_designer_weekly_after_cutter_weekly(session)

    assert result["failed"] == 1
    assert result["error"]["error_class"] == "RuntimeError"
    assert result["generated"] == 0


# ---------------------------------------------------------------------------
# Test 7 — Reihenfolge im Cron-Background: Cutter-Weekly vor Designer-Weekly
# ---------------------------------------------------------------------------


def test_cron_background_runs_cutter_weekly_before_designer_weekly(db, monkeypatch):
    """Position ist additiv: der Designer-Block laeuft NACH dem Cutter-Block
    (siehe Docstring von ``_run_designer_weekly_after_cutter_weekly``).
    Verifiziert via call-order der Spies; Summary traegt beide Bloecke."""
    call_order: list[str] = []

    def fake_warmup(session):
        call_order.append("er_forecasts")
        return {"pairs_total": 0, "generated": 0, "cache_hits": 0,
                "no_einordnung": 0, "failed": 0, "errors": []}

    def fake_cutter(session, **kwargs):
        call_order.append("cutter_weekly")
        return {"enabled": True, "skipped": False, "generated": 1,
                "skipped_cache_hit": 0, "failed": 0}

    def fake_designer(session, **kwargs):
        call_order.append("designer_weekly")
        return {"enabled": True, "skipped": False, "generated": 1,
                "skipped_cache_hit": 0, "failed": 0}

    monkeypatch.setattr(
        cron_module, "_run_er_forecast_warmup_after_roundups", fake_warmup,
    )
    monkeypatch.setattr(
        cron_module, "_run_cutter_weekly_after_forecasts", fake_cutter,
    )
    monkeypatch.setattr(
        cron_module, "_run_designer_weekly_after_cutter_weekly", fake_designer,
    )
    monkeypatch.setattr(
        cron_module, "_run_brief_generation_after_sync",
        lambda session, **kwargs: {"enabled": True, "generated": 0,
                                   "skipped_cache_hit": 0, "failed": 0,
                                   "cost_usd_cents": 0, "errors": []},
    )
    monkeypatch.setattr(
        cron_module, "_run_segment_roundups_after_briefs",
        lambda session, **kwargs: {"enabled": False, "skipped": True,
                                   "generated": 0, "skipped_cache_hit": 0,
                                   "failed": 0, "cost_usd_cents": 0,
                                   "results": [], "errors": []},
    )
    monkeypatch.setattr(
        cron_module, "compute_apify_monthly_spend",
        lambda s: SimpleNamespace(
            hard_cap_exceeded=False, enforced=False, soft_warn_exceeded=False,
            to_dict=lambda: {}, pct_used=0.0,
            spent_usd_cents=0, budget_usd_cents=50000,
        ),
    )
    monkeypatch.setattr(
        cron_module, "compute_anthropic_monthly_spend",
        lambda s: _make_budget_status(exceeded=False),
    )
    monkeypatch.setattr(
        cron_module, "_execute_platform_sync", AsyncMock(return_value=({}, [])),
    )
    monkeypatch.setattr(cron_module, "_run_rematch_after_sync", AsyncMock(return_value={}))
    monkeypatch.setattr(
        cron_module, "aggregate_apify_costs_since",
        lambda s, since: {"estimated_cost_usd": 0.0},
    )
    monkeypatch.setattr(
        cron_module, "aggregate_anthropic_costs_since",
        lambda s, since: {"estimated_cost_usd": 0.0},
    )
    monkeypatch.setattr(
        cron_module, "aggregate_openai_costs_since",
        lambda s, since: {"estimated_cost_usd": 0.0},
    )
    monkeypatch.setattr("app.api.cron.engine", db)
    from app.config import settings as _settings
    monkeypatch.setattr(
        _settings, "cron_vision_backlog_max_assets_per_run", 0, raising=False
    )
    monkeypatch.setenv("ENABLE_TITLE_SYNC_IN_CRON", "false")

    with Session(db) as session:
        run = CronRun()
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id: UUID = run.id

    asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    assert call_order == ["er_forecasts", "cutter_weekly", "designer_weekly"]

    with Session(db) as session:
        run = session.get(CronRun, run_id)
        assert run.status == "completed"
        assert "designer_weekly" in run.summary_json
        assert run.summary_json["designer_weekly"]["generated"] == 1
