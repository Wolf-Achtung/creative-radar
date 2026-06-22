"""Cadence-Sprint 2026-05-17 — Brief-Generation als neue Cron-Stage.

Verifiziert das Verhalten von ``_run_brief_generation_after_sync`` und seiner
Integration in ``_run_cron_sync_background``:

1. Happy-Path: jeder ``enabled=True``-Pair bekommt einen Brief-Gen-Call,
   ``now`` liegt einen Tag in der Vergangenheit, Counter und Cost stimmen.
2. ENV-Toggle ``ENABLE_BRIEF_GEN_IN_CRON=false`` skippt die Stage komplett.
3. Per-Pair-Try/Except: ein failender Pair killt nicht die anderen.
4. ``now - 1 day``-Anker (H4-Mitigation gegen ISO-Wochen-Off-by-One am
   Montag-Cron, der sonst die noch leere neue KW erwischt hätte).
5. Ausfall-Alert (PR #270, Variante B): ``logger.critical(
   'cron_brief_gen.silent_failure')`` feuert bei zwei echten Ausfallmustern —
   ``silent`` (Pfad tat nichts: generated+failed+cache_hit == 0) und
   ``all_failed`` (Pfad lief, aber jeder Versuch scheiterte: generated == 0,
   failed > 0). Die frühere Anthropic-Kostenschwelle (<$5) ist KEIN Trigger
   mehr — Kosten sind kein Erfolgssignal (niedrig bei legitimem Cache, hoch
   bei teuren Totalausfällen). Frühwarnsignal #2 aus dem Premortem (PR #147
   Failure-Mode #2).
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
    # Vision-Backlog-Drain aus: diese Tests seeden keine Assets und prüfen
    # nur die Brief-Gen-Stage; abgeschaltet bleibt das Verhalten
    # deterministisch unabhängig vom Default.
    from app.config import settings as _settings
    monkeypatch.setattr(_settings, "cron_vision_backlog_max_assets_per_run", 0, raising=False)
    monkeypatch.setenv("ENABLE_TITLE_SYNC_IN_CRON", "false")

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


def test_cron_brief_gen_skipped_when_disabled(db, monkeypatch, caplog):
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "false")
    brief_mock = MagicMock()
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    run_id = _seed_run(db)

    with caplog.at_level(logging.CRITICAL, logger="app.api.cron"):
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

    # ``enabled=False`` mit allen Countern auf 0 darf KEINEN silent_failure
    # auslösen — beide Variante-B-Muster (silent/all_failed) verlangen
    # ``enabled`` truthy. Schützt davor, dass ein Drop des enabled-Guards
    # einen deaktivierten Run fälschlich als ``silent`` meldet.
    silent_failure_records = [
        r for r in caplog.records
        if "cron_brief_gen.silent_failure" in r.getMessage()
    ]
    assert silent_failure_records == []


def test_cron_brief_gen_pairs_filter_only_generates_requested(db, monkeypatch):
    """Sprint 16.06.2026 — pair-gescopter Lauf: ``brief_pairs=['disney']`` →
    NUR disney wird in der Brief-Stage angefasst; alle anderen Pairs bleiben
    unberührt."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    brief_mock = MagicMock(
        return_value=SimpleNamespace(cost_usd_estimate=1.0, llm_output=object())
    )
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    run_id = _seed_run(db)

    asyncio.run(cron_module._run_cron_sync_background(
        run_id, run_index=0, brief_pairs=["disney"],
    ))

    called = [c.args[1] for c in brief_mock.call_args_list]
    assert called == ["disney"]
    with Session(db) as session:
        briefs = session.get(CronRun, run_id).summary_json["briefs"]
        assert briefs["generated"] == 1


