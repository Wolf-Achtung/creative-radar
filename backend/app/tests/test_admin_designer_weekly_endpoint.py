"""Tests für den Trockenlauf-Lesezugriff auf das Designer-Wochenbriefing
(``GET /api/admin/designer-weekly/latest``) — mirror
``test_admin_cutter_weekly_endpoint.py`` (Diagnose-Folge 2026-07-06).

Garantien:
1. Ohne Parameter: jüngste Woche (PK-Ordnung), Row roh inkl.
   evidence-Blob — das Kalibrierungs-Produkt der Trockenlauf-Phase.
2. Mit iso_year+iso_week: exakte Row; unbekannte Woche → 404.
3. Nur einer der beiden Parameter → 422.
4. Leere Tabelle → 404.
5. Auth: Endpoint sitzt hinter der globalen Bearer-Middleware (gleiche
   Mechanik wie die übrigen Admin-Endpoints).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import DesignerWeeklyBriefing


AUTH = {"Authorization": "Bearer TESTTOKEN"}


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_admin_designer_", suffix=".db")
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


@pytest.fixture
def client(db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "TESTTOKEN", raising=False)

    def _override():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed_row(db, iso_year: int, iso_week: int, *, model: str = "claude-opus-4-8"):
    with Session(db) as session:
        session.add(
            DesignerWeeklyBriefing(
                iso_year=iso_year,
                iso_week=iso_week,
                evidence={
                    "iso_year": iso_year,
                    "iso_week": iso_week,
                    "platforms": [{"platform": "instagram", "status": "no_pattern"}],
                },
                llm_output={"bloecke": []},
                generated_at=datetime(2026, 6, 8, 6, 0, tzinfo=timezone.utc),
                model=model,
            )
        )
        session.commit()


def test_latest_returns_most_recent_week(client, db):
    _seed_row(db, 2026, 22, model="older")
    _seed_row(db, 2026, 23, model="newer")
    # Jahresgrenze: 2025/52 darf 2026/23 nicht überholen.
    _seed_row(db, 2025, 52, model="oldest")

    resp = client.get("/api/admin/designer-weekly/latest", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert (body["iso_year"], body["iso_week"]) == (2026, 23)
    assert body["model"] == "newer"
    # Row kommt roh inkl. evidence-Blob — der Kalibrier-Lesepfad.
    assert body["evidence"]["platforms"][0]["status"] == "no_pattern"
    assert body["llm_output"] == {"bloecke": []}
    assert body["raw_llm_text"] is None


def test_exact_week_lookup_and_404(client, db):
    _seed_row(db, 2026, 22)

    ok = client.get(
        "/api/admin/designer-weekly/latest",
        params={"iso_year": 2026, "iso_week": 22},
        headers=AUTH,
    )
    assert ok.status_code == 200
    assert ok.json()["iso_week"] == 22

    missing = client.get(
        "/api/admin/designer-weekly/latest",
        params={"iso_year": 2026, "iso_week": 23},
        headers=AUTH,
    )
    assert missing.status_code == 404


def test_single_param_is_422(client, db):
    _seed_row(db, 2026, 22)
    resp = client.get(
        "/api/admin/designer-weekly/latest",
        params={"iso_year": 2026},
        headers=AUTH,
    )
    assert resp.status_code == 422


def test_empty_table_is_404(client):
    resp = client.get("/api/admin/designer-weekly/latest", headers=AUTH)
    assert resp.status_code == 404
    assert "Noch kein Designer-Wochenbriefing" in resp.json()["detail"]


def test_endpoint_requires_bearer_auth(client, db):
    _seed_row(db, 2026, 22)
    resp = client.get("/api/admin/designer-weekly/latest")
    assert resp.status_code in (401, 403)
