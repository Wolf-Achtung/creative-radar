from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.models.entities import Asset, AssetType, ReviewStatus
from app.services import screenshot_capture
from app.services.screenshot_capture import (
    VisualEvidenceResult,
    capture_asset_screenshot,
)


def _asset(*, screenshot_url: str | None = None, thumbnail_url: str | None = None,
           visual_source_url: str | None = None) -> Asset:
    return Asset(
        id=uuid4(),
        post_id=uuid4(),
        asset_type=AssetType.UNKNOWN,
        review_status=ReviewStatus.NEW,
        screenshot_url=screenshot_url,
        thumbnail_url=thumbnail_url,
        visual_source_url=visual_source_url,
    )


def test_capture_returns_no_source_when_no_candidates() -> None:
    result = capture_asset_screenshot(_asset())
    assert result == VisualEvidenceResult(status="no_source")


def test_capture_writes_via_storage_adapter_and_returns_object_key() -> None:
    asset = _asset(screenshot_url="https://cdn.example/source.jpg")

    fake_storage = MagicMock()
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {"content-type": "image/jpeg"}
    fake_response.content = b"x" * 4096

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.get.return_value = fake_response

    with patch.object(screenshot_capture, "get_storage", return_value=fake_storage), \
         patch.object(screenshot_capture.httpx, "Client", return_value=fake_client):
        result = capture_asset_screenshot(asset)

    assert result.status == "captured"
    assert result.evidence_url is not None
    assert result.evidence_url.startswith(f"evidence/{asset.id}_")
    assert result.evidence_url.endswith(".jpg")
    assert "/storage/" not in result.evidence_url, "evidence_url must be an object key, not a URL path"
    assert result.source_url == "https://cdn.example/source.jpg"

    fake_storage.put.assert_called_once()
    call_args = fake_storage.put.call_args
    assert call_args.args[0] == result.evidence_url
    assert call_args.args[1] == b"x" * 4096
    assert call_args.args[2] == "image/jpeg"


def test_capture_returns_fetch_failed_when_all_sources_4xx() -> None:
    asset = _asset(screenshot_url="https://cdn.example/missing.jpg")

    fake_response = MagicMock()
    fake_response.status_code = 404

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.get.return_value = fake_response

    with patch.object(screenshot_capture, "get_storage", return_value=MagicMock()), \
         patch.object(screenshot_capture.httpx, "Client", return_value=fake_client):
        result = capture_asset_screenshot(asset)

    assert result.status == "fetch_failed"
    assert result.evidence_url is None


def test_capture_returns_fetch_failed_when_storage_put_raises() -> None:
    asset = _asset(thumbnail_url="https://cdn.example/thumb.jpg")

    fake_storage = MagicMock()
    fake_storage.put.side_effect = RuntimeError("R2 unreachable")

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {"content-type": "image/png"}
    fake_response.content = b"\x89PNG" + b"\x00" * 4096

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.get.return_value = fake_response

    with patch.object(screenshot_capture, "get_storage", return_value=fake_storage), \
         patch.object(screenshot_capture.httpx, "Client", return_value=fake_client):
        result = capture_asset_screenshot(asset)

    assert result.status == "fetch_failed"
    assert result.evidence_url is None
