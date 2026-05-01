"""End-to-end check that the API serializer resolves stored object keys
into fetchable URLs while leaving legacy URL forms untouched."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.api.assets import _asset_card
from app.config import settings
from app.models.entities import Asset, AssetType, Channel, Market, Post, ReviewStatus


def _session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make(visual_evidence_url: str | None) -> tuple[Asset, Post, Channel]:
    asset = Asset(
        id=uuid4(),
        post_id=uuid4(),
        asset_type=AssetType.UNKNOWN,
        review_status=ReviewStatus.NEW,
        visual_evidence_url=visual_evidence_url,
    )
    channel = Channel(
        id=uuid4(),
        name="Test",
        platform="instagram",
        url="https://example.com/c",
        market=Market.US,
    )
    post = Post(
        id=asset.post_id,
        channel_id=channel.id,
        post_url="https://example.com/p/1",
        detected_at=datetime.now(timezone.utc),
    )
    return asset, post, channel


def test_card_resolves_object_key_to_local_storage_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "storage_backend", "local", raising=False)
    asset, post, channel = _make("evidence/asset_123_abc.jpg")
    card = _asset_card(asset, post, channel, None)

    assert card["visual_evidence_url"] == "/storage/evidence/asset_123_abc.jpg"
    assert card["visual_evidence_key"] == "evidence/asset_123_abc.jpg"


def test_card_passes_through_legacy_storage_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "storage_backend", "local", raising=False)
    asset, post, channel = _make("/storage/evidence/legacy.jpg")
    card = _asset_card(asset, post, channel, None)

    assert card["visual_evidence_url"] == "/storage/evidence/legacy.jpg"
    assert card["visual_evidence_key"] == "/storage/evidence/legacy.jpg"


def test_card_passes_through_external_http_url() -> None:
    asset, post, channel = _make("https://cdn.instagram.com/foo.jpg")
    card = _asset_card(asset, post, channel, None)

    assert card["visual_evidence_url"] == "https://cdn.instagram.com/foo.jpg"
    assert card["visual_evidence_key"] == "https://cdn.instagram.com/foo.jpg"


def test_card_emits_none_when_evidence_missing() -> None:
    asset, post, channel = _make(None)
    card = _asset_card(asset, post, channel, None)

    assert card["visual_evidence_url"] is None
    assert card["visual_evidence_key"] is None
