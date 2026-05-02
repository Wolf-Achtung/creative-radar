"""HTTP-layer tests for /api/channels covering the Sprint 5.2.1 Mini-Run 3b
extension (channel_role, quality_tier, acquisition_strategy,
monitoring_enabled).

The tests verify two things:
1. Backwards compatibility — old payloads that omit the new fields still
   succeed and pick up the documented defaults (P1 / apify / monitoring on
   / channel_role None).
2. Forwards behavior — new payloads round-trip through Pydantic + ORM,
   including explicit nulls and an invalid-enum 422 path.

Pattern matches test_cost_log.py: a shared :memory: SQLite engine via
StaticPool, get_session dependency-overridden to that engine, and
auth_enabled flipped off so the global Bearer middleware doesn't block
the test client. Auth itself is covered in test_auth_middleware.py.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.database import get_session
from app.main import app


def _shared_test_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)

    test_engine = _shared_test_engine()
    SQLModel.metadata.create_all(test_engine)

    def _override_session():
        with Session(test_engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


# ---------- POST: backwards compat ----------


def test_post_channel_old_payload_succeeds_with_defaults(client: TestClient) -> None:
    """A payload from before Mini-Run 3b (only the legacy fields) must still
    create a channel. Pydantic should fill in the defaults defined in
    ChannelCreate (P1 / apify / monitoring_enabled=True / channel_role=None)."""
    payload = {
        "name": "Legacy Channel",
        "url": "https://instagram.com/legacy",
        "handle": "legacy",
        "market": "DE",
    }
    response = client.post("/api/channels", json=payload)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["name"] == "Legacy Channel"
    assert body["channel_role"] is None
    assert body["quality_tier"] == "P1"
    assert body["acquisition_strategy"] == "apify"
    assert body["monitoring_enabled"] is True


def test_post_channel_new_payload_persists_all_fields(client: TestClient) -> None:
    """Full payload with explicit values for every new field. Each must
    survive the create -> response cycle as written."""
    payload = {
        "name": "Marvel Studios",
        "url": "https://instagram.com/marvel",
        "handle": "marvel",
        "market": "US",
        "channel_role": "studio_distributor",
        "quality_tier": "P0",
        "acquisition_strategy": "youtube_api",
        "monitoring_enabled": False,
    }
    response = client.post("/api/channels", json=payload)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["channel_role"] == "studio_distributor"
    assert body["quality_tier"] == "P0"
    assert body["acquisition_strategy"] == "youtube_api"
    assert body["monitoring_enabled"] is False


def test_post_channel_with_explicit_null_channel_role(client: TestClient) -> None:
    """channel_role is the only nullable enum. An explicit ``null`` from the
    client must be accepted and stored as null (not coerced to a default
    role). Frontend will send this when the user clears the field."""
    payload = {
        "name": "No Role",
        "url": "https://instagram.com/norole",
        "channel_role": None,
        "quality_tier": "P2",
    }
    response = client.post("/api/channels", json=payload)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["channel_role"] is None
    assert body["quality_tier"] == "P2"


def test_post_channel_invalid_quality_tier_returns_422(client: TestClient) -> None:
    """The Pydantic validation layer must reject values that are not in
    the QualityTier enum. Without this guard, garbage strings would land
    in the DB on Postgres (where the column is a native ENUM, write would
    fail) and silently land in SQLite tests (where it's a VARCHAR). We
    care more about catching this *before* the DB layer than testing
    every valid enum member."""
    payload = {
        "name": "Bad Tier",
        "url": "https://instagram.com/badtier",
        "quality_tier": "P9",
    }
    response = client.post("/api/channels", json=payload)
    assert response.status_code == 422, response.text


# ---------- PATCH ----------


def test_patch_channel_only_monitoring_enabled_keeps_other_fields(
    client: TestClient,
) -> None:
    """PATCH uses model_dump(exclude_unset=True) — fields not in the
    request body must be left alone on the persisted row. Verify by
    creating with one set of values, patching only monitoring_enabled,
    then reading back via GET."""
    create_payload = {
        "name": "Patch Target",
        "url": "https://instagram.com/patch",
        "channel_role": "talent_cast",
        "quality_tier": "P0",
        "acquisition_strategy": "manual",
        "monitoring_enabled": True,
    }
    created = client.post("/api/channels", json=create_payload).json()
    channel_id = created["id"]

    patch_response = client.patch(
        f"/api/channels/{channel_id}",
        json={"monitoring_enabled": False},
    )
    assert patch_response.status_code == 200, patch_response.text

    body = patch_response.json()
    assert body["monitoring_enabled"] is False
    # The other three new fields must not have been reset to defaults.
    assert body["channel_role"] == "talent_cast"
    assert body["quality_tier"] == "P0"
    assert body["acquisition_strategy"] == "manual"


# ---------- GET ----------


def test_get_channels_response_includes_new_fields(client: TestClient) -> None:
    """The list endpoint serializes the Channel model directly. Every new
    field must appear in the JSON for each row, otherwise the frontend
    reading the response will see undefined and break the registry UI."""
    client.post(
        "/api/channels",
        json={
            "name": "List Target",
            "url": "https://instagram.com/list",
            "channel_role": "regional",
            "quality_tier": "P2",
            "acquisition_strategy": "manual",
            "monitoring_enabled": False,
        },
    )

    response = client.get("/api/channels")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    row = rows[0]

    for key in (
        "channel_role",
        "quality_tier",
        "acquisition_strategy",
        "monitoring_enabled",
    ):
        assert key in row, f"missing field {key!r} in GET /api/channels response"

    assert row["channel_role"] == "regional"
    assert row["quality_tier"] == "P2"
    assert row["acquisition_strategy"] == "manual"
    assert row["monitoring_enabled"] is False
