"""Cron-Integration der Segment-Roundups — Master-Plan-Schritt-4.

Verifiziert ``_run_segment_roundups_after_briefs`` plus die Reihenfolge
und Cap-Mechanik in ``_run_cron_sync_background``:

1. Feature-Flag off → kompletter Block skippt mit ``reason="feature_flag_off"``.
2. Parser-Empty (``cron_roundup_segments=""``) → skip mit
   ``reason="no_parseable_segments"``.
3. Zweiter F0.7-Cap-Check: wenn nach den Pair-Briefs der Anthropic-Cap
   ausgeschoepft ist, skippt der Roundup-Block — Pair-Briefs sind dann
   schon persistiert.
4. Reihenfolge im Cron-Background: Pair-Briefs werden VOR Roundups
   aufgerufen, ueberprueft via call-order auf zwei Spies.
5. Cache-Hit: existing-Row in ``segment_roundup`` skippt LLM-Call
   (Last-Write-Wins-Schutz fuer den naechsten Cron-Lauf der gleichen KW).
6. Per-Segment-Isolation: ein scheiternder Segment-Lauf killt nicht die
   anderen.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.api import cron as cron_module
from app.config import settings
from app.models.entities import ChannelSegment, CronRun, SegmentRoundup
from app.services import segment_roundup as roundup_module


def _engine_for_path(path: str):
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_cron_roundups_", suffix=".db")
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


def _make_budget_status(*, exceeded: bool, enforced: bool = True) -> SimpleNamespace:
    """Mock fuer ``BudgetStatus``-Returns von ``compute_anthropic_monthly_spend``.
    """
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


# ---------------------------------------------------------------------------
# Test 1 — Feature-Flag off → Block skippt sauber
# ---------------------------------------------------------------------------

def test_roundups_skipped_when_feature_flag_off(db, monkeypatch):
    monkeypatch.delenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", raising=False)

    with Session(db) as session:
        result = cron_module._run_segment_roundups_after_briefs(session)

    assert result["enabled"] is False
    assert result["skipped"] is True
    assert result["reason"] == "feature_flag_off"
    assert result["generated"] == 0


# ---------------------------------------------------------------------------
# Test 2 — Parser-empty → block skippt mit no_parseable_segments
# ---------------------------------------------------------------------------

def test_roundups_skipped_when_segments_csv_is_empty(db, monkeypatch):
    monkeypatch.setenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", "true")
    monkeypatch.setattr(settings, "cron_roundup_segments", "", raising=False)

    with Session(db) as session:
        result = cron_module._run_segment_roundups_after_briefs(session)

    assert result["enabled"] is True
    assert result["skipped"] is True
    assert result["reason"] == "no_parseable_segments"
    assert result["generated"] == 0


# ---------------------------------------------------------------------------
# Test 3 — Zweiter Cap-Check: hard-cap-exceeded → block skip
# ---------------------------------------------------------------------------

def test_roundups_skipped_when_anthropic_cap_exceeded_mid_run(db, monkeypatch):
    """Pflicht-Anforderung Ping 1: wenn nach den Pair-Briefs der
    Anthropic-Cap erreicht ist, skippt der Roundup-Block. Pair-Briefs
    sind dann sicher schon durch."""
    monkeypatch.setenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", "true")
    monkeypatch.setattr(
        settings, "cron_roundup_segments", "us_major", raising=False,
    )
    monkeypatch.setattr(
        cron_module, "compute_anthropic_monthly_spend",
        lambda s: _make_budget_status(exceeded=True, enforced=True),
    )

    # Spy: roundup-Generator darf NICHT aufgerufen werden
    gen_mock = MagicMock()
    monkeypatch.setattr(cron_module, "generate_and_persist_roundup", gen_mock)

    with Session(db) as session:
        result = cron_module._run_segment_roundups_after_briefs(session)

    assert result["skipped"] is True
    assert result["reason"] == "anthropic_budget_exceeded"
    assert "anthropic_budget" in result
    assert result["anthropic_budget"]["hard_cap_exceeded"] is True
    gen_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4 — Kill-Switch off: Cap exceeded aber enforced=False → laeuft trotzdem
# ---------------------------------------------------------------------------

def test_roundups_run_when_cap_exceeded_but_enforced_false(db, monkeypatch):
    monkeypatch.setenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", "true")
    monkeypatch.setattr(
        settings, "cron_roundup_segments", "us_major", raising=False,
    )
    monkeypatch.setattr(
        cron_module, "compute_anthropic_monthly_spend",
        lambda s: _make_budget_status(exceeded=True, enforced=False),
    )

    fake_report = SimpleNamespace(
        llm_output=SimpleNamespace(),
        iso_year=2026,
        iso_week=21,
        cost_usd_estimate=0.26,
        aggregation=SimpleNamespace(
            channels_evaluated=33, channels_with_posts=20, total_posts=80,
        ),
    )
    monkeypatch.setattr(
        cron_module, "generate_and_persist_roundup",
        MagicMock(return_value=fake_report),
    )

    with Session(db) as session:
        result = cron_module._run_segment_roundups_after_briefs(session)

    assert result["skipped"] is False
    assert result["generated"] == 1


# ---------------------------------------------------------------------------
# Test 4b — Roundup mit llm_output=None (Parse-/Schema-Fail, persist-skip)
# zaehlt als failed, NICHT als generated. Spiegelt den Brief-Counter-Fix
# (PR #210); generate_and_persist_roundup wirft dabei KEINE Exception.
# ---------------------------------------------------------------------------

def test_roundups_count_empty_llm_output_as_failed(db, monkeypatch):
    monkeypatch.setenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", "true")
    monkeypatch.setattr(
        settings, "cron_roundup_segments", "us_major", raising=False,
    )
    monkeypatch.setattr(
        cron_module, "compute_anthropic_monthly_spend",
        lambda s: _make_budget_status(exceeded=False),
    )

    # Parse-/Schema-Fail-Pfad: report kommt zurueck, aber llm_output=None
    # (persist wurde uebersprungen). Aggregation/Cost sind trotzdem gesetzt.
    failed_report = SimpleNamespace(
        llm_output=None,
        iso_year=2026,
        iso_week=21,
        cost_usd_estimate=0.26,
        aggregation=SimpleNamespace(
            channels_evaluated=33, channels_with_posts=20, total_posts=80,
        ),
    )
    monkeypatch.setattr(
        cron_module, "generate_and_persist_roundup",
        MagicMock(return_value=failed_report),
    )

    with Session(db) as session:
        result = cron_module._run_segment_roundups_after_briefs(session)

    assert result["generated"] == 0
    assert result["failed"] == 1
    assert len(result["errors"]) == 1
    err = result["errors"][0]
    assert err["segment"] == "us_major"
    assert err["error_class"] == "no_llm_output"
    # Cost + results-Eintrag bleiben erhalten (nur der Zaehler aendert sich).
    assert result["cost_usd_cents"] == 26
    assert result["results"][0]["status"] == "persist_skipped"


# ---------------------------------------------------------------------------
# Test 5 — Cache-Hit: existing-Row skippt LLM-Call
# ---------------------------------------------------------------------------

def test_roundups_cache_hit_skips_llm_call(db, monkeypatch):
    monkeypatch.setenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", "true")
    monkeypatch.setattr(
        settings, "cron_roundup_segments", "us_major", raising=False,
    )
    monkeypatch.setattr(
        cron_module, "compute_anthropic_monthly_spend",
        lambda s: _make_budget_status(exceeded=False),
    )

    # Seed existing row fuer die anstehende ISO-Woche (today - 1 day)
    brief_now = datetime.now(timezone.utc) - timedelta(days=1)
    iso_cal = brief_now.isocalendar()
    with Session(db) as session:
        row = SegmentRoundup(
            segment=ChannelSegment.US_MAJOR,
            iso_year=iso_cal.year,
            iso_week=iso_cal.week,
            window_days=14,
            channels_aggregation={},
            llm_output={"headline": "existing"},
            generated_at=datetime.now(timezone.utc),
            model="claude-opus-4-7",
        )
        session.add(row)
        session.commit()

    gen_mock = MagicMock()
    monkeypatch.setattr(cron_module, "generate_and_persist_roundup", gen_mock)

    with Session(db) as session:
        result = cron_module._run_segment_roundups_after_briefs(session)

    assert result["skipped"] is False
    assert result["generated"] == 0
    assert result["skipped_cache_hit"] == 1
    gen_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Test 6 — Per-Segment-Isolation: ein scheiterndes Segment killt nicht
# die anderen
# ---------------------------------------------------------------------------

def test_roundups_per_segment_isolation(db, monkeypatch):
    monkeypatch.setenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", "true")
    monkeypatch.setattr(
        settings, "cron_roundup_segments", "us_major,de_verleih", raising=False,
    )
    monkeypatch.setattr(
        cron_module, "compute_anthropic_monthly_spend",
        lambda s: _make_budget_status(exceeded=False),
    )

    fake_report = SimpleNamespace(
        llm_output=SimpleNamespace(),
        iso_year=2026, iso_week=21,
        cost_usd_estimate=0.26,
        aggregation=SimpleNamespace(
            channels_evaluated=33, channels_with_posts=20, total_posts=80,
        ),
    )

    def fake_generate(session, segment, **kwargs):
        if segment == ChannelSegment.US_MAJOR:
            raise RuntimeError("simulated us_major failure")
        return fake_report

    monkeypatch.setattr(cron_module, "generate_and_persist_roundup", fake_generate)

    with Session(db) as session:
        result = cron_module._run_segment_roundups_after_briefs(session)

    # de_verleih hat trotz us_major-Fehler durchgelaufen
    assert result["generated"] == 1
    assert result["failed"] == 1
    failed_segments = [e["segment"] for e in result["errors"]]
    assert "us_major" in failed_segments


# ---------------------------------------------------------------------------
# Test 7 — Reihenfolge im Cron-Background: Pair-Briefs zuerst, Roundups danach
# ---------------------------------------------------------------------------

def test_cron_background_runs_briefs_before_roundups(db, monkeypatch):
    """Konzept §6 / Wolf-Festlegung 25.05.: Pair-Briefs werden VOR
    Roundups erzeugt. Verifiziert, dass die Spies in dieser
    Reihenfolge aufgerufen werden — wichtig fuer den mid-Run-Cap-
    Schutz."""
    call_order: list[str] = []

    def fake_brief_gen(session):
        call_order.append("briefs")
        return {"enabled": True, "generated": 9, "skipped_cache_hit": 0,
                "failed": 0, "cost_usd_cents": 200, "errors": []}

    def fake_roundup_gen(session):
        call_order.append("roundups")
        return {"enabled": True, "skipped": False, "generated": 4,
                "skipped_cache_hit": 0, "failed": 0, "cost_usd_cents": 100,
                "results": [], "errors": []}

    monkeypatch.setattr(
        cron_module, "_run_brief_generation_after_sync", fake_brief_gen,
    )
    monkeypatch.setattr(
        cron_module, "_run_segment_roundups_after_briefs", fake_roundup_gen,
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
        cron_module, "_execute_platform_sync",
        AsyncMock(return_value=({}, [])),
    )
    monkeypatch.setattr(cron_module, "_run_rematch_after_sync", lambda s: {})
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

    with Session(db) as session:
        run = CronRun()
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id: UUID = run.id

    asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    assert call_order == ["briefs", "roundups"]

    # Summary contains both blocks
    with Session(db) as session:
        run = session.get(CronRun, run_id)
        assert run.status == "completed"
        assert "briefs" in run.summary_json
        assert "roundups" in run.summary_json
        assert run.summary_json["roundups"]["generated"] == 4
