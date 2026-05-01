"""Backfill-Skript-Logik gegen In-Memory-DB testen. capture_asset_screenshot
wird gemockt — kein Netzwerkzugriff. Idempotenz, Per-Asset-Fehlertoleranz und
Summary-Counter werden geprüft."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import (
    Asset,
    AssetType,
    Channel,
    Market,
    Post,
    ReviewStatus,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make(session: Session, *, screenshot_url: str | None = None,
          thumbnail_url: str | None = None,
          visual_evidence_url: str | None = None) -> Asset:
    channel = Channel(
        id=uuid4(), name=f"Ch-{uuid4().hex[:6]}", platform="instagram",
        url=f"https://example.com/c/{uuid4().hex[:6]}", market=Market.US,
    )
    session.add(channel)
    session.commit()
    session.refresh(channel)
    post = Post(
        id=uuid4(), channel_id=channel.id,
        post_url=f"https://example.com/p/{uuid4().hex[:8]}",
        detected_at=datetime.now(timezone.utc),
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    asset = Asset(
        id=uuid4(), post_id=post.id,
        asset_type=AssetType.UNKNOWN, review_status=ReviewStatus.NEW,
        screenshot_url=screenshot_url, thumbnail_url=thumbnail_url,
        visual_evidence_url=visual_evidence_url,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def test_skips_assets_with_object_key(monkeypatch: pytest.MonkeyPatch,
                                      session: Session, capsys) -> None:
    from scripts import backfill_evidence as backfill
    from app.services.screenshot_capture import VisualEvidenceResult

    asset = _make(session, screenshot_url="https://cdn.example/x.jpg",
                  visual_evidence_url="evidence/already_there.jpg")

    fake_capture = lambda a: pytest.fail("capture must not be called for migrated asset")
    monkeypatch.setattr(backfill, "capture_asset_screenshot", fake_capture)

    summary = backfill.run(session)
    out = capsys.readouterr().out

    assert summary == {"total": 1, "migrated": 0, "skipped": 1, "failed": 0}
    assert f"SKIP {asset.id}" in out


def test_skips_legacy_storage_path(monkeypatch: pytest.MonkeyPatch,
                                   session: Session) -> None:
    from scripts import backfill_evidence as backfill

    _make(session, screenshot_url="https://cdn.example/x.jpg",
          visual_evidence_url="/storage/evidence/legacy.jpg")

    monkeypatch.setattr(backfill, "capture_asset_screenshot",
                        lambda a: pytest.fail("must skip legacy"))

    summary = backfill.run(session)
    assert summary["skipped"] == 1
    assert summary["migrated"] == 0


def test_migrates_asset_with_http_url(monkeypatch: pytest.MonkeyPatch,
                                      session: Session, capsys) -> None:
    from scripts import backfill_evidence as backfill
    from app.services.screenshot_capture import VisualEvidenceResult

    asset = _make(session, screenshot_url="https://cdn.example/img.jpg",
                  visual_evidence_url=None)

    captured_key = f"evidence/{asset.id}_abc.jpg"
    monkeypatch.setattr(
        backfill, "capture_asset_screenshot",
        lambda a: VisualEvidenceResult(
            status="captured", evidence_url=captured_key,
            source_url="https://cdn.example/img.jpg",
            captured_at="2026-05-01T12:00:00+00:00",
        ),
    )

    summary = backfill.run(session)
    out = capsys.readouterr().out

    assert summary == {"total": 1, "migrated": 1, "skipped": 0, "failed": 0}
    session.refresh(asset)
    assert asset.visual_evidence_url == captured_key
    assert asset.visual_evidence_status == "captured"
    assert f"OK   {asset.id}" in out


def test_per_asset_capture_exception_does_not_abort(
    monkeypatch: pytest.MonkeyPatch, session: Session, capsys
) -> None:
    from scripts import backfill_evidence as backfill
    from app.services.screenshot_capture import VisualEvidenceResult

    bad = _make(session, screenshot_url="https://cdn.example/bad.jpg")
    good = _make(session, thumbnail_url="https://cdn.example/good.jpg")

    def fake_capture(asset):
        if asset.id == bad.id:
            raise RuntimeError("R2 connection refused")
        return VisualEvidenceResult(
            status="captured", evidence_url=f"evidence/{asset.id}_ok.jpg",
            source_url="https://cdn.example/good.jpg",
        )

    monkeypatch.setattr(backfill, "capture_asset_screenshot", fake_capture)

    summary = backfill.run(session)
    out = capsys.readouterr().out

    assert summary["total"] == 2
    assert summary["migrated"] == 1
    assert summary["failed"] == 1
    assert summary["skipped"] == 0
    assert f"FAIL {bad.id}: RuntimeError: R2 connection refused" in out
    assert f"OK   {good.id}" in out


def test_capture_status_other_than_captured_counts_as_failed(
    monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    from scripts import backfill_evidence as backfill
    from app.services.screenshot_capture import VisualEvidenceResult

    _make(session, screenshot_url="https://cdn.example/dead.jpg")

    monkeypatch.setattr(
        backfill, "capture_asset_screenshot",
        lambda a: VisualEvidenceResult(status="fetch_failed"),
    )
    summary = backfill.run(session)

    assert summary == {"total": 1, "migrated": 0, "skipped": 0, "failed": 1}


def test_idempotent_second_run_is_all_skips(
    monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    from scripts import backfill_evidence as backfill
    from app.services.screenshot_capture import VisualEvidenceResult

    asset = _make(session, screenshot_url="https://cdn.example/once.jpg",
                  visual_evidence_url=None)

    monkeypatch.setattr(
        backfill, "capture_asset_screenshot",
        lambda a: VisualEvidenceResult(
            status="captured", evidence_url=f"evidence/{a.id}_first.jpg",
            source_url="https://cdn.example/once.jpg",
        ),
    )

    first = backfill.run(session)
    assert first["migrated"] == 1

    monkeypatch.setattr(
        backfill, "capture_asset_screenshot",
        lambda a: pytest.fail("second run must not re-capture"),
    )
    second = backfill.run(session)
    assert second == {"total": 1, "migrated": 0, "skipped": 1, "failed": 0}


def test_batches_in_chunks_of_fifty(monkeypatch: pytest.MonkeyPatch,
                                    session: Session, capsys) -> None:
    from scripts import backfill_evidence as backfill
    from app.services.screenshot_capture import VisualEvidenceResult

    for _ in range(75):
        _make(session, screenshot_url=f"https://cdn.example/{uuid4().hex}.jpg")

    monkeypatch.setattr(
        backfill, "capture_asset_screenshot",
        lambda a: VisualEvidenceResult(
            status="captured", evidence_url=f"evidence/{a.id}.jpg",
            source_url="https://cdn.example/x.jpg",
        ),
    )

    summary = backfill.run(session)
    out = capsys.readouterr().out

    assert summary["total"] == 75
    assert summary["migrated"] == 75
    assert "--- Batch 1 (50 assets) ---" in out
    assert "--- Batch 2 (25 assets) ---" in out
    assert "--- Batch 3" not in out