def test_cron_brief_gen_pairs_filter_multiple(db, monkeypatch):
    """Mehrere Pairs: genau diese, sonst keine."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    brief_mock = MagicMock(
        return_value=SimpleNamespace(cost_usd_estimate=1.0, llm_output=object())
    )
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    run_id = _seed_run(db)

    asyncio.run(cron_module._run_cron_sync_background(
        run_id, run_index=0, brief_pairs=["disney", "lionsgate"],
    ))

    assert sorted(c.args[1] for c in brief_mock.call_args_list) == ["disney", "lionsgate"]


def test_cron_brief_gen_no_pairs_filter_generates_all(db, monkeypatch):
    """Regression/Backward-Compat: ohne ``brief_pairs`` werden alle enabled
    Pairs generiert (heutiges Verhalten)."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    brief_mock = MagicMock(
        return_value=SimpleNamespace(cost_usd_estimate=1.0, llm_output=object())
    )
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    run_id = _seed_run(db)

    asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    assert brief_mock.call_count == _enabled_pair_count()


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


def test_cron_brief_gen_surfaces_failure_diagnostic(db, monkeypatch):
    """Diagnose-Instrumentierung (2026-06-22): wenn der Report eine
    ``failure_diagnostic`` traegt, schluesselt der Cron den Sammelfehler
    ``no_llm_output`` in die konkrete Klasse auf und legt Roh-Output +
    Detail in einen dedizierten ``diagnostic``-Block. Rein additiv — der
    Fallback auf ``no_llm_output`` (kein Diagnostic) ist separat getestet."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    raw = "{" + ("x" * 5000) + "}"  # > head+tail → wird gekuerzt
    brief_mock = MagicMock(
        return_value=SimpleNamespace(
            cost_usd_estimate=0.0,
            llm_output=None,
            raw_llm_text=raw,
            failure_diagnostic={
                "kind": "truncation_error",
                "detail": "stop_reason=max_tokens output_token_count=20000 max_tokens=20000",
            },
        )
    )
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    run_id = _seed_run(db)

    asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    n_pairs = _enabled_pair_count()
    with Session(db) as session:
        run = session.get(CronRun, run_id)
        briefs = run.summary_json["briefs"]
        assert briefs["failed"] == n_pairs
        assert len(briefs["errors"]) == n_pairs
        err = briefs["errors"][0]
        # Aufgeschluesselt statt pauschal no_llm_output.
        assert err["error_class"] == "truncation_error"
        assert "output_token_count=20000" in err["error_message"]
        diag = err["diagnostic"]
        assert diag["kind"] == "truncation_error"
        assert "max_tokens=20000" in diag["detail"]
        # Roh-Output ist present und auf head+tail gekuerzt.
        assert "[TRUNCATED" in diag["raw_llm_output"]
        assert len(diag["raw_llm_output"]) < len(raw)


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


def test_cron_brief_gen_all_failed_alert_triggers(db, monkeypatch, caplog):
    """Variante-B-Muster ``all_failed``: jeder Pair scheitert → 0 generated,
    n failed. Unabhängig von den Kosten (hier $0) feuert der Critical."""
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
    assert rec.failure_mode == "all_failed"
    assert rec.anthropic_cost_usd == 0.0

    with Session(db) as session:
        run = session.get(CronRun, run_id)
        briefs = run.summary_json["briefs"]
        assert briefs["generated"] == 0
        assert briefs["failed"] == _enabled_pair_count()


def _seed_brief_rows(db, *, anchor: datetime) -> int:
    """Seed one persisted InsightReport per enabled pair for the ISO week of
    ``anchor``. Returns the number of rows seeded. Used to exercise the
    cache-hit pre-check (skip vs. force-bypass)."""
    from app.models.entities import InsightReport
    iso = anchor.isocalendar()
    n = 0
    with Session(db) as session:
        for k, v in PAIRS.items():
            if not v.get("enabled", False):
                continue
            session.add(InsightReport(
                pair_key=k,
                iso_year=iso.year,
                iso_week=iso.week,
                aggregation={},
                llm_output={},
                model="seed",
            ))
            n += 1
        session.commit()
    return n


def _setup_controlled_pairs(monkeypatch, n: int) -> list[str]:
    """Monkeypatch the cron-module ``PAIRS`` to exactly ``n`` enabled fake
    pairs and neutralise the forecast-warmup stage (which also iterates
    ``PAIRS``) so that ONLY the brief-gen counters drive the assertion.
    Returns the ordered pair keys. Used by the Variante-C quote tests to pin
    precise generated/failed/cache_hit splits independent of the real roster."""
    fake = {f"pair{i}": {"enabled": True} for i in range(n)}
    monkeypatch.setattr(cron_module, "PAIRS", fake)
    monkeypatch.setattr(
        cron_module, "generate_er_forecast",
        lambda *a, **k: {"einordnung_source": "no_einordnung"},
    )
    return list(fake.keys())


def _seed_cache_rows_for(db, pair_keys, *, anchor: datetime) -> None:
    """Seed one persisted InsightReport per given ``pair_key`` for the ISO week
    of ``anchor`` so the cron cache-hit pre-check counts them as
    ``skipped_cache_hit`` (and never reaches the brief-gen mock)."""
    from app.models.entities import InsightReport
    iso = anchor.isocalendar()
    with Session(db) as session:
        for k in pair_keys:
            session.add(InsightReport(
                pair_key=k,
                iso_year=iso.year,
                iso_week=iso.week,
                aggregation={},
                llm_output={},
                model="seed",
            ))
        session.commit()


def _run_briefs(db) -> dict:
    """Run a default (force=false, completed-week) cron and return the briefs
    summary. Helper for the Variante-C quote matrix."""
    run_id = _seed_run(db)
    asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))
    with Session(db) as session:
        return session.get(CronRun, run_id).summary_json["briefs"]


def test_cron_brief_gen_quote_below_threshold_no_alert(db, monkeypatch, caplog):
    """Variante C, Fall 0/1/8: 1 frisches Failure neben 8 legitimen Cache-Hits.
    Quote 1/9 = 0.11 < 0.5 → KEIN Record (der behobene Fehlalarm)."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    keys = _setup_controlled_pairs(monkeypatch, 9)
    brief_mock = MagicMock(side_effect=RuntimeError("one fresh failure"))
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    anchor = datetime.now(timezone.utc) - timedelta(days=1)
    _seed_cache_rows_for(db, keys[:8], anchor=anchor)  # 8 cached, 1 fresh

    with caplog.at_level(logging.CRITICAL, logger="app.api.cron"):
        briefs = _run_briefs(db)

    assert brief_mock.call_count == 1
    assert (briefs["generated"], briefs["failed"], briefs["skipped_cache_hit"]) == (0, 1, 8)
    records = [r for r in caplog.records if "cron_brief_gen.silent_failure" in r.getMessage()]
    assert records == []


