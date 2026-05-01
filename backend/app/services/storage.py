from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import settings


class StorageBackend(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str) -> str:
        """Persist `data` under `key` and return the canonical URL."""

    @abstractmethod
    def get_url(self, key: str) -> str:
        """Return a URL the caller can use to fetch the object."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None:
        """Idempotent — must not raise if the object is already gone."""


def _resolve_local_base() -> Path:
    candidates = [
        Path("storage"),
        Path("backend/storage"),
        Path(__file__).resolve().parents[3] / "storage",
    ]
    base = next((c for c in candidates if c.exists()), candidates[0])
    base.mkdir(parents=True, exist_ok=True)
    return base


class LocalFileStorage(StorageBackend):
    def __init__(self, base_path: Path | None = None) -> None:
        self._base = base_path if base_path is not None else _resolve_local_base()
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        target = (self._base / key).resolve()
        base = self._base.resolve()
        if base not in target.parents and target != base:
            raise ValueError(f"key escapes storage base: {key!r}")
        return target

    def put(self, key: str, data: bytes, content_type: str) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self.get_url(key)

    def get_url(self, key: str) -> str:
        return f"/storage/{key}"

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3Storage(StorageBackend):
    def __init__(self) -> None:
        if not settings.s3_bucket:
            raise RuntimeError("S3_BUCKET must be set when STORAGE_BACKEND=s3")
        self._bucket = settings.s3_bucket
        self._ttl = settings.s3_signed_url_ttl_seconds
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return self.get_url(key)

    def get_url(self, key: str) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=self._ttl,
        )

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404:
                return False
            raise

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)


def get_storage() -> StorageBackend:
    if settings.storage_backend == "s3":
        return S3Storage()
    return LocalFileStorage()


def is_legacy_storage_path(value: str | None) -> bool:
    """Pre-F0.1 evidence reference: literally '/storage/evidence/...' as written
    by the old screenshot_capture before the storage adapter landed. Backing
    files are gone after every Railway redeploy, but the substring still
    classifies as 'internal' for selector purposes during the W3 transition."""
    return bool(value) and "/storage/evidence/" in value


_OBJECT_KEY_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def is_object_key(value: str | None) -> bool:
    """Post-F0.1 evidence reference: a bare object key like
    'evidence/<asset_id>_<uuid>.jpg' written by the storage adapter. Detection
    is heuristic by design — until a dedicated DB column lands, the helper is
    the single source of truth so selector and any future caller share the
    same recogniser."""
    if not value:
        return False
    if value.startswith(("http://", "https://", "data:", "/")):
        # http URLs, data URIs, and any leading slash (legacy /storage/...,
        # absolute filesystem paths) are explicitly *not* object keys.
        return False
    if "/" not in value:
        return False
    return value.lower().endswith(_OBJECT_KEY_EXTENSIONS)


def resolve_url(value: str | None) -> str | None:
    """Resolve a stored evidence reference into a fetchable URL.

    `value` may be:
    - None or empty -> None
    - an http(s) URL or a legacy `/storage/...` path -> returned as-is
    - a bare object key (e.g. ``evidence/asset_123.jpg``) -> resolved via the
      active storage backend (presigned URL for S3, ``/storage/<key>`` for
      LocalFileStorage). Errors during resolution fall back to None so callers
      never crash on a temporarily unreachable backend.
    """
    if not value:
        return None
    if value.startswith(("http://", "https://", "/storage/", "/")):
        return value
    try:
        return get_storage().get_url(value)
    except Exception:  # noqa: BLE001
        return None
