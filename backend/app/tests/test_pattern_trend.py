"""Wochen-Trend im Muster-Bericht (Aufwertung C, 20.08.2026).

``GET /api/insights/patterns`` traegt je belastbarer Zelle einen
Vorwochen-Vergleich: dieselbe deterministische Rechnung mit um
``TREND_WINDOW_SHIFT_DAYS`` verschobenem Fenster — keine persistierte
Zeitreihe, keine Migration, keine Cron-Abhaengigkeit. Verglichen wird
das VERDIKT (z-Test), nicht die rohe Quote: die Quote wackelt an den
Fensterraendern von selbst.

``apply_weekly_trend`` ist eine reine Funktion zweier Berichte — die
Faelle stehen hier als Pure-Function-Tests; ein Integrationstest
belegt, dass der Endpoint die Verschiebung wirklich anwendet.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import Channel, Market, Post
from app.services.trailer_patterns import (
    PatternCell,
    TrailerPatternReport,
    apply_weekly_trend,
)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _zelle(value: str, verdict: str = "over", rate: float = 0.2) -> PatternCell:
    return PatternCell(
        value=value,
        sample_size=10,
        channel_count=4,
        median_lift=1.0,
        median_activation=0.02,
        median_views=1000,
        verdict="neutral",
        breakout_rate=rate,
        expected_breakout_rate=0.1,
        breakout_z=2.5 if verdict != "insufficient" else None,
        breakout_verdict=verdict,
    )


def _report(dimensions: dict) -> TrailerPatternReport:
    return TrailerPatternReport(
        window_days=90,
        window_start=NOW - timedelta(days=90),
        window_end=NOW,
        market=None,
        posts_in_window=100,
        posts_with_baseline=90,
        channels_covered=10,
        analysis_coverage=0.5,
        dimensions=dimensions,
    )


def test_gleiches_verdikt_ist_stabil_mit_vorwochen_zahlen():
    daten = apply_weekly_trend(
        _report({"genre": [_zelle("Romance", "over", 0.21)]}),
        _report({"genre": [_zelle("Romance", "over", 0.18)]}),
    )
    zelle = daten["dimensions"]["genre"][0]
    assert zelle["trend"] == "stabil"
    assert zelle["vorwoche"] == {
        "breakout_rate": 0.18,
        "breakout_verdict": "over",
    }


def test_verdikt_wechsel_wird_ausgewiesen():
    daten = apply_weekly_trend(
        _report({"genre": [_zelle("Romance", "over")]}),
        _report({"genre": [_zelle("Romance", "neutral")]}),
    )
    zelle = daten["dimensions"]["genre"][0]
    assert zelle["trend"] == "gewechselt"
    assert zelle["vorwoche"]["breakout_verdict"] == "neutral"


def test_fehlende_vorwoche_heisst_neu():
    daten = apply_weekly_trend(
        _report({"genre": [_zelle("Romance")]}),
        _report({}),
    )
    zelle = daten["dimensions"]["genre"][0]
    assert zelle["trend"] == "neu"
    assert zelle["vorwoche"] is None


def test_duenne_vorwoche_heisst_ebenfalls_neu():
    """Eine Zelle, die letzte Woche unter der Stichproben-Schwelle lag,
    hatte KEIN Verdikt — 'gewechselt' waere eine Aussage ueber einen
    Vergleichswert, den es nie gab."""
    daten = apply_weekly_trend(
        _report({"genre": [_zelle("Romance", "over")]}),
        _report({"genre": [_zelle("Romance", "insufficient")]}),
    )
    zelle = daten["dimensions"]["genre"][0]
    assert zelle["trend"] == "neu"
    assert zelle["vorwoche"] is None


def test_duenne_aktuelle_zelle_hat_keinen_trend():
    daten = apply_weekly_trend(
        _report({"genre": [_zelle("Romance", "insufficient")]}),
        _report({"genre": [_zelle("Romance", "over")]}),
    )
    zelle = daten["dimensions"]["genre"][0]
    assert zelle["trend"] is None
    assert zelle["vorwoche"] is None


def test_gleicher_wert_in_anderer_dimension_zaehlt_nicht_als_vorwoche():
    """Der Vergleichsschluessel ist (Dimension, Wert) — 'unknown' gibt
    es z.B. in mehreren Dimensionen, und ein music_kind-'unknown' ist
    keine Vorwoche fuer ein tone-'unknown'."""
    daten = apply_weekly_trend(
        _report({"tone": [_zelle("unknown", "over")]}),
        _report({"music_kind": [_zelle("unknown", "neutral")]}),
    )
    zelle = daten["dimensions"]["tone"][0]
    assert zelle["trend"] == "neu"


# ---------------------------------------------------------------------
# Integration: der Endpoint verschiebt das Fenster wirklich
# ---------------------------------------------------------------------


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def client(engine, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "user_auth_enabled", False, raising=False)

    def _override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_endpoint_markiert_frische_zellen_als_neu(client, engine, monkeypatch):
    """Alle Posts der Zelle liegen 2 Tage zurueck. Das aktuelle Fenster
    sieht sie, das um 7 Tage verschobene Vorwochen-Fenster endet vor
    ihnen — die Zelle muss als 'neu' kommen. Wuerde der Endpoint beide
    Rechnungen mit demselben Anker fahren, waere sie 'stabil'."""
    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")
    with Session(engine) as session:
        # Drei Kanaele mit je vier Posts: die Zelle muss die
        # Mindest-Kanalzahl (3) und die Mindest-Stichprobe (5) reissen,
        # sonst ist sie insufficient und traegt per Definition keinen
        # Trend. Der 1000er-Post je Kanal ist ein Breakout (Lift ~5.7)
        # — ohne einen einzigen Breakout im Korpus gaebe es keine
        # Basisquote, der z-Test fiele auf insufficient zurueck.
        likes_muster = [100, 150, 200, 1000]
        for _ in range(3):
            kanal = Channel(
                name=f"ch-{uuid4().hex[:6]}",
                platform="tiktok",
                url=f"https://x.test/{uuid4()}",
                market=Market.US,
            )
            session.add(kanal)
            session.commit()
            session.refresh(kanal)
            for likes in likes_muster:
                session.add(
                    Post(
                        channel_id=kanal.id,
                        platform="tiktok",
                        post_url=f"https://x.test/p/{uuid4()}",
                        caption="x",
                        detected_at=datetime.now(timezone.utc) - timedelta(days=2),
                        visible_views=1000,
                        visible_likes=likes,
                        visible_comments=0,
                        visible_bookmarks=0,
                        raw_payload={},
                        analysis={"tone": "emotional", "confidence": 0.9},
                    )
                )
        session.commit()

    antwort = client.get("/api/insights/patterns?window_days=30")
    assert antwort.status_code == 200
    zellen = antwort.json()["dimensions"]["tone"]
    zelle = next(c for c in zellen if c["value"] == "emotional")
    assert zelle["trend"] == "neu"
    assert zelle["vorwoche"] is None
