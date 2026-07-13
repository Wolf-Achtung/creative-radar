"""Platin 3 (2026-07-13) — Eval-Harness für Brief-Prompts.

Verifiziert ``run_prompt_eval`` (Service-Ebene: EIN gemeinsamer
Aggregations-/User-Prompt-Aufbau, ZWEI Opus-Aufrufe mit unterschiedlichem
System-Prompt, kein Persistieren in ``InsightReport``) sowie
``POST /api/admin/insights/eval-prompt`` (Routing/Validierung: unbekannter
Pair, deaktivierter Pair, zu kurzer Body, Erfolgsfall).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import InsightReport as InsightReportRow
from app.services import insight_engine as engine_module
from app.services.insight_engine import run_prompt_eval
from app.schemas.insights import (
    ChannelStats,
    LLMReport,
    PairAggregation,
    PlatformAggregation,
    RankedPost,
    TitleCoverage,
)


def _engine_for_path(path: str):
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_prompt_eval_", suffix=".db")
    os.close(fd)
    engine = _engine_for_path(path)
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        try:
            os.unlink(path)
        except OSError:
            pass


def _empty_title_coverage() -> TitleCoverage:
    return TitleCoverage.model_construct(
        titles_in_both_markets=[], de_only_titles=[], us_only_titles=[],
        de_assets_with_title=0, de_assets_total=0, us_assets_with_title=0,
        us_assets_total=0, uk_only_titles=[], uk_assets_with_title=0,
        uk_assets_total=0, overall_coverage_pct=0.0,
    )


def _ranked_post(asset_id: str, caption: str) -> RankedPost:
    return RankedPost.model_construct(
        post_url=f"https://example.com/{asset_id}",
        caption_excerpt=caption,
        platform="tiktok",
        published_at=None,
        duration_seconds=None,
        views=0, likes=0, comments=0, saves=0, shares=0,
        engagement_sum=0, activation_rate=0.0,
        title_local=None, title_original=None, franchise=None,
        thumbnail_url=None, content_type=None, asset_id=asset_id,
    )


def _channel_with_posts(handle: str, market: str, posts: list[RankedPost]) -> ChannelStats:
    return ChannelStats.model_construct(
        handle=handle, market=market, channel_id=f"{handle}-id", channel_found=True,
        posts_count=len(posts), assets_count=len(posts), coverage_pct=0.0,
        top_hashtags=[], avg_caption_length=0.0, avg_duration_seconds=None,
        duration_buckets={}, top_posts=[], avg_engagement=0.0,
        avg_activation_rate=0.0, historical_top_posts=[], ranked_posts=posts,
    )


def _build_aggregation(pair_key: str, iso_year: int, iso_week: int) -> PairAggregation:
    de_posts = [_ranked_post("asset_a", "Beispielcaption A")]
    de_channel = _channel_with_posts(pair_key, "DE", de_posts)
    platform_agg = PlatformAggregation.model_construct(
        platform="tiktok", de_channel=de_channel, us_channel=None, uk_channel=None,
        cross_market_matches=[], title_coverage=_empty_title_coverage(), notes=[],
    )
    now = datetime(iso_year, 5, 17, tzinfo=timezone.utc)
    return PairAggregation.model_construct(
        pair_key=pair_key, pair_label=f"{pair_key} test", platform="tiktok",
        window_days=30, window_start=now - timedelta(days=30), window_end=now,
        iso_week=iso_week, iso_year=iso_year,
        de_channel=de_channel, us_channel=None, uk_channel=None,
        cross_market_matches=[], title_coverage=_empty_title_coverage(),
        notes=[], per_platform=[platform_agg],
    )


def _tool_use_message(headline: str) -> SimpleNamespace:
    body = LLMReport.model_validate({
        "headline": headline,
        "tldr": f"{headline}-tldr",
        "trends": [], "actions": [],
        "cross_market_insight": {"de_vs_us": "n/a", "transfer_opportunity": "n/a"},
        "risks": [], "data_caveats": [],
    }).model_dump(mode="json")
    tool_use_block = SimpleNamespace(type="tool_use", name="submit_weekly_brief", input=body)
    usage = SimpleNamespace(input_tokens=4000, output_tokens=1500)
    return SimpleNamespace(content=[tool_use_block], usage=usage, stop_reason="tool_use")


def _patch_engine(monkeypatch, db, *, pair_key: str = "netflix"):
    """Mockt Anthropic-Schicht + aggregate_pair. Der Anthropic-Mock gibt
    je nach ``system``-Prompt eine andere Headline zurueck, damit der Test
    beweisen kann, dass variant_a und variant_b WIRKLICH unterschiedliche
    System-Prompts an den Call durchreichen (nicht nur behaupten)."""
    def fake_call(*, model, system, user_message, tool_name, tool_description, input_schema, max_tokens):
        headline = "baseline-headline" if system == engine_module.SYSTEM_PROMPT else "candidate-headline"
        return _tool_use_message(headline)

    monkeypatch.setattr(engine_module, "messages_create_strict_json", MagicMock(side_effect=fake_call))
    monkeypatch.setattr(engine_module, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(engine_module, "record_anthropic_call", lambda *a, **k: None)

    def fake_aggregate_pair(session, pk, *, window_days=30, now=None):
        return _build_aggregation(pk, 2026, 20)

    monkeypatch.setattr(engine_module, "aggregate_pair", fake_aggregate_pair)
    monkeypatch.setitem(engine_module.PAIRS, pair_key, {
        "display_name": pair_key, "markets": ["DE", "US"], "label": f"{pair_key} test",
        "platforms": {"tiktok": [{"handle": pair_key, "market": "DE"}]}, "platform": "tiktok",
        "channels": [], "enabled": True,
    })


def test_run_prompt_eval_uses_distinct_system_prompts_and_does_not_persist(monkeypatch, db):
    _patch_engine(monkeypatch, db)
    with Session(db) as session:
        result = run_prompt_eval(
            session, "netflix",
            variant_b_system_prompt="Du bist ein Test-Kandidat-Prompt mit genug Zeichen fuer die Validierung.",
        )

    assert result["variant_a"]["status"] == "ok"
    assert result["variant_b"]["status"] == "ok"
    assert result["variant_a"]["headline"] == "baseline-headline"
    assert result["variant_b"]["headline"] == "candidate-headline"
    assert result["variant_a"]["cost_usd"] is not None
    assert result["variant_b"]["cost_usd"] is not None

    # Kein Ergebnis landet im InsightReport-Cache — der Eval-Lauf ist rein
    # lesend/experimentell, unabhaengig vom regulaeren Brief-Pfad.
    with Session(db) as session:
        rows = session.exec(select(InsightReportRow)).all()
    assert rows == []


def test_run_prompt_eval_disabled_pair_raises_key_error_free_path(monkeypatch, db):
    # Service-Ebene prueft Pair-Enabled NICHT selbst (das macht die Route) —
    # aggregate_pair ist gemockt und liefe fuer jeden pair_key durch. Dieser
    # Test dokumentiert bewusst, dass die Enabled-Pruefung Routen-Sache ist.
    _patch_engine(monkeypatch, db, pair_key="disabled-pair")
    monkeypatch.setitem(engine_module.PAIRS, "disabled-pair", {
        **engine_module.PAIRS["disabled-pair"], "enabled": False,
    })
    with Session(db) as session:
        result = run_prompt_eval(
            session, "disabled-pair",
            variant_b_system_prompt="Ein ausreichend langer Kandidaten-Prompt-Text fuer die Validierung.",
        )
    assert result["variant_a"]["status"] == "ok"


@pytest.fixture
def client(db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)

    def _override_session():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_eval_prompt_route_unknown_pair_404(client):
    resp = client.post(
        "/api/admin/insights/eval-prompt?pair=does-not-exist",
        json={"variant_b_system_prompt": "x" * 60},
    )
    assert resp.status_code == 404


def test_eval_prompt_route_disabled_pair_409(client, monkeypatch):
    monkeypatch.setitem(engine_module.PAIRS, "netflix", {
        **engine_module.PAIRS["netflix"], "enabled": False,
    })
    resp = client.post(
        "/api/admin/insights/eval-prompt?pair=netflix",
        json={"variant_b_system_prompt": "x" * 60},
    )
    assert resp.status_code == 409


def test_eval_prompt_route_body_too_short_422(client):
    resp = client.post(
        "/api/admin/insights/eval-prompt?pair=netflix",
        json={"variant_b_system_prompt": "too short"},
    )
    assert resp.status_code == 422


def test_eval_prompt_route_success(client, monkeypatch, db):
    _patch_engine(monkeypatch, db)

    import app.api.admin as admin_module

    captured = {}

    def fake_run_prompt_eval(session, pair_key, *, variant_b_system_prompt, window_days=30, now=None):
        captured["pair_key"] = pair_key
        captured["now"] = now
        captured["variant_b_system_prompt"] = variant_b_system_prompt
        return {
            "pair_key": pair_key, "iso_year": 2026, "iso_week": 20,
            "variant_a": {"status": "ok", "headline": "a", "tldr": "a-tldr", "cost_usd": 0.15},
            "variant_b": {"status": "ok", "headline": "b", "tldr": "b-tldr", "cost_usd": 0.15},
        }

    monkeypatch.setattr(admin_module, "run_prompt_eval", fake_run_prompt_eval)

    resp = client.post(
        "/api/admin/insights/eval-prompt?pair=netflix",
        json={"variant_b_system_prompt": "x" * 60},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["variant_a"]["headline"] == "a"
    assert body["variant_b"]["headline"] == "b"
    assert captured["pair_key"] == "netflix"
    # target_week default 'completed' -> now = utcnow - 1 Tag, NICHT heute.
    assert captured["now"].date() == (datetime.now(timezone.utc) - timedelta(days=1)).date()
