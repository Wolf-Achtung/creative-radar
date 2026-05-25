"""Tests fuer den Segment-Roundup-Generator (Master-Plan-Schritt-3).

Mockt die Anthropic-Schicht und seedet ein synthetisches Channel-/Post-
Inventar. Verifiziert:

1. Channel-Selection: nur Channels mit dem angefragten Segment kommen
   durch (Pair-Pool-Channels mit ``segment = NULL`` werden ignoriert).
2. Per-Channel-Aggregation: Top-N-Posts (Default 5), Hashtag-Counter,
   Posts-Count im Zeitfenster.
3. Segment-Aggregation: channels_evaluated, channels_with_posts,
   total_posts.
4. Prompt-Aufbau: knapper Markdown, kein JSON-Anhang.
5. LLM-Antwort wird in ``SegmentRoundupLLMReport`` geparst und persistiert.
6. Idempotenz: Re-Run mit gleichem (segment, year, week) ueberschreibt
   die existierende Row (Last-Write-Wins).
7. Disjunkt: Pair-Pool-Channel mit ``segment = NULL`` taucht NICHT auf.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import (
    Channel,
    ChannelSegment,
    Post,
    SegmentRoundup as SegmentRoundupRow,
)
from app.services import anthropic_client as anthropic_module
from app.services import segment_roundup as roundup_module
from app.services.segment_roundup import (
    _select_channels_for_segment,
    aggregate_segment,
    generate_and_persist_roundup,
)


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_roundup_", suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        try:
            os.unlink(path)
        except OSError:
            pass


def _seed_channel(db_engine, *, handle: str, platform: str, segment: ChannelSegment | None,
                  market: str = "US", active: bool = True) -> Channel:
    with Session(db_engine) as session:
        ch = Channel(
            id=uuid4(),
            name=handle,
            handle=handle,
            url=f"https://www.{platform}.com/{handle}",
            platform=platform,
            market=market,
            active=active,
            mvp=True,
            segment=segment,
        )
        session.add(ch)
        session.commit()
        session.refresh(ch)
        return ch


def _seed_post(
    db_engine,
    channel_id,
    *,
    days_ago: int,
    engagement: int,
    caption: str = "test caption #tag",
    views: int = 0,
) -> None:
    with Session(db_engine) as session:
        # Engagement = likes + comments + bookmarks + shares — wir
        # legen alles auf likes, damit die Sortierung deterministisch ist.
        post = Post(
            id=uuid4(),
            channel_id=channel_id,
            post_url=f"https://example.com/{uuid4().hex[:8]}",
            platform="instagram",
            published_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
            detected_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
            caption=caption,
            visible_likes=engagement,
            visible_comments=0,
            visible_bookmarks=0,
            visible_shares=0,
            visible_views=views,
            duration_seconds=20,
            raw_payload={},
        )
        session.add(post)
        session.commit()


def _mock_anthropic_response(monkeypatch, body: dict, usage: dict | None = None) -> MagicMock:
    """Patches messages_create_text + record_anthropic_call. Returns the mock.

    Schritt-4-Hinweis: ``messages_create_text`` wird ab Commit 2/N vom
    ``call_with_json_retry``-Helper im ``anthropic_client``-Modul
    aufgerufen — Patch muss am defining-Modul ansetzen, sonst trifft der
    Mock den Helper-Call nicht.
    """
    usage_ns = SimpleNamespace(
        input_tokens=(usage or {}).get("input_tokens", 1000),
        output_tokens=(usage or {}).get("output_tokens", 300),
    )
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(body))],
        usage=usage_ns,
    )
    mock = MagicMock(return_value=message)
    monkeypatch.setattr(anthropic_module, "messages_create_text", mock)
    monkeypatch.setattr(roundup_module, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(
        roundup_module, "record_anthropic_call",
        lambda *args, **kwargs: None,
    )
    return mock


def _minimal_llm_body() -> dict:
    return {
        "headline": "Roundup headline",
        "tldr": "Drei Sätze über das Segment.",
        "what_ran": ["Trailer", "Stills", "BTS-Clip"],
        "channels_in_focus": ["@a24", "@neonrated"],
        "themes": ["Indie-Releases", "Festival-Vorbereitung"],
        "data_caveats": ["2 von 5 Channels ohne Posts im Fenster"],
    }


# ---------------------------------------------------------------------------
# Test 1 — Channel-Selection: filtert auf Segment, ignoriert NULL + andere
# ---------------------------------------------------------------------------

def test_select_channels_filters_by_segment_only(db):
    _seed_channel(db, handle="a24", platform="instagram",
                  segment=ChannelSegment.US_INDEPENDENT)
    _seed_channel(db, handle="neonrated", platform="instagram",
                  segment=ChannelSegment.US_INDEPENDENT)
    # us_major — anderes Segment, darf nicht durchkommen
    _seed_channel(db, handle="hbo", platform="instagram",
                  segment=ChannelSegment.US_MAJOR)
    # Pair-Pool-Channel — segment NULL, disjunkt
    _seed_channel(db, handle="warnerbros", platform="instagram",
                  segment=None)
    # Inactive Channel, gleiches Segment — darf nicht
    _seed_channel(db, handle="oldindie", platform="instagram",
                  segment=ChannelSegment.US_INDEPENDENT, active=False)

    with Session(db) as session:
        results = _select_channels_for_segment(session, ChannelSegment.US_INDEPENDENT)

    handles = sorted(c.handle for c in results)
    assert handles == ["a24", "neonrated"]


# ---------------------------------------------------------------------------
# Test 2 — Per-Channel-Aggregation respektiert Top-N und Zeitfenster
# ---------------------------------------------------------------------------

def test_aggregate_segment_picks_top_n_within_window(db):
    ch = _seed_channel(db, handle="a24", platform="instagram",
                       segment=ChannelSegment.US_INDEPENDENT)
    # Im Fenster (14d) — 7 Posts mit absteigendem engagement
    for i in range(7):
        _seed_post(db, ch.id, days_ago=i, engagement=1000 - i * 100,
                   caption=f"in-window {i} #cinema")
    # Ausserhalb Fenster (20 Tage alt) — darf nicht
    _seed_post(db, ch.id, days_ago=20, engagement=99999, caption="too old")

    with Session(db) as session:
        agg = aggregate_segment(
            session, ChannelSegment.US_INDEPENDENT,
            window_days=14, top_posts_n=5,
        )

    assert agg.segment == "us_independent"
    assert agg.window_days == 14
    assert agg.channels_evaluated == 1
    assert agg.channels_with_posts == 1
    assert agg.total_posts == 7  # 7 im Fenster, 1 ausserhalb ignoriert
    assert len(agg.channels) == 1
    stats = agg.channels[0]
    assert stats.posts_count == 7
    assert len(stats.top_posts) == 5  # Top-5 trotz 7 verfuegbar
    # Sortierung absteigend nach engagement
    engs = [p.engagement_sum for p in stats.top_posts]
    assert engs == sorted(engs, reverse=True)
    # Hashtag-Counter
    assert any(h.tag == "cinema" for h in stats.top_hashtags)


# ---------------------------------------------------------------------------
# Test 3 — Channel ohne Posts im Fenster: erscheint mit posts_count=0
# ---------------------------------------------------------------------------

def test_aggregate_segment_handles_silent_channel(db):
    ch_active = _seed_channel(db, handle="active", platform="instagram",
                              segment=ChannelSegment.US_MAJOR)
    ch_silent = _seed_channel(db, handle="silent", platform="instagram",
                              segment=ChannelSegment.US_MAJOR)
    _seed_post(db, ch_active.id, days_ago=3, engagement=500)

    with Session(db) as session:
        agg = aggregate_segment(session, ChannelSegment.US_MAJOR, window_days=14)

    assert agg.channels_evaluated == 2
    assert agg.channels_with_posts == 1
    assert agg.total_posts == 1
    silent_stats = next(c for c in agg.channels if c.handle == "silent")
    assert silent_stats.posts_count == 0
    assert silent_stats.top_posts == []


# ---------------------------------------------------------------------------
# Test 4 — End-to-end-Generator + Persist (Anthropic gemockt)
# ---------------------------------------------------------------------------

def test_generate_and_persist_roundup_writes_row(db, monkeypatch):
    ch = _seed_channel(db, handle="a24", platform="instagram",
                       segment=ChannelSegment.US_INDEPENDENT)
    _seed_post(db, ch.id, days_ago=3, engagement=500)
    mock = _mock_anthropic_response(monkeypatch, _minimal_llm_body())

    with Session(db) as session:
        report = generate_and_persist_roundup(
            session, ChannelSegment.US_INDEPENDENT, window_days=14,
        )

    assert report.llm_output is not None
    assert report.llm_output.headline == "Roundup headline"
    assert mock.call_count == 1

    # Row in DB persistiert
    with Session(db) as session:
        row = session.get(
            SegmentRoundupRow,
            (ChannelSegment.US_INDEPENDENT, report.iso_year, report.iso_week),
        )
        assert row is not None
        assert row.segment == ChannelSegment.US_INDEPENDENT
        assert row.window_days == 14
        assert row.llm_output["headline"] == "Roundup headline"


# ---------------------------------------------------------------------------
# Test 5 — Idempotenz: Re-Run ueberschreibt die Row (Last-Write-Wins)
# ---------------------------------------------------------------------------

def test_generate_and_persist_roundup_is_last_write_wins(db, monkeypatch):
    ch = _seed_channel(db, handle="a24", platform="instagram",
                       segment=ChannelSegment.US_INDEPENDENT)
    _seed_post(db, ch.id, days_ago=3, engagement=500)
    _mock_anthropic_response(monkeypatch, _minimal_llm_body())

    with Session(db) as session:
        first = generate_and_persist_roundup(
            session, ChannelSegment.US_INDEPENDENT, window_days=14,
        )

    # Zweite Runde mit anderem headline
    second_body = _minimal_llm_body()
    second_body["headline"] = "Second-run headline"
    _mock_anthropic_response(monkeypatch, second_body)

    with Session(db) as session:
        second = generate_and_persist_roundup(
            session, ChannelSegment.US_INDEPENDENT, window_days=14,
        )

    assert second.llm_output.headline == "Second-run headline"
    # Genau eine Row in DB, nicht zwei
    with Session(db) as session:
        rows = list(session.exec(
            __import__("sqlmodel").select(SegmentRoundupRow)
            .where(SegmentRoundupRow.segment == ChannelSegment.US_INDEPENDENT)
        ).all())
        assert len(rows) == 1
        assert rows[0].llm_output["headline"] == "Second-run headline"


# ---------------------------------------------------------------------------
# Test 6 — Disjunkt: Pair-Pool-Channel (segment=NULL) wird nie aggregiert
# ---------------------------------------------------------------------------

def test_silent_channel_list_uses_platform_suffix_for_multi_platform_handles(db):
    """Schritt-4 Dedupe-Fix: Multi-Plattform-Handles im Silent-Block muessen
    ``@handle (platform)`` zeigen — ohne Plattform-Suffix erscheinen sie
    mehrfach als ``@disney`` und der LLM-Prompt verliert die
    Plattform-Unterscheidung."""
    from app.services.segment_roundup import _build_user_prompt
    # Gleicher Handle auf drei Plattformen, alle silent
    _seed_channel(db, handle="disney", platform="instagram",
                  segment=ChannelSegment.US_MAJOR)
    _seed_channel(db, handle="disney", platform="tiktok",
                  segment=ChannelSegment.US_MAJOR)
    _seed_channel(db, handle="disney", platform="youtube",
                  segment=ChannelSegment.US_MAJOR)

    with Session(db) as session:
        agg = aggregate_segment(session, ChannelSegment.US_MAJOR, window_days=14)
    prompt = _build_user_prompt(agg)

    assert "@disney (instagram)" in prompt
    assert "@disney (tiktok)" in prompt
    assert "@disney (youtube)" in prompt
    # Kein bare "@disney," ohne Plattform-Suffix
    assert "@disney," not in prompt
    assert "ohne Posts im Fenster (3)" in prompt


def test_pair_pool_channel_segment_null_is_disjoint(db, monkeypatch):
    pair_ch = _seed_channel(db, handle="warnerbros", platform="instagram",
                            segment=None)  # Pair-Pool
    _seed_post(db, pair_ch.id, days_ago=3, engagement=999)
    # Einen us_major-Channel daneben, sonst keine Posts → leere Aggregation
    _seed_channel(db, handle="hbo", platform="instagram",
                  segment=ChannelSegment.US_MAJOR)
    _mock_anthropic_response(monkeypatch, _minimal_llm_body())

    with Session(db) as session:
        agg = aggregate_segment(session, ChannelSegment.US_MAJOR, window_days=14)

    # warnerbros darf nicht in der us_major-Aggregation auftauchen
    handles = [c.handle for c in agg.channels]
    assert "warnerbros" not in handles
    assert handles == ["hbo"]
    assert agg.total_posts == 0  # hbo hat keine Posts, warnerbros wird ignoriert


# ---------------------------------------------------------------------------
# Test 7 — Bad-JSON-Antwort: llm_output bleibt None, kein Persist
# ---------------------------------------------------------------------------

def test_bad_json_response_persists_nothing(db, monkeypatch):
    ch = _seed_channel(db, handle="a24", platform="instagram",
                       segment=ChannelSegment.US_INDEPENDENT)
    _seed_post(db, ch.id, days_ago=3, engagement=500)

    # Mock returns malformed JSON — Helper repeats the call up to 2 times
    # (M2-retry pattern), all attempts return the same broken text, so
    # ``parsed`` stays None and persist-skip greift.
    text_block = SimpleNamespace(type="text", text='{"headline": "broken')
    usage_ns = SimpleNamespace(input_tokens=1000, output_tokens=100)
    message = SimpleNamespace(content=[text_block], usage=usage_ns)
    monkeypatch.setattr(anthropic_module, "messages_create_text",
                        MagicMock(return_value=message))
    monkeypatch.setattr(roundup_module, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(roundup_module, "record_anthropic_call",
                        lambda *a, **kw: None)

    with Session(db) as session:
        report = generate_and_persist_roundup(
            session, ChannelSegment.US_INDEPENDENT, window_days=14,
        )

    assert report.llm_output is None
    assert report.raw_llm_text is not None
    # Keine Row in DB
    with Session(db) as session:
        rows = list(session.exec(
            __import__("sqlmodel").select(SegmentRoundupRow)
        ).all())
        assert rows == []
