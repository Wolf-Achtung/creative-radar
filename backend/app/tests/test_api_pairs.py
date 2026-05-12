"""HTTP-layer tests for ``GET /api/pairs`` (Sprint 2026-05-12 — landing-page sync).

Verifies the endpoint that drives the landing-page card grid on
``app.creative-radar.de``. Contract:

- 200 + ``PairsResponse`` shape for every enabled pair, in PAIRS-dict
  insertion order (Wolf's curated visual order, not alphabetical).
- Disabled pairs are excluded.
- Markets per pair are emitted in fixed DE → US → UK order
  (``MARKETS_DISPLAY_ORDER``), only including markets the pair actually
  covers (Lionsgate → ``["US", "UK"]`` because it has no DE channel).

Test isolation pattern mirrors ``test_api_insights_weekly.py`` — shared
in-memory SQLite via ``StaticPool`` and ``auth_enabled`` flipped off.
The endpoint does not touch the DB, but the test client still boots the
full app which expects a DB session dependency to exist.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.database import get_session
from app.main import app
from app.services import insight_engine
from app.services.insight_engine import INSIGHT_FREQUENCY_LABEL


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

    def _override_session():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _expected_enabled_keys_in_order() -> list[str]:
    """Insertion-order enabled pair keys — what /api/pairs is expected to emit."""
    return [k for k, v in insight_engine.PAIRS.items() if v.get("enabled", False)]


# ---------- 200: payload shape -------------------------------------------------


def test_pairs_endpoint_returns_all_enabled(client: TestClient):
    response = client.get("/api/pairs")
    assert response.status_code == 200
    payload = response.json()
    assert "pairs" in payload
    returned_keys = [p["pair_key"] for p in payload["pairs"]]
    assert returned_keys == _expected_enabled_keys_in_order()


def test_pairs_endpoint_response_schema(client: TestClient):
    response = client.get("/api/pairs")
    assert response.status_code == 200
    pairs = response.json()["pairs"]
    assert pairs, "endpoint must return at least one pair on a healthy PAIRS dict"
    required_fields = {"pair_key", "display_name", "markets", "frequency_label", "enabled"}
    for pair in pairs:
        assert required_fields <= set(pair.keys()), (
            f"missing fields in {pair['pair_key']!r}: "
            f"{required_fields - set(pair.keys())}"
        )
        assert isinstance(pair["pair_key"], str) and pair["pair_key"]
        assert isinstance(pair["display_name"], str) and pair["display_name"]
        assert isinstance(pair["markets"], list) and pair["markets"]
        assert all(isinstance(m, str) for m in pair["markets"])
        assert pair["frequency_label"] == INSIGHT_FREQUENCY_LABEL
        assert pair["enabled"] is True


# ---------- Exclusion: disabled pairs -----------------------------------------


def test_pairs_endpoint_excludes_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Flip warnerbros to enabled=False and assert it disappears from
    the response. The other 8 stay. This guards the enabled-filter logic
    independent of which pairs happen to be disabled in production today
    (currently none)."""
    pair_def = insight_engine.PAIRS["warnerbros"]
    original_enabled = pair_def["enabled"]
    original_reason = pair_def.get("reason")
    pair_def["enabled"] = False
    pair_def["reason"] = "temporarily disabled for test"
    try:
        response = client.get("/api/pairs")
        assert response.status_code == 200
        returned_keys = [p["pair_key"] for p in response.json()["pairs"]]
        assert "warnerbros" not in returned_keys
        assert len(returned_keys) == sum(
            1
            for k, v in insight_engine.PAIRS.items()
            if v.get("enabled", False) and k != "warnerbros"
        )
    finally:
        pair_def["enabled"] = original_enabled
        pair_def["reason"] = original_reason


# ---------- Markets: stable display order + per-pair correctness --------------


def test_pairs_endpoint_lionsgate_markets_us_only(client: TestClient):
    """X1 (2026-05-12): surface = brief reality. Lionsgate has UK
    channels in the pool but the LLM brief does not surface UK yet,
    so the card promises only ['US']. When B2 brings UK into the
    Lionsgate brief, the pair's ``markets`` field flips to
    ['US', 'UK']."""
    response = client.get("/api/pairs")
    assert response.status_code == 200
    lionsgate = next(
        (p for p in response.json()["pairs"] if p["pair_key"] == "lionsgate"),
        None,
    )
    assert lionsgate is not None, "lionsgate must be returned"
    assert lionsgate["markets"] == ["US"]


def test_pairs_endpoint_paramountplus_markets_de_us(client: TestClient):
    """X1: Paramount+ has DE/US/UK channels in the pool but the LLM
    brief surfaces only DE+US until B2. The endpoint reflects the
    brief reality, not the channel-pool reality."""
    response = client.get("/api/pairs")
    paramountplus = next(
        (p for p in response.json()["pairs"] if p["pair_key"] == "paramountplus"),
        None,
    )
    assert paramountplus is not None
    assert paramountplus["markets"] == ["DE", "US"]


def test_pairs_endpoint_markets_always_in_fixed_display_order(client: TestClient):
    """Whichever markets a pair covers, the emitted order is the
    intersection with DE → US → UK. Guards against the markets-order
    drifting back into channels-insertion order."""
    response = client.get("/api/pairs")
    fixed_order = ["DE", "US", "UK"]
    for pair in response.json()["pairs"]:
        markets = pair["markets"]
        # Each market should be at its expected index in the fixed order.
        indices = [fixed_order.index(m) for m in markets]
        assert indices == sorted(indices), (
            f"{pair['pair_key']!r} emitted markets {markets} not in "
            f"DE → US → UK order"
        )


# ---------- X1 surface override --------------------------------------------


def test_pairs_endpoint_surface_override_warnerbros_de_us_only(client: TestClient):
    """X1 invariant: warnerbros has UK channels in its PAIRS pool
    (Phase A added @warnerbrosuk on TT/IG/YT), but the explicit
    ``markets`` field is ['DE', 'US'] and the endpoint must emit
    exactly that — no leakage of pool-derived UK back into the
    surface response. Guards the override mechanism itself."""
    pool_markets = {
        channel["market"]
        for platform in insight_engine.PAIRS["warnerbros"]["platforms"].values()
        for channel in platform
    }
    assert "UK" in pool_markets, (
        "Precondition: warnerbros must have UK channels in its pool, "
        "otherwise this test is testing nothing"
    )

    response = client.get("/api/pairs")
    warnerbros = next(
        (p for p in response.json()["pairs"] if p["pair_key"] == "warnerbros"),
        None,
    )
    assert warnerbros is not None
    assert warnerbros["markets"] == ["DE", "US"]


def test_pairs_endpoint_falls_back_to_pool_when_markets_field_absent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Backwards-compat: a pair entered before the X1 ``markets``
    convention catches up should fall back to the channel-pool union
    (the pre-X1 derivation). Verified by stripping the field from
    one pair in-place and asserting the endpoint still returns a
    sensible markets list derived from its channels."""
    pair_def = insight_engine.PAIRS["warnerbros"]
    original_markets = pair_def.pop("markets")
    try:
        response = client.get("/api/pairs")
        assert response.status_code == 200
        warnerbros = next(
            (p for p in response.json()["pairs"] if p["pair_key"] == "warnerbros"),
            None,
        )
        assert warnerbros is not None
        # Pool union for warnerbros covers DE/US/UK (Phase A), emitted
        # in fixed display order.
        assert warnerbros["markets"] == ["DE", "US", "UK"]
    finally:
        pair_def["markets"] = original_markets
