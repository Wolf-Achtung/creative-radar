"""HTTP-layer tests for ``GET /api/insights/weekly`` (Sprint-2).

Sprint-2 expanded the Insight-Engine from one to seven Tier-A DE+US TT
pairs (six enabled, one disabled placeholder). The endpoint now returns:

- 200 + ``InsightReport`` for an enabled pair (LLM mocked here),
- 503 + structured ``{error: "pair_not_activated", pair, reason}`` body
  for a registered-but-disabled pair (no DB / LLM call),
- 404 for an unknown pair-key.

Pattern mirrors ``test_api_admin_analyze.py`` — shared in-memory SQLite
via ``StaticPool``, ``get_session`` overridden, ``auth_enabled`` flipped
off so the global Bearer middleware lets the test client through.
``messages_create_text`` is patched at the ``insight_engine`` module
boundary; the LLM is never actually called.

The dry-run path is exercised explicitly to keep the contract that a
disabled pair short-circuits to 503 *before* doing any aggregation work
(important for Sprint-2 cost-awareness — disabled pairs must not cost a
DB query, let alone an Opus call).
"""
from __future__ import annotations

import json as _json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.database import get_session
from app.main import app
from app.services import insight_engine


_MOCK_LLM_OUTPUT = {
    "headline": "Mock Headline für Pair-Test",
    "tldr": "Eins. Zwei. Drei.",
    "trends": [
        {
            "name": "Mock-Trend",
            "evidence": "kein echter Beleg, Mock",
            "implication_for_creation": "Mock-Implikation",
        }
    ],
    "actions": [
        {"what": "Mock-Action", "why": "Mock-Why", "for_whom": "Mock-Cutter"}
    ],
    "cross_market_insight": {
        "de_vs_us": "Mock-Cross-Market",
        "transfer_opportunity": "Mock-Transfer",
    },
    "risks": ["Mock-Risk"],
    "data_caveats": ["Mock-Caveat"],
}


def _shared_test_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def db():
    engine = _shared_test_engine()
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    # The endpoint short-circuits dry_run before reaching the LLM, so the
    # API key is only needed for non-dry-run tests; setting it here keeps
    # both paths equally trivial.
    monkeypatch.setattr(settings, "anthropic_api_key", "TEST-KEY", raising=False)

    def _override_session():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _enabled_pair_keys() -> list[str]:
    return sorted(k for k, v in insight_engine.PAIRS.items() if v.get("enabled", True))


def _disabled_pair_keys() -> list[str]:
    return sorted(k for k, v in insight_engine.PAIRS.items() if not v.get("enabled", True))


# ---------- 404: unknown pair ---------------------------------------------


def test_unknown_pair_returns_404(client: TestClient):
    response = client.get("/api/insights/weekly", params={"pair": "no-such-pair"})
    assert response.status_code == 404
    detail = response.json().get("detail", "")
    # Detail-String enthält die Liste der ENABLED Keys (für 503-vs-404
    # gibt es eine getrennte Behandlung: 404 = nicht-existent, 503 = existiert,
    # nur deaktiviert).
    assert "no-such-pair" in detail


# ---------- 503: registered-but-disabled pair -----------------------------


@pytest.mark.parametrize("pair_key", _disabled_pair_keys())
def test_disabled_pair_returns_503_with_structured_body(
    client: TestClient, pair_key: str
):
    response = client.get("/api/insights/weekly", params={"pair": pair_key})
    assert response.status_code == 503
    body = response.json()
    detail = body.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "pair_not_activated"
    assert detail.get("pair") == pair_key
    assert isinstance(detail.get("reason"), str) and detail["reason"], (
        "503 muss einen menschenlesbaren reason mitliefern"
    )