def test_cron_brief_gen_quote_at_threshold_alerts_despite_cache(db, monkeypatch, caplog):
    """Variante C, Fall 0/8/1: 8 Failures, nur 1 Cache-Hit. Quote 8/9 = 0.89
    >= 0.5 → Record. Die geschlossene Maskierungs-Blindstelle: ein einzelner
    Cache-Hit unterdrückt den Massenausfall NICHT mehr."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    keys = _setup_controlled_pairs(monkeypatch, 9)
    brief_mock = MagicMock(side_effect=RuntimeError("mass failure"))
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    anchor = datetime.now(timezone.utc) - timedelta(days=1)
    _seed_cache_rows_for(db, keys[:1], anchor=anchor)  # 1 cached, 8 fresh

    with caplog.at_level(logging.CRITICAL, logger="app.api.cron"):
        briefs = _run_briefs(db)

    assert brief_mock.call_count == 8
    assert (briefs["generated"], briefs["failed"], briefs["skipped_cache_hit"]) == (0, 8, 1)
    records = [r for r in caplog.records if "cron_brief_gen.silent_failure" in r.getMessage()]
    assert len(records) == 1
    assert records[0].failure_mode == "all_failed"


def test_cron_brief_gen_quote_total_failure_n9_alerts(db, monkeypatch, caplog):
    """Variante C, Fall 0/9/0 (konkret n=9): jeder Pair frisch versucht und
    gescheitert, kein Cache. Quote 9/9 = 1.0 → Record."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    _setup_controlled_pairs(monkeypatch, 9)
    brief_mock = MagicMock(side_effect=RuntimeError("total failure"))
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)

    with caplog.at_level(logging.CRITICAL, logger="app.api.cron"):
        briefs = _run_briefs(db)

    assert brief_mock.call_count == 9
    assert (briefs["generated"], briefs["failed"], briefs["skipped_cache_hit"]) == (0, 9, 0)
    records = [r for r in caplog.records if "cron_brief_gen.silent_failure" in r.getMessage()]
    assert len(records) == 1
    assert records[0].failure_mode == "all_failed"


