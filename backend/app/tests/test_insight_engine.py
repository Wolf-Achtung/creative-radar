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

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

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
    platform: str = "tiktok",
) -> Post:
    """Single post helper. ``url_suffix`` lets the caller hand-pick a URL
    so tests can match against ``post_url`` deterministically."""
    suffix = url_suffix or f"{caption[:8]}-{days_ago}-{likes}"
    if platform == "youtube":
        post_url = f"https://www.youtube.com/watch?v={suffix}"
    else:
        post_url = f"https://tiktok.com/@{channel.handle}/video/{suffix}"
    post = Post(
        channel_id=channel.id,
        platform=platform,
        post_url=post_url,
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
    """Jeder PAIRS-Eintrag muss das Schema erfüllen: label, platform,
    channels (mind. einen Channel, US-Markt vertreten), enabled, reason.
    Sprint 10d: ``channels`` darf mehrere US-Einträge enthalten (Disney
    pooled disneystudios/marvelstudios/pixar/starwars/20thcentury).

    Sprint UK-B1 (2026-05-12): UK ist ab B1 erlaubt als 3. Markt, aber
    optional. Pairs vor Phase A haben keinen UK-Eintrag und das ist ok.

    Sprint 2026-05-12 paramountplus+lionsgate: DE ist seitdem ebenfalls
    optional. Lionsgate ist US+UK-only (kein deutscher Social-Auftritt,
    Vertrieb läuft via Leonine/Studiocanal). Neuer harter Invariant:
    ``US ist Pflicht``, DE+UK sind optional, andere Märkte (INT,
    UNKNOWN, MIXED) bleiben raus. Die Invariant-Lockerung folgt der
    realen Marktrealität — wenn ein Studio in einem Land schlicht
    nicht selbst auftritt, gibt es keinen sinnvollen Channel zu pinnen.
    """
    pair_def = insight_engine.PAIRS[pair_key]
    assert isinstance(pair_def.get("label"), str) and pair_def["label"]
    assert pair_def.get("platform") == "tiktok", "Tier-A-Scope ist TikTok-only"
    assert isinstance(pair_def.get("enabled"), bool)
    if not pair_def["enabled"]:
        assert pair_def.get("reason"), "disabled pair muss reason haben"
    channels = pair_def.get("channels") or []
    assert len(channels) >= 1, "Pair braucht mind. einen Channel"
    markets = {c["market"] for c in channels}
    assert "US" in markets, "US-Markt ist Pflicht in jedem Pair"
    assert markets <= {"DE", "US", "UK"}, (
        f"Nur DE/US/UK erlaubt — {pair_key} hat {markets}"
    )
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
    neue Pair-Konfig den Endpoint trifft, bevor die Channels onboarded sind.

    Sprint 2026-05-12 paramountplus+lionsgate: ``de_channel`` ist nur
    gesetzt, wenn der Pair einen DE-Channel definiert. Lionsgate hat
    keinen DE-Auftritt, also bleibt ``de_channel`` legitim ``None``.
    """
    pair_def = insight_engine.PAIRS[pair_key]
    has_de = any(c["market"] == "DE" for c in pair_def["channels"])
    with _session() as session:
        agg = insight_engine.aggregate_pair(session, pair_key, window_days=30)
        assert agg.pair_key == pair_key
        assert agg.platform == "tiktok"
        assert agg.us_channel is not None
        assert agg.us_channel.channel_found is False
        assert any("US-Channel" in n for n in agg.notes)
        if has_de:
            assert agg.de_channel is not None
            assert agg.de_channel.channel_found is False
            assert any("DE-Channel" in n for n in agg.notes)
        else:
            assert agg.de_channel is None


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

    fake_message = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                name="submit_weekly_brief",
                input=sample_llm_json,
            )
        ],
        usage=SimpleNamespace(input_tokens=5000, output_tokens=800),
    )

    def _fake_call(**kwargs):
        return fake_message

    monkeypatch.setattr(insight_engine, "messages_create_strict_json", _fake_call)
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


def test_generate_weekly_report_logs_to_costlog(monkeypatch):
    """Cost-Tracking-Fix 2026-05-12 regression-guard. Before the fix the
    weekly-brief Opus call was the single most expensive Anthropic call
    we make but never landed in the costlog because
    ``record_anthropic_call`` was only invoked from the post_analyzer
    path. This test pins the persistence: one Opus message ->
    one CostLog row in the ``anthropic_opus`` bucket with the resolved
    millicent cost and the pair_key / iso_week meta."""
    from app.config import settings
    from app.models.entities import CostLog
    from app.services import cost_log as cost_log_module

    monkeypatch.setattr(settings, "anthropic_opus_input_per_1k_usd", 0.015, raising=False)
    monkeypatch.setattr(settings, "anthropic_opus_output_per_1k_usd", 0.075, raising=False)

    sample = {
        "headline": "H",
        "tldr": "x",
        "trends": [],
        "actions": [],
        "cross_market_insight": {"de_vs_us": "a", "transfer_opportunity": "b"},
        "risks": [],
        "data_caveats": [],
    }
    fake_message = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                name="submit_weekly_brief",
                input=sample,
            )
        ],
        usage=SimpleNamespace(input_tokens=5000, output_tokens=800),
    )
    monkeypatch.setattr(insight_engine, "messages_create_strict_json", lambda **k: fake_message)
    monkeypatch.setattr(insight_engine, "is_anthropic_configured", lambda: True)

    with _session() as session:
        # cost_log._persist opens its own Session against the module-level
        # engine. Pin it to the test's in-memory engine so we can read
        # the persisted row back.
        monkeypatch.setattr(cost_log_module, "engine", session.get_bind())

        _seed_warnerbros_pair(session)
        report = insight_engine.generate_weekly_report(session, "warnerbros")
        assert report.llm_output is not None

        rows = session.exec(select(CostLog)).all()
        # input_usd  = 5000/1000 * 0.015 = 0.075
        # output_usd = 800/1000  * 0.075 = 0.06
        # total      = 0.135 USD = 13.5 cents = 13500 millicents
        anthropic_rows = [r for r in rows if r.provider == "anthropic_opus"]
        assert len(anthropic_rows) == 1
        row = anthropic_rows[0]
        assert row.operation == "weekly_brief"
        assert row.cost_usd_millicents == 13_500
        # int(round(13.5)) -> 14 under Python's banker's rounding to int
        assert row.cost_usd_cents in (13, 14)
        assert row.cost_meta["pair_key"] == "warnerbros"
        assert row.cost_meta["model"].startswith("claude-opus")


def test_generate_handles_codefence_wrap(monkeypatch):
    """The model occasionally wraps JSON in ```json … ``` despite the
    instruction. The wrapper strips that defensively.

    Sprint 28.05.2026 (Structured-Outputs-Haertung): API-erzwungenes JSON
    via Tool-Use macht diesen Pfad zur Safety-Net-Strecke — kein
    Produktionsfall mehr, aber der Lenient-Parser bleibt aktiv, falls die
    API mal einen Text-Block statt Tool-Use liefert. Mock simuliert genau
    das: ``type='text'`` + Codefence-Wrap, die Extraktion faellt auf den
    Text-Fallback und der bestehende ``_strip_codefence``-Pfad rettet.
    """
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
    monkeypatch.setattr(insight_engine, "messages_create_strict_json", lambda **k: fake_message)
    monkeypatch.setattr(insight_engine, "is_anthropic_configured", lambda: True)

    with _session() as session:
        _seed_warnerbros_pair(session)
        report = insight_engine.generate_weekly_report(session, "warnerbros")
        assert report.llm_output is not None
        assert report.llm_output.headline == "H"


# ---------- Sprint-Trailerhaus-Prompt-v1 -----------------------------------


def test_historical_top_posts_returns_pre_window_posts():
    """``aggregate_pair`` must surface up to 3 historical top-posts per
    channel from BEFORE the window so the LLM has reference material for
    the ``vergleichbare_posts`` section."""
    with _session() as session:
        data = _seed_warnerbros_pair(session)
        # add three older US posts at 60/90/120 days ago — outside the 30d window
        old_a = _make_post(
            session, data["us_channel"],
            caption="ancient hit", likes=50_000, days_ago=60, url_suffix="oldA",
        )
        _make_post(
            session, data["us_channel"],
            caption="middle hit", likes=20_000, days_ago=90, url_suffix="oldB",
        )
        _make_post(
            session, data["us_channel"],
            caption="early hit", likes=5_000, days_ago=120, url_suffix="oldC",
        )
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        hist = agg.us_channel.historical_top_posts
        assert len(hist) == 3
        # sorted by engagement desc — the 50k post must lead
        assert hist[0].post_url == old_a.post_url
        assert hist[0].engagement_sum >= hist[1].engagement_sum >= hist[2].engagement_sum


def test_historical_top_posts_empty_when_no_pre_window_history():
    """Channel with only in-window posts has no historical reference —
    return an empty list, not an error."""
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        # All seeded posts are inside the window (days_ago<=15)
        assert agg.us_channel.historical_top_posts == []
        assert agg.de_channel.historical_top_posts == []


def test_historical_top_posts_ignores_lookback_too_old():
    """Posts older than the 6-month lookback are dropped — keeps the
    reference window aligned with current campaign era."""
    with _session() as session:
        data = _seed_warnerbros_pair(session)
        _make_post(
            session, data["us_channel"],
            caption="prehistoric", likes=999_999, days_ago=400, url_suffix="prehist",
        )
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        urls = [p.post_url for p in agg.us_channel.historical_top_posts]
        assert not any("prehist" in u for u in urls)


def test_llm_report_parses_new_sections_when_present(monkeypatch):
    """The expanded LLMReport schema parses the six new role-oriented
    sections without dropping the old ones. Verifies backwards-compat
    AND forward-fit."""
    sample = {
        "headline": "H",
        "tldr": "x.",
        "trends": [],
        "actions": [],
        "cross_market_insight": {"de_vs_us": "a", "transfer_opportunity": "b"},
        "risks": [],
        "data_caveats": [],
        "tonalitaet": [
            {"adjektiv": "präzise", "begruendung": "Top-Post mit klarer Hook"}
        ],
        "watch_outs": [
            {"watch_out": "BTS performt", "konsequenz": "als Komplement testen"}
        ],
        "fuer_cutter": {
            "schnitt_pace": "15-30s",
            "hook_strategie": "Cold-Open",
            "empfohlene_laengen": "22s",
            "must_show": ["Hauptkonflikt"],
            "no_go": ["Caption-Overload"],
        },
        "fuer_motion_designer": {
            "caption_style": "kurz",
            "text_overlay": "L3 minimal",
            "branding_einsatz": "End Card 1s",
        },
        "fuer_creative_producer": {
            "strategische_pattern": "Pace-Disziplin",
            "cross_market_chancen": "DE adaptiert US",
            "format_empfehlungen": "22s + 12s",
        },
        "vergleichbare_posts": [
            {
                "post_id": "https://tiktok.com/@warnerbros/video/us1",
                "handle": "warnerbros",
                "performance_kpi": "11k Engagement",
                "relevanz_grund": "Goldstandard 22s-Hook",
            }
        ],
    }
    fake_message = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                name="submit_weekly_brief",
                input=sample,
            )
        ],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=2000),
    )
    monkeypatch.setattr(insight_engine, "messages_create_strict_json", lambda **k: fake_message)
    monkeypatch.setattr(insight_engine, "is_anthropic_configured", lambda: True)

    with _session() as session:
        _seed_warnerbros_pair(session)
        report = insight_engine.generate_weekly_report(session, "warnerbros")
        assert report.llm_output is not None
        out = report.llm_output
        assert out.tonalitaet and out.tonalitaet[0].adjektiv == "präzise"
        assert out.watch_outs and out.watch_outs[0].konsequenz == "als Komplement testen"
        assert out.fuer_cutter and out.fuer_cutter.empfohlene_laengen == "22s"
        assert out.fuer_motion_designer and out.fuer_motion_designer.caption_style == "kurz"
        assert out.fuer_creative_producer and out.fuer_creative_producer.strategische_pattern == "Pace-Disziplin"
        assert out.vergleichbare_posts and out.vergleichbare_posts[0].handle == "warnerbros"


def test_llm_report_parses_old_schema_for_backwards_compat(monkeypatch):
    """A response missing all six new sections must still parse — old
    saved reports and any rare LLM regression must not 500."""
    sample_old = {
        "headline": "H",
        "tldr": "x.",
        "trends": [],
        "actions": [],
        "cross_market_insight": {"de_vs_us": "a", "transfer_opportunity": "b"},
        "risks": ["R"],
        "data_caveats": ["C"],
    }
    fake_message = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                name="submit_weekly_brief",
                input=sample_old,
            )
        ],
        usage=SimpleNamespace(input_tokens=10, output_tokens=10),
    )
    monkeypatch.setattr(insight_engine, "messages_create_strict_json", lambda **k: fake_message)
    monkeypatch.setattr(insight_engine, "is_anthropic_configured", lambda: True)

    with _session() as session:
        _seed_warnerbros_pair(session)
        report = insight_engine.generate_weekly_report(session, "warnerbros")
        assert report.llm_output is not None
        # all new fields are None (not crashes)
        assert report.llm_output.tonalitaet is None
        assert report.llm_output.watch_outs is None
        assert report.llm_output.fuer_cutter is None


def test_system_prompt_blocks_known_anti_patterns():
    """Sanity guard: the system prompt explicitly names the LLM-typical
    English X-Y-Floskeln we want to block. If someone weakens the
    anti-pattern block in a future refactor, this test catches it.

    Cleanup A2: ``Catalog-Reaktivierung`` ist seit der Trailerhaus-Voice-v2-
    Umstellung (commit d7ea8a0) das im Prompt geführte Catalog-Verbot —
    die frühere Standalone-Vokabel ``Catalog-Nostalgie`` wurde durch das
    breitere Bucket ``Catalog-Mid, Catalog-Reaktivierung, Catalog-Hook``
    ersetzt. Test sichert weiterhin den Catalog-Guard, nur mit dem
    aktuellen Token."""
    prompt = insight_engine.SYSTEM_PROMPT
    for forbidden in (
        "Brand-Storytelling",
        "Engagement-Drivers",
        "Hook-Architektur",
        "Live-Event-Framing",
        "Catalog-Reaktivierung",
    ):
        assert forbidden in prompt, (
            f"Anti-pattern guard removed for {forbidden!r} — re-add to "
            "system prompt or remove from this test consciously."
        )
    # The voice anchor must stay
    assert "Audiovisual Communication" in prompt or "Trailerhaus" in prompt


def test_system_prompt_includes_glossary_and_voice_markers():
    prompt = insight_engine.SYSTEM_PROMPT
    for vocab in ("Hook", "Pace", "Cold-Open", "L3", "End Card", "GSA"):
        assert vocab in prompt, f"Glossary entry {vocab!r} missing from system prompt"
    # Tonalitäts-Pool sample
    assert "authentisch" in prompt
    assert "sophisticated" in prompt


def test_user_prompt_mentions_ganz_genau_mode():
    """The framing must tell the model to produce the long-form output —
    otherwise the model defaults to ~500 words."""
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        prompt = insight_engine._build_user_prompt(agg)
        assert "ganz genau" in prompt
        assert "historical_top_posts" in prompt


# ---------- Sprint 6: Multi-Plattform user-prompt -------------------------


def test_user_prompt_uses_platform_headers():
    """Sprint 6 — pro Plattform mit Daten gibt es einen ``## TikTok``/
    ``## Instagram``/``## YouTube``-Header. Multi-Plattform-Awareness im
    Headline/TLDR setzt voraus, dass der LLM die Plattformen scannen kann
    statt sie aus der JSON-Struktur abzuleiten."""
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        prompt = insight_engine._build_user_prompt(agg)
        # Fixture seedet TikTok-Posts, also muss zumindest der TT-Header da sein.
        assert "## TikTok" in prompt


def test_user_prompt_skips_empty_platform():
    """Komplett leere Plattformen (kein DE, kein US, keine Cross-Market-
    Matches) erscheinen NICHT im Prompt — Token-Sparen + kein "Keine
    Daten"-Filler. Die Fixture hat nur TikTok-Daten; IG/YT-Header dürfen
    nicht erscheinen, denn die ``per_platform``-Liste enthält sie nicht."""
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        prompt = insight_engine._build_user_prompt(agg)
        assert "## Instagram" not in prompt
        assert "## YouTube" not in prompt


def test_user_prompt_includes_title_marker_when_present():
    """RankedPost mit ``title_local`` wird im Markdown-Overview als
    ``[*Titel*]`` gerendert — der Marker ist die Eintrittsstelle, an der
    der LLM erkennt, dass *Titel*-Markup in Headline/TLDR erlaubt ist."""
    with _session() as session:
        data = _seed_warnerbros_pair(session)
        # Fixture-Title hat title_local=None — nachsetzen für die Assertion.
        title = data["title"]
        title.title_local = "Mortal Kombat II"
        session.add(title)
        session.commit()

        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        prompt = insight_engine._build_user_prompt(agg)
        assert "[*Mortal Kombat II*]" in prompt


def test_user_prompt_no_title_marker_when_absent():
    """Posts ohne ``title_local`` haben **keinen** ``[*…*]``-Marker — wir
    erfinden keine Titel und der Few-Shot demonstriert die Genre-Fallback-
    Erzählung. Die Fixture-Title hat von Haus aus title_local=None und
    title_original=Mortal Kombat II — also sollte weder ``[*Mortal Kombat II*]``
    noch sonstige eckige-Klammern-Marker im Prompt erscheinen."""
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        prompt = insight_engine._build_user_prompt(agg)
        # Kein ``[*…*]``-Marker im Top-Posts-Block (egal welcher Titel).
        # Die Klammer-Marker tauchen ausschließlich um Filmtitel auf.
        import re as _re
        assert _re.search(r"\[\*[^*\]]+\*\]", prompt) is None


def test_user_prompt_token_budget_under_12k():
    """Token-Budget-Guard: der komplette Multi-Plattform-Prompt für die
    Warnerbros-Fixture muss unter 12k Tokens bleiben. ``_estimate_tokens``
    ist kein echter Tokenizer, aber Zeichen/4 ist die etablierte Faustregel
    bei Anthropic-Claude-Prompts und genau genug für den Sprint-Guard."""
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        prompt = insight_engine._build_user_prompt(agg)
        approx_tokens = len(prompt) / 4
        assert approx_tokens < 12000, (
            f"Multi-Platform-Prompt liegt bei ~{approx_tokens:.0f} Tokens — "
            f"über dem Sprint-6-Budget von 12k. Ranked-Posts-Limit oder "
            f"Caption-Truncation prüfen."
        )


def test_user_prompt_includes_tone_reminder():
    """Ton-Pass — der sachlich-berichtende Reminder steht direkt im
    User-Prompt-Header, vor den Daten. Greift erfahrungsgemäß stärker
    als die BERICHTSTON-Sektion 1500 Tokens weiter oben. Sichert, dass
    der Reminder den neuen Ton (sachlich, ausgeschriebene Zahlen) trägt."""
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        prompt = insight_engine._build_user_prompt(agg)
        assert "Erinnerung zum Ton" in prompt
        assert "sachlich" in prompt
        assert "33.000" in prompt  # Zahlen ausgeschrieben, nicht "33k"


def test_user_prompt_caps_ranked_posts_at_five_per_channel():
    """Sprint-6-Budget-Maßnahme: Top-5 statt Top-10 Posts pro Channel
    im Markdown-Overview. Die Fixture hat 3 US-Posts → alle erscheinen,
    aber bei mehr als 5 würde die Liste abgeschnitten."""
    with _session() as session:
        data = _seed_warnerbros_pair(session)
        # Sechs zusätzliche US-Posts hinzufügen, damit der 5er-Cut greift.
        for i in range(6):
            _make_post(
                session, data["us_channel"],
                caption=f"Filler post #{i} #Filler",
                likes=100 + i, days_ago=4 + i, url_suffix=f"us-filler-{i}",
            )
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        prompt = insight_engine._build_user_prompt(agg)
        # Im US-Block der TikTok-Sektion zählen wir die Top-Posts-Zeilen.
        import re as _re
        us_section = prompt.split("### US:")[1].split("###")[0] if "### US:" in prompt else ""
        post_lines = _re.findall(r"^\s+\d+\.\s", us_section, _re.MULTILINE)
        assert len(post_lines) <= 5, (
            f"Erwartet höchstens 5 Top-Posts im US-Block, fand {len(post_lines)}."
        )


def test_generate_surfaces_raw_text_on_parse_failure(monkeypatch):
    """If the model returns non-JSON, the report still resolves but
    ``llm_output`` is None and ``raw_llm_text`` carries the model's
    actual reply so Wolf can investigate the prompt.

    Sprint 28.05.2026 (Structured-Outputs-Haertung): Mit Tool-Use sollte
    der Total-Fail-Pfad praktisch nie auftreten — wenn die API doch
    irgendwann nur einen Text-Block (kein Tool-Use) liefert, faellt die
    Extraktion auf den Text-Fallback und der bestehende Retry-Loop +
    Final-Fallback (``llm_output=None``, ``raw_llm_text`` persistiert)
    bleiben als Sicherheitsnetz unveraendert.
    """
    fake_message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="oops, not json")],
        usage=SimpleNamespace(input_tokens=10, output_tokens=10),
    )
    monkeypatch.setattr(insight_engine, "messages_create_strict_json", lambda **k: fake_message)
    monkeypatch.setattr(insight_engine, "is_anthropic_configured", lambda: True)

    with _session() as session:
        _seed_warnerbros_pair(session)
        report = insight_engine.generate_weekly_report(session, "warnerbros")
        assert report.llm_output is None
        assert report.raw_llm_text == "oops, not json"


# ---------- Sprint 2: Ranking + Activation-Rate ----------------------------


def test_compute_activation_rate_tiktok():
    """TT/IG path: ``(likes + comments + saves) / views``."""
    post = SimpleNamespace(
        visible_views=1000,
        visible_likes=50,
        visible_comments=10,
        visible_bookmarks=15,  # saves
        visible_shares=5,
    )
    rate = insight_engine.compute_activation_rate(post, "tiktok")
    # (50 + 10 + 15) / 1000 = 0.075
    assert rate == pytest.approx(0.075)


def test_compute_activation_rate_youtube_no_saves():
    """YouTube path: saves are NOT counted (YT API doesn't surface them).
    ``(likes + comments) / views``."""
    post = SimpleNamespace(
        visible_views=1000,
        visible_likes=50,
        visible_comments=10,
        visible_bookmarks=15,  # MUST NOT be added on YT
        visible_shares=5,
    )
    rate = insight_engine.compute_activation_rate(post, "youtube")
    # (50 + 10) / 1000 = 0.06 — saves intentionally ignored.
    assert rate == pytest.approx(0.06)


def test_compute_activation_rate_zero_views_returns_zero():
    """views in {0, None} → 0.0. No NaN, no ZeroDivisionError."""
    post_zero = SimpleNamespace(
        visible_views=0,
        visible_likes=100, visible_comments=10, visible_bookmarks=5, visible_shares=2,
    )
    post_none = SimpleNamespace(
        visible_views=None,
        visible_likes=100, visible_comments=10, visible_bookmarks=5, visible_shares=2,
    )
    assert insight_engine.compute_activation_rate(post_zero, "tiktok") == 0.0
    assert insight_engine.compute_activation_rate(post_none, "tiktok") == 0.0
    assert insight_engine.compute_activation_rate(post_zero, "youtube") == 0.0


def test_ranked_posts_sorted_by_engagement_sum():
    """Backend default-sort is ``engagement_sum`` desc. Frontend re-sorts
    client-side — but Backend order must be stable across calls so
    cache-hit responses match cache-miss responses byte-for-byte (within
    the JSON-roundtrip tolerance)."""
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        us_ranked = agg.us_channel.ranked_posts
        # Fixture seeds 3 US posts; ranked_posts limit=10 → all 3 returned.
        assert len(us_ranked) == 3
        # us_p1: 10000 + 500 + 200 + 400 = 11_100
        # us_p2:  5000 + 100 +  50 +  80 =  5_230
        # us_p3:  1000 +  20 +   5 +  10 =  1_035
        assert us_ranked[0].engagement_sum == 11_100
        assert us_ranked[1].engagement_sum == 5_230
        assert us_ranked[2].engagement_sum == 1_035
        sums = [r.engagement_sum for r in us_ranked]
        assert sums == sorted(sums, reverse=True)
        # Each entry carries the platform tag (today all "tiktok"; pre-wires
        # the multi-platform pill rendering on the Frontend).
        assert all(r.platform == "tiktok" for r in us_ranked)


# ---------- Sprint 5b: title + thumbnail eager-load ------------------------


def test_ranked_posts_loads_thumbnail_when_available():
    """Sprint 5b — wenn das Asset eines Posts ``thumbnail_url`` trägt,
    fließt die URL in die ``RankedPost.thumbnail_url`` durch (per
    JOIN-im-Engine, kein Frontend-Patch)."""
    with _session() as session:
        data = _seed_warnerbros_pair(session)
        # Fixture-Asset von us_p1 hat noch kein thumbnail_url — nachbessern.
        us_asset = session.exec(
            select(Asset).where(Asset.post_id == data["us_posts"][0].id)
        ).first()
        us_asset.thumbnail_url = "https://cdn.example.com/thumb-us1.jpg"
        session.add(us_asset)
        session.commit()

        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        us_top = agg.us_channel.ranked_posts[0]
        assert us_top.post_url == data["us_posts"][0].post_url
        assert us_top.thumbnail_url == "https://cdn.example.com/thumb-us1.jpg"


def test_ranked_posts_loads_title_when_available():
    """Wenn ein Asset über ``title_id`` an einen ``Title`` gebunden ist,
    werden ``title_original`` (+ optional ``title_local``/``franchise``)
    in den RankedPost übernommen. Die Fixture hängt us_p1 an
    'Mortal Kombat II' — der Top-RankedPost muss das spiegeln."""
    with _session() as session:
        data = _seed_warnerbros_pair(session)
        # title_local + franchise nachsetzen für eine vollständige Assertion.
        title = data["title"]
        title.title_local = "Mortal Kombat II"
        title.franchise = "Mortal Kombat"
        session.add(title)
        session.commit()

        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        us_top = agg.us_channel.ranked_posts[0]
        assert us_top.title_original == "Mortal Kombat II"
        assert us_top.title_local == "Mortal Kombat II"
        assert us_top.franchise == "Mortal Kombat"


def test_ranked_posts_populates_content_type_from_title():
    """Sprint 10i: ``Title.content_type`` wird in ``RankedPost.content_type``
    durchgereicht. Default ist 'Film' (laut Title-Model-Default) — Series-
    Titles müssen explizit gesetzt werden. Beide Werte landen im RankedPost,
    damit das LLM-Prompt-Format darauf reagieren kann."""
    with _session() as session:
        data = _seed_warnerbros_pair(session)
        title = data["title"]
        title.title_local = "Mortal Kombat II"
        title.content_type = "Film"
        session.add(title)
        session.commit()

        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        us_top = agg.us_channel.ranked_posts[0]
        assert us_top.content_type == "Film"

        # Switch to Series — verifies the field actually mirrors the column,
        # not a hard-coded default.
        title.content_type = "Series"
        session.add(title)
        session.commit()

        agg2 = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        us_top2 = agg2.us_channel.ranked_posts[0]
        assert us_top2.content_type == "Series"


def test_ranked_posts_content_type_none_when_no_title():
    """Posts ohne gemappten Title (häufiger Fall: ~93% laut Coverage-Stats)
    bekommen weiterhin ``content_type=None``. Back-compat-Default des
    Schemas — keine Pflichtangabe."""
    from app.models.entities import Channel, Market, Post

    with _session() as session:
        ch = Channel(
            name="Warner Bros US",
            platform="tiktok",
            url="https://www.tiktok.com/@warnerbros",
            handle="warnerbros",
            market=Market.US,
        )
        ch_de = Channel(
            name="Warner Bros DE",
            platform="tiktok",
            url="https://www.tiktok.com/@warnerbrosdeutschland",
            handle="warnerbrosdeutschland",
            market=Market.DE,
        )
        session.add_all([ch, ch_de])
        session.commit()
        session.refresh(ch)

        _make_post(
            session, ch,
            caption="Untitled drop #Trailer",
            likes=500, comments=10, shares=2, saves=5, duration=20,
            days_ago=2, url_suffix="us-untitled-1",
        )
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        us_top = agg.us_channel.ranked_posts[0]
        assert us_top.content_type is None


def test_format_ranked_post_line_marks_series_with_suffix():
    """Sprint 10i: Format-Marker ``[*Title* — Serie]`` für Series-RankedPosts,
    Default-Marker ``[*Title*]`` für Films und kein Marker, wenn kein
    ``title_local`` gesetzt ist. Das LLM stützt sich auf diesen Marker als
    Anchor in der Filmtitel-Klausel (System-Prompt)."""
    from app.schemas.insights import RankedPost

    film = RankedPost(
        post_url="https://tt.com/film",
        views=1_000, likes=100, activation_rate=0.05,
        title_local="Mortal Kombat II", content_type="Film",
    )
    series = RankedPost(
        post_url="https://tt.com/series",
        views=2_000, likes=200, activation_rate=0.04,
        title_local="Daredevil: Born Again", content_type="Series",
    )
    untitled = RankedPost(
        post_url="https://tt.com/none", views=500, likes=50,
        activation_rate=0.03,
    )

    film_line = insight_engine._format_ranked_post_line(1, film)
    series_line = insight_engine._format_ranked_post_line(2, series)
    untitled_line = insight_engine._format_ranked_post_line(3, untitled)

    assert "[*Mortal Kombat II*]" in film_line
    assert "— Serie" not in film_line
    assert "[*Daredevil: Born Again* — Serie]" in series_line
    # Untitled-Posts behalten den marker-freien Default-Layout.
    assert "[*" not in untitled_line


def test_ranked_post_legacy_persisted_brief_loads_without_content_type():
    """Persistenz-Vertrag: ein vor Sprint 10i geschriebenes RankedPost-JSON
    hat kein ``content_type``-Feld. ``model_validate`` muss es trotzdem
    parsen und ``content_type=None`` defaulten — der Cache-Hit-Pfad in
    api/insights.py würde sonst auf jedem alten Brief crashen."""
    from app.schemas.insights import RankedPost

    legacy_payload = {
        "post_url": "https://tt.com/legacy",
        "caption_excerpt": "Older post pre-10i",
        "platform": "tiktok",
        "views": 1234,
        "likes": 56,
        "comments": 7,
        "saves": 0,
        "shares": 0,
        "engagement_sum": 63,
        "activation_rate": 0.0512,
        "title_local": "Older Title",
        "title_original": "Older Title",
    }
    rp = RankedPost.model_validate(legacy_payload)
    assert rp.title_local == "Older Title"
    assert rp.content_type is None


def test_system_prompt_documents_series_marker_suffix():
    """Sprint 10i: das System-Prompt-Filmtitel-Kapitel kennt den
    ``— Serie``-Suffix-Marker und unterscheidet Streaming-Series von
    Theatrical-Releases. Wenn jemand die Klausel in einem späteren
    Refactor schwächt, fängt dieser Test es ab."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "— Serie" in prompt, (
        "Filmtitel-Klausel muss den Series-Marker dokumentieren — "
        "sonst weiß das LLM nicht, wie es Streaming-Series im Markup "
        "behandeln soll."
    )


def test_ranked_posts_handles_post_without_asset():
    """Posts ohne irgendein Asset rendern weiterhin als RankedPost — alle
    vier Sprint-5b-Felder bleiben ``None``, kein Crash, kein KeyError im
    Mapping."""
    with _session() as session:
        data = _seed_warnerbros_pair(session)
        us_no_asset = _make_post(
            session, data["us_channel"],
            caption="orphan post — no asset row at all",
            likes=20_000, comments=900, shares=300, saves=600,
            days_ago=1, url_suffix="us-no-asset",
        )

        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        ranked = {r.post_url: r for r in agg.us_channel.ranked_posts}
        orphan = ranked[us_no_asset.post_url]
        assert orphan.thumbnail_url is None
        assert orphan.title_local is None
        assert orphan.title_original is None
        assert orphan.franchise is None


def test_ranked_posts_prefers_asset_with_title_over_one_without():
    """Wenn ein Post mehrere Assets hat, gewinnt das mit ``title_id`` —
    sonst würde das Discovery-Asset (kein Titel) den Filmtitel im
    RankedPost verschlucken, obwohl er bekannt ist."""
    with _session() as session:
        data = _seed_warnerbros_pair(session)
        # us_p3 hat heute nur ein Discovery-Asset (kein title_id). Wir hängen
        # ein zweites Asset MIT title an denselben Post — das muss gewinnen.
        session.add(Asset(
            post_id=data["us_posts"][2].id,
            title_id=data["title"].id,
            thumbnail_url="https://cdn.example.com/thumb-us3-with-title.jpg",
        ))
        session.commit()

        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        us_p3_ranked = next(
            r for r in agg.us_channel.ranked_posts
            if r.post_url == data["us_posts"][2].post_url
        )
        assert us_p3_ranked.title_original == "Mortal Kombat II"
        assert us_p3_ranked.thumbnail_url == "https://cdn.example.com/thumb-us3-with-title.jpg"


def test_ranked_posts_skips_rejected_assets():
    """Assets mit ``review_status='rejected'`` werden vom Eager-Load
    ausgeschlossen — die Ranking-Card darf keine Curator-abgelehnten
    Thumbnails surface'n."""
    from app.models.entities import ReviewStatus
    with _session() as session:
        data = _seed_warnerbros_pair(session)
        # de_p2 hat heute ein Asset ohne title_id und ohne thumbnail. Wir
        # ergänzen ein zweites, MIT thumbnail, aber als REJECTED markiert —
        # das darf NICHT in den RankedPost durchschlagen.
        session.add(Asset(
            post_id=data["de_posts"][1].id,
            title_id=data["title"].id,
            thumbnail_url="https://cdn.example.com/REJECTED.jpg",
            review_status=ReviewStatus.REJECTED,
        ))
        session.commit()

        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        de_p2_ranked = next(
            r for r in agg.de_channel.ranked_posts
            if r.post_url == data["de_posts"][1].post_url
        )
        assert de_p2_ranked.thumbnail_url is None
        assert de_p2_ranked.title_original is None


def test_ranked_posts_eager_load_runs_in_single_query():
    """Performance-Guard: das Eager-Load darf NICHT N+1 sein. Wir
    instrumentieren das SQLAlchemy ``before_cursor_execute``-Event und
    prüfen, dass kein SELECT auf ``asset`` öfter als einmal pro Channel
    feuert. Schwellwert großzügig (≤ 4 Asset-SELECTS für DE+US ohne
    LLM), damit harmlose Side-Queries (z. B. CrossMarket) nicht
    fehlschlagen — die Kernzusicherung ist: kein per-post Select."""
    from sqlalchemy import event
    seen_asset_selects: list[str] = []

    with _session() as session:
        _seed_warnerbros_pair(session)

        def _record(conn, cursor, statement, params, context, executemany):
            if "FROM asset" in statement.lower().replace('"', ''):
                seen_asset_selects.append(statement)

        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", _record)
        try:
            insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        finally:
            event.remove(engine, "before_cursor_execute", _record)

        # Hard cap: keine N+1-Schleife pro Post. Mit der Sprint-5b-Query
        # erwarten wir wenige Aggregat-SELECTS; ein Vielfaches der Post-
        # Anzahl wäre der Regressions-Smell.
        post_count = 5  # 3 US + 2 DE in der Fixture
        assert len(seen_asset_selects) < post_count, (
            f"Expected aggregate Asset SELECTs (< {post_count}), got "
            f"{len(seen_asset_selects)} — possible N+1 regression."
        )


def test_ranked_posts_includes_asset_id_when_asset_loaded():
    """Sprint 5c — wenn ein Asset für den Post existiert, fließt dessen
    UUID als String in ``RankedPost.asset_id`` durch. Frontend nutzt das
    für ``/api/thumbnails/{asset_id}``-Requests."""
    with _session() as session:
        data = _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        us_top = agg.us_channel.ranked_posts[0]
        assert us_top.post_url == data["us_posts"][0].post_url
        # Asset-UUID gewinnt — die Fixture hängt für us_p1 ein Asset mit
        # title_id an; daher gewinnt es im CASE-Sortier-Pfad.
        assert us_top.asset_id is not None
        # Stringified UUID — kein UUID-Objekt durchschlagen lassen, sonst
        # bricht JSON-Persistenz im insight_report-Cache.
        assert isinstance(us_top.asset_id, str)
        assert len(us_top.asset_id) == 36  # canonical UUID length


def test_ranked_posts_handles_missing_asset_id():
    """Posts ohne irgendein Asset bekommen ``asset_id=None`` zurück, ohne
    Crash. Frontend fällt dann auf den direkten ``thumbnail_url`` zurück
    (selbst wiederum None für orphane Posts → Plattform-Fallback)."""
    with _session() as session:
        data = _seed_warnerbros_pair(session)
        us_no_asset = _make_post(
            session, data["us_channel"],
            caption="orphan post — no asset row at all",
            likes=20_000, comments=900, shares=300, saves=600,
            days_ago=1, url_suffix="us-no-asset-5c",
        )

        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        ranked = {r.post_url: r for r in agg.us_channel.ranked_posts}
        orphan = ranked[us_no_asset.post_url]
        assert orphan.asset_id is None
        assert orphan.thumbnail_url is None


def test_backwards_compat_old_brief_without_new_fields():
    """Persistierte Briefe vor Sprint 5b kennen die vier neuen Felder
    nicht. ``RankedPost.model_validate`` muss sie auf ``None``
    defaulten, ohne Schema-Validation-Error."""
    from app.schemas.insights import RankedPost
    legacy_payload = {
        "post_url": "https://tiktok.com/@warnerbros/video/legacy",
        "caption_excerpt": "Legacy brief from before Sprint 5b",
        "platform": "tiktok",
        "views": 1234,
        "likes": 56,
        "comments": 7,
        "saves": 8,
        "shares": 9,
        "engagement_sum": 80,
        "activation_rate": 0.05,
    }
    rp = RankedPost.model_validate(legacy_payload)
    assert rp.title_local is None
    assert rp.title_original is None
    assert rp.franchise is None
    assert rp.thumbnail_url is None
    assert rp.asset_id is None
    assert rp.engagement_sum == 80


def test_avg_activation_rate_in_channel_stats():
    """avg_activation_rate is the arithmetic mean across all posts in the
    window. Fixture posts have visible_views=None, so each rate is 0.0 →
    mean is 0.0. The assertion guards two things: (1) the field is a
    populated float (not None, not NaN) and (2) ranked_posts is filled
    in alongside it."""
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        us_stats = agg.us_channel
        assert us_stats.avg_activation_rate == 0.0
        assert isinstance(us_stats.avg_activation_rate, float)
        assert len(us_stats.ranked_posts) == 3


# ---------- Sprint 3: Voice-Korrektur (Headline/TLDR rules + Few-Shot) -----


def _extract_headline_spec(prompt: str) -> str:
    """Snip from the OUTPUT-Schema-Block: the line describing the headline
    field. We anchor on the JSON-style key and read until the next key —
    the schema block uses one description per line so this is enough."""
    import re
    m = re.search(r'"headline":\s*"(.+?)",\s*\n', prompt, re.DOTALL)
    return m.group(1) if m else ""


def _extract_tldr_spec(prompt: str) -> str:
    import re
    m = re.search(r'"tldr":\s*"(.+?)",\s*\n', prompt, re.DOTALL)
    return m.group(1) if m else ""


def _extract_few_shot(prompt: str) -> str:
    """Everything after the FEW-SHOT marker until the closing of the
    triple-quoted SYSTEM_PROMPT. The block is the realised JSON example."""
    marker = "FEW-SHOT"
    idx = prompt.find(marker)
    return prompt[idx:] if idx != -1 else ""


def _extract_few_shot_headline(prompt: str) -> str:
    """The first ``"headline"`` value AFTER the FEW-SHOT marker — the
    realised example, not the schema description."""
    import re
    fs = _extract_few_shot(prompt)
    m = re.search(r'"headline":\s*"(.+?)",\s*\n', fs)
    return m.group(1) if m else ""


def _extract_few_shot_tldr(prompt: str) -> str:
    import re
    fs = _extract_few_shot(prompt)
    m = re.search(r'"tldr":\s*"(.+?)",\s*\n', fs)
    return m.group(1) if m else ""


def test_brief_voice_is_shared_prefix_of_system_prompt():
    """C1 — BRIEF_VOICE is the field-agnostic tone slice the title brief
    reuses. It must be a true prefix of the (unchanged) SYSTEM_PROMPT, carry
    the voice anchors (#222/#223), and stop before the pair output-schema."""
    voice = insight_engine.BRIEF_VOICE
    prompt = insight_engine.SYSTEM_PROMPT
    # Byte-identical pair prompt: voice is a literal prefix.
    assert prompt.startswith(voice)
    assert len(voice) < len(prompt)
    # Ton-Anker der neuen Berichts-Voice (Ton-Pass).
    assert "BERICHTSTON" in voice
    assert "ZAHLEN AUSSCHREIBEN" in voice       # Regel 3
    assert "VORHER / NACHHER" in voice          # Zielstil-Beispiel
    assert "TONALITÄTS-POOL" in voice
    # Pair output-schema block stays OUT of the shared voice.
    assert "SCHEMA-VOKABEL" not in voice
    assert "FEW-SHOT" not in voice
    assert "submit_weekly_brief" not in voice


def test_anti_pattern_block_includes_headline_tldr_extension():
    """Sprint 3 — the headline+tldr-only anti-pattern carve-out must
    name the four aggregation terms that belong to detail sections, not
    to the GF/CD-facing headline/tldr."""
    prompt = insight_engine.SYSTEM_PROMPT
    # The anti-pattern sub-block exists
    assert "ANTI-PATTERN HEADLINE/TLDR" in prompt, \
        "Headline/TLDR-only anti-pattern sub-block missing"
    # And it lists the four forbidden aggregation terms
    for forbidden in ("Coverage", "Cross-Market Match", "Längen-Bucket", "Engagement-Sum"):
        assert forbidden in prompt, \
            f"Anti-pattern term {forbidden!r} missing from prompt"


def test_few_shot_uses_real_umlauts():
    """No ae/oe/ue/ss pseudo-umlauts left in the few-shot's German
    prose. JSON keys (fuer_cutter, tonalitaet, etc.) are exempt — those
    are the ASCII-key contract from Sprint 1."""
    few_shot = _extract_few_shot(insight_engine.SYSTEM_PROMPT).lower()
    forbidden_pseudo = ("aendern", "fuer ", " ueber", "groesser", "muessen", "haette", "naechst")
    for token in forbidden_pseudo:
        assert token not in few_shot, \
            f"Pseudo-umlaut {token!r} found in few-shot prose"


def test_few_shot_headline_under_90_chars():
    """The realised few-shot headline obeys the 90-char rule — if the
    teaching example exceeds the rule the model gets a mixed signal."""
    headline = _extract_few_shot_headline(insight_engine.SYSTEM_PROMPT)
    assert headline, "few-shot headline not found"
    assert len(headline) <= 90, \
        f"few-shot headline {len(headline)} chars (> 90): {headline!r}"


def test_few_shot_tldr_max_three_sentences():
    """The realised few-shot tldr stays at <= 3 sentences (period-counted,
    em-dash and comma OK)."""
    tldr = _extract_few_shot_tldr(insight_engine.SYSTEM_PROMPT)
    assert tldr, "few-shot tldr not found"
    import re as _re
    # Ton-Pass: Zahlen werden mit Tausender-Punkt ausgeschrieben (33.000) —
    # der Punkt im Zahl-Cluster ist kein Satzende, vor dem Zählen entfernen.
    normalized = _re.sub(r"(\d)\.(\d)", r"\1\2", tldr)
    sentences = [s for s in normalized.split(".") if s.strip()]
    assert len(sentences) <= 3, \
        f"few-shot tldr has {len(sentences)} sentences (> 3): {tldr!r}"


# ---------- Sprint 6: Multi-Plattform-Voice + Filmtitel-Klausel ------------


def test_system_prompt_has_multi_platform_clause():
    """Sprint 6 — Headline/TLDR dürfen Plattform-Asymmetrien thematisieren.
    Die Klausel macht das explizit, sonst bleibt der LLM beim Sprint-1-3-
    Default und schreibt single-platform TT-Headlines."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "PLATTFORM-VERGLEICH" in prompt
    # Plattform-Header-Marker werden im User-Prompt verwendet — der
    # System-Prompt referenziert sie als Anker.
    assert "## TikTok" in prompt
    assert "## Instagram" in prompt
    assert "## YouTube" in prompt


def test_system_prompt_has_youtube_activation_caveat():
    """YT hat strukturell keine Saves/Shares — der LLM darf YT-Akt-Raten
    nicht 1:1 mit TT/IG-Werten vergleichen, wenn die Formel-Asymmetrie
    nicht erwähnt wird."""
    prompt = insight_engine.SYSTEM_PROMPT
    # Eine der beiden Formeln muss im Prompt benannt sein, damit der
    # Hinweis konkret bleibt statt nur "YT ist anders".
    assert "(Likes + Kommentare) / Views" in prompt


def test_system_prompt_has_film_title_clause():
    """Filmtitel-Klausel mit Coverage-Hinweis — Konkretion erlaubt
    (``*Titel*``-Markup), aber NICHT Pflicht. Coverage in der Praxis
    wird explizit benannt, damit der LLM den Genre/Format-Fallback als
    Default-Erzählung versteht und nicht als Notbehelf."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "FILMTITEL" in prompt
    assert "[*Titel*]" in prompt  # User-Prompt-Marker referenziert
    assert "*Titel*" in prompt    # Output-Markup-Format
    # Coverage-Realität benannt — der LLM weiß, dass title-arme Briefe der
    # Default-Fall sind und Genre-Sprache nicht als Schwäche gelesen wird.
    assert "1.7-7.4" in prompt or "1,7-7,4" in prompt


def test_system_prompt_forbids_inventing_titles():
    """Der LLM darf nur ``*Titel*``-Markup nutzen, wenn der ``[*Titel*]``-
    Marker im User-Prompt steht. Andernfalls erfindet er sonst Titel
    aus Hashtag-Hinweisen oder Caption-Fragmenten."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "Erfinde keine Titel" in prompt


def test_few_shot_includes_multiple_platforms():
    """Few-Shot-Headline + TLDR referenzieren mindestens zwei der drei
    Plattformen — sonst bleibt das Beispiel im Sprint-1-3-Single-
    Plattform-Modus und der LLM lernt die Multi-Plattform-Klausel
    nicht."""
    fs = _extract_few_shot(insight_engine.SYSTEM_PROMPT)
    headline = _extract_few_shot_headline(insight_engine.SYSTEM_PROMPT)
    tldr = _extract_few_shot_tldr(insight_engine.SYSTEM_PROMPT)
    combined = (headline + " " + tldr).lower()
    mentions = sum(
        1 for token in ("tiktok", " tt ", "tt-", "instagram", " ig ", "ig-",
                        "youtube", " yt ", "yt-")
        if token in combined
    )
    assert mentions >= 2, (
        f"Few-Shot Headline+TLDR nennt keine zwei Plattformen — bleibt "
        f"single-platform: {headline!r} / {tldr!r}"
    )


def test_few_shot_uses_title_markup_at_least_once():
    """Mindestens 1× ``*Titel*``-Markup in Headline oder TLDR demonstriert
    das Format. Coverage in der Praxis ist niedrig — der Few-Shot zeigt
    aber, wie es aussieht, wenn ein Top-Post tatsächlich einen Titel
    trägt."""
    headline = _extract_few_shot_headline(insight_engine.SYSTEM_PROMPT)
    tldr = _extract_few_shot_tldr(insight_engine.SYSTEM_PROMPT)
    combined = headline + " " + tldr
    import re as _re
    # Ein nicht-leeres ``*…*``-Markup. Strikter Match: keine Sternchen in
    # JSON-Strukturen, nur als Wortgrenze.
    assert _re.search(r"\*[A-Za-zÄÖÜäöüß][^*]+\*", combined), (
        f"Kein *Titel*-Markup im Few-Shot Headline+TLDR: {combined!r}"
    )


def test_few_shot_demonstrates_no_title_fallback():
    """Genre/Format-Sprache als Fallback-Demo: bei niedriger Coverage
    erzählt der LLM mit "Backkatalog-Anriss", "Reminder", "Klammer",
    "Spot" — der Few-Shot zeigt mindestens einen dieser Begriffe in
    Headline+TLDR, sonst lernt der LLM die Klausel nur theoretisch."""
    headline = _extract_few_shot_headline(insight_engine.SYSTEM_PROMPT)
    tldr = _extract_few_shot_tldr(insight_engine.SYSTEM_PROMPT)
    combined = headline + " " + tldr
    fallback_terms = ["Anriss", "Reminder", "Backkatalog", "Klammer",
                      "Spot", "Hook"]
    hits = [t for t in fallback_terms if t in combined]
    assert hits, (
        f"Kein Genre/Format-Fallback-Term im Few-Shot — der LLM lernt "
        f"die Filmtitel-Coverage-Klausel nur theoretisch: {combined!r}"
    )


def test_few_shot_max_two_title_markups():
    """Maximal zwei ``*Titel*``-Markups in Headline + TLDR — sonst wirkt
    der Brief überladen und der LLM lernt das Limit nicht aus dem
    Beispiel."""
    headline = _extract_few_shot_headline(insight_engine.SYSTEM_PROMPT)
    tldr = _extract_few_shot_tldr(insight_engine.SYSTEM_PROMPT)
    combined = headline + " " + tldr
    import re as _re
    matches = _re.findall(r"\*[A-Za-zÄÖÜäöüß][^*]+\*", combined)
    assert len(matches) <= 2, (
        f"Few-Shot hat {len(matches)} *Titel*-Markups (> 2): {matches!r}"
    )


def test_anti_pattern_block_unchanged():
    """Sprint-3-Anti-Pattern-Liste bleibt intakt — Sprint 6 erweitert,
    aber löscht nicht. Coverage / Cross-Market Match / Längen-Bucket /
    Engagement-Sum sind in Headline+TLDR weiterhin verboten."""
    prompt = insight_engine.SYSTEM_PROMPT
    for forbidden in ("Coverage", "Cross-Market Match",
                      "Längen-Bucket", "Engagement-Sum"):
        assert forbidden in prompt, (
            f"Anti-Pattern-Begriff '{forbidden}' fehlt im SYSTEM_PROMPT — "
            f"Sprint-3-Liste wurde versehentlich gekürzt."
        )


# ---------- Sprint 7-iter-2: Voice-Refinement (cluster 1-3 + trägt) -------


def test_voice_25_iter2_blacklist_traegt():
    """Sprint 7-iter-2 — 'trägt' als Voice-Verb komplett raus aus dem
    Output. Die Blacklist-Sektion benennt es explizit; Cutter-Vokabel
    in der allgemeinen VOICE-Sektion bleibt erlaubt, der LLM muss aber
    aus dem Output-Verbot lernen, dass 'trägt' im realisierten Brief
    nicht auftaucht."""
    blacklist = _voice_blacklist_section(insight_engine.SYSTEM_PROMPT)
    assert '"trägt"-Wort komplett raus' in blacklist or "trägt-Wort" in blacklist


def test_voice_25_iter2_blacklist_kommt_durch():
    """'kommt durch'-Familie überall verboten."""
    blacklist = _voice_blacklist_section(insight_engine.SYSTEM_PROMPT)
    assert "kommt durch" in blacklist or "kommt nicht durch" in blacklist


def test_voice_25_iter2_blacklist_discovery_clip():
    """Discovery-Klassifikation im Fließtext verboten — bleibt nur in
    aktuell_im_fokus.format_typ erlaubt, das ist im Prompt explizit
    abgegrenzt."""
    blacklist = _voice_blacklist_section(insight_engine.SYSTEM_PROMPT)
    assert "Discovery-Clip" in blacklist or "Discovery-Schnipsel" in blacklist


def test_voice_25_iter2_blacklist_pseudo_precision_zeichen_counts():
    """Pseudo-Präzision in Detail-Sektionen: Zeichen-Zahlen für
    Captions explizit verboten."""
    blacklist = _voice_blacklist_section(insight_engine.SYSTEM_PROMPT)
    assert "Zeichen" in blacklist
    # Hashtag-Counts ebenfalls als verboten benannt.
    assert "Hashtag-Counts" in blacklist or "Hashtag" in blacklist


def test_voice_25_iter2_headline_form_section_present():
    """Sprint 7-iter-2 — HEADLINE-FORM-Klausel mit drei Beispiel-
    Headlines im Wolf-Sprach-Anker-Stil."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "HEADLINE-FORM" in prompt
    # Drei Beispiel-Headlines sind die konkrete Lehre — Ton-Pass: Zahlen
    # ausgeschrieben, sachliche Verben.
    assert "Disney US erreicht 33.000 Reaktionen mit *Drawn to You*" in prompt
    assert "Sony US erzielt" in prompt or "Warner Deutschland setzt auf" in prompt


def test_sprint9b_was_diese_woche_removed_from_prompt():
    """Sprint 9b (Entdopplung, Commit A): ``was_diese_woche`` ist
    vollständig aus dem Pair-Prompt gestrichen — weder Befüll-Auftrag
    noch Schema-Feld noch Few-Shot. Regression-Guard: das Feld darf
    nirgends im SYSTEM_PROMPT mehr auftauchen, sonst füllt der LLM den
    redundanten Wochen-Befund in allen drei Rollen-Sektionen nach."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "was_diese_woche" not in prompt
    # Die rollenspezifischen Felder tragen den Inhalt disjunkt weiter.
    assert "schnitt_pace" in prompt
    assert "caption_style" in prompt
    assert "strategische_pattern" in prompt


def test_sprint9b_cross_market_chancen_removed_from_prompt():
    """Sprint 9b (Entdopplung, Commit B): ``cross_market_chancen`` ist aus
    fuer_creative_producer gestrichen (Schema + Few-Shot). ``cross_market_insight``
    bleibt die einzige Markt-Vergleichs-Sektion — sonst beschreiben zwei
    Sektionen denselben DE↔US/DE↔UK/US↔UK-Transfer."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "cross_market_chancen" not in prompt
    # Die einzige verbleibende Markt-Sektion ist weiterhin da.
    assert "cross_market_insight" in prompt


def test_sprint9b_zahlen_katalog_clause_present():
    """Sprint 9b (Entdopplung, Commit C): die Klausel etabliert
    aktuell_im_fokus als einzigen Zahlen-Titel-Katalog; ganz_konkret /
    trends / fuer_cutter referenzieren Belege statt sie neu aufzulisten."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "ZAHLEN-KATALOG-REGEL" in prompt
    assert "EINZIGE Zahlen-Titel-Katalog" in prompt


def test_sprint9b_lern_take_vs_action_separation():
    """Sprint 9b (Entdopplung, Commit D): lern_take = Einsicht, Handlung
    nur in actions, Muster-Konsequenz in trends.implication_for_creation.
    Das Few-Shot zeigt für trends.implication keine duplizierte Handlung
    mehr."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "die lebt ausschließlich in actions" in prompt
    assert "NICHT die konkrete Einzel-Handlung, die in actions steht" in prompt
    few_shot = _extract_few_shot(insight_engine.SYSTEM_PROMPT)
    assert "Auf Muster-Ebene heißt das" in few_shot
    assert "gegen die 22-Sekunden-Variante testen" not in few_shot


def test_sprint9b_trends_vs_konkurrenz_scope_separation():
    """Sprint 9b (Entdopplung, Commit E): trends speist sich aus den
    Pair-eigenen Daten, konkurrenz.format_trend aus der Branche außerhalb
    des Pairs — keine Dopplung derselben Bewegung."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "branchenweite Bewegungen gehören in konkurrenz.format_trend" in prompt
    assert "AUSSERHALB des aktuellen Pairs" in prompt
    few_shot = _extract_few_shot(insight_engine.SYSTEM_PROMPT)
    assert "Bei Disney erzielen kurze Anfänge" in few_shot
    assert "Außerhalb des aktuellen Pairs steigt branchenweit" in few_shot


def test_sprint9b_laengen_granularity_separation():
    """Sprint 9b (Entdopplung, Commit F): konkrete Sekunden-Längen leben
    nur in fuer_cutter.empfohlene_laengen; format_empfehlungen (Producer)
    trägt Format-Mix/Rhythmus, trends.implication bleibt übergeordnet —
    beide ohne Sekunden-Detail."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "die EINZIGE Sektion mit Sekunden-Angaben" in prompt
    assert "KEINE konkreten Sekunden-Längen, die stehen ausschließlich in fuer_cutter.empfohlene_laengen" in prompt


def test_voice_25_iter2_few_shot_no_traegt():
    """Im kompletten Few-Shot darf 'trägt' / 'tragen' nicht als
    eigenständiges Verb auftauchen — die Voice-Anchoring kommt sonst
    nicht durch (der LLM imitiert die Wendungen aus dem Beispiel).

    Wortgrenze-Regex statt Substring-Match: 'übertragen' (transfer)
    enthält 'tragen', ist aber semantisch ein anderes Verb und in
    Cross-Market-Kontext absolut legitim."""
    import re as _re
    few_shot = _extract_few_shot(insight_engine.SYSTEM_PROMPT)
    assert _re.search(r"\bträgt\b", few_shot, _re.IGNORECASE) is None, (
        "'trägt' als eigenständiges Verb noch im Few-Shot — "
        "Sprint 7-iter-2-Scrub unvollständig."
    )
    assert _re.search(r"\btragen\b", few_shot, _re.IGNORECASE) is None, (
        "'tragen' als eigenständiges Verb noch im Few-Shot — Plural / "
        "Infinitiv hat dieselbe Voice-Wirkung wie 'trägt' und ist "
        "ebenfalls verboten."
    )


def test_voice_25_iter2_few_shot_no_discovery_classification():
    """Few-Shot enthält keine Discovery-Klassifikation, weder im
    Fließtext noch in format_typ-Werten — der LLM lernt sonst, dass
    'Discovery-Clip' eine legitime format_typ ist und nutzt sie auch
    im Fließtext weiter."""
    few_shot = _extract_few_shot(insight_engine.SYSTEM_PROMPT)
    assert "Discovery-Clip" not in few_shot
    assert "Discovery-Schnipsel" not in few_shot
    assert "Discovery-Cut" not in few_shot
    # Backkatalog-Anriss als Klassifikation ebenfalls raus aus dem Few-Shot.
    assert "Backkatalog-Anriss" not in few_shot
    assert "Backkatalog-Schnipsel" not in few_shot


def test_voice_25_iter2_few_shot_no_kommt_durch_family():
    """'kommt durch' / 'verliert sich' / 'holt günstig' raus aus dem
    Few-Shot — sonst trainiert der LLM darauf weiter."""
    few_shot = _extract_few_shot(insight_engine.SYSTEM_PROMPT)
    for forbidden in ("kommt durch", "kommt nicht durch", "verliert sich",
                      "holt günstig", "verbrennt Schnittzeit"):
        assert forbidden not in few_shot, (
            f"'{forbidden}' ist noch im Few-Shot — "
            f"Sprint 7-iter-2-Scrub unvollständig."
        )


def test_voice_25_iter2_few_shot_no_zeichen_counts():
    """Few-Shot vermeidet Pseudo-Präzision wie '130 Zeichen', '216
    Zeichen' in den Detail-Sektionen."""
    import re as _re
    few_shot = _extract_few_shot(insight_engine.SYSTEM_PROMPT)
    char_count_pattern = r"\d+\s*Zeichen"
    matches = _re.findall(char_count_pattern, few_shot)
    assert not matches, (
        f"Pseudo-Präzision (Zeichen-Counts) im Few-Shot: {matches!r}"
    )


def test_voice_25_iter2_few_shot_uses_holt_or_punktet_in_headline():
    """Headline nutzt aktive Verben (holt/punktet/zieht/wirkt/macht/
    läuft/fährt/kommt) statt 'trägt'."""
    headline = _extract_few_shot_headline(insight_engine.SYSTEM_PROMPT)
    active_verbs = ("holt", "punktet", "zieht", "wirkt", "macht",
                    "läuft", "fährt", "kommt", "erreicht", "erzielt",
                    "veröffentlicht", "setzt")
    assert any(verb in headline for verb in active_verbs), (
        f"Headline nutzt kein aktives Verb aus der iter-2-Erlaubt-Liste: "
        f"{headline!r}"
    )


def test_sprint9b_few_shot_has_no_was_diese_woche():
    """Sprint 9b (Entdopplung, Commit A): das Few-Shot zeigt
    ``was_diese_woche`` nicht mehr. Ein Few-Shot mit einem Feld, das im
    Tool-Schema nicht mehr existiert, würde den Tool-Call brechen und dem
    LLM die entfernte Redundanz nachträglich beibringen."""
    few_shot = _extract_few_shot(insight_engine.SYSTEM_PROMPT)
    assert '"was_diese_woche"' not in few_shot


def test_voice_25_iter2_few_shot_no_must_show_no_go():
    """Compliance-Listen sind strukturell aus dem Schema entfernt —
    der Few-Shot demonstriert das, indem must_show/no_go nicht mehr
    auftauchen."""
    few_shot = _extract_few_shot(insight_engine.SYSTEM_PROMPT)
    assert '"must_show"' not in few_shot
    assert '"no_go"' not in few_shot


# ---------- Sprint 7: Voice 2.5 — Berater-Vokabel-Blacklist + Tone --------


def _voice_blacklist_section(prompt: str) -> str:
    """Bereich zwischen 'VERBOTENE BERATER-VOKABEL' und der nächsten
    Großbuchstaben-Sektion (PLATTFORM-VERGLEICH oder TONALITÄTS-POOL).
    Wird von mehreren Tests reused."""
    start = prompt.find("VERBOTENE BERATER-VOKABEL")
    if start < 0:
        return ""
    # Nächster Sektions-Header — wir suchen den nächsten Doppelpunkt nach
    # einem Wort in ALL CAPS am Zeilenanfang.
    rest = prompt[start:]
    end = rest.find("\nPLATTFORM-VERGLEICH")
    return rest[:end] if end > 0 else rest


def test_berichtston_section_present():
    """Ton-Pass — die BERICHTSTON-Sektion mit den sechs Regeln ist die
    Tone-Quelle des sachlich-berichtenden Stils (löst die alte
    Voice-2.5-Schnittraum-Identität ab)."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "BERICHTSTON" in prompt
    assert "sachlich" in prompt.lower()
    assert "VORHER / NACHHER" in prompt


def test_voice_25_blacklist_friedhof():
    """Friedhof-Vokabel als Berater-Wertbegriff explizit verboten."""
    blacklist = _voice_blacklist_section(insight_engine.SYSTEM_PROMPT)
    assert "Friedhof" in blacklist


def test_voice_25_blacklist_korridor():
    """Korridor / Mittelkorridor als Berater-Substantive verboten."""
    blacklist = _voice_blacklist_section(insight_engine.SYSTEM_PROMPT)
    assert "Korridor" in blacklist


def test_voice_25_blacklist_format_spur_or_block():
    """Format-Spur / Format-Block als Berater-Klassifikation verboten."""
    blacklist = _voice_blacklist_section(insight_engine.SYSTEM_PROMPT)
    assert "Format-Spur" in blacklist or "Format-Block" in blacklist


def test_voice_25_blacklist_skalierbar():
    """``leicht skalierbar`` als Pitch-Vokabel verboten."""
    blacklist = _voice_blacklist_section(insight_engine.SYSTEM_PROMPT)
    assert "skalierbar" in blacklist or "skaliert" in blacklist


def test_voice_25_blacklist_substantive_ungetueme():
    """Substantiv-Ungetüme wie ``Aktivierungsverhalten`` /
    ``Reichweitendynamik`` werden explizit aufgelistet, sonst rutscht
    der LLM in Berater-Substantive ohne dass die Klausel greift."""
    blacklist = _voice_blacklist_section(insight_engine.SYSTEM_PROMPT)
    assert "Aktivierungsverhalten" in blacklist or "Reichweitendynamik" in blacklist


def test_voice_25_pseudo_precision_block_present():
    """Doppel-Beziffung explizit als Anti-Pattern aufgenommen — sonst
    bleibt der LLM bei der Sprint-3-6-Voice und packt drei Zahlen in
    einen Atemzug."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "VERBOTENE PSEUDO-PRÄZISION" in prompt
    assert "Doppel-Beziffung" in prompt


def test_voice_25_compliance_structure_block_present():
    """``Must Show`` / ``No-Go`` als Listen-Header sind verboten —
    Erzähl-Sektionen sollen Fließtext sein, kein Compliance-Brief."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "VERBOTENE COMPLIANCE-STRUKTUR" in prompt
    assert "Must Show" in prompt or "No-Go" in prompt


def test_voice_25_schema_vocabulary_section_present():
    """Schema-Vokabel-Hinweis listet die drei Voice-2.5 verdict-Werte
    explizit auf, damit der LLM sie nicht aus der alten Few-Shot-Mem
    rekonstruieren muss."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "SCHEMA-VOKABEL" in prompt
    assert "funktioniert" in prompt
    assert "kommt nicht an" in prompt
    assert "noch ausbaufähig" in prompt


def test_voice_25_tldr_arc_pattern_present():
    """TLDR-3-Sätze-Bogen als explizites Pattern im Prompt — Satz 1
    Beobachtung, Satz 2 Kontrast, Satz 3 Pointe."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "TLDR-STRUKTUR" in prompt
    # Dreiteilung explizit benannt
    assert "Satz 1" in prompt
    assert "Satz 2" in prompt
    assert "Satz 3" in prompt


def test_voice_25_few_shot_uses_new_verdict_values():
    """Few-Shot-aktuell_im_fokus-Items verwenden ausschließlich die
    Voice-2.5-verdict-Werte. Alte Werte als verdict-String dürfen nicht
    mehr im Few-Shot stehen — sonst trainiert der LLM weiter darauf."""
    few_shot = _extract_few_shot(insight_engine.SYSTEM_PROMPT)
    assert '"verdict": "funktioniert"' in few_shot
    assert '"verdict": "kommt nicht an"' in few_shot
    # Alte verdict-Strings sollten als JSON-Werte NICHT mehr auftauchen.
    for old in ('"verdict": "trägt"', '"verdict": "zerläuft"',
                '"verdict": "sitzt"', '"verdict": "ausbaufähig"',
                '"verdict": "zweischneidig"'):
        assert old not in few_shot, (
            f"Alter verdict-Wert {old!r} ist noch im Few-Shot — "
            f"Sprint-7-Vokabel-Migration unvollständig."
        )


def test_voice_25_few_shot_no_friedhof_or_korridor():
    """Few-Shot ist von Berater-Vokabel bereinigt — sonst widerspricht
    er der Blacklist-Klausel und der LLM nimmt das Beispiel als
    Lizenz, die Vokabel weiter zu nutzen."""
    few_shot = _extract_few_shot(insight_engine.SYSTEM_PROMPT)
    assert "Friedhof" not in few_shot
    assert "Korridor" not in few_shot
    # ``Format-Block`` als Klassifikation auch raus aus dem Few-Shot.
    assert "Format-Block" not in few_shot


def test_voice_25_few_shot_no_double_beziffung_in_tldr():
    """TLDR enthält keine Drei-Zahlen-Sätze wie '11.200 Reaktionen bei
    108k Aufrufen — 10,4% Aktivierung'. Heuristik: in einem TLDR-Satz
    dürfen höchstens zwei Zahlen-Tokens vorkommen, sonst ist die
    Doppel-Beziffung wieder da."""
    import re as _re
    tldr = _extract_few_shot_tldr(insight_engine.SYSTEM_PROMPT)
    sentences = [s for s in tldr.split(".") if s.strip()]
    for s in sentences:
        # Zahlen-Tokens: Sequenzen aus Ziffern, optional Komma/Prozent/k.
        nums = _re.findall(r"\d[\d.,]*\s*[k%]?", s)
        # Stripped: nur Tokens, die wirklich numerische Substanz haben
        # (mind. eine Ziffer + optional %/k).
        numeric_tokens = [n for n in nums if _re.search(r"\d", n)]
        assert len(numeric_tokens) <= 2, (
            f"TLDR-Satz hat {len(numeric_tokens)} Zahlen-Tokens — "
            f"Doppel-Beziffung-Regression: {s!r} → {numeric_tokens!r}"
        )


# ---------- Sprint 4: Multi-Plattform PAIRS ---------------------------------


def test_pairs_have_platforms_dict_for_enabled_pairs():
    """Every enabled PAIR carries a ``platforms`` dict with at least one
    platform key — Sprint-4 source of truth for which channels to aggregate."""
    for key, pair_def in insight_engine.PAIRS.items():
        if not pair_def.get("enabled", False):
            continue
        assert "platforms" in pair_def, f"{key}: missing platforms dict"
        assert isinstance(pair_def["platforms"], dict), f"{key}: platforms not a dict"
        assert len(pair_def["platforms"]) >= 1, f"{key}: empty platforms dict"


def test_pairs_backwards_compat_mirror_first_platform():
    """Legacy ``platform`` and ``channels`` fields mirror the first platform
    in the new ``platforms`` dict — so legacy code paths (LLM user prompt,
    fixture-based tests) keep working without an audit. TikTok stays first
    by convention; if the order ever changes, the mirror tracks it."""
    for key, pair_def in insight_engine.PAIRS.items():
        if not pair_def.get("enabled", False):
            continue
        platforms = pair_def["platforms"]
        first_platform = next(iter(platforms.keys()))
        assert pair_def["platform"] == first_platform, (
            f"{key}: platform={pair_def['platform']!r} does not mirror "
            f"first platforms entry {first_platform!r}"
        )
        assert pair_def["channels"] == platforms[first_platform], (
            f"{key}: channels do not mirror platforms[{first_platform!r}]"
        )


# ---------- Sprint 4: Multi-Plattform-Aggregation ---------------------------


def test_aggregate_pair_returns_per_platform_list():
    """Sprint-4: aggregate_pair populates per_platform with one entry per
    platform in the PAIRS definition. The fixture seeds only TikTok
    channels, so IG/YT entries appear with empty channel slots and the
    'Channel nicht in der DB'-note — that's the expected graceful-degrade
    behaviour and proves the iteration runs end-to-end without crashing
    when only one platform has data."""
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        platforms = [p.platform for p in agg.per_platform]
        assert platforms == ["tiktok", "instagram", "youtube"]


def test_aggregate_pair_backwards_compat_mirrors_first_platform():
    """Legacy fields on PairAggregation mirror the first platform
    (TikTok by convention). Old consumers — LLM user-prompt and the
    pre-Sprint-4 Frontend render path — see the same shape they did
    before, but pulled from per_platform[0] under the hood."""
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        tt = next(p for p in agg.per_platform if p.platform == "tiktok")
        assert agg.platform == "tiktok"
        assert agg.de_channel == tt.de_channel
        assert agg.us_channel == tt.us_channel
        assert agg.cross_market_matches == tt.cross_market_matches
        assert agg.title_coverage == tt.title_coverage


def test_aggregate_pair_handles_single_market_youtube():
    """Disney/Prime/Paramount have only US-side YouTube channels.
    _aggregate_platform must leave de_channel=None without crashing,
    not throw on the missing DE spec."""
    with _session() as session:
        agg = insight_engine.aggregate_pair(session, "disney", window_days=30)
        yt_agg = next((p for p in agg.per_platform if p.platform == "youtube"), None)
        assert yt_agg is not None
        # No DE-side YouTube channel exists in PAIRS["disney"]["platforms"]["youtube"];
        # the de_channel slot must be None rather than a stub-zero ChannelStats.
        assert yt_agg.de_channel is None
        # cross_market_matches naturally empty when one side is missing.
        assert yt_agg.cross_market_matches == []


def test_aggregate_pair_pools_disney_us_multi_channels_on_tiktok():
    """Sprint 10d: Disney US TikTok ist Multi-Channel-Pool. Posts mehrerer
    Sub-Brand-Channels (disneystudios + marvelstudios) müssen in einem
    gepoolten ChannelStats landen — nicht pro Channel separat. Coverage
    und ranked_posts spiegeln die Vereinigung der Pools, der display
    handle bleibt der erste in der PAIRS-Spec gelistete (disneystudios)."""
    with _session() as session:
        # Seed two Disney US TikTok sub-brand channels — handles must match
        # the PAIRS["disney"]["platforms"]["tiktok"] spec so _find_channels
        # picks them up. DE side stays empty so the test can isolate the
        # multi-channel-US-pool effect from the cross-market path.
        ds = Channel(
            name="Disney Studios",
            platform="tiktok",
            url="https://www.tiktok.com/@disneystudios",
            handle="disneystudios",
            market=Market.US,
        )
        marvel = Channel(
            name="Marvel Studios",
            platform="tiktok",
            url="https://www.tiktok.com/@marvelstudios",
            handle="marvelstudios",
            market=Market.US,
        )
        session.add_all([ds, marvel])
        session.commit()
        session.refresh(ds)
        session.refresh(marvel)

        title = Title(title_original="Avengers: Doomsday")
        session.add(title)
        session.commit()
        session.refresh(title)

        # 2 disneystudios posts, 2 marvelstudios posts — pool size 4.
        ds_p1 = _make_post(
            session, ds,
            caption="New trailer drop #DisneyStudios",
            likes=8_000, comments=200, shares=80, saves=120, duration=24,
            days_ago=2, url_suffix="ds1",
        )
        ds_p2 = _make_post(
            session, ds,
            caption="Behind the scenes #BTS",
            likes=2_000, comments=40, shares=10, saves=20, duration=15,
            days_ago=8, url_suffix="ds2",
        )
        mv_p1 = _make_post(
            session, marvel,
            caption="Suit up! #MarvelStudios #Avengers",
            likes=15_000, comments=600, shares=300, saves=500, duration=30,
            days_ago=3, url_suffix="mv1",
        )
        mv_p2 = _make_post(
            session, marvel,
            caption="The team assembles #Avengers",
            likes=5_000, comments=120, shares=60, saves=90, duration=20,
            days_ago=12, url_suffix="mv2",
        )

        # 3 of 4 posts get a title — pool coverage = 3/4 = 75%.
        session.add_all([
            Asset(post_id=ds_p1.id, title_id=title.id),
            Asset(post_id=ds_p2.id),  # no title
            Asset(post_id=mv_p1.id, title_id=title.id),
            Asset(post_id=mv_p2.id, title_id=title.id),
        ])
        session.commit()

        agg = insight_engine.aggregate_pair(session, "disney", window_days=30)
        tt_agg = next(p for p in agg.per_platform if p.platform == "tiktok")
        us_stats = tt_agg.us_channel

        # US-pool stats reflect both sub-brand channels combined.
        assert us_stats is not None
        assert us_stats.handle == "disneystudios", (
            "Display handle must be the first spec-listed handle (the lead "
            "cinema-master), not a per-channel breakdown."
        )
        assert us_stats.posts_count == 4, "Pool covers both sub-brand channels"
        assert us_stats.assets_count == 4
        assert us_stats.coverage_pct == 75.0

        # ranked_posts must contain post URLs from BOTH sub-brand channels —
        # if pooling were broken, only one channel's posts would appear.
        ranked_urls = {rp.post_url for rp in us_stats.ranked_posts}
        assert any("disneystudios" in url for url in ranked_urls)
        assert any("marvelstudios" in url for url in ranked_urls)

        # primary channel_id mirrors the first resolved pool member
        # (disneystudios). Audit-trail convention from Sprint 10d.
        assert us_stats.channel_id == str(ds.id)


def test_aggregate_pair_pools_warner_us_multi_channels_on_tiktok():
    """Sprint 10h: Warner US TikTok ist Multi-Channel-Pool (warnerbros + dc).
    Posts beider Sub-Brand-Channels müssen im selben us_channel-Pool landen.
    Display-handle bleibt der erste Spec-Eintrag (warnerbros)."""
    with _session() as session:
        wb = Channel(
            name="Warner Bros US",
            platform="tiktok",
            url="https://www.tiktok.com/@warnerbros",
            handle="warnerbros",
            market=Market.US,
        )
        dc = Channel(
            name="DC",
            platform="tiktok",
            url="https://www.tiktok.com/@dc",
            handle="dc",
            market=Market.US,
        )
        session.add_all([wb, dc])
        session.commit()
        session.refresh(wb)
        session.refresh(dc)

        title = Title(title_original="Superman: Legacy")
        session.add(title)
        session.commit()
        session.refresh(title)

        wb_p1 = _make_post(
            session, wb,
            caption="Mortal Kombat II hits theaters #MK2",
            likes=9_000, comments=300, shares=120, saves=180, duration=28,
            days_ago=2, url_suffix="wb1",
        )
        wb_p2 = _make_post(
            session, wb,
            caption="Behind the scenes #BTS",
            likes=1_500, comments=30, shares=10, saves=20, duration=14,
            days_ago=9, url_suffix="wb2",
        )
        dc_p1 = _make_post(
            session, dc,
            caption="Superman returns #SupermanLegacy",
            likes=20_000, comments=800, shares=400, saves=600, duration=32,
            days_ago=3, url_suffix="dc1",
        )
        dc_p2 = _make_post(
            session, dc,
            caption="Up, up and away #DC",
            likes=6_000, comments=150, shares=80, saves=110, duration=18,
            days_ago=11, url_suffix="dc2",
        )

        session.add_all([
            Asset(post_id=wb_p1.id, title_id=title.id),
            Asset(post_id=wb_p2.id),  # no title
            Asset(post_id=dc_p1.id, title_id=title.id),
            Asset(post_id=dc_p2.id, title_id=title.id),
        ])
        session.commit()

        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        tt_agg = next(p for p in agg.per_platform if p.platform == "tiktok")
        us_stats = tt_agg.us_channel

        assert us_stats is not None
        assert us_stats.handle == "warnerbros", (
            "Display handle must be the first spec-listed handle (warnerbros), "
            "not the DC sub-brand."
        )
        assert us_stats.posts_count == 4, "Pool covers both sub-brand channels"
        assert us_stats.assets_count == 4
        assert us_stats.coverage_pct == 75.0

        ranked_urls = {rp.post_url for rp in us_stats.ranked_posts}
        assert any("warnerbros" in url for url in ranked_urls)
        assert any("@dc/" in url for url in ranked_urls)

        assert us_stats.channel_id == str(wb.id)


def test_aggregate_pair_pools_sony_us_multi_channels_on_tiktok():
    """Sprint 10h: Sony US TikTok ist Multi-Channel-Pool (sonypictures +
    sonypicturesanimation). Display-handle bleibt sonypictures."""
    with _session() as session:
        sp = Channel(
            name="Sony Pictures US",
            platform="tiktok",
            url="https://www.tiktok.com/@sonypictures",
            handle="sonypictures",
            market=Market.US,
        )
        spa = Channel(
            name="Sony Pictures Animation",
            platform="tiktok",
            url="https://www.tiktok.com/@sonypicturesanimation",
            handle="sonypicturesanimation",
            market=Market.US,
        )
        session.add_all([sp, spa])
        session.commit()
        session.refresh(sp)
        session.refresh(spa)

        title = Title(title_original="Spider-Man: Beyond the Spider-Verse")
        session.add(title)
        session.commit()
        session.refresh(title)

        sp_p1 = _make_post(
            session, sp,
            caption="Karate Kid Legends in cinemas #KarateKid",
            likes=7_500, comments=210, shares=90, saves=140, duration=26,
            days_ago=2, url_suffix="sp1",
        )
        sp_p2 = _make_post(
            session, sp,
            caption="Trailer dropping soon #Sony",
            likes=2_000, comments=50, shares=15, saves=25, duration=12,
            days_ago=10, url_suffix="sp2",
        )
        spa_p1 = _make_post(
            session, spa,
            caption="Miles is back #SpiderVerse",
            likes=18_000, comments=700, shares=350, saves=520, duration=30,
            days_ago=3, url_suffix="spa1",
        )
        spa_p2 = _make_post(
            session, spa,
            caption="Animation reel #SonyAnimation",
            likes=4_500, comments=100, shares=40, saves=70, duration=20,
            days_ago=14, url_suffix="spa2",
        )

        session.add_all([
            Asset(post_id=sp_p1.id, title_id=title.id),
            Asset(post_id=sp_p2.id),  # no title
            Asset(post_id=spa_p1.id, title_id=title.id),
            Asset(post_id=spa_p2.id, title_id=title.id),
        ])
        session.commit()

        agg = insight_engine.aggregate_pair(session, "sonypictures", window_days=30)
        tt_agg = next(p for p in agg.per_platform if p.platform == "tiktok")
        us_stats = tt_agg.us_channel

        assert us_stats is not None
        assert us_stats.handle == "sonypictures", (
            "Display handle must be the first spec-listed handle (sonypictures), "
            "not the Sony Pictures Animation sub-brand."
        )
        assert us_stats.posts_count == 4
        assert us_stats.assets_count == 4
        assert us_stats.coverage_pct == 75.0

        ranked_urls = {rp.post_url for rp in us_stats.ranked_posts}
        assert any("@sonypictures/" in url for url in ranked_urls)
        assert any("sonypicturesanimation" in url for url in ranked_urls)

        assert us_stats.channel_id == str(sp.id)


def test_aggregate_pair_pools_disney_us_multi_channels_on_youtube():
    """Sprint 10j: Disney US YouTube ist Multi-Channel-Pool. Marvel-Trailer
    auf @marvel und Catalog-Posts auf @WaltDisneyStudios müssen im selben
    us_channel-Pool landen. Display-handle bleibt WaltDisneyStudios (erster
    Spec-Eintrag). DE-Seite bleibt single-market None."""
    with _session() as session:
        wds = Channel(
            name="Walt Disney Studios",
            platform="youtube",
            url="https://www.youtube.com/@WaltDisneyStudios",
            handle="WaltDisneyStudios",
            market=Market.US,
        )
        marvel = Channel(
            name="Marvel Entertainment",
            platform="youtube",
            url="https://www.youtube.com/@marvel",
            handle="marvel",
            market=Market.US,
        )
        session.add_all([wds, marvel])
        session.commit()
        session.refresh(wds)
        session.refresh(marvel)

        title = Title(title_original="Avengers: Doomsday")
        session.add(title)
        session.commit()
        session.refresh(title)

        wds_p1 = _make_post(
            session, wds,
            caption="Official trailer #WaltDisneyStudios",
            likes=12_000, comments=400, shares=160, saves=240, duration=120,
            days_ago=2, url_suffix="wds-yt1", platform="youtube",
        )
        wds_p2 = _make_post(
            session, wds,
            caption="Featurette #BTS",
            likes=3_000, comments=80, shares=20, saves=40, duration=90,
            days_ago=9, url_suffix="wds-yt2", platform="youtube",
        )
        mv_p1 = _make_post(
            session, marvel,
            caption="Avengers: Doomsday | Official Teaser",
            likes=25_000, comments=900, shares=500, saves=700, duration=150,
            days_ago=3, url_suffix="mv-yt1", platform="youtube",
        )
        mv_p2 = _make_post(
            session, marvel,
            caption="Marvel | Phase 6 sizzle",
            likes=7_000, comments=180, shares=80, saves=120, duration=60,
            days_ago=12, url_suffix="mv-yt2", platform="youtube",
        )

        session.add_all([
            Asset(post_id=wds_p1.id, title_id=title.id),
            Asset(post_id=wds_p2.id),  # no title
            Asset(post_id=mv_p1.id, title_id=title.id),
            Asset(post_id=mv_p2.id, title_id=title.id),
        ])
        session.commit()

        agg = insight_engine.aggregate_pair(session, "disney", window_days=30)
        yt_agg = next(p for p in agg.per_platform if p.platform == "youtube")
        us_stats = yt_agg.us_channel

        assert us_stats is not None
        assert us_stats.handle == "WaltDisneyStudios", (
            "Display handle must be the first spec-listed handle "
            "(WaltDisneyStudios), not the Marvel sub-brand."
        )
        assert us_stats.posts_count == 4, "Pool covers both sub-brand channels"
        assert us_stats.assets_count == 4
        assert us_stats.coverage_pct == 75.0

        ranked_urls = {rp.post_url for rp in us_stats.ranked_posts}
        assert any("wds-yt" in url for url in ranked_urls)
        assert any("mv-yt" in url for url in ranked_urls)

        assert us_stats.channel_id == str(wds.id)

        # DE-Seite bleibt single-market None — keine YT-DE-Channels.
        assert yt_agg.de_channel is None


def test_pair_aggregation_parses_legacy_persisted_brief():
    """Sprint-1 persistence contract: an aggregation JSON written before
    Sprint-4 has no per_platform field. model_validate must still parse
    it cleanly, defaulting per_platform to []. This guards against a
    silent Sprint-1-cache breakage at deploy time — the cache hit path
    in api/insights.py would otherwise crash on every old brief."""
    from app.schemas.insights import PairAggregation
    legacy_payload = {
        "pair_key": "warnerbros",
        "pair_label": "Warner Bros DE+US",
        "platform": "tiktok",
        "window_days": 30,
        "window_start": "2026-04-08T00:00:00+00:00",
        "window_end": "2026-05-08T00:00:00+00:00",
        "iso_week": 19,
        "iso_year": 2026,
        "de_channel": None,
        "us_channel": None,
        "cross_market_matches": [],
        "title_coverage": {
            "titles_in_both_markets": [],
            "de_only_titles": [],
            "us_only_titles": [],
            "de_assets_with_title": 0,
            "de_assets_total": 0,
            "us_assets_with_title": 0,
            "us_assets_total": 0,
            "overall_coverage_pct": 0.0,
        },
        "notes": [],
    }
    parsed = PairAggregation.model_validate(legacy_payload)
    assert parsed.per_platform == []
    assert parsed.platform == "tiktok"


# ---------- Sprint 7-iter-2.5: Discovery-Klassifikation strukturell weg ----


def _extract_schema_vocab_section(prompt: str) -> str:
    """Bereich zwischen 'SCHEMA-VOKABEL' und der OUTPUT-Schema-Klausel.
    Enthält die format_typ-Beispielliste, die iter-2.5 bereinigt."""
    start = prompt.find("SCHEMA-VOKABEL")
    if start < 0:
        return ""
    rest = prompt[start:]
    # Boundary marker: the OUTPUT clause that follows the SCHEMA-VOKABEL
    # section. Anchored on the stable ``OUTPUT —`` prefix (the trailing
    # wording changed when the output contract was switched to tool-direct
    # language, 2026-06-01).
    end = rest.find("OUTPUT —")
    return rest[:end] if end > 0 else rest


def test_voice_25_iter25_no_discovery_clip_in_format_typ_examples():
    """Schema-Vokabel-Sektion listet 'Discovery-Clip' nicht mehr als
    format_typ-Beispiel — der LLM hat das Label sonst aus der Schema-
    Sektion gezogen und in den Fließtext geleakt."""
    schema_block = _extract_schema_vocab_section(insight_engine.SYSTEM_PROMPT)
    assert '- "Discovery-Clip"' not in schema_block
    assert "- 'Discovery-Clip'" not in schema_block


def test_voice_25_iter25_few_shot_no_discovery_clip():
    """Few-Shot enthält Discovery-Klassifikation nirgends mehr."""
    few_shot = _extract_few_shot(insight_engine.SYSTEM_PROMPT)
    assert "Discovery-Clip" not in few_shot
    assert "Discovery-Cut" not in few_shot
    assert "Discovery-Schnipsel" not in few_shot


def test_voice_25_iter25_blacklist_kostet_schnittzeit():
    """'kostet Schnittzeit' / 'kostet Zeit' explizit als verbotener
    Berater-Wortschatz gelistet (Variante von 'verbrennt Schnittzeit',
    iter-2 hat das Pattern bereits geblockt)."""
    blacklist = _voice_blacklist_section(insight_engine.SYSTEM_PROMPT)
    assert "kostet Schnittzeit" in blacklist or "kostet Zeit" in blacklist


def test_voice_25_iter25_few_shot_no_kostet_schnittzeit():
    """Producer-strategische_pattern im Few-Shot scrubbed: 'kostet
    Schnittzeit' / 'verbrennt Schnittzeit' ersetzt durch 'lohnt nicht'
    o.ä. — sonst trainiert der LLM darauf weiter."""
    few_shot = _extract_few_shot(insight_engine.SYSTEM_PROMPT)
    assert "kostet Schnittzeit" not in few_shot
    assert "verbrennt Schnittzeit" not in few_shot


def test_voice_25_iter25_format_typ_examples_complete():
    """Vier beschreibende format_typ-Beispiele müssen weiter im Schema-
    Block stehen (Marken-Spot, Kurzer Clip, Kino-Reminder, Ankündigungs-
    Post) — das ist die positive Vokabel, die Discovery-Clip ersetzt."""
    schema_block = _extract_schema_vocab_section(insight_engine.SYSTEM_PROMPT)
    for example in (
        "Marken-Spot",
        "Kurzer Clip mit bekanntem Titel",
        "Kino-Reminder",
        "Ankündigungs-Post",
    ):
        assert example in schema_block, (
            f"format_typ-Beispiel {example!r} fehlt im SCHEMA-VOKABEL-Block — "
            f"iter-2.5 erwartet alle vier beschreibenden Beispiele."
        )


# ---------- Sprint UK-B1: 3-Markt-Aggregation -------------------------------


def test_aggregate_pair_includes_uk_channel_when_specced():
    """Sprint UK-B1: warnerbros TT hat seit B1 einen UK-Eintrag
    (warnerbrosuk). Wenn der UK-Channel mit Posts in der DB liegt, muss
    ``per_platform[tiktok].uk_channel`` befüllt sein und die Posts zählen."""
    with _session() as session:
        # Minimal-Seed: US-Hauptchannel + UK-Schwester. DE bleibt leer
        # (für diesen Test irrelevant — er fokussiert auf den UK-Pfad).
        us = Channel(
            name="Warner Bros US",
            platform="tiktok",
            url="https://www.tiktok.com/@warnerbros",
            handle="warnerbros",
            market=Market.US,
        )
        uk = Channel(
            name="Warner Bros UK",
            platform="tiktok",
            url="https://www.tiktok.com/@warnerbrosuk",
            handle="warnerbrosuk",
            market=Market.UK,
        )
        session.add_all([us, uk])
        session.commit()
        session.refresh(us)
        session.refresh(uk)

        _make_post(
            session, us,
            caption="US Drop #Trailer",
            likes=1_000, days_ago=2, url_suffix="us-uk-1",
        )
        _make_post(
            session, uk,
            caption="UK premiere #Trailer #Cinema",
            likes=2_000, comments=50, shares=10, saves=30, duration=22,
            days_ago=3, url_suffix="uk-1",
        )

        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        tt_agg = next(p for p in agg.per_platform if p.platform == "tiktok")
        assert tt_agg.uk_channel is not None, "UK-Channel muss bei vorhandener UK-Spec befüllt sein"
        assert tt_agg.uk_channel.handle == "warnerbrosuk"
        assert tt_agg.uk_channel.market == "UK"
        assert tt_agg.uk_channel.posts_count == 1
        # Mirror auf PairAggregation-Ebene zeigt auf erste Plattform (TikTok).
        assert agg.uk_channel is not None
        assert agg.uk_channel.handle == "warnerbrosuk"


def test_aggregate_pair_uk_channel_none_when_pair_has_no_uk_specs(monkeypatch):
    """Sprint UK-B1: ein Pair ohne UK-Spec auf irgendeiner Plattform
    muss ``uk_channel`` auf jeder Plattform-Slice ``None`` lassen.
    Persistierte Briefe vor B1 enthalten das Feld nicht — Default
    ``None`` deckt den Re-Hydrate-Pfad ab.

    Sprint 2026-05-12: universalpictures ist seit der Reaktivierung
    selbst UK-aktiv, deshalb wird hier ein synthetischer DE+US-only-
    Pair in die Registry geschoben."""
    synthetic_pair = {
        "label": "synthetic DE+US",
        "platforms": {
            "tiktok": [
                {"handle": "synthetic_us", "market": "US"},
                {"handle": "synthetic_de", "market": "DE"},
            ],
        },
        "platform": "tiktok",
        "channels": [
            {"handle": "synthetic_us", "market": "US"},
            {"handle": "synthetic_de", "market": "DE"},
        ],
        "enabled": True,
        "reason": None,
    }
    monkeypatch.setitem(insight_engine.PAIRS, "_uk_less_synthetic", synthetic_pair)
    with _session() as session:
        agg = insight_engine.aggregate_pair(session, "_uk_less_synthetic", window_days=30)
        for platform_agg in agg.per_platform:
            assert platform_agg.uk_channel is None, (
                f"Pair ohne UK-Spec darf kein uk_channel produzieren "
                f"(platform={platform_agg.platform})"
            )
        # Mirror auf PairAggregation-Ebene ebenfalls None.
        assert agg.uk_channel is None


def test_title_coverage_includes_uk_only_titles():
    """Sprint UK-B1: ein UK-Post mit Title-Match (und kein DE/US-Post mit
    demselben Titel) muss in ``title_coverage.uk_only_titles`` auftauchen.
    ``uk_assets_total`` / ``uk_assets_with_title`` zählen die UK-Assets."""
    with _session() as session:
        us = Channel(
            name="Warner Bros US",
            platform="tiktok",
            url="https://www.tiktok.com/@warnerbros",
            handle="warnerbros",
            market=Market.US,
        )
        uk = Channel(
            name="Warner Bros UK",
            platform="tiktok",
            url="https://www.tiktok.com/@warnerbrosuk",
            handle="warnerbrosuk",
            market=Market.UK,
        )
        session.add_all([us, uk])
        session.commit()
        session.refresh(us)
        session.refresh(uk)

        uk_only_title = Title(title_original="The Boy and the Heron UK Cut")
        session.add(uk_only_title)
        session.commit()
        session.refresh(uk_only_title)

        us_post = _make_post(
            session, us,
            caption="US release #Trailer",
            likes=500, days_ago=2, url_suffix="us-no-title",
        )
        uk_post = _make_post(
            session, uk,
            caption="UK-exclusive premiere #Cinema",
            likes=900, days_ago=3, url_suffix="uk-title",
        )
        # US-Post bleibt ohne Title, UK-Post bekommt den UK-exklusiven Title.
        session.add(Asset(post_id=us_post.id))
        session.add(Asset(post_id=uk_post.id, title_id=uk_only_title.id))
        session.commit()

        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        tt_agg = next(p for p in agg.per_platform if p.platform == "tiktok")
        coverage = tt_agg.title_coverage
        assert "The Boy and the Heron UK Cut" in coverage.uk_only_titles
        assert coverage.uk_assets_total == 1
        assert coverage.uk_assets_with_title == 1
        # Der UK-only-Titel darf NICHT in titles_in_both_markets oder
        # de_only/us_only auftauchen — sonst leakt die DE∩US-Semantik.
        assert "The Boy and the Heron UK Cut" not in coverage.titles_in_both_markets
        assert "The Boy and the Heron UK Cut" not in coverage.us_only_titles
        assert "The Boy and the Heron UK Cut" not in coverage.de_only_titles


def test_format_channel_section_renders_uk_header():
    """Sprint UK-B1: ``_format_channel_section`` nimmt das Markt-Kürzel
    als String-Parameter — der Markdown-Header "### UK: @handle" entsteht
    daher ohne Funktions-Edit. Unit-Test gegen den Renderer, damit ein
    versehentlicher Hardcode (z. B. "DE/US-only"-Check) sofort auffällt."""
    stats = insight_engine.ChannelStats(
        handle="warnerbrosuk",
        market="UK",
        channel_id=None,
        channel_found=True,
        posts_count=4,
        assets_count=4,
        coverage_pct=75.0,
        top_hashtags=[insight_engine.HashtagFrequency(tag="cinema", count=2)],
        avg_caption_length=42.0,
        avg_duration_seconds=24.0,
        duration_buckets={"<15s": 0, "15-30s": 3, "30-60s": 1, ">=60s": 0},
        top_posts=[],
        avg_engagement=1500.0,
        avg_activation_rate=0.05,
    )
    rendered = insight_engine._format_channel_section("UK", stats, "tiktok")
    assert "### UK: @warnerbrosuk" in rendered
    assert "4 Posts" in rendered
    # Top-Hashtag-Block taucht inline auf.
    assert "#cinema" in rendered


def test_pairs_registry_schema_accepts_uk_market():
    """Sprint UK-B1 expliziter Test zur Invariant-Lockerung: die
    enabled Pairs müssen ab B1 alle einen UK-Eintrag im channels-Mirror
    haben. Sprint 2026-05-12: universalpictures ist reaktiviert mit
    voller DE+US+UK-Spec, deshalb steht es jetzt mit in der Liste."""
    expected_uk_pairs = {
        "warnerbros", "sonypictures", "primevideo",
        "disney", "netflix", "paramountpictures",
        "universalpictures",
    }
    for pair_key in expected_uk_pairs:
        pair_def = insight_engine.PAIRS[pair_key]
        markets = {c["market"] for c in pair_def["channels"]}
        assert "UK" in markets, (
            f"Pair {pair_key!r} muss seit B1 einen UK-Channel im "
            f"channels-Mirror haben — gefunden: {markets}"
        )


# ---------- Sprint 2026-05-12: paramountplus + lionsgate -------------------


def test_paramountplus_pair_has_all_three_markets():
    """paramountplus ist ein voll-Pair: DE+US+UK auf TT und IG. YT
    fehlt UK (kein separater Paramount+ UK YouTube-Channel)."""
    pair_def = insight_engine.PAIRS["paramountplus"]
    assert pair_def["enabled"] is True
    assert pair_def["reason"] is None

    tt = pair_def["platforms"]["tiktok"]
    ig = pair_def["platforms"]["instagram"]
    yt = pair_def["platforms"]["youtube"]

    assert {c["market"] for c in tt} == {"DE", "US", "UK"}
    assert {c["market"] for c in ig} == {"DE", "US", "UK"}
    # YT: nur DE+US, kein UK.
    assert {c["market"] for c in yt} == {"DE", "US"}

    # channels-Mirror == tiktok-Liste (Backwards-Compat-Konvention).
    assert pair_def["channels"] == tt


def test_lionsgate_pair_has_us_and_uk_only():
    """lionsgate ist US+UK-only. Lionsgate hat keinen eigenen DE-
    Social-Auftritt — Vertrieb läuft via Leonine/Studiocanal. KEIN
    DE-Channel im Pair, das ist absichtlich und akzeptiert."""
    pair_def = insight_engine.PAIRS["lionsgate"]
    assert pair_def["enabled"] is True
    assert pair_def["reason"] is None

    tt = pair_def["platforms"]["tiktok"]
    ig = pair_def["platforms"]["instagram"]
    yt = pair_def["platforms"]["youtube"]

    assert {c["market"] for c in tt} == {"US", "UK"}
    assert {c["market"] for c in ig} == {"US", "UK"}
    # YT: nur US, kein UK, kein DE.
    assert {c["market"] for c in yt} == {"US"}

    # DE darf NICHT auftauchen — Regression-Guard.
    all_markets = {c["market"] for plat in pair_def["platforms"].values() for c in plat}
    assert "DE" not in all_markets, "lionsgate hat keinen DE-Auftritt"


def test_aggregate_pair_lionsgate_handles_missing_de_channel_gracefully():
    """lionsgate hat keinen DE-Channel definiert. aggregate_pair darf
    weder crashen noch eine 'DE-Channel fehlt'-Note generieren — der
    DE-Block bleibt einfach komplett aus, weil der Pair so konfiguriert
    ist."""
    with _session() as session:
        agg = insight_engine.aggregate_pair(session, "lionsgate", window_days=30)
        assert agg.pair_key == "lionsgate"
        assert agg.de_channel is None, (
            "lionsgate definiert keinen DE-Channel — de_channel muss None bleiben"
        )
        # US und UK Specs sind da, aber gegen leere DB unresolved.
        assert agg.us_channel is not None
        assert agg.us_channel.channel_found is False
        # KEINE 'DE-Channel fehlt'-Note: wir haben ja keinen DE-Spec.
        assert not any("DE-Channel" in n for n in agg.notes), (
            f"Unerwartete DE-Channel-Note für lionsgate: {agg.notes}"
        )


def test_aggregate_pair_paramountplus_uk_only_on_ig_tt_not_yt():
    """paramountplus voll-Pair-Garantie: TT + IG haben alle drei Märkte,
    YT hat keinen UK-Channel. aggregate_pair liefert eine
    PairAggregation mit ``platforms: list[PlatformAggregation]`` — wir
    iterieren über die per-Plattform-Slices und prüfen das
    uk_channel-Vorhandensein."""
    with _session() as session:
        agg = insight_engine.aggregate_pair(
            session, "paramountplus", window_days=30,
        )
    by_platform = {p.platform: p for p in agg.per_platform}
    assert by_platform["tiktok"].uk_channel is not None, (
        "paramountplus muss UK auf TikTok haben"
    )
    assert by_platform["instagram"].uk_channel is not None, (
        "paramountplus muss UK auf Instagram haben"
    )
    assert by_platform["youtube"].uk_channel is None, (
        "paramountplus hat keinen UK-YT-Channel — uk_channel muss None bleiben"
    )
    # DE und US sind auf allen drei Plattformen vorhanden.
    for plat in ("tiktok", "instagram", "youtube"):
        assert by_platform[plat].de_channel is not None
        assert by_platform[plat].us_channel is not None


# ---------- Sprint 2026-05-12: universalpictures reactivation ---------------


def test_universalpictures_pair_enabled():
    """Sprint 2026-05-12: universalpictures ist nach DE/US/UK-Channel-
    Aktivitäts-Check reaktiviert. Pair muss enabled sein, ``reason`` =
    None und das volle platforms-Dict mit TT/IG/YT mitbringen."""
    pair_def = insight_engine.PAIRS["universalpictures"]
    assert pair_def["enabled"] is True
    assert pair_def["reason"] is None
    assert "platforms" in pair_def
    assert set(pair_def["platforms"].keys()) == {"tiktok", "instagram", "youtube"}


def test_universalpictures_us_pool_on_instagram_contains_horror_sub_brand():
    """Sprint 2026-05-12: US-Seite auf Instagram ist Multi-Channel-Pool
    analog warnerbros/sonypictures/disney. @universalhorror ist die
    Horror-Slate-Sub-Brand (Blumhouse/Monkeypaw-Releases) und IG-only,
    es gibt sie nicht auf TT/YT."""
    pair_def = insight_engine.PAIRS["universalpictures"]
    ig_us_handles = {
        c["handle"] for c in pair_def["platforms"]["instagram"]
        if c["market"] == "US"
    }
    assert "universalpictures" in ig_us_handles, "IG-US-Master muss im Pool sein"
    assert "universalhorror" in ig_us_handles, (
        "@universalhorror als US-Sub-Brand-Pool muss IG-seitig im US-Pool sein"
    )
    # TT/YT haben kein @universalhorror-Pendant.
    tt_us_handles = {
        c["handle"] for c in pair_def["platforms"]["tiktok"]
        if c["market"] == "US"
    }
    yt_us_handles = {
        c["handle"].lower() for c in pair_def["platforms"]["youtube"]
        if c["market"] == "US"
    }
    assert "universalhorror" not in tt_us_handles
    assert "universalhorror" not in yt_us_handles


def test_universalpictures_uk_present_on_all_platforms():
    """Sprint 2026-05-12: UK-Channels seit Phase A registered (TT/IG/YT
    alle mvp=True). Pair muss UK auf jeder Plattform-Spec mitbringen,
    auch wenn UK-Seite Sa 17.05. ihre ersten Cron-Daten erst bekommt."""
    pair_def = insight_engine.PAIRS["universalpictures"]
    for platform in ("tiktok", "instagram", "youtube"):
        uk_handles = [
            c["handle"] for c in pair_def["platforms"][platform]
            if c["market"] == "UK"
        ]
        assert len(uk_handles) == 1, (
            f"universalpictures muss genau einen UK-Channel auf {platform} "
            f"haben — gefunden: {uk_handles}"
        )
    # channels-Mirror == tiktok-Liste (Backwards-Compat-Konvention).
    assert pair_def["channels"] == pair_def["platforms"]["tiktok"]


def test_aggregate_pair_universalpictures_returns_data():
    """Smoke-Test: aggregate_pair läuft für universalpictures gegen eine
    Mini-DB mit DE+US-Posts ohne Crash und liefert eine valide
    PairAggregation mit den erwarteten per-Plattform-Slices."""
    with _session() as session:
        us = Channel(
            name="Universal Pictures",
            platform="instagram",
            url="https://www.instagram.com/universalpictures/",
            handle="universalpictures",
            market=Market.US,
        )
        horror = Channel(
            name="Universal Horror",
            platform="instagram",
            url="https://www.instagram.com/universalhorror/",
            handle="universalhorror",
            market=Market.US,
        )
        de = Channel(
            name="Universal Pictures DE",
            platform="instagram",
            url="https://www.instagram.com/universalpicturesde/",
            handle="universalpicturesde",
            market=Market.DE,
        )
        session.add_all([us, horror, de])
        session.commit()
        for ch in (us, horror, de):
            session.refresh(ch)

        _make_post(
            session, us,
            caption="New trailer drop #Trailer",
            likes=8_000, comments=200, shares=80, saves=120,
            days_ago=3, url_suffix="up-us-1", platform="instagram",
        )
        _make_post(
            session, horror,
            caption="Horror slate #Blumhouse",
            likes=5_000, comments=120, shares=40, saves=60,
            days_ago=4, url_suffix="up-horror-1", platform="instagram",
        )
        _make_post(
            session, de,
            caption="Im Kino diese Woche #Kino",
            likes=1_200, comments=30, shares=10, saves=20,
            days_ago=2, url_suffix="up-de-1", platform="instagram",
        )

        agg = insight_engine.aggregate_pair(
            session, "universalpictures", window_days=30,
        )

    assert agg.pair_key == "universalpictures"
    by_platform = {p.platform: p for p in agg.per_platform}
    assert set(by_platform.keys()) == {"tiktok", "instagram", "youtube"}

    ig = by_platform["instagram"]
    assert ig.us_channel is not None
    # Pool addiert Master + Horror-Sub-Brand → 2 Posts auf US-Seite IG.
    assert ig.us_channel.posts_count == 2
    assert ig.de_channel is not None
    assert ig.de_channel.posts_count == 1
    # UK-Spec da, aber DB ist UK-leer → channel_found=False, kein Crash.
    assert ig.uk_channel is not None
    assert ig.uk_channel.channel_found is False


# ---------- Sprint 2026-05-12: Disney IG UK sub-brand gap ------------------


def test_disney_pair_uk_ig_pool_includes_starwarsuk():
    """Sprint 2026-05-12: @starwarsuk (108k Follower, IG-only) wurde
    via sprint_disney_uk_subbrand_gap_2026_05_12 angelegt und muss im
    disney-Pair IG-UK-Pool stehen. YT-Pool hat StarWarsUK schon, TT
    hat kein StarWarsUK-Pendant — daher Edit nur auf IG."""
    ig_uk_handles = {
        c["handle"] for c in insight_engine.PAIRS["disney"]["platforms"]["instagram"]
        if c["market"] == "UK"
    }
    assert ig_uk_handles == {"disneyuk", "disneystudiosuk", "marvel_uk", "starwarsuk"}, (
        f"disney IG-UK-Pool muss 4 Channels enthalten (Master + 3 Sub-Brands) — "
        f"gefunden: {ig_uk_handles}"
    )
    # TT-UK bleibt single-handle (kein @starwarsuk auf TikTok).
    tt_uk_handles = {
        c["handle"] for c in insight_engine.PAIRS["disney"]["platforms"]["tiktok"]
        if c["market"] == "UK"
    }
    assert "starwarsuk" not in tt_uk_handles


# ---------- Retry-Echo-Debounce (2026-05-12) -------------------------------


def _persist_a_warnerbros_brief(monkeypatch) -> tuple[Session, dict]:
    """Hilfsroutine: generiere via generate_and_persist_report einen Brief
    mit gemocktem Opus-Call und gib (session, mock_state) zurück. Aus
    mock_state.calls liest der Test, wie oft Opus tatsächlich gerufen
    wurde."""
    state = {"calls": 0}
    sample = {
        "headline": "H",
        "tldr": "x",
        "trends": [],
        "actions": [],
        "cross_market_insight": {"de_vs_us": "a", "transfer_opportunity": "b"},
        "risks": [],
        "data_caveats": [],
    }
    fake_message = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                name="submit_weekly_brief",
                input=sample,
            )
        ],
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )

    def _fake_call(**kwargs):
        state["calls"] += 1
        return fake_message

    monkeypatch.setattr(insight_engine, "messages_create_strict_json", _fake_call)
    monkeypatch.setattr(insight_engine, "is_anthropic_configured", lambda: True)

    return state


def test_generate_and_persist_recent_force_returns_existing(monkeypatch):
    """Retry-Echo-Fix: force=true innerhalb des 120s-Debounce-Window
    MUSS die bereits persistierte Row zurückgeben, ohne einen zweiten
    Opus-Call zu starten. Das ist die zentrale Garantie nach dem
    2026-05-12-Diagnose-Befund (zwei Anthropic-Calls bei einem User-
    Trigger durch Edge-Proxy-Retry)."""
    state = _persist_a_warnerbros_brief(monkeypatch)
    with _session() as session:
        _seed_warnerbros_pair(session)

        first = insight_engine.generate_and_persist_report(
            session, "warnerbros", force=True,
        )
        assert state["calls"] == 1
        assert first.llm_output is not None

        # Zweiter Call innerhalb der 120s-Debounce — gleiche User-
        # Trigger-Sekunde simuliert ein Retry-Echo.
        second = insight_engine.generate_and_persist_report(
            session, "warnerbros", force=True,
        )

        assert state["calls"] == 1, (
            "Zweiter force-Call innerhalb 120s darf KEINEN neuen Opus-"
            f"Call starten, war aber {state['calls']}."
        )
        # Inhaltlich gleich: gleicher pair/iso_week.
        assert second.pair_key == first.pair_key
        assert second.iso_week == first.iso_week


def test_generate_and_persist_force_dedups_against_aged_row(monkeypatch):
    """Sprint 3c semantics flip: force=true no longer re-generates against
    an aged row. The composite PK ``(pair_key, iso_year, iso_week)`` is
    authoritative — once a brief exists for the slot, every force-call
    short-circuits via the lock_dedup path. A caller who genuinely wants
    to overwrite deletes the row first. Old behaviour (regenerate after
    120s) leaked $1.81 LLM calls in the Sprint-3b smoke when two parallel
    force-curls raced and Opus latency exceeded 120s."""
    state = _persist_a_warnerbros_brief(monkeypatch)
    with _session() as session:
        _seed_warnerbros_pair(session)

        first = insight_engine.generate_and_persist_report(
            session, "warnerbros", force=True,
        )
        assert state["calls"] == 1

        from app.models.entities import InsightReport as InsightReportRow

        stale_row = session.get(
            InsightReportRow,
            (first.pair_key, first.iso_year, first.iso_week),
        )
        stale_row.generated_at = datetime.now(timezone.utc) - timedelta(seconds=200)
        session.add(stale_row)
        session.commit()

        second = insight_engine.generate_and_persist_report(
            session, "warnerbros", force=True,
        )
        assert state["calls"] == 1, (
            f"force-call against aged row MUST dedup against the composite "
            f"PK, but LLM was invoked {state['calls']} times."
        )
        assert second.pair_key == first.pair_key
        assert second.iso_week == first.iso_week


def test_generate_and_persist_no_recent_row_generates(monkeypatch):
    """Wenn gar keine persistierte Row existiert, MUSS force=true
    einen Opus-Call starten — der Debounce-Check darf nicht
    fälschlich kurzschließen."""
    state = _persist_a_warnerbros_brief(monkeypatch)
    with _session() as session:
        _seed_warnerbros_pair(session)

        report = insight_engine.generate_and_persist_report(
            session, "warnerbros", force=True,
        )
        assert state["calls"] == 1
        assert report.llm_output is not None


def test_messages_create_text_no_longer_sends_idempotency_header(monkeypatch):
    """PR-#123-Idempotency-Key wurde nach Smoke-Test-Befund 2026-05-12
    entfernt (Anthropic dedupliziert den Header nicht). Regression-
    Guard: messages_create_text DARF KEINE extra_headers mehr an die
    SDK reichen — der Aufruf bleibt minimal, alles über dem ist
    dead code."""
    from app.config import settings
    from app.services import anthropic_client

    monkeypatch.setattr(settings, "anthropic_api_key", "test-key", raising=False)

    captured: dict = {}

    class _FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(content=[], usage=None)

    class _FakeClient:
        messages = _FakeMessages()

    monkeypatch.setattr(anthropic_client, "_client", lambda: _FakeClient())

    anthropic_client.messages_create_text(
        model="claude-opus-4-7",
        system="x",
        user_message="y",
    )
    # Nach dem Cleanup darf der Header-Pfad nicht mehr existieren.
    assert "extra_headers" not in captured


# ---------- Advisory-Lock race-condition fix (2026-05-12 follow-up) -------


def test_advisory_lock_skipped_on_sqlite_dialect():
    """SQLite-Test-Pfad: _acquire_brief_lock muss als no-op zurückkehren
    (False), damit Pre-Check + LLM-Call wie bisher laufen. Postgres-
    only feature — keine Lock-SQL gegen SQLite gesendet."""
    with _session() as session:
        acquired = insight_engine._acquire_brief_lock(
            session,
            pair_key="warnerbros",
            iso_year=2026,
            iso_week=20,
        )
    assert acquired is False


def test_advisory_lock_issued_on_postgres_dialect(monkeypatch):
    """Postgres-Pfad: _acquire_brief_lock muss SET LOCAL lock_timeout
    UND pg_advisory_xact_lock(int) an die Session schicken. Wir
    mocken den Dialect auf 'postgresql' und sammeln alle SQL-Texte,
    die durch session.exec laufen."""
    from sqlmodel import SQLModel, create_engine

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    session = Session(engine)

    # Dialect-Override: wir kaufen vor, dass session.bind.dialect.name
    # auf 'postgresql' zeigt, damit der Lock-Pfad zündet.
    monkeypatch.setattr(session.bind.dialect, "name", "postgresql", raising=False)

    issued: list[tuple[str, dict | None]] = []

    def _fake_exec(statement, params=None, **kwargs):
        issued.append((str(statement), params))
        # Return-Wert wird auf dem Postgres-Pfad nicht weiterverwendet
        # für die Lock-Statements.
        return None

    monkeypatch.setattr(session, "exec", _fake_exec)

    acquired = insight_engine._acquire_brief_lock(
        session,
        pair_key="warnerbros",
        iso_year=2026,
        iso_week=20,
    )
    assert acquired is True
    # Erwartet: SET LOCAL lock_timeout VOR dem advisory lock.
    sqls = [stmt for stmt, _ in issued]
    assert any("SET LOCAL lock_timeout" in s for s in sqls)
    assert any("pg_advisory_xact_lock" in s for s in sqls)


def test_generate_and_persist_advisory_lock_short_circuits_second_call(monkeypatch):
    """Hauptgarantie: ein zweiter Aufruf von generate_and_persist_report,
    der erst NACH dem ersten an die Reihe kommt (das simulieren wir
    durch sequenzielles Aufrufen mit force=True), MUSS den persistierten
    Row sehen und KEINEN zweiten LLM-Call starten — auch wenn das
    Postgres-Dialekt-Mocking nicht greift (SQLite-Pfad). Vor dem
    Advisory-Lock-Fix: 2 LLM-Calls bei 5s curl-spacing weil die Pre-
    Check-TOCTOU-Lücke offen war."""
    state = _persist_a_warnerbros_brief(monkeypatch)
    with _session() as session:
        _seed_warnerbros_pair(session)
        first = insight_engine.generate_and_persist_report(
            session, "warnerbros", force=True,
        )
        assert state["calls"] == 1
        assert first.llm_output is not None

        # Zweiter sequenzieller Call: Row bereits committed, Re-Check
        # INNERHALB des Lock-Pfads (oder Pre-Check auf SQLite) muss
        # die Row sehen und kurzschließen.
        second = insight_engine.generate_and_persist_report(
            session, "warnerbros", force=True,
        )
        assert state["calls"] == 1, (
            "Zweiter sequenzieller Call innerhalb 120s darf KEINEN "
            f"neuen Opus-Call starten, war aber {state['calls']}."
        )
        assert second.pair_key == first.pair_key


# ---------- Sprint 3c: Double-Check-Locking semantics ----------------------


def test_force_call_after_existing_returns_existing(monkeypatch, caplog):
    """Sprint 3c #1: a pre-existing row + force=true returns the existing
    row, no LLM call, with outcome=lock_dedup. This is the dedup path that
    the Sprint-3b race-condition smoke hit when C2 acquired the lock after
    C1 had committed."""
    import logging

    state = _persist_a_warnerbros_brief(monkeypatch)
    with _session() as session:
        _seed_warnerbros_pair(session)

        # First call generates and persists the row that the second call
        # will then dedup against.
        first = insight_engine.generate_and_persist_report(
            session, "warnerbros", force=True,
        )
        assert state["calls"] == 1
        assert first.llm_output is not None

        # Second force=true call: no new LLM invocation, returns existing.
        caplog.set_level(logging.INFO, logger="app.services.insight_engine")
        caplog.clear()

        second = insight_engine.generate_and_persist_report(
            session, "warnerbros", force=True,
        )

        assert state["calls"] == 1, (
            f"force-call with existing row must NOT invoke LLM; was {state['calls']}."
        )
        assert second.pair_key == first.pair_key
        assert second.iso_week == first.iso_week

        outcomes = [
            rec.__dict__.get("outcome")
            for rec in caplog.records
            if rec.message == "brief_pipeline_done"
        ]
        assert outcomes == ["lock_dedup"], (
            f"expected exactly one brief_pipeline_done with outcome=lock_dedup, "
            f"got: {outcomes}"
        )


def test_force_call_no_existing_creates_new(monkeypatch, caplog):
    """Sprint 3c #2: with no row in the DB, force=true must invoke the LLM
    and persist exactly once, with outcome=fresh_generation."""
    import logging

    state = _persist_a_warnerbros_brief(monkeypatch)
    with _session() as session:
        _seed_warnerbros_pair(session)

        caplog.set_level(logging.INFO, logger="app.services.insight_engine")
        caplog.clear()

        report = insight_engine.generate_and_persist_report(
            session, "warnerbros", force=True,
        )

        assert state["calls"] == 1, (
            f"force-call with no existing row must invoke LLM exactly once; "
            f"was {state['calls']}."
        )
        assert report.llm_output is not None

        outcomes = [
            rec.__dict__.get("outcome")
            for rec in caplog.records
            if rec.message == "brief_pipeline_done"
        ]
        assert outcomes == ["fresh_generation"], (
            f"expected exactly one brief_pipeline_done with "
            f"outcome=fresh_generation, got: {outcomes}"
        )


def test_double_check_locking_concurrent(monkeypatch, caplog):
    """Sprint 3c #3: two parallel force=true calls with a mocked lock end
    with exactly one LLM invocation, both callers receive a brief, and
    the loser logs outcome=lock_dedup. This is the canonical Sprint-3b
    race-condition reproduction at unit-test scale.

    The mocked _acquire_brief_lock wraps a threading.Lock so the second
    thread really does block on the first. Release is wired to
    SQLAlchemy's after_commit / after_rollback events to mirror the
    real pg_advisory_xact_lock semantics (transaction-bound) — when T1
    commits in _persist_report, the mock-lock releases and T2's call
    unblocks. T2's session.get inside the lock then finds T1's committed
    row and short-circuits via lock_dedup.

    The cross-thread SQLite engine uses StaticPool + check_same_thread=
    False so a single in-memory database is visible to both threads.
    """
    import logging
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import sqlalchemy as sa
    from sqlalchemy.pool import StaticPool

    state = _persist_a_warnerbros_brief(monkeypatch)

    # Single shared in-memory engine across the two threads.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as setup_session:
        _seed_warnerbros_pair(setup_session)

    mock_lock = threading.Lock()

    def _mocked_acquire(session, *, pair_key, iso_year, iso_week):
        mock_lock.acquire()

        def _release(*_args, **_kwargs):
            try:
                mock_lock.release()
            except RuntimeError:
                # Already released or never held on this branch — safe to ignore.
                pass

        sa.event.listen(session, "after_commit", _release, once=True)
        sa.event.listen(session, "after_rollback", _release, once=True)
        return True

    monkeypatch.setattr(insight_engine, "_acquire_brief_lock", _mocked_acquire)

    # T1 in mock-LLM holds briefly to let T2 actually contend on the lock.
    t1_inside_llm = threading.Event()
    t2_started = threading.Event()
    original_fake_call = insight_engine.messages_create_strict_json

    def _gated_call(**kwargs):
        # Only the very first invocation (T1) waits — T2 should never
        # reach this path if the lock-dedup works.
        if not t1_inside_llm.is_set():
            t1_inside_llm.set()
            # Wait up to 5s for T2 to enter _acquire_brief_lock; if T2 is
            # already blocked on the mock lock, that's exactly the
            # condition we want before T1 returns and commits.
            t2_started.wait(timeout=5)
        return original_fake_call(**kwargs)

    monkeypatch.setattr(insight_engine, "messages_create_strict_json", _gated_call)

    caplog.set_level(logging.INFO, logger="app.services.insight_engine")
    caplog.clear()

    def _worker(label: str):
        with Session(engine) as session:
            if label == "T2":
                # Make sure T2 starts only AFTER T1 is inside the LLM call.
                t1_inside_llm.wait(timeout=5)
                t2_started.set()
            return insight_engine.generate_and_persist_report(
                session, "warnerbros", force=True,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        t1_future = pool.submit(_worker, "T1")
        t2_future = pool.submit(_worker, "T2")
        results = {"T1": t1_future.result(timeout=15), "T2": t2_future.result(timeout=15)}

    # Exactly one LLM call total.
    assert state["calls"] == 1, (
        f"two parallel force-calls must collapse to one LLM call via the "
        f"lock + recheck; was {state['calls']}."
    )

    # Both callers got a brief, same PK.
    assert results["T1"].pair_key == "warnerbros"
    assert results["T2"].pair_key == "warnerbros"
    assert results["T1"].iso_week == results["T2"].iso_week

    # One outcome=fresh_generation (T1), one outcome=lock_dedup (T2).
    outcomes = sorted(
        rec.__dict__.get("outcome")
        for rec in caplog.records
        if rec.message == "brief_pipeline_done"
    )
    assert outcomes == ["fresh_generation", "lock_dedup"], (
        f"expected one fresh_generation + one lock_dedup, got: {outcomes}"
    )


# ---------- Sprint 28.05.2026: Evidenz-Block / Citation-Validator --------


def test_build_citation_allow_set_collects_post_urls_and_match_keys():
    """``_build_citation_allow_set`` muss alle zitier-faehigen IDs aus
    ``PairAggregation`` einsammeln: ``post_url`` aus top/historical/ranked
    Posts, ``asset_id`` aus ranked Posts, ``match_key`` aus den drei
    Cross-Market-Match-Listen.
    """
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)

    allow_set = insight_engine._build_citation_allow_set(agg)

    assert len(allow_set) > 0, "allow_set darf bei seeded pair nicht leer sein"
    # Cross-market match-key aus dem Seed muss drin sein
    assert "mk2-trailer-1" in allow_set
    # Mindestens eine US-Post-URL muss drin sein (URL-Set haengt vom Seed ab)
    assert any("tiktok.com" in s for s in allow_set), (
        "mindestens eine TikTok-post_url muss im allow_set landen"
    )


def test_validate_citations_returns_true_when_all_ids_known(caplog):
    """Citation-Validator gibt ``True`` zurueck, wenn jede zitierte ID
    im Allow-Set vorkommt. Phase-1-Summary landet als INFO mit
    ``all_belegt=True``."""
    with _session() as session:
        data = _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        valid_post_url = data["us_posts"][0].post_url

        from app.schemas.insights import (
            Action, CrossMarketInsight, LLMReport, Trend,
        )
        report = LLMReport(
            headline="H",
            tldr="x",
            trends=[Trend(
                name="n", evidence="e", implication_for_creation="i",
                cited_post_ids=[valid_post_url],
            )],
            actions=[Action(
                what="w", why="y", for_whom="f",
                cited_post_ids=[valid_post_url],
            )],
            cross_market_insight=CrossMarketInsight(
                de_vs_us="a", transfer_opportunity="b",
                cited_post_ids=["mk2-trailer-1"],  # match_key
            ),
            risks=[], data_caveats=[],
        )

        with caplog.at_level(logging.INFO, logger="app.services.insight_engine"):
            ok = insight_engine._validate_citations(
                report, agg,
                pair_key="warnerbros", iso_year=2026, iso_week=22,
            )

        assert ok is True
        summary = [r for r in caplog.records if r.message == "insight-engine-citation-summary"]
        assert len(summary) == 1
        rec = summary[0]
        assert rec.__dict__.get("all_belegt") is True
        assert rec.__dict__.get("missing_ids_total") == 0


def test_validate_citations_returns_false_and_logs_when_id_unknown(caplog):
    """Citation-Validator gibt ``False`` zurueck, wenn eine zitierte ID
    nicht im Allow-Set ist, und logged
    ``insight-engine-citation-unverified`` pro betroffener Sektion."""
    with _session() as session:
        _seed_warnerbros_pair(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)

        from app.schemas.insights import (
            Action, CrossMarketInsight, LLMReport, Trend,
        )
        report = LLMReport(
            headline="H", tldr="x",
            trends=[Trend(
                name="n", evidence="e", implication_for_creation="i",
                cited_post_ids=["https://tiktok.com/@invented/video/999999"],
            )],
            actions=[Action(what="w", why="y", for_whom="f")],  # leer → kein Check
            cross_market_insight=CrossMarketInsight(
                de_vs_us="a", transfer_opportunity="b",
            ),
            risks=[], data_caveats=[],
        )

        with caplog.at_level(logging.WARNING, logger="app.services.insight_engine"):
            ok = insight_engine._validate_citations(
                report, agg,
                pair_key="warnerbros", iso_year=2026, iso_week=22,
            )

        assert ok is False
        unverified = [
            r for r in caplog.records
            if r.message == "insight-engine-citation-unverified"
        ]
        assert len(unverified) == 1
        rec = unverified[0]
        assert rec.__dict__.get("section") == "trends[0].cited_post_ids"
        assert rec.__dict__.get("missing_count") == 1
        assert rec.__dict__.get("pair_key") == "warnerbros"


def test_generate_weekly_report_soft_mode_delivers_brief_despite_unverified_citation(
    monkeypatch, caplog,
):
    """Phase 1 / Soft-Modus (Default): das LLM zitiert eine erfundene ID
    → Validator loggt ``unverified``, aber der Brief wird trotzdem
    ausgeliefert (llm_output ist nicht None, kein zusaetzlicher Retry).
    Das ist der Stufenmodell-B-Cutover-Zustand vor Strikt-Flip.
    """
    sample = {
        "headline": "H", "tldr": "x",
        "trends": [{
            "name": "n", "evidence": "e", "implication_for_creation": "i",
            "cited_post_ids": ["https://tiktok.com/@invented/video/999999"],
        }],
        "actions": [],
        "cross_market_insight": {"de_vs_us": "a", "transfer_opportunity": "b"},
        "risks": [], "data_caveats": [],
    }
    fake_message = SimpleNamespace(
        content=[SimpleNamespace(
            type="tool_use", name="submit_weekly_brief", input=sample,
        )],
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )
    call_count = {"n": 0}

    def _fake_call(**kwargs):
        call_count["n"] += 1
        return fake_message

    monkeypatch.setattr(insight_engine, "messages_create_strict_json", _fake_call)
    monkeypatch.setattr(insight_engine, "is_anthropic_configured", lambda: True)
    # Soft-Modus garantieren (Default, aber explizit setzen damit der Test
    # nicht von Globals abhaengt).
    monkeypatch.setattr(
        insight_engine.settings, "insight_citation_strict_enforce", False,
        raising=False,
    )

    with _session() as session:
        _seed_warnerbros_pair(session)
        with caplog.at_level(logging.WARNING, logger="app.services.insight_engine"):
            report = insight_engine.generate_weekly_report(session, "warnerbros")

    # Brief geht raus
    assert report.llm_output is not None
    assert report.llm_output.headline == "H"
    # Genau ein API-Call (kein Strikt-Retry)
    assert call_count["n"] == 1
    # Unverified-Log ist da
    unverified = [
        r for r in caplog.records
        if r.message == "insight-engine-citation-unverified"
    ]
    assert len(unverified) == 1


def test_generate_weekly_report_strict_mode_retries_on_unverified_citation(
    monkeypatch, caplog,
):
    """Phase 2 / Strikt-Modus: Citation-Fail loest die bestehende
    Retry-Schleife aus. Erste zwei Antworten zitieren erfunden, dritte
    zitiert eine bekannte ID → Brief wird mit drittem Anlauf
    ausgeliefert. Dasselbe Retry-Limit wie bei Parse-Fail (MAX_RECALLS=2,
    also bis zu 3 Calls)."""
    bad_sample = {
        "headline": "H", "tldr": "x",
        "trends": [{
            "name": "n", "evidence": "e", "implication_for_creation": "i",
            "cited_post_ids": ["https://tiktok.com/@invented/video/999999"],
        }],
        "actions": [],
        "cross_market_insight": {"de_vs_us": "a", "transfer_opportunity": "b"},
        "risks": [], "data_caveats": [],
    }

    monkeypatch.setattr(insight_engine, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(
        insight_engine.settings, "insight_citation_strict_enforce", True,
        raising=False,
    )

    with _session() as session:
        data = _seed_warnerbros_pair(session)
        good_url = data["us_posts"][0].post_url
        good_sample = {**bad_sample, "trends": [{
            "name": "n", "evidence": "e", "implication_for_creation": "i",
            "cited_post_ids": [good_url],
        }]}

        def _msg(payload):
            return SimpleNamespace(
                content=[SimpleNamespace(
                    type="tool_use", name="submit_weekly_brief", input=payload,
                )],
                usage=SimpleNamespace(input_tokens=100, output_tokens=50),
            )

        responses = iter([_msg(bad_sample), _msg(bad_sample), _msg(good_sample)])

        def _fake_call(**kwargs):
            return next(responses)

        monkeypatch.setattr(insight_engine, "messages_create_strict_json", _fake_call)

        with caplog.at_level(logging.WARNING, logger="app.services.insight_engine"):
            report = insight_engine.generate_weekly_report(session, "warnerbros")

    assert report.llm_output is not None
    assert report.llm_output.trends[0].cited_post_ids == [good_url]
    # Zwei Retry-Log-Lines mit reason=citation-strict-unverified
    retry_lines = [
        r for r in caplog.records
        if r.message == "insight-engine-json-parse-retry"
    ]
    assert len(retry_lines) == 2
    assert all(
        r.__dict__.get("reason") == "citation-strict-unverified"
        for r in retry_lines
    )


def test_generate_weekly_report_strict_mode_exhausted_falls_back_to_raw(
    monkeypatch, caplog,
):
    """Phase 2 / Strikt-Modus: alle Retries scheitern an nicht-belegten
    Zitaten → ``llm_output=None`` (Persist-Skip-Pfad), ``raw_llm_text``
    haelt die letzte LLM-Antwort fuer Wolf-Diagnose, eigenes Log-Event
    ``insight-engine-citation-strict-exhausted``."""
    bad_sample = {
        "headline": "H", "tldr": "x",
        "trends": [{
            "name": "n", "evidence": "e", "implication_for_creation": "i",
            "cited_post_ids": ["https://tiktok.com/@invented/video/999999"],
        }],
        "actions": [],
        "cross_market_insight": {"de_vs_us": "a", "transfer_opportunity": "b"},
        "risks": [], "data_caveats": [],
    }
    fake_message = SimpleNamespace(
        content=[SimpleNamespace(
            type="tool_use", name="submit_weekly_brief", input=bad_sample,
        )],
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )

    monkeypatch.setattr(insight_engine, "messages_create_strict_json", lambda **k: fake_message)
    monkeypatch.setattr(insight_engine, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(
        insight_engine.settings, "insight_citation_strict_enforce", True,
        raising=False,
    )

    with _session() as session:
        _seed_warnerbros_pair(session)
        with caplog.at_level(logging.ERROR, logger="app.services.insight_engine"):
            report = insight_engine.generate_weekly_report(session, "warnerbros")

    assert report.llm_output is None
    assert report.raw_llm_text is not None and "invented" in report.raw_llm_text
    exhausted = [
        r for r in caplog.records
        if r.message == "insight-engine-citation-strict-exhausted"
    ]
    assert len(exhausted) == 1


# ---------- Sprint 28.05.2026 Punkt 4: Breakout-Score ----------------------


def _seed_pair_with_breakout_pool(session: Session) -> dict:
    """Seed-Fixture fuer Breakout-Tests: ein Channel (warnerbros US) mit
    6 Posts, davon einer klarer Ausreisser (~10x ueber dem Cluster), und
    DE-Channel mit nur 2 Posts (unter der ``BREAKOUT_MIN_SAMPLE_SIZE``-
    Schwelle, sodass dort kein Score berechnet wird).
    """
    us = Channel(
        name="Warner Bros US", platform="tiktok",
        url="https://www.tiktok.com/@warnerbros", handle="warnerbros",
        market=Market.US,
    )
    de = Channel(
        name="Warner Bros DE", platform="tiktok",
        url="https://www.tiktok.com/@warnerbrosdeutschland",
        handle="warnerbrosdeutschland", market=Market.DE,
    )
    session.add_all([us, de])
    session.commit(); session.refresh(us); session.refresh(de)

    # 5 Cluster-Posts um 100 Engagement + 1 Breakout @ 1000, jung.
    cluster = [
        _make_post(session, us, caption=f"cluster {i}",
                   likes=100 + i * 10, days_ago=10 - i, url_suffix=f"c{i}")
        for i in range(5)
    ]
    breakout = _make_post(
        session, us, caption="big breakout",
        likes=1000, days_ago=2, url_suffix="break",
    )
    # DE: zu wenig Posts → kein Score erwartet.
    de_p1 = _make_post(session, de, caption="de a", likes=80, days_ago=3, url_suffix="de1")
    de_p2 = _make_post(session, de, caption="de b", likes=120, days_ago=5, url_suffix="de2")

    return {
        "us": us, "de": de,
        "us_cluster": cluster, "us_breakout": breakout,
        "de_posts": [de_p1, de_p2],
    }


def test_breakout_score_identifies_outlier_above_channel_baseline():
    """Channel mit 6 Posts (5 Cluster ~100 + 1 Hit @ 1000) → der Hit
    bekommt den hoechsten weighted_score und multiplier deutlich > 1,
    die Cluster-Posts haben multiplier ~ 0.5 (Mean wird vom Hit
    hochgezogen)."""
    from app.services.insight_engine import _compute_breakout_scores
    from datetime import timezone, datetime
    with _session() as session:
        data = _seed_pair_with_breakout_pool(session)
        all_posts = list(data["us_cluster"]) + [data["us_breakout"]]
        now = datetime.now(timezone.utc)
        scores = _compute_breakout_scores(all_posts, now=now)

        assert len(scores) == 6  # alle 6 Posts haben Score
        hit_score = scores[data["us_breakout"].id]
        assert hit_score.multiplier > 2.5, (
            f"Breakout-Post sollte deutlich ueber Schnitt liegen, got "
            f"{hit_score.multiplier:.2f}x"
        )
        assert hit_score.z_score > 1.5, (
            f"Breakout sollte > 1.5 sigma sein, got z={hit_score.z_score:.2f}"
        )
        # Cluster-Posts liegen unter dem (vom Hit hochgezogenen) Mean.
        for p in data["us_cluster"]:
            assert scores[p.id].multiplier < 1.0


def test_breakout_score_skipped_below_min_sample_size():
    """Channel mit < 5 Posts → keine Scores berechnet (Robustheits-Regel
    aus dem Briefing)."""
    from app.services.insight_engine import _compute_breakout_scores
    from datetime import timezone, datetime
    with _session() as session:
        data = _seed_pair_with_breakout_pool(session)
        now = datetime.now(timezone.utc)
        scores = _compute_breakout_scores(data["de_posts"], now=now)
        assert scores == {}


def test_breakout_score_skipped_when_all_engagements_identical():
    """Wenn alle Posts identische Engagement-Summe haben, ist std=0 und
    der z-Score nicht definiert → leeres Dict, alle Posts score-los."""
    from app.services.insight_engine import _compute_breakout_scores
    from datetime import timezone, datetime
    with _session() as session:
        us = Channel(
            name="Warner Bros US", platform="tiktok",
            url="https://x", handle="warnerbros", market=Market.US,
        )
        session.add(us); session.commit(); session.refresh(us)
        posts = [
            _make_post(session, us, caption=f"flat {i}", likes=100,
                       days_ago=i + 1, url_suffix=f"f{i}")
            for i in range(7)
        ]
        scores = _compute_breakout_scores(posts, now=datetime.now(timezone.utc))
        assert scores == {}


def test_breakout_score_decay_weights_newer_posts_higher():
    """Recency-Decay: zwei Posts mit identischer Engagement-Summe (= z-
    Score identisch), unterschiedlichem Alter → weighted_score ist beim
    jueneren hoeher. Beweist dass die Decay-Komponente tatsaechlich
    multiplikativ greift, nicht nur dekorativ ist."""
    from app.services.insight_engine import _compute_breakout_scores, BREAKOUT_HALFLIFE_DAYS
    from datetime import timezone, datetime
    with _session() as session:
        us = Channel(
            name="Warner Bros US", platform="tiktok",
            url="https://x", handle="warnerbros", market=Market.US,
        )
        session.add(us); session.commit(); session.refresh(us)
        # 4 Cluster-Posts mit verteilten Engagements (damit std > 0) +
        # 2 Breakout-Posts mit IDENTISCHER Engagement-Summe, aber 0 vs
        # 14 Tage alt.
        cluster_eng = [50, 70, 90, 110]
        cluster = [
            _make_post(session, us, caption=f"c{i}",
                       likes=cluster_eng[i], days_ago=20, url_suffix=f"c{i}")
            for i in range(4)
        ]
        recent = _make_post(session, us, caption="recent hit", likes=500,
                            days_ago=0, url_suffix="recent")
        old = _make_post(session, us, caption="old hit", likes=500,
                         days_ago=int(2 * BREAKOUT_HALFLIFE_DAYS),
                         url_suffix="old")
        scores = _compute_breakout_scores(cluster + [recent, old],
                                          now=datetime.now(timezone.utc))
        recent_s = scores[recent.id]
        old_s = scores[old.id]
        # Z-Score identisch (gleiche Engagement → gleiche Position relativ
        # zu Mean/Std).
        assert abs(recent_s.z_score - old_s.z_score) < 1e-9
        # Decay-Weight beim Frischen ~1.0, beim 2-Halbwertzeiten-alten ~0.25.
        assert recent_s.decay_weight > 0.95
        assert 0.20 < old_s.decay_weight < 0.30
        # Folge: weighted_score deutlich groesser beim frischen.
        assert recent_s.weighted_score > old_s.weighted_score * 3


def test_channel_stats_attaches_breakout_score_and_populates_breakouts_slot():
    """Integration: ``_channel_stats`` muss den Score an jeden RankedPost
    haengen und die Top-N nach weighted_score in ``breakouts``
    befuellen."""
    with _session() as session:
        data = _seed_pair_with_breakout_pool(session)
        agg = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        us_stats = agg.us_channel

        # Alle 6 US-RankedPosts haben breakout_score gesetzt.
        assert len(us_stats.ranked_posts) == 6
        scored = [r for r in us_stats.ranked_posts if r.breakout_score is not None]
        assert len(scored) == 6

        # breakouts: Top-3 nach weighted_score; Position 1 ist der Hit.
        assert 1 <= len(us_stats.breakouts) <= 3
        top_breakout = us_stats.breakouts[0]
        assert top_breakout.post_url and "break" in top_breakout.post_url
        assert top_breakout.breakout_score.multiplier > 2.5

        # DE-Channel hat nur 2 Posts → keine Scores, leerer breakouts-Slot.
        de_stats = agg.de_channel
        assert len(de_stats.ranked_posts) == 2
        assert all(r.breakout_score is None for r in de_stats.ranked_posts)
        assert de_stats.breakouts == []


def test_breakout_score_is_deterministic_across_calls():
    """Persistenz-Cache-Garantie: zwei ``aggregate_pair``-Aufrufe
    innerhalb derselben ISO-Woche produzieren identische
    ``breakouts``-Reihenfolge. Wichtig, damit Cache-Hit-Briefe
    byte-fuer-byte zu Cache-Miss-Briefen passen."""
    with _session() as session:
        _seed_pair_with_breakout_pool(session)
        agg1 = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        agg2 = insight_engine.aggregate_pair(session, "warnerbros", window_days=30)
        urls1 = [r.post_url for r in agg1.us_channel.breakouts]
        urls2 = [r.post_url for r in agg2.us_channel.breakouts]
        assert urls1 == urls2
        scores1 = [r.breakout_score.weighted_score for r in agg1.us_channel.breakouts]
        scores2 = [r.breakout_score.weighted_score for r in agg2.us_channel.breakouts]
        assert scores1 == scores2


# ---------- Option A: last_completed_iso_week_anchor -----------------------


@pytest.mark.parametrize("dt", [
    datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),   # Monday,    KW23/2026
    datetime(2026, 6, 3, 9, 0, tzinfo=timezone.utc),   # Wednesday, KW23/2026
    datetime(2026, 6, 7, 9, 0, tzinfo=timezone.utc),   # Sunday,    KW23/2026
])
def test_last_completed_iso_week_anchor_hits_previous_week_every_weekday(dt):
    """On Mon/Wed/Sun of KW23 the anchor lands in the previous ISO week
    (KW22). The old ``now - 1 day`` idiom only worked on Monday — this is
    the core of the read/write Monday-mismatch fix."""
    cal = dt.isocalendar()
    assert (cal.year, cal.week) == (2026, 23)  # sanity: input really is KW23
    anchor = insight_engine.last_completed_iso_week_anchor(dt)
    a = anchor.isocalendar()
    assert (a.year, a.week) == (2026, 22)


@pytest.mark.parametrize("dt", [
    datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
    datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),   # New Year's Day (KW1)
    datetime(2027, 1, 4, 12, 0, tzinfo=timezone.utc),   # Mon of ISO-week-1/2027
])
def test_last_completed_iso_week_anchor_is_exactly_one_iso_week_back(dt):
    """Invariant incl. year boundaries: the anchor sits in the same ISO week
    as ``dt - 7 days`` (exactly one ISO week earlier), independent of the
    weekday — e.g. 2026-01-01 → 2025-KW52, 2027-01-04 → 2026-KW53."""
    anchor = insight_engine.last_completed_iso_week_anchor(dt)
    a = anchor.isocalendar()
    expected = (dt - timedelta(days=7)).isocalendar()
    assert (a.year, a.week) == (expected.year, expected.week)

