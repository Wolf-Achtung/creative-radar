"""Datenblock-Deckel des Cron-Laufs (31.08.2026).

Anlass: der Montagslauf brauchte 224 von 240 erlaubten Minuten — und die
Reihenfolge im Cron ist Daten zuerst, Produkt zuletzt. Reisst der
Gesamtdeckel, fallen genau die sechs LLM-Schlussstufen samt
Playbook-Mail weg, waehrend die Datenarbeit committet danebensteht.

Der Deckel gibt Scrape, Vision, Post-Analyse, Titel-Sync und Rematch EIN
gemeinsames Zeitfenster. Diese Tests nageln fest: die Kappung wirkt, die
Gates sitzen an den richtigen Stellen — und die Produktstufen laufen
auch dann, wenn das Datenfenster komplett verbraucht ist.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import tempfile
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.api import cron as cron_module
from app.models.entities import CronRun


# --- _DatenBudget -----------------------------------------------------


def test_budget_null_schaltet_den_waechter_ab():
    budget = cron_module._DatenBudget(0)
    assert budget.aktiv is False
    assert budget.erschoepft() is False
    assert budget.rest_seconds() == float("inf")
    assert budget.kappe(1800) == 1800, "Inaktiv darf nichts kappen."


def test_erschoepftes_budget_meldet_sich_und_kappt_auf_minimum():
    budget = cron_module._DatenBudget(50, start=time.monotonic() - 100)
    assert budget.erschoepft() is True
    assert budget.kappe(1800) == 1.0, (
        "Nach Ablauf bleibt nur das 1s-Minimum — die Stage soll gar "
        "nicht mehr ernsthaft starten."
    )
    skip = budget.skip_summary()
    assert skip["skipped"] is True
    assert skip["reason"] == "data_budget_exceeded"
    assert skip["budget_seconds"] == 50


def test_kappe_beschraenkt_auf_die_restzeit():
    budget = cron_module._DatenBudget(1000, start=time.monotonic() - 900)
    gekappt = budget.kappe(1800)
    assert 90 <= gekappt <= 100, "1800s Stage-Wunsch, aber nur ~100s Rest."
    assert budget.kappe(30) == 30, "Kleinere Stage-Budgets bleiben unangetastet."


def test_env_parser_default_und_abschaltung(monkeypatch):
    monkeypatch.delenv("CRON_DATA_STAGES_TIMEOUT_SECONDS", raising=False)
    assert cron_module._cron_data_stages_timeout_seconds() == 9000
    monkeypatch.setenv("CRON_DATA_STAGES_TIMEOUT_SECONDS", "0")
    assert cron_module._cron_data_stages_timeout_seconds() == 0
    monkeypatch.setenv("CRON_DATA_STAGES_TIMEOUT_SECONDS", "quatsch")
    assert cron_module._cron_data_stages_timeout_seconds() == 9000


# --- Kappung in den Stage-Funktionen ----------------------------------


def test_title_sync_respektiert_die_restzeit(monkeypatch):
    """max_seconds kappt das 1800s-Stage-Budget: ein haengender Sync
    bricht nach der Restzeit ab statt das ganze Fenster zu fressen."""

    async def _haengt(session):
        await asyncio.sleep(30)

    monkeypatch.setattr(cron_module, "sync_titles_from_tmdb", _haengt)
    monkeypatch.setattr(
        cron_module, "_mark_stuck_title_sync_run_error", lambda *a, **k: None
    )

    started = time.monotonic()
    ergebnis = asyncio.run(
        cron_module._run_title_sync_after_scrape(None, max_seconds=1.0)
    )
    elapsed = time.monotonic() - started

    assert ergebnis["timed_out"] is True
    assert elapsed < 5, f"Kappung wirkungslos — Stage lief {elapsed:.1f}s."


def test_rematch_reicht_die_gekappte_restzeit_als_soft_budget_weiter(monkeypatch):
    erhalten: list[float] = []

    class _Summary:
        def to_dict(self):
            return {"checked": 0}

    def _stub(session, *, time_budget_seconds=None):
        erhalten.append(time_budget_seconds)
        return _Summary()

    monkeypatch.setattr(cron_module, "rematch_unassigned_assets", _stub)

    asyncio.run(cron_module._run_rematch_after_sync(None, max_seconds=300.0))

    assert erhalten == [180.0], (
        "300s Rest minus 120s Marge = 180s weiches Budget — die Kappung "
        "muss VOR der Margen-Rechnung greifen."
    )


# --- Verdrahtung im Hintergrund-Lauf ----------------------------------


def test_verdrahtung_gates_sitzen_und_produktblock_bleibt_frei():
    """AST-freier Quelltext-Waechter (Muster Cron-Stage-Anker): jede
    Daten-Stage prueft das Budget, der Produktblock kennt es nicht."""
    quelle = inspect.getsource(cron_module._run_cron_sync_background_impl)

    assert "daten_budget = _DatenBudget(_cron_data_stages_timeout_seconds())" in quelle
    assert quelle.index("daten_budget = _DatenBudget") < quelle.index(
        "_execute_platform_sync"
    ), "Der Startpunkt muss VOR dem Scrape liegen — dessen Zeit zaehlt mit."

    assert quelle.count("daten_budget.erschoepft()") >= 5, (
        "Vision, Vision-Backlog, Post-Analyse, Titel-Sync und Rematch "
        "brauchen je ein Gate."
    )
    assert "max_seconds=daten_budget.rest_seconds()" in quelle

    # Evidence-Backfill bleibt AUSSERHALB des Fensters: die CDN-Links
    # der frischen Posts verfallen in 24-48h — was er heute nicht
    # sichert, ist weg.
    assert (
        'summary["evidence_backfill"] = await _run_evidence_backfill_stage(session)'
        in quelle
    )

    # Der Produktblock (Briefs bis Playbook) darf das Datenbudget nie
    # sehen — er ist der Grund fuer den Deckel.
    produktblock = quelle[quelle.index('summary["candidate_llm_assist"]'):]
    assert "daten_budget" not in produktblock


# --- Ende-zu-Ende: erschoepftes Fenster, Produkt laeuft trotzdem -------


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_datenblock_", suffix=".db")
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


class _FakeBudget:
    hard_cap_exceeded = False
    enforced = True
    soft_warn_exceeded = False

    def to_dict(self):
        return {}


def test_produktstufen_laufen_trotz_erschoepftem_datenfenster(monkeypatch, db):
    """Die eigentliche Zusicherung: frisst der Datenblock sein Fenster
    auf, werden die Daten-Stages uebersprungen — und JEDE Produktstufe
    laeuft trotzdem. Genau der Fall, der beim 224-Minuten-Lauf vom
    31.08.2026 die Montags-Mail gekostet haette."""
    import app.services.pattern_playbook as playbook_module

    monkeypatch.setattr(cron_module, "engine", db)
    monkeypatch.setenv("CRON_DATA_STAGES_TIMEOUT_SECONDS", "1")

    gelaufen: list[str] = []

    async def _scrape(session, run_index):
        await asyncio.sleep(1.2)  # frisst das ganze 1s-Fenster
        gelaufen.append("scrape")
        return {}, [uuid4()]

    def _stage(name, ergebnis=None):
        def _f(*a, **k):
            gelaufen.append(name)
            return ergebnis if ergebnis is not None else {}
        return _f

    def _async_stage_factory(name, ergebnis=None):
        async def _f(*a, **k):
            gelaufen.append(name)
            return ergebnis if ergebnis is not None else {}
        return _f

    monkeypatch.setattr(cron_module, "_execute_platform_sync", _scrape)
    monkeypatch.setattr(cron_module, "compute_apify_monthly_spend", lambda s: _FakeBudget())
    monkeypatch.setattr(cron_module, "compute_anthropic_monthly_spend", lambda s: _FakeBudget())
    monkeypatch.setattr(cron_module, "compute_openai_monthly_spend", lambda s: _FakeBudget())
    monkeypatch.setattr(cron_module, "aggregate_apify_costs_since", lambda *a: {})
    monkeypatch.setattr(cron_module, "aggregate_anthropic_costs_since", lambda *a: {})
    monkeypatch.setattr(cron_module, "aggregate_openai_costs_since", lambda *a: {})

    # Daten-Stages: duerfen NICHT laufen.
    monkeypatch.setattr(cron_module, "_run_vision_after_sync", _stage("vision"))
    monkeypatch.setattr(cron_module, "_run_vision_backlog", _stage("vision_backlog"))
    monkeypatch.setattr(cron_module, "_run_post_analysis_backlog", _stage("post_analysis"))
    monkeypatch.setattr(cron_module, "_run_title_sync_after_scrape", _async_stage_factory("title_sync"))
    monkeypatch.setattr(cron_module, "_run_rematch_after_sync", _async_stage_factory("rematch"))

    # Ausserhalb des Fensters: laufen immer.
    monkeypatch.setattr(cron_module, "_run_evidence_backfill_stage", _async_stage_factory("evidence"))
    class _Autopilot:
        def to_dict(self):
            return {}
    monkeypatch.setattr(cron_module, "run_candidate_autopilot", _stage("autopilot", _Autopilot()))
    monkeypatch.setattr(cron_module, "_run_candidate_llm_assist_stage", _async_stage_factory("ki"))
    monkeypatch.setattr(cron_module, "_run_katalog_nachladen_stage", _async_stage_factory("nachladen"))
    monkeypatch.setattr(cron_module, "_run_recommendation_snapshot_stage", _stage("snapshot"))

    # Produktblock: MUSS laufen.
    monkeypatch.setattr(cron_module, "_run_brief_generation_after_sync", _stage("briefs"))
    monkeypatch.setattr(cron_module, "_run_segment_roundups_after_briefs", _stage("roundups"))
    monkeypatch.setattr(cron_module, "_run_er_forecast_warmup_after_roundups", _stage("forecasts"))
    monkeypatch.setattr(cron_module, "_run_cutter_weekly_after_forecasts", _stage("cutter"))
    monkeypatch.setattr(cron_module, "_run_designer_weekly_after_cutter_weekly", _stage("designer"))
    monkeypatch.setattr(cron_module, "_run_pattern_briefing_after_designer_weekly", _stage("pattern"))
    monkeypatch.setattr(playbook_module, "send_pattern_playbook", _async_stage_factory("playbook"))

    with Session(db) as session:
        run = CronRun()
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    asyncio.run(cron_module._run_cron_sync_background_impl(run_id, run_index=0))

    with Session(db) as session:
        run = session.get(CronRun, run_id)
        assert run.status == "completed"
        summary = run.summary_json

    for daten_stage in ("vision", "vision_backlog", "post_analysis", "title_sync", "rematch"):
        assert daten_stage not in gelaufen, f"{daten_stage} lief trotz erschoepftem Fenster."
        assert summary[daten_stage]["reason"] == "data_budget_exceeded"
    for produkt_stage in ("evidence", "autopilot", "ki", "nachladen", "snapshot",
                          "briefs", "roundups", "forecasts", "cutter", "designer",
                          "pattern", "playbook"):
        assert produkt_stage in gelaufen, f"{produkt_stage} fehlt — der Deckel wuergt das Produkt ab."
    assert summary["data_stages_budget_seconds"] == 1
