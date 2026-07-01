"""Upload-size guard for POST /api/channels/import-excel
(Sicherheits-Audit 2026-07-01, Memory-DoS-Finding)."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.channels import _MAX_IMPORT_EXCEL_BYTES
from app.config import settings
from app.database import get_session
from app.main import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)

    test_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(test_engine)

    def _override_session():
        with Session(test_engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_rejects_non_excel_filename(client: TestClient) -> None:
    response = client.post(
        "/api/channels/import-excel",
        files={"file": ("channels.csv", io.BytesIO(b"a,b,c"), "text/csv")},
    )
    assert response.status_code == 400


def test_rejects_file_over_size_limit(client: TestClient) -> None:
    oversized = b"x" * (_MAX_IMPORT_EXCEL_BYTES + 1024)
    response = client.post(
        "/api/channels/import-excel",
        files={"file": ("channels.xlsx", io.BytesIO(oversized), "application/vnd.ms-excel")},
    )
    assert response.status_code == 413
    assert "zu groß" in response.json()["detail"]
