from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.config import settings
from app.services.storage import (
    LocalFileStorage,
    S3Storage,
    StorageBackend,
    get_storage,
    is_legacy_storage_path,
    is_object_key,
    resolve_url,
)


def test_local_file_storage_round_trip(tmp_path: Path) -> None:
    storage = LocalFileStorage(base_path=tmp_path)
    key = "evidence/test.bin"
    payload = b"hello creative radar"

    url = storage.put(key, payload, "application/octet-stream")

    assert url == "/storage/evidence/test.bin"
    assert storage.exists(key)
    assert (tmp_path / "evidence" / "test.bin").read_bytes() == payload
    assert storage.get_url(key) == "/storage/evidence/test.bin"

    storage.delete(key)
    assert not storage.exists(key)
    storage.delete(key)  # idempotent


def test_s3_storage_uploads_and_signs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "s3_bucket", "creative-radar-assets", raising=False)
    monkeypatch.setattr(settings, "s3_endpoint_url", "https://example.r2.cloudflarestorage.com", raising=False)
    monkeypatch.setattr(settings, "s3_access_key_id", "AKIA-TEST", raising=False)
    monkeypatch.setattr(settings, "s3_secret_access_key", "secret-test", raising=False)
    monkeypatch.setattr(settings, "s3_region", "auto", raising=False)
    monkeypatch.setattr(settings, "s3_signed_url_ttl_seconds", 1800, raising=False)

    fake_client = MagicMock()
    fake_client.generate_presigned_url.return_value = "https://signed.example/test.bin?sig=abc"

    with patch("app.services.storage.boto3.client", return_value=fake_client) as boto_factory:
        storage = S3Storage()

        url = storage.put("evidence/test.bin", b"payload-bytes", "image/png")
        signed = storage.get_url("evidence/test.bin")

    boto_factory.assert_called_once()
    factory_kwargs = boto_factory.call_args.kwargs
    assert factory_kwargs["endpoint_url"] == "https://example.r2.cloudflarestorage.com"
    assert factory_kwargs["aws_access_key_id"] == "AKIA-TEST"
    assert factory_kwargs["aws_secret_access_key"] == "secret-test"
    assert factory_kwargs["region_name"] == "auto"

    fake_client.put_object.assert_called_once_with(
        Bucket="creative-radar-assets",
        Key="evidence/test.bin",
        Body=b"payload-bytes",
        ContentType="image/png",
    )
    fake_client.generate_presigned_url.assert_called_with(
        "get_object",
        Params={"Bucket": "creative-radar-assets", "Key": "evidence/test.bin"},
        ExpiresIn=1800,
    )
    assert url == "https://signed.example/test.bin?sig=abc"
    assert signed == "https://signed.example/test.bin?sig=abc"


def test_s3_storage_exists_returns_false_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "s3_bucket", "creative-radar-assets", raising=False)

    fake_client = MagicMock()
    fake_client.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
        "HeadObject",
    )

    with patch("app.services.storage.boto3.client", return_value=fake_client):
        storage = S3Storage()
        assert storage.exists("missing/key.bin") is False


def test_factory_returns_local_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "storage_backend", "local", raising=False)
    assert isinstance(get_storage(), LocalFileStorage)


def test_factory_returns_s3_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "storage_backend", "s3", raising=False)
    monkeypatch.setattr(settings, "s3_bucket", "creative-radar-assets", raising=False)

    with patch("app.services.storage.boto3.client", return_value=MagicMock()):
        backend = get_storage()

    assert isinstance(backend, S3Storage)
    assert isinstance(backend, StorageBackend)


def test_resolve_url_passes_through_http() -> None:
    assert resolve_url("https://cdn.example/foo.jpg") == "https://cdn.example/foo.jpg"
    assert resolve_url("http://internal.example/bar.png") == "http://internal.example/bar.png"


def test_resolve_url_passes_through_legacy_storage_path() -> None:
    assert resolve_url("/storage/evidence/abc123.jpg") == "/storage/evidence/abc123.jpg"


def test_resolve_url_returns_none_for_falsy() -> None:
    assert resolve_url(None) is None
    assert resolve_url("") is None


def test_resolve_url_resolves_object_key_via_local_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "storage_backend", "local", raising=False)
    assert resolve_url("evidence/asset_123.jpg") == "/storage/evidence/asset_123.jpg"


def test_resolve_url_returns_none_when_backend_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "storage_backend", "s3", raising=False)
    fake_client = MagicMock()
    fake_client.generate_presigned_url.side_effect = RuntimeError("R2 down")
    monkeypatch.setattr(settings, "s3_bucket", "creative-radar-assets", raising=False)
    with patch("app.services.storage.boto3.client", return_value=fake_client):
        assert resolve_url("evidence/key.jpg") is None


# --- W3 / F0.4 helpers ---


def test_is_legacy_storage_path_recognises_substring() -> None:
    assert is_legacy_storage_path("/storage/evidence/abc.jpg") is True
    assert is_legacy_storage_path("https://app.example.com/storage/evidence/abc.jpg") is True


def test_is_legacy_storage_path_falsy_inputs() -> None:
    assert is_legacy_storage_path(None) is False
    assert is_legacy_storage_path("") is False
    assert is_legacy_storage_path("evidence/abc.jpg") is False
    assert is_legacy_storage_path("https://cdn/foo.jpg") is False


def test_is_object_key_recognises_bare_key() -> None:
    assert is_object_key("evidence/asset_123_abc.jpg") is True
    assert is_object_key("evidence/2026/05/01/asset.png") is True


def test_is_object_key_rejects_url_forms() -> None:
    assert is_object_key("https://cdn/foo.jpg") is False
    assert is_object_key("http://example.com/x.png") is False
    assert is_object_key("data:image/png;base64,iVBOR") is False
    assert is_object_key("/storage/evidence/abc.jpg") is False


def test_is_object_key_rejects_falsy_or_no_extension() -> None:
    assert is_object_key(None) is False
    assert is_object_key("") is False
    assert is_object_key("evidence/no_extension") is False
    assert is_object_key("notakey.jpg") is False  # no slash
