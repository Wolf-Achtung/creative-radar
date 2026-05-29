"""Sprint 29.05.2026 — Stufe-2 PR-B / P1.

Tests fuer ``days_to_release_distribution`` in PairAggregation:
- Bucket-Grenzen exakt (halboffen, Briefing-Spec).
- Release-Week-Window symmetrisch (±3 Tage).
- Markt-Fallback (DE-Channel ohne DE-Date, mit US-Date).
- NULL-Pfade (kein Title, kein Release-Date, kein Asset).
- UK nutzt US-Date als Proxy.
- Doppel-Join Post→Asset→Title.
- Distribution-Counter pro Pair korrekt.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Asset, Channel, Market, Post, Title
from app.services.insight_engine import (
    DaysToReleaseBucket,
    _classify_days_to_release,
    _compute_days_to_release_distribution,
    _pick_release_date,
    PAIRS,
)


# ---- Reine Klassifikations-Tests (kein DB-Pfad) -----------------------


def test_classify_bucket_boundaries_pre():
    """Briefing: Tag 29 = `>4w_pre`, Tag 28 = `1-4w_pre`, Tag 27 =
    `1-4w_pre`. Halboffene Intervalle, scharf."""
    assert _classify_days_to_release(29) == DaysToReleaseBucket.PRE_FAR
    assert _classify_days_to_release(28) == DaysToReleaseBucket.PRE_NEAR
    assert _classify_days_to_release(27) == DaysToReleaseBucket.PRE_NEAR
    assert _classify_days_to_release(4) == DaysToReleaseBucket.PRE_NEAR
    assert _classify_days_to_release(3) == DaysToReleaseBucket.RELEASE_WEEK


def test_classify_bucket_release_week_symmetric():
    """Release-Week ist ±3 Tage symmetrisch um Release-Date."""
    assert _classify_days_to_release(3) == DaysToReleaseBucket.RELEASE_WEEK
    assert _classify_days_to_release(0) == DaysToReleaseBucket.RELEASE_WEEK
    assert _classify_days_to_release(-3) == DaysToReleaseBucket.RELEASE_WEEK
    assert _classify_days_to_release(-4) == DaysToReleaseBucket.POST_NEAR


def test_classify_bucket_boundaries_post():
    assert _classify_days_to_release(-4) == DaysToReleaseBucket.POST_NEAR
    assert _classify_days_to_release(-28) == DaysToReleaseBucket.POST_NEAR
    assert _classify_days_to_release(-29) == DaysToReleaseBucket.POST_FAR
    assert _classify_days_to_release(-365) == DaysToReleaseBucket.POST_FAR
    assert _classify_days_to_release(-366) == DaysToReleaseBucket.EVERGREEN


def test_classify_bucket_none_is_unknown():
    """``None`` als Eingabe (z.B. NULL-Release-Date) → UNKNOWN."""
    assert _classify_days_to_release(None) == DaysToReleaseBucket.UNKNOWN


# ---- Markt-Fallback -------------------------------------------------


def test_pick_release_date_de_primary_de_date():
    t = Title(
        title_original="X",
        release_date_de=date(2026, 6, 10),
        release_date_us=date(2026, 7, 10),
    )
    assert _pick_release_date(t, "DE") == date(2026, 6, 10)


def test_pick_release_date_de_fallback_us():
    """DE-Channel, kein DE-Date, US-Date vorhanden → US-Date."""
    t = Title(title_original="X",
              release_date_de=None,
              release_date_us=date(2026, 7, 10))
    assert _pick_release_date(t, "DE") == date(2026, 7, 10)


def test_pick_release_date_us_prefers_us():
    t = Title(title_original="X",
              release_date_de=date(2026, 6, 10),
              release_date_us=date(2026, 7, 10))
    assert _pick_release_date(t, "US") == date(2026, 7, 10)


def test_pick_release_date_uk_uses_us_as_proxy():
    """UK-Channel nutzt US-Datum als Proxy (Briefing-Vorgabe)."""
    t = Title(title_original="X",
              release_date_de=date(2026, 6, 10),
              release_date_us=date(2026, 7, 10))
    assert _pick_release_date(t, "UK") == date(2026, 7, 10)


def test_pick_release_date_both_null_returns_none():
    t = Title(title_original="X", release_date_de=None, release_date_us=None)
    assert _pick_release_date(t, "DE") is None
    assert _pick_release_date(t, "US") is None
    assert _pick_release_date(t, "UK") is None


# ---- End-to-End: aggregator-Distribution ----------------------------


def _shared_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def db():
    engine = _shared_engine()
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_warnerbros_us_channel(session: Session) -> Channel:
    ch = Channel(
        name="Warner Bros US", platform="tiktok",
        url="https://www.tiktok.com/@warnerbros", handle="warnerbros",
        market=Market.US,
    )
    session.add(ch); session.commit(); session.refresh(ch)
    return ch


def _seed_title(
    session: Session, *, release_us: date | None = None, release_de: date | None = None,
) -> Title:
    t = Title(
        title_original=f"Title-{uuid4()}",
        release_date_us=release_us,
        release_date_de=release_de,
    )
    session.add(t); session.commit(); session.refresh(t)
    return t


def _seed_post_with_title(
    session: Session, channel: Channel, title: Title | None,
    *, published_at: datetime, url_suffix: str = "",
) -> Post:
    p = Post(
        channel_id=channel.id, platform=channel.platform,
        post_url=f"https://x/{channel.handle}/{url_suffix or uuid4()}",
        caption="t",
        published_at=published_at,
        detected_at=published_at,
    )
    session.add(p); session.commit(); session.refresh(p)
    if title is not None:
        a = Asset(post_id=p.id, title_id=title.id, asset_type="UNKNOWN")
        session.add(a); session.commit()
    return p


def test_distribution_classifies_warnerbros_posts_into_buckets(db):
    """End-to-End: Posts mit verschiedenen days_to_release-Distanzen
    fallen in die richtigen Buckets."""
    with Session(db) as session:
        ch = _seed_warnerbros_us_channel(session)
        release = date(2026, 6, 15)  # Mortal Kombat II US
        title = _seed_title(session, release_us=release)

        # Post 60 Tage VOR Release → PRE_FAR
        _seed_post_with_title(session, ch, title,
                              published_at=datetime(2026, 4, 16, tzinfo=timezone.utc),
                              url_suffix="pre-far")
        # Post 14 Tage VOR Release → PRE_NEAR
        _seed_post_with_title(session, ch, title,
                              published_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                              url_suffix="pre-near")
        # Post 1 Tag VOR Release → RELEASE_WEEK
        _seed_post_with_title(session, ch, title,
                              published_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
                              url_suffix="rw")
        # Post 10 Tage NACH Release → POST_NEAR
        _seed_post_with_title(session, ch, title,
                              published_at=datetime(2026, 6, 25, tzinfo=timezone.utc),
                              url_suffix="post-near")

        pair_def = PAIRS["warnerbros"]
        # Window deckt alle Posts ab
        ws = datetime(2026, 1, 1, tzinfo=timezone.utc)
        we = datetime(2026, 12, 31, tzinfo=timezone.utc)
        dist = _compute_days_to_release_distribution(session, pair_def, ws, we)

    assert dist.get(DaysToReleaseBucket.PRE_FAR.value) == 1
    assert dist.get(DaysToReleaseBucket.PRE_NEAR.value) == 1
    assert dist.get(DaysToReleaseBucket.RELEASE_WEEK.value) == 1
    assert dist.get(DaysToReleaseBucket.POST_NEAR.value) == 1


def test_distribution_post_without_asset_is_unknown(db):
    """Post ohne Asset → UNKNOWN-Bucket."""
    with Session(db) as session:
        ch = _seed_warnerbros_us_channel(session)
        _seed_post_with_title(session, ch, None,
                              published_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
                              url_suffix="no-asset")
        pair_def = PAIRS["warnerbros"]
        ws = datetime(2026, 1, 1, tzinfo=timezone.utc)
        we = datetime(2026, 12, 31, tzinfo=timezone.utc)
        dist = _compute_days_to_release_distribution(session, pair_def, ws, we)
    assert dist.get(DaysToReleaseBucket.UNKNOWN.value) == 1


def test_distribution_asset_without_release_date_is_unknown(db):
    """Post hat Asset → Title, aber Title ohne Release-Date → UNKNOWN."""
    with Session(db) as session:
        ch = _seed_warnerbros_us_channel(session)
        title = _seed_title(session, release_us=None, release_de=None)
        _seed_post_with_title(session, ch, title,
                              published_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
                              url_suffix="no-release")
        pair_def = PAIRS["warnerbros"]
        ws = datetime(2026, 1, 1, tzinfo=timezone.utc)
        we = datetime(2026, 12, 31, tzinfo=timezone.utc)
        dist = _compute_days_to_release_distribution(session, pair_def, ws, we)
    assert dist.get(DaysToReleaseBucket.UNKNOWN.value) == 1


def test_distribution_de_channel_falls_back_to_us_release(db):
    """Markt-Fallback End-to-End: DE-Channel, kein DE-Date, US-Date
    vorhanden → Post wird gegen US-Date klassifiziert (nicht UNKNOWN)."""
    with Session(db) as session:
        # warnerbrosdeutschland ist DE-handle aus PAIRS
        ch = Channel(
            name="Warner Bros DE", platform="tiktok",
            url="https://www.tiktok.com/@warnerbrosdeutschland",
            handle="warnerbrosdeutschland", market=Market.DE,
        )
        session.add(ch); session.commit(); session.refresh(ch)
        # Kein DE-Date, aber US-Date 2026-06-15
        title = _seed_title(session, release_us=date(2026, 6, 15))
        # Post 1 Tag VOR US-Release → RELEASE_WEEK
        _seed_post_with_title(session, ch, title,
                              published_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
                              url_suffix="de-fallback")

        pair_def = PAIRS["warnerbros"]
        ws = datetime(2026, 1, 1, tzinfo=timezone.utc)
        we = datetime(2026, 12, 31, tzinfo=timezone.utc)
        dist = _compute_days_to_release_distribution(session, pair_def, ws, we)
    assert dist.get(DaysToReleaseBucket.RELEASE_WEEK.value) == 1


def test_distribution_aggregate_pair_writes_field(db):
    """``aggregate_pair`` muss das Distribution-Feld
    auf das PairAggregation-Objekt schreiben (vorher leeres
    Default-Dict, nach PR-B mit Werten)."""
    from app.services.insight_engine import aggregate_pair
    with Session(db) as session:
        ch = _seed_warnerbros_us_channel(session)
        title = _seed_title(session, release_us=date(2026, 6, 15))
        # ein PRE_NEAR-Post
        _seed_post_with_title(session, ch, title,
                              published_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                              url_suffix="agg")
        agg = aggregate_pair(
            session, "warnerbros", window_days=365,
            now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
    assert isinstance(agg.days_to_release_distribution, dict)
    assert agg.days_to_release_distribution.get(
        DaysToReleaseBucket.PRE_NEAR.value, 0
    ) == 1


def test_distribution_empty_when_no_posts(db):
    """Pair ohne Posts → leeres Dict (nicht None, nicht Crash)."""
    with Session(db) as session:
        pair_def = PAIRS["warnerbros"]
        ws = datetime(2026, 1, 1, tzinfo=timezone.utc)
        we = datetime(2026, 12, 31, tzinfo=timezone.utc)
        dist = _compute_days_to_release_distribution(session, pair_def, ws, we)
    assert dist == {}
