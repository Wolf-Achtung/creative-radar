"""Tests for the title brief generator (C3). aggregate_title and the Anthropic
call are mocked — no DB, no network. Exercises prompt build, the shared
_run_brief_llm path (happy/truncation), soft citation, dry-run, unknown title."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.services import insight_engine as engine_module
from app.services import title_brief as tb
from app.services.title_aggregation import (
    TitleAggregation,
    TitleChannelRef,
    TitleMarketStats,
    TitlePlatformStats,
    TitlePostRef,
    TitleWeekBucket,
)


def _post(url: str, eng: int) -> TitlePostRef:
    return TitlePostRef(
        post_url=url, platform="instagram", market="US",
        channel_handle="primevideo", channel_name="Prime Video", pair_keys=["primevideo"],
        engagement_sum=eng, likes=eng, comments=0, shares=0, saves=0, views=eng * 10,
        activation_rate=0.1, duration_seconds=30,
        published_at=None, detected_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        caption_excerpt="x",
    )


def _make_agg(*, weekly_buckets: int = 2) -> TitleAggregation:
    tid = uuid4()
    tp = [_post("https://ig/p/1", 230), _post("https://ig/p/2", 120)]
    weekly = [
        TitleWeekBucket(iso_year=2026, iso_week=22 + i, post_count=1, engagement_sum=100 + i)
        for i in range(weekly_buckets)
    ]
    return TitleAggregation(
        title_id=tid, title_original="Mortal Kombat", title_local=None,
        content_type="Film", franchise="Mortal Kombat", tmdb_id=123, aliases=[],
        release_date_de=None, release_date_us=None,
        window_days=30,
        window_start=datetime(2026, 5, 5, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 4, tzinfo=timezone.utc),
        first_post_at=datetime(2026, 5, 10, tzinfo=timezone.utc),
        last_post_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        total_posts_all_time=3, total_posts=2,
        total_engagement=350, total_views=3500, activation_rate_avg=0.1,
        platforms=[TitlePlatformStats(
            platform="instagram", post_count=2, engagement_sum=350, engagement_avg=175.0,
            views_sum=3500, views_avg=1750.0, activation_rate_avg=0.1, top_post=tp[0],
        )],
        markets=[TitleMarketStats(
            market="US", post_count=2, engagement_sum=350, engagement_avg=175.0,
            views_sum=3500, views_avg=1750.0, activation_rate_avg=0.1,
        )],
        channels=[TitleChannelRef(
            channel_handle="primevideo", channel_name="Prime Video", platform="instagram",
            market="US", pair_keys=["primevideo"], post_count=2, engagement_sum=350,
        )],
        pair_keys=["primevideo"], top_posts=tp, weekly=weekly,
    )


def _fake_msg(payload: dict, *, stop_reason: str = "tool_use",
              input_tokens: int = 4000, output_tokens: int = 1500):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", input=payload)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        stop_reason=stop_reason,
    )


def _valid_body(**over) -> dict:
    body = {
        "headline": "Mortal Kombat zieht auf IG US",
        "tldr": "IG US trägt, YT UK nur Views.",
        "plattform_vergleich": "IG US 230 Reaktionen, YT UK nur Aufrufe.",
        "data_caveats": ["dünne DE-Daten"],
    }
    body.update(over)
    return body


def _patch_llm(monkeypatch, msg):
    monkeypatch.setattr(tb, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(engine_module, "messages_create_strict_json", MagicMock(return_value=msg))
    monkeypatch.setattr(engine_module, "record_anthropic_call", MagicMock())


def test_build_title_user_prompt_has_sections():
    prompt = tb._build_title_user_prompt(_make_agg())
    assert "Mortal Kombat" in prompt
    assert "## Plattformen" in prompt
    assert "## Märkte" in prompt
    assert "## Top-Posts" in prompt
    assert "JSON-Datenanhang" in prompt
    # >=2 weekly buckets -> verlauf requested
    assert "Fülle ``verlauf``" in prompt


def test_build_title_user_prompt_verlauf_null_with_one_bucket():
    prompt = tb._build_title_user_prompt(_make_agg(weekly_buckets=1))
    assert "setze ``verlauf`` auf null" in prompt


def test_generate_title_brief_happy_path(monkeypatch):
    agg = _make_agg()
    monkeypatch.setattr(tb, "aggregate_title", lambda *a, **k: agg)
    _patch_llm(monkeypatch, _fake_msg(_valid_body()))

    report = tb.generate_title_brief(None, "Mortal Kombat",
                                     now=datetime(2026, 6, 4, tzinfo=timezone.utc))

    assert report is not None
    assert report.llm_output is not None
    assert report.llm_output.headline == "Mortal Kombat zieht auf IG US"
    assert report.title_id == str(agg.title_id)
    assert report.iso_week == datetime(2026, 6, 4, tzinfo=timezone.utc).isocalendar().week
    assert report.cost_usd_estimate is not None
    assert report.input_tokens == 4000


def test_generate_title_brief_unknown_returns_none(monkeypatch):
    monkeypatch.setattr(tb, "aggregate_title", lambda *a, **k: None)
    assert tb.generate_title_brief(None, "No Such Title") is None


def test_generate_title_brief_truncation_no_partial(monkeypatch):
    agg = _make_agg()
    monkeypatch.setattr(tb, "aggregate_title", lambda *a, **k: agg)
    # Even a parseable body is rejected when stop_reason=max_tokens (shared guard).
    _patch_llm(monkeypatch, _fake_msg(_valid_body(), stop_reason="max_tokens"))

    report = tb.generate_title_brief(None, "Mortal Kombat")
    assert report is not None
    assert report.llm_output is None  # no silent partial


def test_soft_citation_does_not_block(monkeypatch):
    agg = _make_agg()
    monkeypatch.setattr(tb, "aggregate_title", lambda *a, **k: agg)
    # cited_post_ids references a URL NOT in the allow-set -> soft mode keeps it.
    body = _valid_body(cited_post_ids=["https://ig/p/UNKNOWN"])
    _patch_llm(monkeypatch, _fake_msg(body))

    report = tb.generate_title_brief(None, "Mortal Kombat")
    assert report.llm_output is not None
    assert report.llm_output.cited_post_ids == ["https://ig/p/UNKNOWN"]


def test_dry_run_makes_no_llm_call(monkeypatch):
    agg = _make_agg()
    monkeypatch.setattr(tb, "aggregate_title", lambda *a, **k: agg)
    called = MagicMock(side_effect=AssertionError("LLM must not be called in dry-run"))
    monkeypatch.setattr(engine_module, "messages_create_strict_json", called)
    monkeypatch.setattr(tb, "is_anthropic_configured", lambda: True)

    report = tb.generate_title_brief(None, "Mortal Kombat", dry_run=True)
    assert report is not None
    assert report.dry_run is True
    assert report.llm_output is None
    assert report.aggregation["title_original"] == "Mortal Kombat"
