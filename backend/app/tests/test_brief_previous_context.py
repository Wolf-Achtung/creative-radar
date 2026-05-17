"""Anti-Repetition-Sprint (PR #152, 17.05.2026) — Vorgaenger-Brief-Kontext
im Brief-Prompt verifizieren.

Verifiziert das Verhalten der neuen ``_load_previous_brief``-Lookup-Funktion,
des Top-Post-Diff-Helpers und der ``_format_previous_context_block``-
Markdown-Generierung. Tests pruefen sowohl die DB-Lookup-Mechanik (KW-1
direkt, Jahresgrenze, replace-auf-gleiche-Woche skip) als auch den
End-to-End-Pfad in ``generate_and_persist_report`` (Mock auf
``messages_create_text``, assertion auf den ``user_message``-String).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import InsightReport as InsightReportRow
from app.services import insight_engine as engine_module
from app.services.insight_engine import (
    _compute_top_post_diff,
    _load_previous_brief,
    generate_and_persist_report,
)
from app.schemas.insights import (
    ChannelStats,
    LLMReport,
    PairAggregation,
    PlatformAggregation,
    RankedPost,
    TitleCoverage,
)


def _engine_for_path(path: str):
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_prev_ctx_", suffix=".db")
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
        titles_in_both_markets=[],
        de_only_titles=[],
        us_only_titles=[],
        de_assets_with_title=0,
        de_assets_total=0,
        us_assets_with_title=0,
        us_assets_total=0,
        uk_only_titles=[],
        uk_assets_with_title=0,
        uk_assets_total=0,
        overall_coverage_pct=0.0,
    )


def _channel_with_posts(handle: str, market: str, posts: list[RankedPost]) -> ChannelStats:
    return ChannelStats.model_construct(
        handle=handle,
        market=market,
        channel_id=f"{handle}-id",
        channel_found=True,
        posts_count=len(posts),
        assets_count=len(posts),
        coverage_pct=0.0,
        top_hashtags=[],
        avg_caption_length=0.0,
        avg_duration_seconds=None,
        duration_buckets={},
        top_posts=[],
        avg_engagement=0.0,
        avg_activation_rate=0.0,
        historical_top_posts=[],
        ranked_posts=posts,
    )


def _ranked_post(asset_id: str, caption: str) -> RankedPost:
    return RankedPost.model_construct(
        post_url=f"https://example.com/{asset_id}",
        caption_excerpt=caption,
        platform="tiktok",
        published_at=None,
        duration_seconds=None,
        views=0,
        likes=0,
        comments=0,
        saves=0,
        shares=0,
        engagement_sum=0,
        activation_rate=0.0,
        title_local=None,
        title_original=None,
        franchise=None,
        thumbnail_url=None,
        content_type=None,
        asset_id=asset_id,
    )


def _build_aggregation(
    pair_key: str,
    iso_year: int,
    iso_week: int,
    *,
    de_post_specs: list[tuple[str, str]] | None = None,
    us_post_specs: list[tuple[str, str]] | None = None,
) -> PairAggregation:
    de_posts = [_ranked_post(aid, cap) for aid, cap in (de_post_specs or [])]
    us_posts = [_ranked_post(aid, cap) for aid, cap in (us_post_specs or [])]
    platform_agg = PlatformAggregation.model_construct(
        platform="tiktok",
        de_channel=_channel_with_posts("test_de", "DE", de_posts) if de_posts else None,
        us_channel=_channel_with_posts("test_us", "US", us_posts) if us_posts else None,
        uk_channel=None,
        cross_market_matches=[],
        title_coverage=_empty_title_coverage(),
        notes=[],
    )
    now = datetime(iso_year, 5, 17, tzinfo=timezone.utc)
    return PairAggregation.model_construct(
        pair_key=pair_key,
        pair_label=f"{pair_key} test",
        platform="tiktok",
        window_days=30,
        window_start=now - timedelta(days=30),
        window_end=now,
        iso_week=iso_week,
        iso_year=iso_year,
        de_channel=platform_agg.de_channel,
        us_channel=platform_agg.us_channel,
        uk_channel=None,
        cross_market_matches=[],
        title_coverage=_empty_title_coverage(),
        notes=[],
        per_platform=[platform_agg],
    )


def _seed_previous_brief(
    db_engine,
    pair_key: str,
    iso_year: int,
    iso_week: int,
    *,
    headline: str,
    top_post_specs: list[tuple[str, str]] | None = None,
    llm_output_override: dict | None = None,
) -> None:
    agg = _build_aggregation(
        pair_key, iso_year, iso_week,
        de_post_specs=top_post_specs,
    )
    if llm_output_override is not None:
        llm_output_json = llm_output_override
    else:
        llm_output_json = LLMReport.model_validate({
            "headline": headline,
            "tldr": "stale-tldr",
            "trends": [],
            "actions": [],
            "cross_market_insight": {
                "de_vs_us": "stale-de-vs-us",
                "transfer_opportunity": "stale-transfer",
            },
            "risks": [],
            "data_caveats": [],
        }).model_dump(mode="json")
    row = InsightReportRow(
        pair_key=pair_key,
        iso_year=iso_year,
        iso_week=iso_week,
        aggregation=agg.model_dump(mode="json"),
        llm_output=llm_output_json,
        generated_at=datetime(iso_year, 5, 11, tzinfo=timezone.utc),
        model="claude-opus-4-7",
        cost_usd_cents=99,
    )
    with Session(db_engine) as session:
        session.add(row)
        session.commit()


def _make_anthropic_mock(headline: str = "fresh-headline") -> MagicMock:
    body = LLMReport.model_validate({
        "headline": headline,
        "tldr": "fresh-tldr",
        "trends": [],
        "actions": [],
        "cross_market_insight": {
            "de_vs_us": "fresh-de-vs-us",
            "transfer_opportunity": "fresh-transfer",
        },
        "risks": [],
        "data_caveats": [],
    }).model_dump(mode="json")
    text_block = SimpleNamespace(type="text", text=json.dumps(body))
    usage = SimpleNamespace(input_tokens=8000, output_tokens=3000)
    message = SimpleNamespace(content=[text_block], usage=usage)
    return MagicMock(return_value=message)


def _patch_engine_call(monkeypatch, db, *, headline: str = "fresh-headline") -> MagicMock:
    """Mockt die Anthropic-Schicht und die ``aggregate_pair``-Funktion,
    damit der Test nicht von Database-Seeds fuer Channels/Posts abhaengt.
    Returns den Anthropic-Mock fuer Inspektion des ``user_message``-Args."""
    anthropic_mock = _make_anthropic_mock(headline)
    monkeypatch.setattr(engine_module, "messages_create_text", anthropic_mock)
    monkeypatch.setattr(engine_module, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(
        engine_module, "record_anthropic_call",
        lambda *args, **kwargs: None,
    )

    def fake_aggregate_pair(session, pair_key, *, window_days=30, now=None):
        return _build_aggregation(
            pair_key, 2026, 20,
            de_post_specs=[
                ("asset_b", "Carried-Post Beispielcaption B"),
                ("asset_d", "Brandneue Post-Caption D"),
            ],
        )
    monkeypatch.setattr(engine_module, "aggregate_pair", fake_aggregate_pair)
    return anthropic_mock


def _seed_pair_in_pairs(monkeypatch, pair_key: str) -> None:
    """Schreibt einen Test-Pair-Key in ``PAIRS`` rein, damit
    ``aggregate_pair`` und ``generate_and_persist_report`` ihn als valide
    sehen. ``monkeypatch.setitem`` sorgt fuer automatischen Cleanup nach
    dem Test — direkte Dict-Mutation wuerde sonst ueber Test-Files hinweg
    leaken und Pairs-Backwards-Compat-Assertions anderer Tests brechen."""
    monkeypatch.setitem(engine_module.PAIRS, pair_key, {
        "display_name": pair_key,
        "markets": ["DE", "US"],
        "label": f"{pair_key} test",
        "platforms": {"tiktok": [{"handle": pair_key, "market": "DE"}]},
        "platform": "tiktok",
        "channels": [],
        "enabled": True,
        "reason": None,
    })


# ---------------------------------------------------------------------------
# Test 1 — Erstes Brief fuer ein Pair: kein Previous-Context im Prompt.
# ---------------------------------------------------------------------------

def test_first_brief_for_pair_has_no_previous_context(db, monkeypatch):
    _seed_pair_in_pairs(monkeypatch, "test_pair_solo")
    anthropic_mock = _patch_engine_call(monkeypatch, db)

    with Session(db) as session:
        generate_and_persist_report(
            session, "test_pair_solo", window_days=30,
        )

    assert anthropic_mock.call_count == 1
    user_message = anthropic_mock.call_args.kwargs["user_message"]
    assert "Bezug zur Vorwoche" not in user_message
    assert "Vorgänger-Headline" not in user_message


# ---------------------------------------------------------------------------
# Test 2 — Folge-Brief: Previous-Context im Prompt mit Headline + Beispielen.
# ---------------------------------------------------------------------------

def test_subsequent_brief_includes_previous_context(db, monkeypatch):
    _seed_pair_in_pairs(monkeypatch, "test_pair")
    _seed_previous_brief(
        db, "test_pair", 2026, 19,
        headline="Vorwoche-Headline mit Disney-Mandalorian-Klammer",
        top_post_specs=[
            ("asset_a", "Carried-Post A — wuerde rausfallen"),
            ("asset_b", "Carried-Post Beispielcaption B"),
            ("asset_c", "Carried-Post C — wuerde rausfallen"),
        ],
    )
    anthropic_mock = _patch_engine_call(monkeypatch, db)

    with Session(db) as session:
        generate_and_persist_report(
            session, "test_pair", window_days=30,
        )

    assert anthropic_mock.call_count == 1
    prompt = anthropic_mock.call_args.kwargs["user_message"]
    assert "## Bezug zur Vorwoche (KW 19/2026)" in prompt
    assert "Vorwoche-Headline mit Disney-Mandalorian-Klammer" in prompt
    # current: asset_b + asset_d, previous: a/b/c → carried=1, new=1, dropped=2
    assert "1 Posts übernommen" in prompt
    assert "1 neu" in prompt
    assert "2 aus dem Vorgänger nicht mehr unter den Top-Performern" in prompt
    assert "Carried-Post Beispielcaption B" in prompt
    assert "Brandneue Post-Caption D" in prompt
    # Wolf-Ping-#1-Final-Wortlaut Stichproben
    assert "benenne die zeitliche Entwicklung" in prompt
    assert "Phasenwechsel der Kampagne" in prompt
    assert "Mechanik-Shift im Content" in prompt


# ---------------------------------------------------------------------------
# Test 3 — replace=True auf existierende KW: Vorgaenger ist KW-1, nicht
# der zu ueberschreibende Brief selbst.
# ---------------------------------------------------------------------------

def test_load_previous_brief_skips_current_week_on_replace(db):
    _seed_previous_brief(
        db, "test_pair", 2026, 19,
        headline="Vor-Vorwoche-Headline",
        top_post_specs=[("asset_x", "irrelevant")],
    )
    _seed_previous_brief(
        db, "test_pair", 2026, 20,
        headline="Brief der gerade ueberschrieben werden soll",
        top_post_specs=[("asset_y", "irrelevant")],
    )

    with Session(db) as session:
        previous = _load_previous_brief(session, "test_pair", 2026, 20)

    assert previous is not None
    assert previous.iso_year == 2026
    assert previous.iso_week == 19, (
        "Bei replace=True auf KW 20 muss der KW-19-Brief als Vorgaenger "
        "gefunden werden, NICHT der KW-20-Brief selbst."
    )


# ---------------------------------------------------------------------------
# Test 4 — Jahresgrenze: KW 1/2027 findet KW 52/2026.
# ---------------------------------------------------------------------------

def test_load_previous_brief_across_year_boundary(db):
    _seed_previous_brief(
        db, "test_pair", 2026, 52,
        headline="Letzte KW 2026",
        top_post_specs=[("asset_late", "irrelevant")],
    )

    with Session(db) as session:
        previous = _load_previous_brief(session, "test_pair", 2027, 1)

    assert previous is not None
    assert previous.iso_year == 2026
    assert previous.iso_week == 52


# ---------------------------------------------------------------------------
# Test 5 — Korrupte/leere Vorgaenger-Headline: ganzer Block weggelassen,
# logger.warning("previous_context_skipped") fired, Brief laeuft normal.
# ---------------------------------------------------------------------------

def test_corrupt_previous_headline_skips_block_and_warns(db, monkeypatch, caplog):
    _seed_pair_in_pairs(monkeypatch, "test_pair")
    _seed_previous_brief(
        db, "test_pair", 2026, 19,
        headline="",
        top_post_specs=[("asset_b", "Carried-Post B")],
        llm_output_override={
            "headline": "",
            "tldr": "irrelevant",
            "trends": [],
            "actions": [],
            "cross_market_insight": {
                "de_vs_us": "x",
                "transfer_opportunity": "y",
            },
            "risks": [],
            "data_caveats": [],
        },
    )
    anthropic_mock = _patch_engine_call(monkeypatch, db)

    with caplog.at_level(logging.WARNING, logger="app.services.insight_engine"):
        with Session(db) as session:
            generate_and_persist_report(
                session, "test_pair", window_days=30,
            )

    assert anthropic_mock.call_count == 1
    prompt = anthropic_mock.call_args.kwargs["user_message"]
    assert "Bezug zur Vorwoche" not in prompt
    assert "Vorgänger-Headline" not in prompt
    warnings = [
        r for r in caplog.records
        if r.levelname == "WARNING"
        and "previous_context_skipped" in r.getMessage()
    ]
    assert len(warnings) == 1
    rec = warnings[0]
    assert rec.reason == "headline_unavailable"
    assert rec.pair_key == "test_pair"
    assert rec.previous_iso_week == 19


# ---------------------------------------------------------------------------
# Test 6 (bonus) — _compute_top_post_diff direkt: Set-Semantik + Examples.
# ---------------------------------------------------------------------------

def test_compute_top_post_diff_set_semantics():
    current = _build_aggregation(
        "test_pair", 2026, 20,
        de_post_specs=[
            ("asset_b", "Carried B"),
            ("asset_d", "Brandneu D"),
            ("asset_e", "Brandneu E"),
        ],
    )
    previous = _build_aggregation(
        "test_pair", 2026, 19,
        de_post_specs=[
            ("asset_a", "Dropped A"),
            ("asset_b", "Carried B aus dem Vorgaenger"),
            ("asset_c", "Dropped C"),
        ],
    )

    diff = _compute_top_post_diff(current, previous)

    assert diff["carried_count"] == 1
    assert diff["new_count"] == 2
    assert diff["dropped_count"] == 2
    assert diff["examples_carried"] == ["Carried B"]
    assert diff["examples_new"] == ["Brandneu D", "Brandneu E"]
