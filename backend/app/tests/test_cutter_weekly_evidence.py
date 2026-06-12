"""Tests für die deterministische Evidenz-Prüfung des Cutter-Wochenbriefings
(Commit A des Master-Plan-Sprints 2026-06-12).

Abgedeckt (Pflicht-Fälle aus der Freigabe):
1. Muster über 3 Filme → ``pattern_released``.
2. Alles auf 1 Film konzentriert → ``no_pattern`` (Distinct-Schwelle).
3. < 5 Posts über p75 → ``no_pattern`` (Posts-Schwelle).
4. Plattform mit zu wenig p75-Daten → ``no_threshold`` (ehrlicher Leerlauf).

Zusätzlich: Wochen-Filter (published_at außerhalb der ISO-Woche fliegt
raus), p75-Berechnung gegen bekannte Verteilung, Sentinel-Guard
(likes=-1), Dedup über post_url, title_key_share-Messung und
unlesbare Blobs → ``sources.unreadable_rows`` statt Crash.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import (
    Channel,
    ChannelSegment,
    InsightReport as InsightReportRow,
    Market,
    Post,
    SegmentRoundup as SegmentRoundupRow,
)
from app.schemas.insights import (
    ChannelRoundupStats,
    ChannelStats,
    CutterEvidencePost,
    PairAggregation,
    PlatformAggregation,
    RankedPost,
    SegmentAggregation,
    TitleCoverage,
)
from app.services import cutter_weekly


# Fester Wochen-Anker: Mittwoch der ISO-Woche 23/2026. Alle Zeit-Fixtures
# rechnen relativ dazu, damit der Test nicht an der realen Uhr hängt.
ANCHOR = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
ISO_YEAR, ISO_WEEK = ANCHOR.isocalendar().year, ANCHOR.isocalendar().week
WEEK_START, WEEK_END = cutter_weekly.week_bounds(ISO_YEAR, ISO_WEEK)
IN_WEEK = WEEK_START + timedelta(days=1, hours=10)


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_cutter_weekly_", suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}
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


# ---------------------------------------------------------------------------
# Fixture-Helper
# ---------------------------------------------------------------------------


def _ranked_post(
    url: str,
    *,
    views: int = 1000,
    likes: int = 100,
    comments: int = 10,
    published_at: Optional[datetime] = IN_WEEK,
    title: Optional[str] = None,
    platform: str = "instagram",
) -> RankedPost:
    return RankedPost(
        post_url=url,
        caption_excerpt="x",
        platform=platform,
        published_at=published_at,
        views=views,
        likes=likes,
        comments=comments,
        engagement_sum=likes + comments,
        title_original=title,
    )


def _channel_stats(ranked_posts: list[RankedPost], *, market: str = "DE") -> ChannelStats:
    return ChannelStats(
        handle="testhandle",
        market=market,
        channel_id=None,
        channel_found=True,
        posts_count=len(ranked_posts),
        assets_count=0,
        coverage_pct=0.0,
        top_hashtags=[],
        avg_caption_length=0.0,
        avg_duration_seconds=None,
        duration_buckets={},
        top_posts=[],
        avg_engagement=0.0,
        ranked_posts=ranked_posts,
    )


def _title_coverage() -> TitleCoverage:
    return TitleCoverage(
        titles_in_both_markets=[],
        de_only_titles=[],
        us_only_titles=[],
        de_assets_with_title=0,
        de_assets_total=0,
        us_assets_with_title=0,
        us_assets_total=0,
        overall_coverage_pct=0.0,
    )


def _pair_blob(pair_key: str, per_platform: list[PlatformAggregation]) -> dict:
    agg = PairAggregation(
        pair_key=pair_key,
        pair_label=pair_key.title(),
        platform=per_platform[0].platform if per_platform else "tiktok",
        window_days=30,
        window_start=ANCHOR - timedelta(days=30),
        window_end=ANCHOR,
        iso_week=ISO_WEEK,
        iso_year=ISO_YEAR,
        de_channel=None,
        us_channel=None,
        cross_market_matches=[],
        title_coverage=_title_coverage(),
        notes=[],
        per_platform=per_platform,
    )
    return agg.model_dump(mode="json")


def _platform_agg(platform: str, ranked_posts: list[RankedPost]) -> PlatformAggregation:
    return PlatformAggregation(
        platform=platform,
        de_channel=_channel_stats(ranked_posts),
        title_coverage=_title_coverage(),
    )


def _persist_pair_row(session: Session, pair_key: str, per_platform: list[PlatformAggregation]) -> None:
    session.add(
        InsightReportRow(
            pair_key=pair_key,
            iso_year=ISO_YEAR,
            iso_week=ISO_WEEK,
            aggregation=_pair_blob(pair_key, per_platform),
            llm_output={"headline": "x"},
            model="test",
        )
    )
    session.commit()


def _roundup_blob(segment: str, channels: list[ChannelRoundupStats]) -> dict:
    agg = SegmentAggregation(
        segment=segment,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        window_days=14,
        window_start=ANCHOR - timedelta(days=14),
        window_end=ANCHOR,
        channels_evaluated=len(channels),
        channels_with_posts=len(channels),
        total_posts=sum(c.posts_count for c in channels),
        channels=channels,
    )
    return agg.model_dump(mode="json")


def _roundup_channel(
    handle: str, platform: str, top_posts: list[RankedPost]
) -> ChannelRoundupStats:
    return ChannelRoundupStats(
        channel_id=None,
        handle=handle,
        platform=platform,
        market="US",
        posts_count=len(top_posts),
        avg_engagement=0.0,
        avg_caption_length=0.0,
        avg_duration_seconds=None,
        top_hashtags=[],
        top_posts=top_posts,
    )


def _persist_roundup_row(
    session: Session, segment: ChannelSegment, channels: list[ChannelRoundupStats]
) -> None:
    session.add(
        SegmentRoundupRow(
            segment=segment,
            iso_year=ISO_YEAR,
            iso_week=ISO_WEEK,
            window_days=14,
            channels_aggregation=_roundup_blob(segment.value, channels),
            llm_output={"headline": "x"},
            model="test",
        )
    )
    session.commit()


def _seed_p75_population(
    session: Session,
    platform: str,
    ers: list[float],
    *,
    views: int = 1000,
) -> None:
    """N Posts mit kontrollierter ER im Rollfenster (1 Woche vor Anker) —
    Grundgesamtheit für ``compute_platform_p75``. ER wird über die
    Likes-Zahl bei festen Views eingestellt (comments=0)."""
    channel = Channel(
        name=f"p75-{platform}",
        platform=platform,
        url=f"https://example.com/{platform}",
        handle=f"p75{platform}",
        market=Market.DE,
    )
    session.add(channel)
    session.commit()
    for i, er in enumerate(ers):
        session.add(
            Post(
                channel_id=channel.id,
                platform=platform,
                post_url=f"https://example.com/{platform}/p75/{uuid4()}",
                published_at=WEEK_START - timedelta(days=3, minutes=i),
                visible_views=views,
                visible_likes=int(round(er * views)),
                visible_comments=0,
            )
        )
    session.commit()


# ---------------------------------------------------------------------------
# Einheit: Perzentil + post_er
# ---------------------------------------------------------------------------


def test_percentile_linear_interpolation():
    values = sorted([0.1, 0.2, 0.3, 0.4, 0.5])
    # rank = 0.75 * 4 = 3.0 → exakt values[3]
    assert cutter_weekly._percentile(values, 0.75) == pytest.approx(0.4)
    values = sorted([0.0, 1.0])
    # rank = 0.75 → 0.0 + 0.75 * 1.0
    assert cutter_weekly._percentile(values, 0.75) == pytest.approx(0.75)


def test_post_er_sentinel_guard_and_zero_views():
    # Apify-Sentinel likes=-1 darf die ER nicht negativ drücken.
    assert cutter_weekly.post_er(1000, -1, 50) == pytest.approx(0.05)
    assert cutter_weekly.post_er(0, 100, 10) is None
    assert cutter_weekly.post_er(None, 100, 10) is None


# ---------------------------------------------------------------------------
# Einheit: check_platform_pattern — die vier Pflicht-Fälle
# ---------------------------------------------------------------------------


def _candidate(url: str, er: float, key: str) -> CutterEvidencePost:
    views = 1000
    return CutterEvidencePost(
        post_url=url,
        platform="instagram",
        er=er,
        views=views,
        likes=int(er * views),
        comments=0,
        engagement_sum=int(er * views),
        distinct_key=key,
        source="pair:disney",
    )


THRESHOLD = {"p75": 0.10, "sample_size": 50}


def test_pattern_released_with_three_distinct_keys():
    candidates = [
        _candidate(f"u{i}", 0.12 + i * 0.01, key)
        for i, key in enumerate(["Film A", "Film A", "Film B", "Film C", "Film C"])
    ]
    result = cutter_weekly.check_platform_pattern(
        "instagram", candidates, THRESHOLD, min_posts=5, min_distinct=3
    )
    assert result.status == "pattern_released"
    assert result.candidates_above_p75 == 5
    assert sorted(result.distinct_keys) == ["Film A", "Film B", "Film C"]
    assert len(result.supporting_posts) == 5
    # Sortierung ER absteigend — der stärkste Beleg zuerst.
    assert result.supporting_posts[0].er >= result.supporting_posts[-1].er


def test_pattern_rejected_when_single_film():
    candidates = [_candidate(f"u{i}", 0.15, "Film A") for i in range(6)]
    result = cutter_weekly.check_platform_pattern(
        "instagram", candidates, THRESHOLD, min_posts=5, min_distinct=3
    )
    assert result.status == "no_pattern"
    assert "1 Distinct-Key" in result.reason
    # Verworfene Kandidaten bleiben für die Kalibrierung sichtbar.
    assert len(result.supporting_posts) == 6


def test_pattern_rejected_when_fewer_than_min_posts():
    candidates = [
        _candidate(f"u{i}", 0.15, key)
        for i, key in enumerate(["Film A", "Film B", "Film C", "Film D"])
    ]
    result = cutter_weekly.check_platform_pattern(
        "instagram", candidates, THRESHOLD, min_posts=5, min_distinct=3
    )
    assert result.status == "no_pattern"
    assert result.candidates_above_p75 == 4


def test_no_threshold_yields_honest_idle():
    candidates = [_candidate("u1", 0.9, "Film A")]
    result = cutter_weekly.check_platform_pattern(
        "instagram", candidates, {"p75": None, "sample_size": 7},
        min_posts=5, min_distinct=3,
    )
    assert result.status == "no_threshold"
    assert "p75 nicht definiert" in result.reason
    assert result.supporting_posts == []


def test_below_threshold_posts_are_not_candidates():
    # 5 Posts, 3 Keys — aber alle UNTER p75: kein Muster.
    candidates = [
        _candidate(f"u{i}", 0.05, key)
        for i, key in enumerate(["A", "A", "B", "C", "C"])
    ]
    result = cutter_weekly.check_platform_pattern(
        "instagram", candidates, THRESHOLD, min_posts=5, min_distinct=3
    )
    assert result.status == "no_pattern"
    assert result.candidates_above_p75 == 0


# ---------------------------------------------------------------------------
# Integration: p75 aus der Post-Tabelle
# ---------------------------------------------------------------------------


def test_compute_platform_p75_known_distribution(db):
    with Session(db) as session:
        # 40 Posts mit ER 0.01..0.40 → p75 bei rank 29.25 → 0.3025.
        _seed_p75_population(
            session, "instagram", [i / 100 for i in range(1, 41)]
        )
        thresholds = cutter_weekly.compute_platform_p75(
            session,
            iso_year=ISO_YEAR,
            iso_week=ISO_WEEK,
            window_weeks=8,
            min_sample=10,
        )
    ig = thresholds["instagram"]
    assert ig["sample_size"] == 40
    assert ig["p75"] == pytest.approx(0.3025, abs=1e-4)
    # TikTok/YouTube ohne Posts → Schwelle undefiniert.
    assert thresholds["tiktok"] == {"p75": None, "sample_size": 0}
    assert thresholds["youtube"] == {"p75": None, "sample_size": 0}


def test_compute_platform_p75_excludes_posts_outside_window(db):
    with Session(db) as session:
        _seed_p75_population(session, "tiktok", [0.10] * 12)
        # 20 Posts weit vor dem Rollfenster — dürfen nicht zählen.
        channel = Channel(
            name="old", platform="tiktok", url="https://example.com/old",
            handle="old", market=Market.DE,
        )
        session.add(channel)
        session.commit()
        for i in range(20):
            session.add(
                Post(
                    channel_id=channel.id,
                    platform="tiktok",
                    post_url=f"https://example.com/old/{i}",
                    published_at=WEEK_START - timedelta(weeks=30, minutes=i),
                    visible_views=1000,
                    visible_likes=900,
                )
            )
        session.commit()
        thresholds = cutter_weekly.compute_platform_p75(
            session,
            iso_year=ISO_YEAR,
            iso_week=ISO_WEEK,
            window_weeks=8,
            min_sample=10,
        )
    assert thresholds["tiktok"]["sample_size"] == 12
    assert thresholds["tiktok"]["p75"] == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# Integration: Reader (Blobs → Kandidaten)
# ---------------------------------------------------------------------------


def test_collect_week_posts_filters_to_iso_week_and_dedups(db):
    with Session(db) as session:
        in_week = _ranked_post("https://t.example/p1", title="Film A")
        before_week = _ranked_post(
            "https://t.example/p2",
            published_at=WEEK_START - timedelta(hours=1),
        )
        no_published_at = _ranked_post("https://t.example/p3", published_at=None)
        zero_views = _ranked_post("https://t.example/p4", views=0)
        _persist_pair_row(
            session, "disney",
            [_platform_agg("tiktok", [in_week, before_week, no_published_at, zero_views])],
        )
        # Derselbe Post nochmal im Roundup-Blob → Dedup über post_url.
        _persist_roundup_row(
            session, ChannelSegment.US_MAJOR,
            [_roundup_channel("a24", "tiktok", [in_week])],
        )

        by_platform, sources = cutter_weekly.collect_week_posts(
            session, ISO_YEAR, ISO_WEEK
        )

    assert sources.pair_briefs == ["disney"]
    assert sources.segment_roundups == ["us_major"]
    assert sources.unreadable_rows == []
    urls = [c.post_url for c in by_platform["tiktok"]]
    assert urls == ["https://t.example/p1"]
    assert by_platform["instagram"] == []
    # Distinct-Key: title_original gewinnt über den Pair-Fallback.
    assert by_platform["tiktok"][0].distinct_key == "Film A"
    assert by_platform["tiktok"][0].source == "pair:disney"


def test_collect_week_posts_fallback_keys_and_sources(db):
    with Session(db) as session:
        _persist_pair_row(
            session, "warner",
            [_platform_agg("youtube", [_ranked_post("https://y.example/p1")])],
        )
        _persist_roundup_row(
            session, ChannelSegment.DE_VERLEIH,
            [_roundup_channel("leonine", "youtube", [_ranked_post("https://y.example/p2")])],
        )
        by_platform, _sources = cutter_weekly.collect_week_posts(
            session, ISO_YEAR, ISO_WEEK
        )
    keys = sorted(c.distinct_key for c in by_platform["youtube"])
    assert keys == ["pair:warner", "segment:de_verleih:leonine"]


def test_collect_week_posts_skips_unreadable_blob(db):
    with Session(db) as session:
        session.add(
            InsightReportRow(
                pair_key="broken",
                iso_year=ISO_YEAR,
                iso_week=ISO_WEEK,
                aggregation={"not": "a pair aggregation"},
                llm_output={"headline": "x"},
                model="test",
            )
        )
        session.commit()
        _persist_pair_row(
            session, "disney",
            [_platform_agg("tiktok", [_ranked_post("https://t.example/ok")])],
        )
        by_platform, sources = cutter_weekly.collect_week_posts(
            session, ISO_YEAR, ISO_WEEK
        )
    assert sources.unreadable_rows == ["pair:broken"]
    assert sources.pair_briefs == ["disney"]
    assert len(by_platform["tiktok"]) == 1


# ---------------------------------------------------------------------------
# Integration: build_weekly_evidence End-to-End
# ---------------------------------------------------------------------------


def test_build_weekly_evidence_end_to_end(db):
    with Session(db) as session:
        # p75-Population Instagram: 20 Posts ER 0.01..0.20 → p75 ≈ 0.1525.
        _seed_p75_population(
            session, "instagram", [i / 100 for i in range(1, 21)]
        )
        # 5 Woche-Posts über p75 aus 3 Filmen → Muster freigegeben.
        ig_posts = [
            _ranked_post(
                f"https://ig.example/{i}",
                likes=300,  # ER 0.3 > p75
                title=title,
                platform="instagram",
            )
            for i, title in enumerate(
                ["Film A", "Film A", "Film B", "Film C", "Film C"]
            )
        ]
        _persist_pair_row(session, "disney", [_platform_agg("instagram", ig_posts)])

        evidence = cutter_weekly.build_weekly_evidence(
            session,
            now=ANCHOR,
            p75_min_sample=10,
        )

    assert evidence.iso_year == ISO_YEAR and evidence.iso_week == ISO_WEEK
    by_platform = {p.platform: p for p in evidence.platforms}
    assert set(by_platform) == {"instagram", "tiktok", "youtube"}

    ig = by_platform["instagram"]
    assert ig.status == "pattern_released"
    assert ig.candidates_above_p75 == 5
    assert len(ig.distinct_keys) == 3

    # Keine Daten auf TikTok/YouTube → ehrlicher Leerlauf (no_threshold).
    assert by_platform["tiktok"].status == "no_threshold"
    assert by_platform["youtube"].status == "no_threshold"

    # Kalibrier-Messung: alle 5 Kandidaten trugen einen echten Titel.
    assert evidence.title_key_share == pytest.approx(1.0)
    assert evidence.week_posts_total == 5
    # Parameter-Stempel für den Evidence-Blob.
    assert evidence.params.min_posts == 5
    assert evidence.params.p75_min_sample == 10


def test_build_weekly_evidence_empty_week(db):
    with Session(db) as session:
        evidence = cutter_weekly.build_weekly_evidence(session, now=ANCHOR)
    assert evidence.week_posts_total == 0
    assert evidence.title_key_share is None
    assert all(p.status == "no_threshold" for p in evidence.platforms)
    assert evidence.sources.pair_briefs == []