def test_cron_brief_gen_partial_failure_with_generated_no_alert(db, monkeypatch, caplog):
    """Variante C, Fall 5/3/0: 5 frisch erzeugt, 3 gescheitert, kein Cache.
    ``generated > 0`` → all_failed greift nicht (verlangt generated==0), kein
    Record — auch wenn die Failure-Quote über 0.5 läge wäre generated==0 die
    harte Voraussetzung. Normalbetrieb mit Teilfehlern alarmiert nicht."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    keys = _setup_controlled_pairs(monkeypatch, 8)
    failing = set(keys[:3])

    def side_effect(session, pair_key, **kwargs):
        if pair_key in failing:
            raise RuntimeError("partial fail")
        return SimpleNamespace(cost_usd_estimate=0.0, llm_output=object())

    brief_mock = MagicMock(side_effect=side_effect)
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)

    with caplog.at_level(logging.CRITICAL, logger="app.api.cron"):
        briefs = _run_briefs(db)

    assert (briefs["generated"], briefs["failed"], briefs["skipped_cache_hit"]) == (5, 3, 0)
    records = [r for r in caplog.records if "cron_brief_gen.silent_failure" in r.getMessage()]
    assert records == []


def test_cron_default_passes_no_force_and_completed_week(db, monkeypatch):
    """Regression-Guard: ohne target_week/force (= wöchentlicher GitHub-Action-
    Pfad) ruft die Brief-Stage ``generate_and_persist_report`` mit
    ``force=False, replace=False`` und ``now`` in der gerade abgeschlossenen
    KW (``utcnow - 1 day``) auf. summary.run_mode == completed/false."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    brief_mock = MagicMock(
        return_value=SimpleNamespace(cost_usd_estimate=0.0, llm_output=object())
    )
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    run_id = _seed_run(db)

    asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    n_pairs = _enabled_pair_count()
    assert brief_mock.call_count == n_pairs
    today_utc = datetime.now(timezone.utc).date()
    for call in brief_mock.call_args_list:
        assert call.kwargs["force"] is False
        assert call.kwargs["replace"] is False
        assert (today_utc - call.kwargs["now"].date()) == timedelta(days=1)

    with Session(db) as session:
        run = session.get(CronRun, run_id)
        assert run.summary_json["run_mode"] == {"target_week": "completed", "force": False}


