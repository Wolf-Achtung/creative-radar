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
    Title,
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
    # W4 convergence: 'analyzed' is the canonical success status set by the
    # in-repo pipeline. 'done' remains tolerated by the selector and counter
    # for 14d, but new writes use 'analyzed'.
    assert result.visual_analysis_status == "analyzed"


def test_vision_no_longer_assigns_title_id(session: Session,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """Variante D / X: Vision weist keine title_id mehr zu. Selbst wenn ein
    aktiver Titel als Substring im OCR/Caption steht, bleibt das Asset
    title_id=None — die Zuweisung übernimmt der nachgelagerte Rematch über
    find_best_title_match (D-Logik), nicht der frühere ungeschützte Matcher."""
    title = Title(title_original="Solo", franchise="Solo", active=True)
    session.add(title)
    session.commit()

    asset = _make(session)
    asset.ocr_text = "Solo official trailer"
    session.add(asset)
    session.commit()

    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot", lambda a: _captured(a))
    _mock_openai(monkeypatch, return_content='{"ocr_text": "Solo", "visual_summary_de": "Trailer"}')

    result = analyze_asset_visual(session, asset)

    assert result.title_id is None


def _sent_image_url(fake_client: MagicMock) -> str:
    """Extract the image_url value passed to chat.completions.create()."""
    kwargs = fake_client.chat.completions.create.call_args.kwargs
    user_content = kwargs["messages"][1]["content"]
    block = next(c for c in user_content if c.get("type") == "image_url")
    return block["image_url"]["url"]


def test_vision_inlines_evidence_as_base64_data_url(session: Session,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """H1 fix: a stored evidence object key is read from storage and sent to
    OpenAI as a base64 data URL — not a (possibly unreachable) presigned URL."""
    import base64

    asset = _make(session)
    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot", lambda a: _captured(a))

    fake_storage = MagicMock()
    fake_storage.read.return_value = b"\xff\xd8\xffJPEGBYTES"
    monkeypatch.setattr(visual_analysis, "get_storage", lambda: fake_storage)

    fake = _mock_openai(monkeypatch, return_content='{"ocr_text": "X"}')
    result = analyze_asset_visual(session, asset)

    assert result.visual_analysis_status == "analyzed"
    sent = _sent_image_url(fake)
    expected_b64 = base64.b64encode(b"\xff\xd8\xffJPEGBYTES").decode("ascii")
    assert sent == f"data:image/jpeg;base64,{expected_b64}"
    fake_storage.read.assert_called_once_with(f"evidence/{asset.id}_test.jpg")


def test_vision_falls_back_to_url_when_storage_read_fails(session: Session,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """If reading the evidence bytes fails, the call must fall back to the
    resolved URL instead of regressing the whole path."""
    monkeypatch.setattr(settings, "storage_backend", "local", raising=False)
    asset = _make(session)
    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot", lambda a: _captured(a))

    fake_storage = MagicMock()
    fake_storage.read.side_effect = RuntimeError("object gone")
    monkeypatch.setattr(visual_analysis, "get_storage", lambda: fake_storage)

    fake = _mock_openai(monkeypatch, return_content='{"ocr_text": "X"}')
    result = analyze_asset_visual(session, asset)

    assert result.visual_analysis_status == "analyzed"
    sent = _sent_image_url(fake)
    assert not sent.startswith("data:")
    assert sent == f"/storage/evidence/{asset.id}_test.jpg"


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


def test_reuses_ingest_time_evidence_without_refetching(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Follow-up to Incident 2026-07-13: Sprint 5.3.6
    (asset_screenshot_persistence.py) already captures evidence at INGEST
    time -- before the Asset row is even committed -- and stores it
    permanently in our own S3/R2 bucket. Vision must reuse that secured
    copy instead of discarding it and re-fetching from the original
    (possibly since-expired) social CDN URL. This is exactly why the
    Vision-Backlog run 41a80fc1 hit 200/200 fetch_failed: every one of
    those assets already had ingest-time evidence, thrown away."""
    asset = _make(session)
    asset.visual_evidence_status = "captured"
    asset.visual_evidence_url = "evidence/already-secured.jpg"
    asset.visual_source_url = "https://cdn.example/original.jpg"
    session.add(asset)
    session.commit()

    capture_mock = MagicMock()
    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot", capture_mock)
    _mock_openai(monkeypatch, return_content='{"ocr_text": "x", "visual_summary_de": "y"}')

    result = analyze_asset_visual(session, asset)

    capture_mock.assert_not_called()
    assert result.visual_evidence_url == "evidence/already-secured.jpg"
    assert result.visual_analysis_status == "analyzed"
    # Re-Audit-Folgefund 2026-07-13: captured_at must NOT silently land as
    # None just because the reused evidence came from ingest time (which
    # records no timestamp of its own) -- that would misread as "capture
    # failed" to a future reader. asset.created_at is the closest available
    # approximation.
    assert result.visual_evidence_pack["captured_at"] == result.created_at.isoformat()


def test_still_live_fetches_when_no_ingest_time_evidence(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ingest-time evidence (asset predates Sprint 5.3.6, or ingest-time
    capture itself failed) -> the existing live-fetch path still runs,
    unchanged."""
    asset = _make(session)
    assert asset.visual_evidence_status is None  # never captured at ingest

    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot",
                        lambda a: _captured(a))
    _mock_openai(monkeypatch, return_content='{"ocr_text": "x", "visual_summary_de": "y"}')

    result = analyze_asset_visual(session, asset)

    assert result.visual_analysis_status == "analyzed"
    assert result.visual_evidence_url == f"evidence/{asset.id}_test.jpg"


def test_no_source_status_unchanged(session: Session,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    asset = _make(session)
    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot",
                        lambda a: VisualEvidenceResult(status="no_source"))
    _mock_openai(monkeypatch, return_content="{}")

    result = analyze_asset_visual(session, asset)
    assert result.visual_analysis_status == "no_source"


def test_openai_not_called_when_capture_already_fetch_failed(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Incident 2026-07-13: a Vision-Backlog run burned ~$3 on 200/200
    fetch_failed assets because the OpenAI call still fired with the exact
    same dead thumbnail_url/screenshot_url that capture_asset_screenshot()
    had just proven unreachable -- and the result was discarded anyway
    (fetch_failed always dominates, see test above). The fix must skip the
    OpenAI call entirely once capture already exhausted every candidate
    source, not just discard its result afterwards."""
    asset = _make(session)
    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot",
                        lambda a: VisualEvidenceResult(status="fetch_failed"))
    fake_client = _mock_openai(monkeypatch, return_content='{"ocr_text": "x"}')

    result = analyze_asset_visual(session, asset)

    assert result.visual_analysis_status == "fetch_failed"
    fake_client.chat.completions.create.assert_not_called()


def test_openai_not_called_when_capture_has_no_source(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset = _make(session)
    monkeypatch.setattr(visual_analysis, "capture_asset_screenshot",
                        lambda a: VisualEvidenceResult(status="no_source"))
    fake_client = _mock_openai(monkeypatch, return_content="{}")

    result = analyze_asset_visual(session, asset)

    assert result.visual_analysis_status == "no_source"
    fake_client.chat.completions.create.assert_not_called()


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


# --- W3 Hebel B: explicit no-analysis-possible phrase ---


def test_heuristic_returns_explicit_phrase_when_caption_and_ocr_empty(
    session: Session,
) -> None:
    """Hebel B: production sample 7 (24 Bilder) had no image, no caption,
    no OCR — yet the heuristic invented '...im internationalen Kontext'.
    Now: explicit, non-misleading phrase."""
    from app.services.visual_analysis import _heuristic_analysis

    asset = _make(session)
    asset.thumbnail_url = None
    asset.screenshot_url = None
    asset.visual_source_url = None
    asset.ocr_text = None

    # post.caption is None / empty
    post = MagicMock()
    post.caption = ""

    data = _heuristic_analysis(asset, post, None)
    assert data["visual_notes"] == (
        "Keine Inhaltsanalyse möglich — weder Bild noch Caption-Text vorhanden."
    )
    assert data["has_title_placement"] is False
    assert data["has_kinetic"] is False
    assert data["placement_strength"] == "none"
    assert data["visual_confidence_score"] == 0.0


def test_heuristic_uses_caption_when_present(session: Session) -> None:
    """Hebel B doesn't kick in when caption has content — old heuristic path."""
    from app.services.visual_analysis import _heuristic_analysis

    asset = _make(session)
    asset.ocr_text = None
    post = MagicMock()
    post.caption = "Watch the trailer for Mortal Kombat — only in theaters October 2026."

    data = _heuristic_analysis(asset, post, None)
    assert "Heuristische Analyse" in data["visual_notes"]
    assert "Keine Inhaltsanalyse möglich" not in data["visual_notes"]


def test_done_status_from_data_dict_is_still_accepted_for_compat(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W4 Mini-Run convergence: 14d toleranz fenster. If anything in the
    pipeline (e.g. an out-of-repo script that wrote 'done' before) feeds
    'done' through data['visual_analysis_status'], the whitelist guard
    must still let it through, not collapse to text_fallback."""
    asset = _make(session)
    asset.thumbnail_url = None
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
            "visual_analysis_status": "done",
            "ocr_text": "x",
        },
    )

    result = analyze_asset_visual(session, asset)
    assert result.visual_analysis_status == "done"


def test_analyzed_status_from_data_dict_is_accepted(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symmetric: an out-of-repo path that already writes 'analyzed' must
    also pass through the whitelist guard verbatim."""
    asset = _make(session)
    asset.thumbnail_url = None
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
            "visual_analysis_status": "analyzed",
            "ocr_text": "x",
        },
    )

    result = analyze_asset_visual(session, asset)
    assert result.visual_analysis_status == "analyzed"
