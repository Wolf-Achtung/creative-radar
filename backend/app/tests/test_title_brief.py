"""Tests for the title brief generator (C3). aggregate_title and the Anthropic
call are mocked — no DB, no network. Exercises prompt build, the shared
_run_brief_llm path (happy/truncation), soft citation, dry-run, unknown title."""
from __future__ import annotations

import json
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


def test_title_tool_schema_inlines_nested_objects():
    """Bugfix: the forced tool-use input_schema must present fuer_cutter as a
    DIRECT nested object (no $ref / anyOf), so Claude fills it as JSON instead
    of leaking <parameter ...> XML into a string (Mortal-Kombat failure)."""
    s = tb._TITLE_TOOL_INPUT_SCHEMA
    assert "$defs" not in s
    fc = s["properties"]["fuer_cutter"]
    assert fc.get("type") == "object"
    assert "$ref" not in fc and "anyOf" not in fc
    assert {"schnitt_pace", "hook_strategie", "empfohlene_laengen", "was_diese_woche"}.issubset(
        fc["properties"].keys()
    )
    # Optionality preserved via required-list, not the dropped null-union.
    assert s["required"] == ["headline", "tldr", "plattform_vergleich", "data_caveats"]
    # No $ref leftover anywhere in the schema (all nested objects inlined).
    assert "$ref" not in json.dumps(s)


def test_generate_title_brief_with_fuer_cutter_object(monkeypatch):
    """Regression for the Mortal-Kombat failure: a brief with a filled
    fuer_cutter OBJECT validates + survives the pipeline (previously the field
    arrived as a string and schema-validation failed -> generation_failed)."""
    agg = _make_agg()
    monkeypatch.setattr(tb, "aggregate_title", lambda *a, **k: agg)
    body = _valid_body(fuer_cutter={
        "schnitt_pace": "Top-Cuts unter 25s",
        "hook_strategie": "Cold-Open mit Fight-Beat",
        "empfohlene_laengen": "20-25s",
        "was_diese_woche": "Kurze Fight-Cuts ziehen, lange Featurettes laufen leer.",
    })
    _patch_llm(monkeypatch, _fake_msg(body))

    report = tb.generate_title_brief(None, "Mortal Kombat")
    assert report.llm_output is not None
    assert report.llm_output.fuer_cutter is not None
    assert report.llm_output.fuer_cutter.schnitt_pace == "Top-Cuts unter 25s"
    assert report.llm_output.fuer_cutter.hook_strategie == "Cold-Open mit Fight-Beat"


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


# --------------------------------------------------------------------------
# C4 — persistence + cache (real sqlite table via metadata.create_all).
# --------------------------------------------------------------------------

import os  # noqa: E402
import tempfile  # noqa: E402

import pytest  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from app.models.entities import (  # noqa: E402
    Asset, AssetType, Channel, Market, Post, ReviewStatus, Title,
    TitleInsightReport as TitleInsightReportRow,
)


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_title_brief_", suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        try:
            os.unlink(path)
        except OSError:
            pass


def _seed_title_with_post(session, *, now) -> Title:
    title = Title(id=uuid4(), title_original="Mortal Kombat", content_type="Film")
    session.add(title)
    channel = Channel(id=uuid4(), name="primevideo", handle="primevideo",
                      url="https://x/primevideo", platform="instagram", market=Market.US)
    session.add(channel)
    session.commit()
    post = Post(id=uuid4(), channel_id=channel.id, platform="instagram",
                post_url="https://ig/p/seed", detected_at=now,
                visible_likes=200, visible_comments=20, visible_views=2000)
    session.add(post)
    session.commit()
    session.add(Asset(id=uuid4(), post_id=post.id, title_id=title.id,
                      asset_type=AssetType.UNKNOWN, review_status=ReviewStatus.NEW))
    session.commit()
    return title


def test_persist_then_cache_hit(db, monkeypatch):
    now = datetime(2026, 6, 4, tzinfo=timezone.utc)
    monkeypatch.setattr(tb, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(engine_module, "record_anthropic_call", MagicMock())
    llm = MagicMock(return_value=_fake_msg(_valid_body()))
    monkeypatch.setattr(engine_module, "messages_create_strict_json", llm)

    with Session(db) as session:
        _seed_title_with_post(session, now=now)
        # First call: generates + persists.
        r1 = tb.generate_and_persist_title_brief(session, "Mortal Kombat", now=now)
        assert r1 is not None and r1.llm_output is not None
        assert llm.call_count == 1
        rows = session.exec(__import__("sqlmodel").select(TitleInsightReportRow)).all()
        assert len(rows) == 1

        # Second call without replace: cache hit, NO new LLM call.
        r2 = tb.generate_and_persist_title_brief(session, "Mortal Kombat", now=now)
        assert r2 is not None and r2.llm_output is not None
        assert llm.call_count == 1  # unchanged


def test_replace_overwrites(db, monkeypatch):
    now = datetime(2026, 6, 4, tzinfo=timezone.utc)
    monkeypatch.setattr(tb, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(engine_module, "record_anthropic_call", MagicMock())
    monkeypatch.setattr(engine_module, "messages_create_strict_json",
                        MagicMock(return_value=_fake_msg(_valid_body(headline="V1"))))

    with Session(db) as session:
        _seed_title_with_post(session, now=now)
        tb.generate_and_persist_title_brief(session, "Mortal Kombat", now=now)
        # replace=True regenerates with a new body and overwrites.
        monkeypatch.setattr(engine_module, "messages_create_strict_json",
                            MagicMock(return_value=_fake_msg(_valid_body(headline="V2"))))
        r2 = tb.generate_and_persist_title_brief(session, "Mortal Kombat", now=now, replace=True)
        assert r2.llm_output.headline == "V2"
        rows = session.exec(__import__("sqlmodel").select(TitleInsightReportRow)).all()
        assert len(rows) == 1  # overwritten, not duplicated
        assert rows[0].llm_output["headline"] == "V2"


def test_failed_generation_not_persisted(db, monkeypatch):
    now = datetime(2026, 6, 4, tzinfo=timezone.utc)
    monkeypatch.setattr(tb, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(engine_module, "record_anthropic_call", MagicMock())
    # Truncated -> llm_output None -> skip persist.
    monkeypatch.setattr(engine_module, "messages_create_strict_json",
                        MagicMock(return_value=_fake_msg(_valid_body(), stop_reason="max_tokens")))

    with Session(db) as session:
        _seed_title_with_post(session, now=now)
        r = tb.generate_and_persist_title_brief(session, "Mortal Kombat", now=now)
        assert r is not None and r.llm_output is None
        rows = session.exec(__import__("sqlmodel").select(TitleInsightReportRow)).all()
        assert rows == []  # nothing persisted


def test_persist_unknown_title_returns_none(db):
    with Session(db) as session:
        assert tb.generate_and_persist_title_brief(session, "No Such Title") is None