def test_cron_force_current_week_passes_force_replace_and_current_week(db, monkeypatch):
    """Manueller On-Demand-Lauf (Admin-Button): target_week='current' +
    force=True → ``generate_and_persist_report(force=True, replace=True)`` und
    ``now`` in der LAUFENDEN KW (nicht -1 day). summary.run_mode == current/true."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    brief_mock = MagicMock(
        return_value=SimpleNamespace(cost_usd_estimate=0.0, llm_output=object())
    )
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    run_id = _seed_run(db)

    before = datetime.now(timezone.utc)
    asyncio.run(cron_module._run_cron_sync_background(
        run_id, run_index=0, target_week="current", force=True,
    ))
    after = datetime.now(timezone.utc)

    n_pairs = _enabled_pair_count()
    assert brief_mock.call_count == n_pairs
    for call in brief_mock.call_args_list:
        assert call.kwargs["force"] is True
        assert call.kwargs["replace"] is True
        passed_now = call.kwargs["now"]
        # Laufende KW: now liegt zwischen before und after (KEIN -1 day).
        assert before - timedelta(seconds=2) <= passed_now <= after + timedelta(seconds=2)

    with Session(db) as session:
        run = session.get(CronRun, run_id)
        assert run.summary_json["run_mode"] == {"target_week": "current", "force": True}


def test_cron_force_current_week_bypasses_cache_precheck(db, monkeypatch):
    """Force-Pfad überspringt den Cache-Hit-Pre-Check: obwohl für jede aktive
    Pair bereits ein Brief der laufenden KW existiert, wird jeder Pair neu
    generiert (kein skipped_cache_hit)."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    brief_mock = MagicMock(
        return_value=SimpleNamespace(cost_usd_estimate=0.0, llm_output=object())
    )
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    seeded = _seed_brief_rows(db, anchor=datetime.now(timezone.utc))
    run_id = _seed_run(db)

    asyncio.run(cron_module._run_cron_sync_background(
        run_id, run_index=0, target_week="current", force=True,
    ))

    assert seeded == _enabled_pair_count()
    # Pre-Check übersprungen → trotz vorhandener Zeilen alle neu generiert.
    assert brief_mock.call_count == seeded
    with Session(db) as session:
        run = session.get(CronRun, run_id)
        briefs = run.summary_json["briefs"]
        assert briefs["skipped_cache_hit"] == 0
        assert briefs["generated"] == seeded


def test_cron_default_honors_cache_precheck(db, monkeypatch):
    """Gegenprobe: im Default-Pfad (kein force) führt ein bereits vorhandener
    Brief der abgeschlossenen KW zu skipped_cache_hit, kein LLM-Call."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    brief_mock = MagicMock(
        return_value=SimpleNamespace(cost_usd_estimate=0.0, llm_output=object())
    )
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    # Abgeschlossene KW = utcnow - 1 day (gleicher Anker wie die Stage).
    seeded = _seed_brief_rows(db, anchor=datetime.now(timezone.utc) - timedelta(days=1))
    run_id = _seed_run(db)

    asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    assert brief_mock.call_count == 0
    with Session(db) as session:
        run = session.get(CronRun, run_id)
        briefs = run.summary_json["briefs"]
        assert briefs["skipped_cache_hit"] == seeded
        assert briefs["generated"] == 0


def test_cron_brief_gen_all_failed_alert_fires_even_with_high_cost(db, monkeypatch, caplog):
    """Die Blindstelle, die Variante B schliesst: jeder Pair ruft das LLM,
    scheitert aber nach dem Call → 0 generated, n failed UND hohe Kosten
    ($12.34). Unter der alten ``cost < $5``-Logik wäre das STUMM geblieben
    (teure Kosten ⇒ kein Alarm). Jetzt feuert ``all_failed`` unabhängig von
    den Kosten; ``anthropic_cost_usd`` bleibt nur als Diagnose-Info im Payload."""
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
    assert len(silent_failure_records) == 1
    rec = silent_failure_records[0]
    assert rec.briefs_generated == 0
    assert rec.briefs_failed == _enabled_pair_count()
    assert rec.failure_mode == "all_failed"
    # Kosten sind kein Trigger mehr, aber als Diagnose-Info erhalten.
    assert rec.anthropic_cost_usd == 12.34


def test_cron_brief_gen_no_alert_on_full_cache_hit(db, monkeypatch, caplog):
    """Fall 1 (Cache): ein force=false-Re-Run auf eine abgeschlossene KW
    cached jeden Pair (generated=0, failed=0, skipped_cache_hit=n, $0 Cost) —
    das ist KEIN Ausfall. Weder ``silent`` (cache_hit>0) noch ``all_failed``
    (failed==0) greift, also feuert kein Critical."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    brief_mock = MagicMock(
        return_value=SimpleNamespace(cost_usd_estimate=0.0, llm_output=object())
    )
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    # Abgeschlossene KW = utcnow - 1 day (gleicher Anker wie die Stage).
    seeded = _seed_brief_rows(db, anchor=datetime.now(timezone.utc) - timedelta(days=1))
    run_id = _seed_run(db)

    with caplog.at_level(logging.CRITICAL, logger="app.api.cron"):
        asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    # Pre-Check griff für jeden Pair → 0 generiert, alle als Cache-Hit gezählt.
    assert brief_mock.call_count == 0
    with Session(db) as session:
        run = session.get(CronRun, run_id)
        briefs = run.summary_json["briefs"]
        assert briefs["generated"] == 0
        assert briefs["skipped_cache_hit"] == seeded

    silent_failure_records = [
        r for r in caplog.records
        if "cron_brief_gen.silent_failure" in r.getMessage()
    ]
    assert silent_failure_records == []


