"""Tests for the W3 honest-status fixes in services/visual_analysis.py.

OpenAI is mocked end-to-end — no real API calls. Capture is mocked too so we
can drive each branch without hitting the storage adapter."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.models.entities import (
    Asset,
    AssetType,
    Channel,
    Market,
    Post,
    ReviewStatus,
)
from app.services import visual_analysis
from app.services.screenshot_capture import VisualEvidenceResult
from app.services.visual_analysis import (
    _classify_openai_exception,
    _vision_data_is_empty,
    analyze_asset_visual,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make(session: Session) -> Asset:
    channel = Channel(
        id=uuid4(), name="Test", platform="instagram",
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
        thumbnail_url="https://cdn.example/thumb.jpg",
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def _captured(asset: Asset) -> VisualEvidenceResult:
    return VisualEvidenceResult(
        status="captured",
        evidence_url=f"evidence/{asset.id}_test.jpg",
        source_url="https://cdn.example/thumb.jpg",
        captured_at="2026-05-01T12:00:00+00:00",
    )


def _mock_openai(monkeypatch: pytest.MonkeyPatch, *, return_content: str | None = None,
                 raise_exc: BaseException | None = None) -> MagicMock:
    """Patch the OpenAI client constructor so client.chat.completions.create()
    either returns the given content string or raises the given exception."""
    fake_client = MagicMock()
    if raise_exc is not None:
        fake_client.chat.completions.create.side_effect = raise_exc
    else:
        fake_resp = MagicMock()
        fake_resp.choices = [MagicMock()]
        fake_resp.choices[0].message.content = return_content
        fake_client.chat.completions.create.return_value = fake_resp

    monkeypatch.setattr(visual_analysis, "OpenAI", lambda **kwargs: fake_client)
    monkeypatch.setattr(settings, "openai_api_key", "test-key", raising=False)
    return fake_client


# ------------------------------------------------------------------ helpers ---


def test_vision_data_is_empty_treats_blank_dict_as_empty() -> None:
    assert _vision_data_is_empty({}) is True


def test_vision_data_is_empty_treats_dict_with_only_whitespace_as_empty() -> None:
    assert _vision_data_is_empty({"ocr_text": "  ", "visual_summary_de": ""}) is True


def test_vision_data_is_empty_recognises_useful_payload() -> None:
    assert _vision_data_is_empty({"ocr_text": "MOTHER MARY"}) is False
    assert _vision_data_is_empty({"visual_summary_de": "ein Trailer"}) is False


def test_classify_openai_exception_timeout_by_class_name() -> None:
    class APITimeoutError(Exception):
        pass

    assert _classify_openai_exception(APITimeoutError("foo")) == "vision_timeout"


def test_classify_openai_exception_timeout_by_message() -> None:
    assert _classify_openai_exception(RuntimeError("read timeout while waiting")) == "vision_timeout"


def test_classify_openai_exception_image_unreachable_marker() -> None:
    exc = RuntimeError("Could not download image at https://cdn.example/x.jpg")
    assert _classify_openai_exception(exc) == "image_unreachable"


def test_classify_openai_exception_unknown_falls_to_vision_error() -> None:
    assert _classify_openai_exception(RuntimeError("rate limit exceeded")) == "vision_error"


# ----------------------------------------------------------- pipeline tests ---


def test_done_when_vision_returns_useful_json(session: Session,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    asset = _make(session)
    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot",
                        lambda a: _captured(a))
    _mock_openai(monkeypatch, return_content='{"ocr_text": "MOTHER MARY", "visual_summary_de": "Trailer"}')

    result = analyze_asset_visual(session, asset)
    assert result.visual_analysis_status == "done"


def test_vision_empty_when_openai_returns_empty_object(session: Session,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    asset = _make(session)
    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot",
                        lambda a: _captured(a))
    _mock_openai(monkeypatch, return_content="{}")

    result = analyze_asset_visual(session, asset)
    assert result.visual_analysis_status == "vision_empty"


def test_vision_empty_when_openai_returns_unparseable(session: Session,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    asset = _make(session)
    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot",
                        lambda a: _captured(a))
    _mock_openai(monkeypatch, return_content="not json at all")

    result = analyze_asset_visual(session, asset)
    assert result.visual_analysis_status == "vision_empty"


def test_vision_timeout_on_openai_timeout_exception(session: Session,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    asset = _make(session)
    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot",
                        lambda a: _captured(a))

    class APITimeoutError(Exception):
        pass

    _mock_openai(monkeypatch, raise_exc=APITimeoutError("read timed out"))

    result = analyze_asset_visual(session, asset)
    assert result.visual_analysis_status == "vision_timeout"


def test_image_unreachable_on_could_not_download(session: Session,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    asset = _make(session)
    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot",
                        lambda a: _captured(a))
    _mock_openai(monkeypatch, raise_exc=RuntimeError(
        "Error: Could not download image at https://r2.example/foo.jpg (403)"
    ))

    result = analyze_asset_visual(session, asset)
    assert result.visual_analysis_status == "image_unreachable"


def test_vision_error_on_unknown_provider_failure(session: Session,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    asset = _make(session)
    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot",
                        lambda a: _captured(a))
    _mock_openai(monkeypatch, raise_exc=RuntimeError("unexpected provider 503"))

    result = analyze_asset_visual(session, asset)
    assert result.visual_analysis_status == "vision_error"


def test_capture_fetch_failed_dominates_over_vision_done(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Edge-Case #5: even if vision claims 'done' (e.g. heuristic fallback
    on a stale image), a capture-stage fetch_failed must win."""
    asset = _make(session)
    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot",
                        lambda a: VisualEvidenceResult(status="fetch_failed"))
    _mock_openai(monkeypatch, return_content='{"ocr_text": "x", "visual_summary_de": "y"}')

    result = analyze_asset_visual(session, asset)
    assert result.visual_analysis_status == "fetch_failed"


