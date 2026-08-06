"""Tests for the cross-platform AI analysis pipeline (Sprint 5.3.1
Mini-Run 2).

Strategy: mock at the wrapper boundary
(``post_analyzer.messages_create_text`` / ``messages_create_vision``)
rather than at httpx — this exercises the orchestration logic
(extract -> vision -> haiku -> sonnet -> merge -> persist) without
depending on the SDK's request shape. Errors are simulated by having
the patched mock raise the wrapper's typed exceptions
(AnthropicAuthError / AnthropicRateLimitError / AnthropicAPIError).

Cost-logging is exercised via the real ``record_anthropic_call`` (the
test patches the module-level ``engine`` to point at a test SQLite,
matching the cost_log test pattern from Phase 4).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.models.entities import (
    AcquisitionStrategy,
    Asset,
    Channel,
    CostLog,
    Market,
    Post,
    Priority,
    QualityTier,
)
from app.services import cost_log as cost_log_module
from app.services import post_analyzer
from app.services.anthropic_client import (
    AnthropicAPIError,
    AnthropicAuthError,
    AnthropicRateLimitError,
)


# ---------- Fixtures --------------------------------------------------


def _fake_message(text: str, *, input_tokens: int = 100, output_tokens: int = 30) -> SimpleNamespace:
    """Duck-type a Messages API response: ``content[0].text`` + ``usage``."""
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    # Route record_anthropic_call's _persist into the same in-memory DB.
    monkeypatch.setattr(cost_log_module, "engine", engine)
    # Stable model strings for the assertions; callers may still override.
    monkeypatch.setattr(settings, "anthropic_haiku_model", "claude-haiku-4-5-20251001", raising=False)
    monkeypatch.setattr(settings, "anthropic_sonnet_model", "claude-sonnet-4-6", raising=False)
    monkeypatch.setattr(settings, "anthropic_haiku_input_per_1k_usd", 0.001, raising=False)
    monkeypatch.setattr(settings, "anthropic_haiku_output_per_1k_usd", 0.005, raising=False)
    monkeypatch.setattr(settings, "anthropic_sonnet_input_per_1k_usd", 0.003, raising=False)
    monkeypatch.setattr(settings, "anthropic_sonnet_output_per_1k_usd", 0.015, raising=False)
    return Session(engine)


def _make_channel(session: Session, *, platform: str = "youtube") -> Channel:
    ch = Channel(
        name="TestChannel",
        platform=platform,
        url="https://example.test/ch",
        market=Market.INT,
        priority=Priority.B,
        quality_tier=QualityTier.P1,
        acquisition_strategy=(
            AcquisitionStrategy.YOUTUBE_API if platform == "youtube" else AcquisitionStrategy.APIFY
        ),
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


def _make_post(
    session: Session,
    channel: Channel,
    *,
    platform: str = "youtube",
    caption: str = "Official trailer is here. STRANGER THINGS Season 5.",
    raw_payload: dict | None = None,
) -> Post:
    post = Post(
        channel_id=channel.id,
        platform=platform,
        post_url=f"https://example.test/p/{uuid4()}",
        caption=caption,
        raw_payload=raw_payload or {},
        published_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    return post


# Realistic raw_payload skeletons per platform — the minimum fields
# extract_asset_url needs.
YOUTUBE_PAYLOAD = {
    "snippet": {
        "thumbnails": {
            "maxres": {"url": "https://i.ytimg.com/vi/abc/maxresdefault.jpg"},
            "high": {"url": "https://i.ytimg.com/vi/abc/hqdefault.jpg"},
        }
    }
}
INSTAGRAM_PAYLOAD = {
    "displayUrl": "https://instagram.fcdn.net/v/t51.../sample.jpg",
    "caption": "Some IG caption",
}
TIKTOK_PAYLOAD = {
    "videoMeta": {"coverUrl": "https://p16-sign.tiktokcdn-us.com/cover.jpg"},
}


# ---------- extract_asset_url (DIAG-5) --------------------------------


def test_extract_asset_url_youtube_picks_maxres():
    p = SimpleNamespace(platform="youtube", raw_payload=YOUTUBE_PAYLOAD)
    assert post_analyzer.extract_asset_url(p) == YOUTUBE_PAYLOAD["snippet"]["thumbnails"]["maxres"]["url"]


def test_extract_asset_url_instagram_picks_displayurl():
    p = SimpleNamespace(platform="instagram", raw_payload=INSTAGRAM_PAYLOAD)
    assert post_analyzer.extract_asset_url(p) == INSTAGRAM_PAYLOAD["displayUrl"]


def test_extract_asset_url_tiktok_picks_coverurl():
    p = SimpleNamespace(platform="tiktok", raw_payload=TIKTOK_PAYLOAD)
    assert post_analyzer.extract_asset_url(p) == TIKTOK_PAYLOAD["videoMeta"]["coverUrl"]


def test_extract_asset_url_unknown_platform_returns_none():
    p = SimpleNamespace(platform="vimeo", raw_payload={"foo": "bar"})
    assert post_analyzer.extract_asset_url(p) is None


def test_extract_asset_url_empty_payload_returns_none():
    p = SimpleNamespace(platform="youtube", raw_payload={})
    assert post_analyzer.extract_asset_url(p) is None


# ---------- Happy path: full analyze_post -----------------------------


def _patch_calls(vision_text="A wide nighttime composition with three teens silhouetted against a streetlamp.",
                 haiku_json='{"format":"trailer","tone":"suspenseful","confidence":0.9}',
                 sonnet_json='{"purpose":"release_week","lifecycle_stage":"launch","confidence":0.8}'):
    """Returns the patch tuple to apply in a `with` block. Order matters
    only for the error-injection variants below."""
    return (
        patch.object(post_analyzer, "messages_create_vision",
                     return_value=_fake_message(vision_text, input_tokens=1500, output_tokens=80)),
        patch.object(post_analyzer, "messages_create_text",
                     side_effect=[
                         _fake_message(haiku_json, input_tokens=400, output_tokens=20),
                         _fake_message(sonnet_json, input_tokens=600, output_tokens=40),
                     ]),
    )


def test_analyze_post_happy_path_youtube(session: Session):
    ch = _make_channel(session, platform="youtube")
    post = _make_post(session, ch, platform="youtube", raw_payload=YOUTUBE_PAYLOAD)

    p_vision, p_text = _patch_calls()
    with p_vision, p_text:
        result = post_analyzer.analyze_post(session, post)
    session.commit()

    assert result.status == "analyzed"
    assert result.asset_created is True
    assert result.calls == {"haiku": 1, "sonnet": 1, "sonnet_vision": 1}
    assert result.errors == []

    # Post.analysis populated with the merged classification + mean
    # confidence (haiku=0.9, sonnet=0.8 -> 0.85 from _patch_calls defaults).
    session.refresh(post)
    assert post.last_analyzed_at is not None
    assert post.analysis["format"] == "trailer"
    assert post.analysis["tone"] == "suspenseful"
    assert post.analysis["purpose"] == "release_week"
    assert post.analysis["lifecycle_stage"] == "launch"
    assert post.analysis["confidence"] == pytest.approx(0.85)
    assert post.analysis["haiku_model"] == "claude-haiku-4-5-20251001"
    assert post.analysis["sonnet_model"] == "claude-sonnet-4-6"

    # Asset row written with the four 5.3.1 vision fields.
    asset = session.exec(select(Asset).where(Asset.post_id == post.id)).first()
    assert asset is not None
    assert asset.asset_url == YOUTUBE_PAYLOAD["snippet"]["thumbnails"]["maxres"]["url"]
    assert asset.vision_description.startswith("A wide nighttime")
    assert asset.vision_model == "claude-sonnet-4-6"
    assert asset.analyzed_at is not None


def test_analyze_post_happy_path_instagram(session: Session):
    ch = _make_channel(session, platform="instagram")
    post = _make_post(session, ch, platform="instagram",
                      caption="Wenn dein Lieblingscharakter zum dritten Mal stirbt 💀😂",
                      raw_payload=INSTAGRAM_PAYLOAD)

    p_vision, p_text = _patch_calls(
        haiku_json='{"format":"clip","tone":"humorous","confidence":0.7}',
        sonnet_json='{"purpose":"audience_engagement","lifecycle_stage":"post_launch","confidence":0.6}',
    )
    with p_vision, p_text:
        result = post_analyzer.analyze_post(session, post)
    session.commit()

    assert result.status == "analyzed"
    session.refresh(post)
    assert post.analysis["tone"] == "humorous"
    assert post.analysis["format"] == "clip"


# ---------- Cost-log accounting --------------------------------------


def test_three_calls_log_three_buckets(session: Session):
    """One Haiku, one Sonnet, one Sonnet-Vision call -> three CostLog
    rows with provider names anthropic_haiku / anthropic_sonnet /
    anthropic_sonnet_vision."""
    ch = _make_channel(session, platform="youtube")
    post = _make_post(session, ch, raw_payload=YOUTUBE_PAYLOAD)

    p_vision, p_text = _patch_calls()
    with p_vision, p_text:
        post_analyzer.analyze_post(session, post)

    rows = session.exec(select(CostLog).order_by(CostLog.timestamp.asc())).all()
    providers = sorted(r.provider for r in rows)
    assert providers == ["anthropic_haiku", "anthropic_sonnet", "anthropic_sonnet_vision"]

    # Spot-check pricing math: 1500 in + 80 out @ Sonnet rates
    # = 1.5 * 0.003 + 0.08 * 0.015 = 0.0045 + 0.0012 = 0.0057 USD
    # = 0.57 cents -> rounded to 1 cent.
    vision_row = next(r for r in rows if r.provider == "anthropic_sonnet_vision")
    assert vision_row.cost_usd_cents == 1
    assert vision_row.operation == "vision_describe"
    assert vision_row.cost_meta["model"] == "claude-sonnet-4-6"
    assert vision_row.cost_meta["input_tokens"] == 1500
    assert vision_row.cost_meta["output_tokens"] == 80


# ---------- Error paths ----------------------------------------------


def test_auth_error_propagates_up(session: Session):
    """Auth failures are non-recoverable — they must raise so the
    endpoint maps to 401, not be silently absorbed into the per-post
    errors list."""
    ch = _make_channel(session, platform="youtube")
    post = _make_post(session, ch, raw_payload=YOUTUBE_PAYLOAD)

    with patch.object(post_analyzer, "messages_create_vision",
                      side_effect=AnthropicAuthError("invalid api key")):
        with pytest.raises(AnthropicAuthError):
            post_analyzer.analyze_post(session, post)


def test_rate_limit_after_retries_skips_post(session: Session):
    """RateLimit (raised by wrapper after its 3 internal retries) on a
    classifier call -> result.status == 'error', error appended."""
    ch = _make_channel(session, platform="youtube")
    post = _make_post(session, ch, raw_payload=YOUTUBE_PAYLOAD)

    with patch.object(post_analyzer, "messages_create_vision",
                      return_value=_fake_message("desc")), \
         patch.object(post_analyzer, "messages_create_text",
                      side_effect=AnthropicRateLimitError("rate limit after 3 retries")):
        result = post_analyzer.analyze_post(session, post)

    assert result.status == "error"
    assert any("haiku-rate-limit" in e for e in result.errors)


def test_invalid_json_retries_then_skips(session: Session):
    """Bad JSON from Haiku -> one retry; if retry also bad, skip with
    haiku-invalid-json error. The orchestrator should NOT crash."""
    ch = _make_channel(session, platform="youtube")
    post = _make_post(session, ch, raw_payload=YOUTUBE_PAYLOAD)

    with patch.object(post_analyzer, "messages_create_vision",
                      return_value=_fake_message("desc")), \
         patch.object(post_analyzer, "messages_create_text",
                      side_effect=[
                          _fake_message("not json at all"),
                          _fake_message("still not json"),
                      ]):
        result = post_analyzer.analyze_post(session, post)

    assert result.status == "error"
    assert any("haiku-invalid-json" in e for e in result.errors)
    # Both Haiku attempts should have been counted.
    assert result.calls["haiku"] == 2


def test_invalid_json_first_then_valid_succeeds(session: Session):
    """Bad JSON on first call, valid on retry -> classifier still
    yields a result, post gets analyzed normally."""
    ch = _make_channel(session, platform="youtube")
    post = _make_post(session, ch, raw_payload=YOUTUBE_PAYLOAD)

    with patch.object(post_analyzer, "messages_create_vision",
                      return_value=_fake_message("desc")), \
         patch.object(post_analyzer, "messages_create_text",
                      side_effect=[
                          _fake_message("oops, no JSON here"),
                          _fake_message('{"format":"trailer","tone":"suspenseful","confidence":0.9}'),
                          _fake_message('{"purpose":"release_week","lifecycle_stage":"launch","confidence":0.8}'),
                      ]):
        result = post_analyzer.analyze_post(session, post)
    session.commit()

    assert result.status == "analyzed"
    assert result.calls["haiku"] == 2  # one fail + one retry
    assert result.calls["sonnet"] == 1


def test_vision_404_continues_to_classification(session: Session):
    """Vision URL unreachable (Anthropic returns API error for image
    fetch) -> vision_description=None, but Haiku + Sonnet still run
    and post still gets analyzed."""
    ch = _make_channel(session, platform="youtube")
    post = _make_post(session, ch, raw_payload=YOUTUBE_PAYLOAD)

    with patch.object(post_analyzer, "messages_create_vision",
                      side_effect=AnthropicAPIError("could not fetch image: 404")), \
         patch.object(post_analyzer, "messages_create_text",
                      side_effect=[
                          _fake_message('{"format":"trailer","tone":"suspenseful","confidence":0.9}'),
                          _fake_message('{"purpose":"release_week","lifecycle_stage":"launch","confidence":0.8}'),
                      ]):
        result = post_analyzer.analyze_post(session, post)
    session.commit()

    assert result.status == "analyzed"
    assert any("vision-api-error" in e for e in result.errors)
    # Asset row created with NULL vision_description (graceful degrade).
    asset = session.exec(select(Asset).where(Asset.post_id == post.id)).first()
    assert asset is not None
    assert asset.vision_description is None
    assert asset.asset_url == YOUTUBE_PAYLOAD["snippet"]["thumbnails"]["maxres"]["url"]


# ---------- Confidence merge -----------------------------------------


def test_confidence_is_mean_of_self_reported_scores(session: Session):
    """PostAnalysis.confidence must be the arithmetic mean of haiku +
    sonnet self-reports — no fake constants. Picks lopsided values
    (0.95 + 0.55) so a fake mid-range default would be obviously wrong."""
    ch = _make_channel(session, platform="youtube")
    post = _make_post(session, ch, raw_payload=YOUTUBE_PAYLOAD)

    with patch.object(post_analyzer, "messages_create_vision",
                      return_value=_fake_message("desc")), \
         patch.object(post_analyzer, "messages_create_text",
                      side_effect=[
                          _fake_message('{"format":"trailer","tone":"suspenseful","confidence":0.95}'),
                          _fake_message('{"purpose":"release_week","lifecycle_stage":"launch","confidence":0.55}'),
                      ]):
        post_analyzer.analyze_post(session, post)
    session.commit()
    session.refresh(post)
    assert post.analysis["confidence"] == pytest.approx(0.75)


def test_confidence_falls_back_when_model_omits_field(session: Session):
    """Defensive: if a model ignores the schema and omits confidence
    on one side, the mean is taken over whatever values are present.
    If neither side reports, the result is 0.0 — transparent ignorance,
    not a fake mid-range constant."""
    ch = _make_channel(session, platform="youtube")
    post = _make_post(session, ch, raw_payload=YOUTUBE_PAYLOAD)

    with patch.object(post_analyzer, "messages_create_vision",
                      return_value=_fake_message("desc")), \
         patch.object(post_analyzer, "messages_create_text",
                      side_effect=[
                          # Haiku omits confidence; sonnet reports 0.8
                          _fake_message('{"format":"trailer","tone":"suspenseful"}'),
                          _fake_message('{"purpose":"release_week","lifecycle_stage":"launch","confidence":0.8}'),
                      ]):
        post_analyzer.analyze_post(session, post)
    session.commit()
    session.refresh(post)
    assert post.analysis["confidence"] == pytest.approx(0.8)


# ---------- Idempotency ----------------------------------------------


def test_existing_vision_asset_skips_vision_call(session: Session):
    """Pre-existing Asset with non-null vision_description for the
    same (post_id, asset_url) -> vision call is skipped, only the
    two classifiers run. asset_created=False because we reused the
    existing row."""
    ch = _make_channel(session, platform="youtube")
    post = _make_post(session, ch, raw_payload=YOUTUBE_PAYLOAD)

    asset_url = YOUTUBE_PAYLOAD["snippet"]["thumbnails"]["maxres"]["url"]
    pre = Asset(
        post_id=post.id,
        asset_url=asset_url,
        vision_description="previously generated",
        vision_model="claude-sonnet-4-6",
        analyzed_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    session.add(pre)
    session.commit()

    with patch.object(post_analyzer, "messages_create_vision") as vision_mock, \
         patch.object(post_analyzer, "messages_create_text",
                      side_effect=[
                          _fake_message('{"format":"trailer","tone":"suspenseful","confidence":0.9}'),
                          _fake_message('{"purpose":"release_week","lifecycle_stage":"launch","confidence":0.8}'),
                      ]):
        result = post_analyzer.analyze_post(session, post)

    vision_mock.assert_not_called()
    assert result.calls["sonnet_vision"] == 0
    assert result.calls["haiku"] == 1
    assert result.calls["sonnet"] == 1


def test_post_without_image_skips_vision_only(session: Session):
    """raw_payload without a discoverable image URL -> no vision call,
    no Asset row, but classifiers still run and post gets analyzed."""
    ch = _make_channel(session, platform="youtube")
    post = _make_post(session, ch, raw_payload={})  # no snippet/thumbnails

    with patch.object(post_analyzer, "messages_create_vision") as vision_mock, \
         patch.object(post_analyzer, "messages_create_text",
                      side_effect=[
                          _fake_message('{"format":"trailer","tone":"suspenseful","confidence":0.9}'),
                          _fake_message('{"purpose":"release_week","lifecycle_stage":"launch","confidence":0.8}'),
                      ]):
        result = post_analyzer.analyze_post(session, post)
    session.commit()

    vision_mock.assert_not_called()
    assert result.status == "analyzed"
    assert result.calls["sonnet_vision"] == 0
    asset_row = session.exec(select(Asset).where(Asset.post_id == post.id)).first()
    assert asset_row is None


def test_skip_vision_runs_classifiers_only(session: Session):
    """``skip_vision=True`` laesst den Vision-Call aus, auch wenn ein
    Bild im raw_payload steckt. Die vier PostAnalysis-Felder kommen aus
    der Caption und muessen unveraendert entstehen; nur die Asset-Zeile
    mit ``vision_description`` faellt weg.

    Das ist der Pfad, den die Cron-Stage nutzt: der Vision-Call ist ~72 %
    der Kosten pro Post, liefert aber nichts, was der
    Empfehlungs-Baustein im insight_engine liest.
    """
    ch = _make_channel(session, platform="youtube")
    post = _make_post(session, ch, raw_payload=YOUTUBE_PAYLOAD)

    with patch.object(post_analyzer, "messages_create_vision") as vision_mock, \
         patch.object(post_analyzer, "messages_create_text",
                      side_effect=[
                          _fake_message('{"format":"trailer","tone":"suspenseful","confidence":0.9}'),
                          _fake_message('{"purpose":"release_week","lifecycle_stage":"launch","confidence":0.8}'),
                      ]):
        result = post_analyzer.analyze_post(session, post, skip_vision=True)
    session.commit()

    vision_mock.assert_not_called()
    assert result.status == "analyzed"
    assert result.calls == {"haiku": 1, "sonnet": 1, "sonnet_vision": 0}
    assert result.asset_created is False

    # Die vier Klassifikationsfelder sind vollstaendig — das ist der Punkt.
    session.refresh(post)
    assert post.analysis["format"] == "trailer"
    assert post.analysis["tone"] == "suspenseful"
    assert post.analysis["purpose"] == "release_week"
    assert post.analysis["lifecycle_stage"] == "launch"
    assert post.last_analyzed_at is not None

    # Keine Asset-Zeile, weil der Vision-Pfad uebersprungen wurde.
    assert session.exec(select(Asset).where(Asset.post_id == post.id)).first() is None
