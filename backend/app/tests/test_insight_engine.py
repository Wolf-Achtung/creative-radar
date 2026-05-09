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
    import json as _json

    fake_message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=_json.dumps(sample))],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=2000),
    )
    monkeypatch.setattr(insight_engine, "messages_create_text", lambda **k: fake_message)
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
    import json as _json

    fake_message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=_json.dumps(sample_old))],
        usage=SimpleNamespace(input_tokens=10, output_tokens=10),
    )
    monkeypatch.setattr(insight_engine, "messages_create_text", lambda **k: fake_message)
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
    anti-pattern block in a future refactor, this test catches it."""
    prompt = insight_engine.SYSTEM_PROMPT
    for forbidden in (
        "Brand-Storytelling",
        "Engagement-Drivers",
        "Hook-Architektur",
        "Live-Event-Framing",
        "Catalog-Nostalgie",
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
    sentences = [s for s in tldr.split(".") if s.strip()]
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


def test_voice_25_voice_identity_section_present():
    """Sprint-7-Voice-Identitäts-Sektion ist im Prompt — der
    Schnittraum-Kaffee-Anker ist die Tone-Quelle für Voice 2.5."""
    prompt = insight_engine.SYSTEM_PROMPT
    assert "VOICE-IDENTITÄT" in prompt
    # Schnittraum-Anker konkret referenziert (nicht nur Sektions-Header).
    assert "Schnittraum" in prompt
    assert "Kaffee" in prompt


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