def test_capture_fetch_failed_dominates_over_vision_text_fallback(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Edge-Case #5: previously, when vision exception'd to text_fallback
    AND capture had fetch_failed, the text_fallback hid the capture failure.
    Now capture wins."""
    asset = _make(session)
    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot",
                        lambda a: VisualEvidenceResult(status="fetch_failed"))
    _mock_openai(monkeypatch, raise_exc=RuntimeError("rate limit exceeded"))

    result = analyze_asset_visual(session, asset)
    assert result.visual_analysis_status == "fetch_failed"


def test_no_source_status_unchanged(session: Session,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    asset = _make(session)
    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot",
                        lambda a: VisualEvidenceResult(status="no_source"))
    _mock_openai(monkeypatch, return_content="{}")

    result = analyze_asset_visual(session, asset)
    assert result.visual_analysis_status == "no_source"


def test_whitelist_guard_collapses_hallucinated_status_from_heuristic_path(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Edge-Case #7 defense-in-depth: a hallucinated status that arrives via
    the data dict (e.g. if _heuristic_analysis ever returns something exotic
    in the future) must collapse to text_fallback. We simulate this by
    monkey-patching _heuristic_analysis to return a bogus status, then
    triggering the no-image-url branch which feeds data through unchanged."""
    asset = _make(session)
    asset.thumbnail_url = None  # force no image_url -> heuristic-only path
    session.add(asset)
    session.commit()
    session.refresh(asset)

    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot",
                        lambda a: VisualEvidenceResult(status="captured",
                                                       evidence_url=None))
    monkeypatch.setattr(
        visual_analysis,
        "_heuristic_analysis",
        lambda asset, post, title: {
            "visual_analysis_status": "broken_by_model",
            "ocr_text": "x",
        },
    )

    result = analyze_asset_visual(session, asset)
    assert result.visual_analysis_status == "text_fallback"


def test_text_fallback_when_no_api_key(session: Session,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    asset = _make(session)
    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot",
                        lambda a: _captured(a))
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)

    result = analyze_asset_visual(session, asset)
    assert result.visual_analysis_status == "text_fallback"