def test_cron_brief_gen_silent_alert_when_nothing_attempted(db, monkeypatch, caplog):
    """Fall 3 (stiller Block): Brief-Gen aktiviert, aber der Pfad tat nichts —
    generated+failed+cache_hit == 0. Simuliert per leerem ``PAIRS`` (Code-Pfad-
    Regression / Mock-Leak, der die Pair-Schleife auf null Iterationen
    reduziert). ``silent`` greift → Critical mit failure_mode='silent'."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    brief_mock = MagicMock(
        return_value=SimpleNamespace(cost_usd_estimate=0.0, llm_output=object())
    )
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    # Pair-Set verdampft → enabled_pairs == [] → 0 generiert/failed/cached,
    # aber enabled-Toggle steht auf true: genau das stille Ausfallmuster.
    monkeypatch.setattr(cron_module, "PAIRS", {})
    run_id = _seed_run(db)

    with caplog.at_level(logging.CRITICAL, logger="app.api.cron"):
        asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    assert brief_mock.call_count == 0
    silent_failure_records = [
        r for r in caplog.records
        if "cron_brief_gen.silent_failure" in r.getMessage()
    ]
    assert len(silent_failure_records) == 1
    rec = silent_failure_records[0]
    assert rec.briefs_enabled is True
    assert rec.briefs_generated == 0
    assert rec.briefs_failed == 0
    assert rec.briefs_skipped_cache_hit == 0
    assert rec.failure_mode == "silent"


def test_cron_brief_gen_no_alert_on_normal_run(db, monkeypatch, caplog):
    """Fall 4 (Normalbetrieb): generated > 0 → kein Ausfall, kein Critical.
    Weder ``silent`` (generated>0) noch ``all_failed`` (generated>0) greift."""
    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    brief_mock = MagicMock(
        return_value=SimpleNamespace(cost_usd_estimate=1.50, llm_output=object())
    )
    _patch_cron_neighbors(monkeypatch, db, brief_gen_mock=brief_mock)
    run_id = _seed_run(db)

    with caplog.at_level(logging.CRITICAL, logger="app.api.cron"):
        asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    assert brief_mock.call_count == _enabled_pair_count()
    with Session(db) as session:
        run = session.get(CronRun, run_id)
        briefs = run.summary_json["briefs"]
        assert briefs["generated"] == _enabled_pair_count()

    silent_failure_records = [
        r for r in caplog.records
        if "cron_brief_gen.silent_failure" in r.getMessage()
    ]
    assert silent_failure_records == []