def test_disabled_pair_short_circuits_before_llm(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Garantie für die Cost-Awareness aus dem Sprint-2-Briefing: ein
    deaktivierter Pair darf NIEMALS eine LLM-Aufruf-Funktion erreichen,
    auch nicht versehentlich. Patcht den Anthropic-Wrapper auf eine
    Sentinel-Exception und erwartet 503 statt 500."""
    def _explode(**kwargs):
        raise AssertionError("LLM darf für disabled pair nicht aufgerufen werden")

    monkeypatch.setattr(insight_engine, "messages_create_strict_json", _explode)

    disabled = _disabled_pair_keys()
    if not disabled:
        pytest.skip("Keine disabled pairs in der aktuellen Konfiguration")

    response = client.get("/api/insights/weekly", params={"pair": disabled[0]})
    assert response.status_code == 503


# ---------- 200: enabled pair, dry-run -------------------------------------


@pytest.mark.parametrize("pair_key", _enabled_pair_keys())
def test_enabled_pair_dry_run_returns_aggregation_only(
    client: TestClient, pair_key: str
):
    """Dry-Run + leere DB: aggregate_pair liefert eine valide Aggregation
    mit Notes (Channels nicht in DB). Kein LLM-Call. Antwortet 200 für
    JEDEN aktivierten Pair-Key — schützt davor, dass eine neue Pair-Konfig
    eine andere Code-Pfad-Nutzung triggert als warnerbros.

    Sprint 2026-05-12 paramountplus+lionsgate: DE ist seitdem optional.
    Lionsgate hat keinen DE-Channel definiert, also entfällt die
    DE-Channel-Note. US ist Pflicht in jedem Pair → US-Note wird auf
    leerer DB immer generiert.
    """
    from app.services.insight_engine import PAIRS
    pair_def = PAIRS[pair_key]
    has_de = any(c["market"] == "DE" for c in pair_def["channels"])

    response = client.get(
        "/api/insights/weekly",
        params={"pair": pair_key, "dry_run": "true"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pair_key"] == pair_key
    assert body["dry_run"] is True
    assert body["llm_output"] is None
    # Notes-Pfad: leere DB → Channels mit Spec aber ohne DB-Match
    # tauchen als Notes auf. Channels ohne Spec (Lionsgate-DE) emittieren
    # NICHTS und das ist absichtlich.
    notes = body.get("aggregation", {}).get("notes", [])
    if has_de:
        assert any("DE-Channel" in n for n in notes)
    assert any("US-Channel" in n for n in notes)


# ---------- 200: enabled pair, mocked LLM ---------------------------------


def test_enabled_pair_full_run_with_mocked_llm(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    fake_message = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                name="submit_weekly_brief",
                input=_MOCK_LLM_OUTPUT,
            )
        ],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
    )

    def _fake_call(**kwargs):
        return fake_message

    monkeypatch.setattr(insight_engine, "messages_create_strict_json", _fake_call)
    monkeypatch.setattr(insight_engine, "is_anthropic_configured", lambda: True)

    response = client.get(
        "/api/insights/weekly", params={"pair": "warnerbros", "dry_run": "false"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pair_key"] == "warnerbros"
    assert body["dry_run"] is False
    assert body["llm_output"]["headline"] == _MOCK_LLM_OUTPUT["headline"]


# ---------- Option A: read the LAST COMPLETED ISO week, cache-hit ----------


def test_weekly_endpoint_reads_last_completed_week_and_caches(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """The detail endpoint must operate on the last COMPLETED ISO week
    (what the Monday cron persists), not the in-progress current week, and
    must cache-hit on the persisted row instead of regenerating per visit.

    Regression for the Monday read/write mismatch: first visit generates +
    persists for KW-1, second visit is served from that row (no second LLM
    call)."""
    from datetime import datetime, timezone

    from app.services.insight_engine import last_completed_iso_week_anchor

    fake_message = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                name="submit_weekly_brief",
                input=_MOCK_LLM_OUTPUT,
            )
        ],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
    )
    llm_mock = MagicMock(return_value=fake_message)
    monkeypatch.setattr(insight_engine, "messages_create_strict_json", llm_mock)
    monkeypatch.setattr(insight_engine, "is_anthropic_configured", lambda: True)

    expected = last_completed_iso_week_anchor().isocalendar()
    current = datetime.now(timezone.utc).isocalendar()
    # Anchor is always the PREVIOUS ISO week — never the current one.
    assert (expected.year, expected.week) != (current.year, current.week)

    # First visit: cache miss → generate + persist for the completed week.
    r1 = client.get(
        "/api/insights/weekly", params={"pair": "warnerbros", "dry_run": "false"}
    )
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    assert (b1["iso_year"], b1["iso_week"]) == (expected.year, expected.week)
    assert llm_mock.call_count == 1

    # Second visit: row exists for the completed week → cache hit, no regen.
    r2 = client.get(
        "/api/insights/weekly", params={"pair": "warnerbros", "dry_run": "false"}
    )
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert (b2["iso_year"], b2["iso_week"]) == (expected.year, expected.week)
    assert b2["llm_output"]["headline"] == _MOCK_LLM_OUTPUT["headline"]
    assert llm_mock.call_count == 1  # NOT regenerated on the second visit
