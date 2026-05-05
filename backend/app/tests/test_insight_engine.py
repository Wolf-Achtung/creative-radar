"""Tests for the Insight-Engine MVP (Sprint 1).

These exercise:

* the deterministic aggregation (hashtag extraction, top-N posts,
  duration buckets, cross-market match-key joining, coverage %),
* the dry-run path (no LLM call, no API key required),
* the end-to-end ``generate_weekly_report`` happy-path with a mocked
  Anthropic message,
* the error paths (unknown pair, missing channels, JSON-parse failure).

The fixture builds a tiny, hand-curated mini-DB so the assertions can
reference exact numbers — that way regressions in the aggregation
surface as concrete diffs rather than fuzzy "looks different".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Asset, Channel, Market, Post, Title
from app.schemas.insights import InsightReport
from app.services import insight_engine


# ---------- Fixtures --------------------------------------------------------


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make_post(
    session: Session,
    channel: Channel,
    *,
    caption: str,
    likes: int = 0,
    comments: int = 0,
    shares: int = 0,
    saves: int = 0,
    duration: Optional[int] = None,
    days_ago: int = 1,
    raw_payload: Optional[dict] = None,
    url_suffix: Optional[str] = None,
) -> Post:
    """Single post helper. ``url_suffix`` lets the caller hand-pick a URL
    so tests can match against ``post_url`` deterministically."""
    suffix = url_suffix or f"{caption[:8]}-{days_ago}-{likes}"
    post = Post(
        channel_id=channel.id,
        platform="tiktok",
        post_url=f"https://tiktok.com/@{channel.handle}/video/{suffix}",
        caption=caption,
        published_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        detected_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        visible_likes=likes,
        visible_comments=comments,
        visible_shares=shares,
        visible_bookmarks=saves,
        duration_seconds=duration,
        raw_payload=raw_payload or {},
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    return post


def _seed_warnerbros_pair(session: Session) -> dict:
    """Build the canonical fixture: two channels, a handful of posts,
    one cross-market match-key. Returns refs so individual tests can
    reach into the data without re-querying."""
    us = Channel(
        name="Warner Bros US",
        platform="tiktok",
        url="https://www.tiktok.com/@warnerbros",
        handle="warnerbros",
        market=Market.US,
    )
    de = Channel(
        name="Warner Bros DE",
        platform="tiktok",
        url="https://www.tiktok.com/@warnerbrosdeutschland",
        handle="warnerbrosdeutschland",
        market=Market.DE,
    )
    session.add(us)
    session.add(de)
    session.commit()
    session.refresh(us)
    session.refresh(de)

    title = Title(title_original="Mortal Kombat II")
    session.add(title)
    session.commit()
    session.refresh(title)

    # US: 3 posts with overlapping hashtags + one outlier
    us_p1 = _make_post(
        session, us,
        caption="Get ready! #MortalKombat2 #Trailer #BloodSport",
        likes=10_000, comments=500, shares=200, saves=400, duration=22,
        days_ago=2, url_suffix="us1",
    )
    us_p2 = _make_post(
        session, us,
        caption="The fight begins #MortalKombat2 #Action",
        likes=5_000, comments=100, shares=50, saves=80, duration=58,
        days_ago=10, url_suffix="us2",
    )
    us_p3 = _make_post(
        session, us,
        caption="Behind the scenes #BTS",
        likes=1_000, comments=20, shares=5, saves=10, duration=12,
        days_ago=15, url_suffix="us3",
    )

    # DE: 2 posts (less data — the prompt should reflect that)
    de_p1 = _make_post(
        session, de,
        caption="Es geht los! #MortalKombat2 #Kino #Trailer",
        likes=3_000, comments=80, shares=20, saves=50, duration=28,
        days_ago=3, url_suffix="de1",
    )
    de_p2 = _make_post(
        session, de,
        caption="Im Kino #BlockbusterDE",
        likes=800, comments=15, shares=4, saves=8, duration=70,
        days_ago=8, url_suffix="de2",
    )

    # Cross-market match — same trailer drop, different markets.
    us_asset = Asset(post_id=us_p1.id, title_id=title.id, de_us_match_key="mk2-trailer-1")
    de_asset = Asset(post_id=de_p1.id, title_id=title.id, de_us_match_key="mk2-trailer-1")
    # Discovery / no title
    discovery = Asset(post_id=us_p3.id)
    # US asset for 2nd post — has title but no match-key
    us_asset_2 = Asset(post_id=us_p2.id, title_id=title.id)
    de_asset_2 = Asset(post_id=de_p2.id)
    session.add_all([us_asset, de_asset, discovery, us_asset_2, de_asset_2])
    session.commit()

    return {
        "us_channel": us,
        "de_channel": de,
        "title": title,
        "us_posts": [us_p1, us_p2, us_p3],
        "de_posts": [de_p1, de_p2],
    }


# ---------- Aggregation -----------------------------------------------------


def test_aggregate_basic_counts_and_top_post():
    with _session() as session:
        data = _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)

        assert agg.pair_key == "warnerbros"
        assert agg.platform == "tiktok"
        assert agg.de_channel is not None and agg.us_channel is not None
        assert agg.us_channel.posts_count == 3
        assert agg.de_channel.posts_count == 2

        # Top US post must be us_p1 (highest engagement)
        top_us = agg.us_channel.top_posts[0]
        assert top_us.post_url == data["us_posts"][0].post_url
        assert top_us.engagement_sum == 10_000 + 500 + 200 + 400


def test_hashtag_frequency_is_lowercased_and_counted():
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        us_tags = {h.tag: h.count for h in agg.us_channel.top_hashtags}
        # #MortalKombat2 appears in us_p1 + us_p2 → 2 (case-insensitive)
        assert us_tags.get("mortalkombat2") == 2


def test_duration_buckets():
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        # US: 22s -> 15-30, 58s -> 30-60, 12s -> <15
        us_buckets = agg.us_channel.duration_buckets
        assert us_buckets["<15s"] == 1
        assert us_buckets["15-30s"] == 1
        assert us_buckets["30-60s"] == 1


def test_cross_market_match_picks_up_shared_match_key():
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        assert len(agg.cross_market_matches) == 1
        m = agg.cross_market_matches[0]
        assert m.match_key == "mk2-trailer-1"
        assert m.title == "Mortal Kombat II"
        assert m.us_engagement > m.de_engagement


def test_cross_market_excludes_unknown_match_key():
    """The match-key builder writes 'unknown' as a sentinel when no
    useful key can be derived. Joining on that would yield spurious
    matches between unrelated posts; the aggregation must drop those.
    Covers Sprint-1 polish 0g."""
    with _session() as session:
        data = _seed_warnerbros_pair(session)
        # Seed two extra assets — both with match_key='unknown'. Without the
        # filter, this would surface as an extra cross-market match alongside
        # the legit mk2-trailer-1 one.
        us_unknown_post = _make_post(
            session, data["us_channel"],
            caption="random unrelated #thing", likes=42,
            days_ago=4, url_suffix="us-unknown",
        )
        de_unknown_post = _make_post(
            session, data["de_channel"],
            caption="random unrelated DE", likes=10,
            days_ago=4, url_suffix="de-unknown",
        )
        session.add(Asset(post_id=us_unknown_post.id, de_us_match_key="unknown"))
        # Mixed-case to verify the filter is case-insensitive
        session.add(Asset(post_id=de_unknown_post.id, de_us_match_key="Unknown"))
        session.commit()

        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        keys = [m.match_key for m in agg.cross_market_matches]
        assert "unknown" not in [k.lower() for k in keys]
        # The legit match is still there
        assert any(k == "mk2-trailer-1" for k in keys)


def test_coverage_pct_reflects_assets_with_title():
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        # US: 3 assets, 2 with title → 66.7. DE: 2 assets, 1 with title → 50. Overall: 3/5 = 60.
        assert agg.us_channel.coverage_pct == pytest.approx(66.7, abs=0.1)
        assert agg.de_channel.coverage_pct == pytest.approx(50.0, abs=0.1)
        assert agg.title_coverage.overall_coverage_pct == pytest.approx(60.0, abs=0.1)


def test_unknown_pair_raises():
    with _session() as session:
        with pytest.raises(ValueError):
            insight_engine.aggregate_pair(session, "no-such-pair")


def test_missing_channels_record_notes_but_dont_crash():
    with _session() as session:
        # Empty DB — neither handle exists
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        assert agg.us_channel.channel_found is False
        assert agg.de_channel.channel_found is False
        assert any("DE-Channel" in n for n in agg.notes)
        assert any("US-Channel" in n for n in agg.notes)


# ---------- Sprint-2: PAIRS-Registry-Konsistenz ----------------------------


@pytest.mark.parametrize("pair_key", sorted(insight_engine.PAIRS.keys()))
def test_pairs_registry_schema(pair_key: str):
    """Jeder PAIRS-Eintrag muss das Sprint-2-Schema erfüllen: label, platform,
    channels (DE+US), enabled, reason. Verhindert, dass eine neue Pair-PR
    versehentlich einen Schlüssel vergisst und das Endpoint dann mit
    KeyError stirbt."""
    pair_def = insight_engine.PAIRS[pair_key]
    assert isinstance(pair_def.get("label"), str) and pair_def["label"]
    assert pair_def.get("platform") == "tiktok", "Tier-A-Scope ist TikTok-only"
    assert isinstance(pair_def.get("enabled"), bool)
    if not pair_def["enabled"]:
        assert pair_def.get("reason"), "disabled pair muss reason haben"
    channels = pair_def.get("channels") or []
    assert len(channels) == 2, "Pair hat genau einen DE- und einen US-Channel"
    markets = {c["market"] for c in channels}
    assert markets == {"DE", "US"}
    for c in channels:
        assert isinstance(c.get("handle"), str) and c["handle"], (
            f"Channel-Handle fehlt für {pair_key}/{c.get('market')}"
        )


@pytest.mark.parametrize(
    "pair_key",
    sorted(k for k, v in insight_engine.PAIRS.items() if v.get("enabled", True)),
)
def test_aggregate_pair_handles_missing_channels_for_all_enabled_pairs(pair_key: str):
    """Pair-agnostische Sicherheit: aggregate_pair darf für JEDEN aktivierten
    Pair gegen eine leere DB laufen und liefert eine valide Aggregation mit
    Notes statt zu crashen. Schmale Garantie, aber sie greift, sobald eine
    neue Pair-Konfig den Endpoint trifft, bevor die Channels onboarded sind."""
    with _session() as session:
        agg = insight_engine.aggregate_pair(session, pair_key, window_days=30)
        assert agg.pair_key == pair_key
        assert agg.platform == "tiktok"
        assert agg.us_channel is not None and agg.de_channel is not None
        assert agg.us_channel.channel_found is False
        assert agg.de_channel.channel_found is False
        assert any("DE-Channel" in n for n in agg.notes)
        assert any("US-Channel" in n for n in agg.notes)


def test_window_filters_old_posts():
    with _session() as session:
        data = _seed_warnerbros_pair(session)
        # add an ancient US post that should be excluded by window_days=30
        _make_post(
            session, data["us_channel"],
            caption="ancient", likes=99, days_ago=200, url_suffix="oldie",
        )
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        assert agg.us_channel.posts_count == 3  # ancient excluded


# ---------- Dry-run + LLM happy/error paths --------------------------------


def test_dry_run_skips_llm_and_returns_aggregation():
    with _session() as session:
        _seed_warnerbros_pair(session)
        report = insight_engine.generate_weekly_report(
            session, "warnerbros", dry_run=True
        )
        assert isinstance(report, InsightReport)
        assert report.dry_run is True
        assert report.llm_output is None
        assert report.aggregation.us_channel.posts_count == 3
        assert report.cost_usd_estimate == 0.0


def test_generate_with_mocked_llm(monkeypatch):
    sample_llm_json = {
        "headline": "Headline test",
        "tldr": "Eins. Zwei. Drei.",
        "trends": [
            {
                "name": "Hook unter 15s",
                "evidence": "us_p3 — 12s, 1k Likes",
                "implication_for_creation": "Cutter sollte Hook-Variante unter 15s testen.",
            }
        ],
        "actions": [
            {
                "what": "Cut MK2-Trailer auf 22s",
                "why": "us_p1 leadt mit 22s und 11k Engagement",
                "for_whom": "Cutter MK2",
            }
        ],
        "cross_market_insight": {
            "de_vs_us": "DE läuft deutlich verhaltener (3k vs 10k Likes).",
            "transfer_opportunity": "DE 28s-Cut auf 22s straffen, parallel zur US-Variante.",
        },
        "risks": ["Coverage moderat (60%)"],
        "data_caveats": ["Nur 2 DE-Posts im Fenster"],
    }

    import json as _json

    fake_message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=_json.dumps(sample_llm_json))],
        usage=SimpleNamespace(input_tokens=5000, output_tokens=800),
    )

    def _fake_call(**kwargs):
        return fake_message

    monkeypatch.setattr(insight_engine, "messages_create_text", _fake_call)
    monkeypatch.setattr(insight_engine, "is_anthropic_configured", lambda: True)

    with _session() as session:
        _seed_warnerbros_pair(session)
        report = insight_engine.generate_weekly_report(
            session, "warnerbros", dry_run=False
        )
        assert report.dry_run is False
        assert report.llm_output is not None
        assert report.llm_output.headline == "Headline test"
        assert report.llm_output.trends[0].name == "Hook unter 15s"
        # Cost estimate = (5000/1000)*0.015 + (800/1000)*0.075 = 0.075 + 0.06 = 0.135
        assert report.cost_usd_estimate == pytest.approx(0.135, abs=0.001)


def test_generate_handles_codefence_wrap(monkeypatch):
    """The model occasionally wraps JSON in ```json … ``` despite the
    instruction. The wrapper strips that defensively."""
    payload = (
        "```json\n"
        '{"headline":"H","tldr":"x","trends":[],"actions":[],'
        '"cross_market_insight":{"de_vs_us":"a","transfer_opportunity":"b"},'
        '"risks":[],"data_caveats":[]}\n'
        "```"
    )
    fake_message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=payload)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=10),
    )
    monkeypatch.setattr(insight_engine, "messages_create_text", lambda **k: fake_message)
    monkeypatch.setattr(insight_engine, "is_anthropic_configured", lambda: True)

    with _session() as session:
        _seed_warnerbros_pair(session)
        report = insight_engine.generate_weekly_report(session, "warnerbros")
        assert report.llm_output is not None
        assert report.llm_output.headline == "H"


def test_generate_surfaces_raw_text_on_parse_failure(monkeypatch):
    """If the model returns non-JSON, the report still resolves but
    ``llm_output`` is None and ``raw_llm_text`` carries the model's
    actual reply so Wolf can investigate the prompt."""
    fake_message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="oops, not json")],
        usage=SimpleNamespace(input_tokens=10, output_tokens=10),
    )
    monkeypatch.setattr(insight_engine, "messages_create_text", lambda **k: fake_message)
    monkeypatch.setattr(insight_engine, "is_anthropic_configured", lambda: True)

    with _session() as session:
        _seed_warnerbros_pair(session)
        report = insight_engine.generate_weekly_report(session, "warnerbros")
        assert report.llm_output is None
        assert report.raw_llm_text == "oops, not json"
