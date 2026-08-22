"""Empfehlungs-Snapshots (22.08.2026) — was hat das System wann empfohlen?

Das Vorher/Nachher-Design der Wir-Schleife braucht eingefrorene
Empfehlungs-Zeitpunkte; bislang rechnete das System die Empfehlungen
bei jedem Abruf frisch und vergass sie wieder. Vertragspunkte:

- Persistiert werden NUR die ``over``-Zellen — dieselbe MACHEN-Auswahl
  wie Playbook und Wir-Segment, keine Zweitdefinition.
- Eine Row pro ISO-Woche; ein Re-Run derselben Woche ueberschreibt
  (Last-Write-Wins), statt Duplikate zu stapeln.
- Die Cron-Stage ist verdrahtet, hat einen Not-Aus und kippt bei
  Fehlern nicht den Lauf.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import cron as cron_module
from app.config import settings
from app.models.entities import RecommendationSnapshot
from app.services import recommendation_snapshot as rs

NOW = datetime(2026, 8, 24, 6, 30, tzinfo=timezone.utc)  # Montag, KW 35


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


def _zelle(value, verdict, *, lift=1.2, z=3.0, n=40):
    return SimpleNamespace(
        value=value, breakout_verdict=verdict,
        median_lift=lift, breakout_z=z, sample_size=n,
    )


def _stub_report(monkeypatch, dimensions):
    monkeypatch.setattr(
        rs, "compute_trailer_patterns",
        lambda *a, **k: SimpleNamespace(dimensions=dimensions),
    )


def test_persistiert_nur_die_over_zellen(session, monkeypatch):
    _stub_report(monkeypatch, {
        "genre": [
            _zelle("SciFi", "over", lift=1.31, z=4.7, n=52),
            _zelle("Crime", "under"),
            _zelle("Doku", "insufficient"),
        ],
        "cover_kinetik": [_zelle("title_card", "over", lift=1.18, z=3.1, n=33)],
    })

    ergebnis = rs.persist_recommendation_snapshot(session, now=NOW)

    assert ergebnis == {"week": "2026-W35", "zellen": 2, "ersetzt": False}
    [row] = session.exec(select(RecommendationSnapshot)).all()
    assert (row.iso_year, row.iso_week) == (2026, 35)
    werte = {(z["dimension"], z["value"]) for z in row.cells}
    assert werte == {("genre", "SciFi"), ("cover_kinetik", "title_card")}, (
        "Nur over-Zellen sind Empfehlungen — under/insufficient duerfen "
        "nicht als 'damals empfohlen' in die Historie."
    )
    scifi = next(z for z in row.cells if z["value"] == "SciFi")
    assert scifi == {
        "dimension": "genre", "value": "SciFi",
        "median_lift": 1.31, "breakout_z": 4.7, "sample_size": 52,
    }


def test_re_run_derselben_woche_ueberschreibt_statt_zu_stapeln(session, monkeypatch):
    _stub_report(monkeypatch, {"genre": [_zelle("SciFi", "over")]})
    rs.persist_recommendation_snapshot(session, now=NOW)

    _stub_report(monkeypatch, {"genre": [_zelle("Horror", "over")]})
    ergebnis = rs.persist_recommendation_snapshot(session, now=NOW)

    assert ergebnis["ersetzt"] is True
    rows = session.exec(select(RecommendationSnapshot)).all()
    assert len(rows) == 1, "Force-Re-Run derselben Woche darf keine Duplikate stapeln."
    assert rows[0].cells[0]["value"] == "Horror", "Last-Write-Wins wie bei den Briefs."


def test_verschiedene_wochen_ergeben_verschiedene_rows(session, monkeypatch):
    _stub_report(monkeypatch, {"genre": [_zelle("SciFi", "over")]})
    rs.persist_recommendation_snapshot(session, now=NOW)
    rs.persist_recommendation_snapshot(
        session, now=datetime(2026, 8, 31, 6, 30, tzinfo=timezone.utc)  # KW 36
    )

    wochen = {
        (r.iso_year, r.iso_week)
        for r in session.exec(select(RecommendationSnapshot)).all()
    }
    assert wochen == {(2026, 35), (2026, 36)}


def test_stage_not_aus_skippt_ohne_rechnung(session, monkeypatch):
    monkeypatch.setattr(settings, "recommendation_snapshot_in_cron", False, raising=False)
    aufrufe = []
    monkeypatch.setattr(
        cron_module, "persist_recommendation_snapshot",
        lambda *a, **k: aufrufe.append(1),
    )

    ergebnis = cron_module._run_recommendation_snapshot_stage(session)

    assert ergebnis == {"skipped": True, "reason": "disabled"}
    assert aufrufe == []


def test_stage_fehler_kippt_den_lauf_nicht(session, monkeypatch):
    monkeypatch.setattr(settings, "recommendation_snapshot_in_cron", True, raising=False)

    def _stub(session_):
        raise RuntimeError("db weg")

    monkeypatch.setattr(cron_module, "persist_recommendation_snapshot", _stub)

    ergebnis = cron_module._run_recommendation_snapshot_stage(session)

    assert "db weg" in ergebnis["error"]


def test_hintergrund_lauf_schreibt_den_snapshot_nach_dem_rematch():
    """Quell-Waechter (Muster candidate_llm_assist): die Stage muss im
    Hintergrund-Lauf verdrahtet sein, und zwar NACH Autopilot/KI-Pruefung
    — die Zellen sollen auf den frisch zugeordneten Daten rechnen."""
    quelle = inspect.getsource(cron_module._run_cron_sync_background_impl)
    anker = 'summary["recommendation_snapshot"] = _run_recommendation_snapshot_stage(session)'
    assert anker in quelle, "Die Snapshot-Stage ist nicht im Hintergrund-Lauf verdrahtet."
    assert quelle.index("candidate_llm_assist") < quelle.index(anker)
