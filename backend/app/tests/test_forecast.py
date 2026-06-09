"""V3 Sprint 7 — ER-Prognose: Regression-Kern (pure) + Forecast-Endpoint.

Unit-Tests decken die dependency-freie Least-Squares-Regression ab (bekannte
Punkte → erwarteter Wert + R², <3 valide Punkte → insufficient_data, flache
Linie → kein NaN, negative Extrapolation auf 0 begrenzt). HTTP-Tests decken die
Endpoint-Form, den Daten-Pfad (LLM gestubbt) und das Admin-Gate ab.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.services.forecast as forecast_module
from app.config import settings
from app.database import get_session
from app.main import app
from app.services.forecast import _forecast_one_market, _linear_regression
from app.services.market_timeline import iso_week_monday, pair_handles
from app.models.entities import Channel, InsightReport as InsightReportRow, Market, Post
from app.services.insight_engine import PAIRS

PAIR = "warnerbros"


def _pts(ers):
    """Achsen-positionsgleiche Punkt-Liste (dicts mit er) für _forecast_one_market."""
    return [{"iso_year": 2026, "iso_week": 19 + i, "views": 0, "er": er, "posts": 0}
            for i, er in enumerate(ers)]


# ---------------- Regression-Kern (pure) ----------------

def test_linear_regression_perfect_line_r2_one():
    fit = _linear_regression([(0.0, 0.10), (1.0, 0.12), (2.0, 0.14), (3.0, 0.16)])
    assert abs(fit["slope"] - 0.02) < 1e-9
    assert abs(fit["intercept"] - 0.10) < 1e-9
    assert abs(fit["r2"] - 1.0) < 1e-9


def test_forecast_known_points_value_and_r2():
    # er steigt linear 0.10→0.16 über 4 Wochen; nächste KW (x=4) → 0.18.
    out = _forecast_one_market(_pts([0.10, 0.12, 0.14, 0.16]))
    assert out["status"] == "ok"
    assert out["n_points"] == 4
    assert abs(out["forecast_er"] - 0.18) < 1e-9
    assert abs(out["r2"] - 1.0) < 1e-9
    assert out["direction"] == "steigend"


def test_forecast_insufficient_data_under_three_valid():
    # Nur zwei valide Punkte (Rest er=None) → keine Prognose.
    out = _forecast_one_market(_pts([0.10, None, 0.14]))
    assert out["status"] == "insufficient_data"
    assert out["n_points"] == 2
    assert "forecast_er" not in out


def test_forecast_flat_line_no_nan():
    out = _forecast_one_market(_pts([0.10, 0.10, 0.10, 0.10]))
    assert out["status"] == "ok"
    assert out["direction"] == "stabil"
    assert math.isfinite(out["forecast_er"]) and abs(out["forecast_er"] - 0.10) < 1e-9
    assert math.isfinite(out["r2"]) and abs(out["r2"] - 1.0) < 1e-9  # Syy=0 → 1.0, kein NaN


def test_forecast_negative_extrapolation_clamped_to_zero():
    # Steiler Abfall → rohe Extrapolation < 0 → auf 0 begrenzt, endlich.
    out = _forecast_one_market(_pts([0.20, 0.10, 0.02]))
    assert out["status"] == "ok" and out["direction"] == "fallend"
    assert out["forecast_er"] == 0.0
    assert math.isfinite(out["forecast_er"])


def test_forecast_respects_gaps_in_x():
    # er an Achsen-Index 0,2,3 (Lücke bei 1); x nutzt die Achsen-Position.
    out = _forecast_one_market(_pts([0.10, None, 0.14, 0.16]))
    assert out["status"] == "ok" and out["n_points"] == 3
    # Punkte (0,0.10),(2,0.14),(3,0.16): least-squares, forecast bei x=4.
    fit = _linear_regression([(0.0, 0.10), (2.0, 0.14), (3.0, 0.16)])
    expected = max(0.0, fit["slope"] * 4 + fit["intercept"])
    assert abs(out["forecast_er"] - expected) < 1e-9


# ---------------- HTTP-Endpoint ----------------

@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(db, monkeypatch: pytest.MonkeyPatch):
    # Beide Auth-Schichten aus → Fokus auf Routen-Logik (wie bestehende
    # Admin-Endpoint-Tests). Das Gate selbst hat einen eigenen Test unten.
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "admin_auth_enabled", False, raising=False)
    # LLM aus → keine echten Anthropic-Calls; einordnung bleibt None.
    monkeypatch.setattr(forecast_module, "is_anthropic_configured", lambda: False)

    def _override_session():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _seed_three_weeks_de(db):
    h = sorted(set(pair_handles(PAIRS[PAIR])))
    with Session(db) as s:
        de = Channel(id=uuid4(), name=h[0], handle=h[0], url=f"https://x/{h[0]}",
                     platform="tiktok", market=Market.DE)
        s.add(de)
        for wk, (likes, views) in zip((19, 20, 21), [(100, 1000), (150, 1000), (200, 1000)]):
            s.add(InsightReportRow(pair_key=PAIR, iso_year=2026, iso_week=wk,
                                   aggregation={}, llm_output={},
                                   generated_at=datetime.now(timezone.utc), model="test"))
            pub = iso_week_monday(2026, wk) + timedelta(days=1, hours=12)
            s.add(Post(id=uuid4(), channel_id=de.id, platform="tiktok",
                       post_url=f"https://x/p/{uuid4()}", published_at=pub,
                       visible_views=views, visible_likes=likes, visible_comments=0))
        s.commit()


def test_forecast_endpoint_no_data_returns_ok_insufficient(client):
    r = client.post(f"/api/admin/insights/forecast?pair={PAIR}")
    assert r.status_code == 200
    body = r.json()
    assert body["pair_key"] == PAIR
    assert set(body["markets"].keys()) == {"DE", "US", "UK"}
    assert all(mk["status"] == "insufficient_data" for mk in body["markets"].values())
    assert body["einordnung"] is None


def test_forecast_endpoint_with_data_ok(client, db):
    _seed_three_weeks_de(db)
    r = client.post(f"/api/admin/insights/forecast?pair={PAIR}")
    assert r.status_code == 200
    body = r.json()
    de = body["markets"]["DE"]
    assert de["status"] == "ok" and de["n_points"] == 3
    # ER 0.10→0.15→0.20 steigend; Prognose KW22 ~0.25, R²=1.0.
    assert de["direction"] == "steigend"
    assert abs(de["forecast_er"] - 0.25) < 1e-6
    assert abs(de["r2"] - 1.0) < 1e-9
    assert body["next_week"] == {"iso_year": 2026, "iso_week": 22}
    assert body["einordnung"] is None  # LLM in der Fixture aus


def test_forecast_endpoint_unknown_pair_404(client):
    r = client.post("/api/admin/insights/forecast?pair=does-not-exist")
    assert r.status_code == 404


def test_forecast_endpoint_admin_gate_401_without_session(db, monkeypatch):
    """Mit aktiver Admin-Auth und ohne gültiges Session-Cookie → 401
    (Router-Gate require_admin_session). Mit deaktivierter Auth (anderer Test)
    erreichbar."""
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "admin_auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "admin_session_secret", "test-secret", raising=False)

    def _override_session():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    try:
        c = TestClient(app)
        r = c.post(f"/api/admin/insights/forecast?pair={PAIR}")
        assert r.status_code == 401
    finally:
        app.dependency_overrides.pop(get_session, None)
