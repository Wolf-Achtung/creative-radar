"""KI-Pruefung der Rest-Vorschlaege als Cron-Stage (22.08.2026).

Der Autopilot schliesst nur Exakt-Treffer; alles Uebrige blieb liegen,
bis Wolf im Admin den Button klickte. Die Stage laesst denselben
Service einmal pro Cron-Lauf mit groesserem Batch laufen — die
Montags-Queue kommt vorgeprueft an.

Vertragspunkte:
- Kill-Switch ``candidate_llm_assist_in_cron`` skippt ohne Service-Call.
- Der Batch-Deckel aus den Settings wird durchgereicht.
- Ein Fehler in der Stage kippt den Lauf nicht (error-Dict statt Raise).
- Der Hintergrund-Lauf ruft die Stage NACH dem Autopiloten auf.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import cron as cron_module
from app.config import settings


@pytest.fixture
def session():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        with Session(eng) as s:
            yield s
    finally:
        eng.dispose()


class _Summary:
    def to_dict(self):
        return {"geprueft": 5, "zugeordnet": 2}


def test_kill_switch_skippt_ohne_service_call(session, monkeypatch):
    monkeypatch.setattr(settings, "candidate_llm_assist_in_cron", False, raising=False)
    aufrufe = []
    monkeypatch.setattr(
        cron_module, "run_candidate_llm_assist",
        lambda *a, **k: aufrufe.append(k) or _Summary(),
    )

    ergebnis = asyncio.run(cron_module._run_candidate_llm_assist_stage(session))

    assert ergebnis == {"skipped": True, "reason": "disabled"}
    assert aufrufe == [], "Bei abgeschaltetem Flag darf kein LLM-Call rausgehen."


def test_stage_reicht_den_cron_batch_deckel_durch(session, monkeypatch):
    monkeypatch.setattr(settings, "candidate_llm_assist_in_cron", True, raising=False)
    monkeypatch.setattr(settings, "candidate_llm_assist_cron_max", 60, raising=False)
    aufrufe = []

    def _stub(session_, *, max_candidates):
        aufrufe.append(max_candidates)
        return _Summary()

    monkeypatch.setattr(cron_module, "run_candidate_llm_assist", _stub)

    ergebnis = asyncio.run(cron_module._run_candidate_llm_assist_stage(session))

    assert aufrufe == [60], (
        "Der Cron-Batch muss den Settings-Deckel nutzen, nicht den "
        "12er-Klick-Default — sonst braeuchte eine volle Queue fuenf "
        "Wochen statt eines Laufs."
    )
    assert ergebnis == {"geprueft": 5, "zugeordnet": 2}


def test_stage_fehler_kippt_den_lauf_nicht(session, monkeypatch):
    monkeypatch.setattr(settings, "candidate_llm_assist_in_cron", True, raising=False)

    def _stub(session_, *, max_candidates):
        raise RuntimeError("anthropic down")

    monkeypatch.setattr(cron_module, "run_candidate_llm_assist", _stub)

    ergebnis = asyncio.run(cron_module._run_candidate_llm_assist_stage(session))

    assert "anthropic down" in ergebnis["error"]


def test_hintergrund_lauf_ruft_die_stage_nach_dem_autopiloten():
    """Quell-Waechter (Muster Migrations-Namen-Waechter): die Stage muss
    im Hintergrund-Lauf verdrahtet sein, und zwar NACH dem Autopiloten —
    der kostenlose Exakt-Treffer-Pfad soll zuerst abraeumen, bevor
    Haiku-Geld fliesst."""
    quelle = inspect.getsource(cron_module._run_cron_sync_background_impl)
    anker = 'summary["candidate_llm_assist"] = await _run_candidate_llm_assist_stage(session)'
    assert anker in quelle, "Die Stage ist nicht im Hintergrund-Lauf verdrahtet."
    assert quelle.index("candidate_autopilot") < quelle.index(anker)
