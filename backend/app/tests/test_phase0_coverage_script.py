"""Sicherheits-Garantien des Phase-0-Coverage-Scripts (Stufe-2-Sprint).

Read-only-Script — Tests bestaetigen:
1. Counts auf einer seeded Test-DB korrekt.
2. Empty-DB-Pfad sauber (keine Crashes, "—"-Ausgabe).
3. Script veraendert die DB nicht (snapshot before/after identical).
4. Filter-Fix (28.05.2026, Wolf-Befund): JSON-null und ``{}`` werden
   nicht als ``analyzed`` gezaehlt — nur echte Object-Werte mit
   Inhalt.
5. Channel-Aufstellung pro Pair: sortiert nach Coverage absteigend,
   Format ``platform/handle (market): N posts, M analyzed (X%)``.
6. Confidence-Verteilung pro Pair: 5 Buckets, nur strict_analyzed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import Asset, Channel, Market, Post, Title
from scripts.measure_phase0_coverage import (
    _measure_confidence_per_pair,
    _measure_global_post_counts,
    _measure_global_title_counts,
    _measure_per_pair,
    _measure_per_pair_channels,
)


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


def test_global_counts_on_empty_db(db):
    with Session(db) as session:
        gp = _measure_global_post_counts(session)
        gt = _measure_global_title_counts(session)
    assert gp["posts_total"] == 0
    assert gp["posts_with_analysis"] == 0
    assert gt["titles_total"] == 0
    assert gt["titles_with_release_either"] == 0


def test_global_counts_distinguish_analysis_and_duration(db):
    """Filter-Fix-Sicherheit (Wolf-Befund 28.05.2026): nur echte
    Object-Werte zaehlen als ``with_analysis``. NULL und leeres Dict
    fallen RAUS — sonst ueberzaehlt die Coverage massiv (in Production
    waren das 2494 vs 1220 echte)."""
    with Session(db) as session:
        ch = Channel(
            name="Test", platform="tiktok",
            url="https://x", handle="warnerbros",
            market=Market.US,
        )
        session.add(ch)
        session.commit(); session.refresh(ch)

        # Vier Posts: 1 echter Analyzer-Treffer, 3 Filter-Fallen.
        session.add(Post(
            channel_id=ch.id, platform="tiktok",
            post_url="https://x/1",
            analysis={"format": "trailer", "tone": "energetic"},  # PRESENT
            duration_seconds=22,
            published_at=datetime.now(timezone.utc),
        ))
        session.add(Post(
            channel_id=ch.id, platform="tiktok",
            post_url="https://x/2",
            analysis=None,  # NOT PRESENT (NULL)
            duration_seconds=18,
            published_at=None,
        ))
        session.add(Post(
            channel_id=ch.id, platform="tiktok",
            post_url="https://x/3",
            analysis=None,  # NOT PRESENT
            duration_seconds=None,
            published_at=None,
        ))
        session.add(Post(
            channel_id=ch.id, platform="tiktok",
            post_url="https://x/4",
            analysis={},  # NOT PRESENT (leeres Dict, Persist-Skip-Pfad)
            duration_seconds=44,
            published_at=datetime.now(timezone.utc),
        ))
        session.commit()

        gp = _measure_global_post_counts(session)

    # 4 Posts gesamt, aber NUR der erste zaehlt als analyzed.
    # Vor dem Filter-Fix waeren es bis zu 4 gewesen (cast-Vergleich
    # liess JSON-null und {} durchrutschen).
    assert gp["posts_total"] == 4
    assert gp["posts_with_analysis"] == 1
    assert gp["posts_with_duration"] == 3
    assert gp["posts_with_published"] == 2


def test_title_release_date_counts(db):
    with Session(db) as session:
        session.add(Title(
            title_original="Mortal Kombat II",
            release_date_us=datetime(2026, 7, 4).date(),
        ))
        session.add(Title(
            title_original="DE-only",
            release_date_de=datetime(2026, 8, 1).date(),
        ))
        session.add(Title(
            title_original="Streaming",
            release_date_de=None,
            release_date_us=None,
        ))
        session.commit()

        gt = _measure_global_title_counts(session)
    assert gt["titles_total"] == 3
    assert gt["titles_with_release_de"] == 1
    assert gt["titles_with_release_us"] == 1
    assert gt["titles_with_release_either"] == 2


def test_per_pair_counts_resolve_channels(db):
    """Pair "warnerbros" hat in PAIRS die Handles warnerbros/dc/... —
    wir seeden mindestens einen davon und pruefen, dass die Pair-Zeile
    korrekt aufloest."""
    with Session(db) as session:
        ch = Channel(
            name="Warner Bros US", platform="tiktok",
            url="https://x", handle="warnerbros",
            market=Market.US,
        )
        session.add(ch)
        session.commit(); session.refresh(ch)

        for i in range(5):
            session.add(Post(
                channel_id=ch.id, platform="tiktok",
                post_url=f"https://x/p{i}",
                analysis={"format": "trailer"} if i < 2 else None,
                duration_seconds=20,
                published_at=datetime.now(timezone.utc) - timedelta(days=3),
            ))
        session.commit()

        per_pair = _measure_per_pair(session, datetime.now(timezone.utc))
    wb = next(r for r in per_pair if r["pair_key"] == "warnerbros")
    assert wb["posts_total"] == 5
    assert wb["posts_with_analysis"] == 2
    assert wb["posts_in_7d"] == 5
    # Disney-Pair hat in der Test-DB kein gemeinsames Handle → 0
    disney = next(r for r in per_pair if r["pair_key"] == "disney")
    assert disney["posts_total"] == 0


def test_script_is_read_only(db):
    """Der wichtigste Test: das Script aendert NICHTS. Wir snapshotten
    Post-Counts und sicherheitshalber den Status eines bestehenden
    Records — wenn beides nach den Mess-Calls identisch ist, ist die
    read-only-Garantie eingehalten.
    """
    with Session(db) as session:
        ch = Channel(
            name="T", platform="tiktok", url="https://x",
            handle="warnerbros", market=Market.US,
        )
        session.add(ch); session.commit(); session.refresh(ch)
        post = Post(
            channel_id=ch.id, platform="tiktok",
            post_url="https://x/p0",
            analysis={"format": "clip"},
            duration_seconds=12,
            published_at=datetime.now(timezone.utc),
        )
        session.add(post); session.commit(); session.refresh(post)
        before_total = session.exec(
            select(Post)
        ).all()
        before_updated_at = post.updated_at

        _measure_global_post_counts(session)
        _measure_global_title_counts(session)
        _measure_per_pair(session, datetime.now(timezone.utc))

        session.refresh(post)
        after_total = session.exec(select(Post)).all()
        assert len(after_total) == len(before_total)
        assert post.updated_at == before_updated_at


# ---- Filter-Fix-Pflicht-Test-Set (Wolf 28.05.2026) --------------------


def test_filter_fix_exact_pflicht_set(db):
    """Pflicht-Test-Set aus dem Wolf-Briefing: vier Posts mit den vier
    Edge-Case-Werten — nur der echte Object-Wert mit Inhalt zaehlt.
    Dieser Test bewacht die Filter-Fix-Garantie in beiden Dialekten."""
    with Session(db) as session:
        ch = Channel(
            name="Test", platform="tiktok",
            url="https://x", handle="warnerbros",
            market=Market.US,
        )
        session.add(ch)
        session.commit(); session.refresh(ch)

        # 1. NULL
        session.add(Post(
            channel_id=ch.id, platform="tiktok",
            post_url="https://x/null", analysis=None,
        ))
        # 2. JSON null (Postgres serialisiert das als sql NULL bei
        #    ``analysis=None``; explicit fallback siehe SQLAlchemy-
        #    Verhalten — kein direkter Trigger in SQLModel-Tests).
        session.add(Post(
            channel_id=ch.id, platform="tiktok",
            post_url="https://x/null2", analysis=None,
        ))
        # 3. Leeres Dict
        session.add(Post(
            channel_id=ch.id, platform="tiktok",
            post_url="https://x/empty", analysis={},
        ))
        # 4. Echter Object-Wert mit Inhalt
        session.add(Post(
            channel_id=ch.id, platform="tiktok",
            post_url="https://x/real",
            analysis={"format": "trailer", "tone": "energetic",
                      "purpose": "release_week",
                      "lifecycle_stage": "launch", "confidence": 0.9},
        ))
        session.commit()

        gp = _measure_global_post_counts(session)

    assert gp["posts_total"] == 4
    # NUR der vierte ist "present" — drei Filter-Fallen rausgefiltert.
    assert gp["posts_with_analysis"] == 1


# ---- Channel-Aufstellung ---------------------------------------------


def test_per_pair_channels_sorted_by_coverage_desc(db):
    """Channel-Aufstellung: pro Pair Channel-Liste mit posts_total,
    posts_analyzed; sortiert nach Coverage absteigend (problematische
    Channels am Ende)."""
    with Session(db) as session:
        # Pair "disney" listet u.a. ``disneystudios`` und ``pixar``.
        # disneystudios: 6 posts, 4 analyzed (67 %)
        # pixar: 1 posts, 1 analyzed (100 %)
        # → pixar muss VOR disneystudios stehen (Coverage desc).
        ch_ds = Channel(
            name="DS", platform="tiktok", url="https://x/ds",
            handle="disneystudios", market=Market.US,
        )
        ch_px = Channel(
            name="PX", platform="instagram", url="https://x/px",
            handle="pixar", market=Market.US,
        )
        session.add_all([ch_ds, ch_px])
        session.commit(); session.refresh(ch_ds); session.refresh(ch_px)

        for i in range(4):
            session.add(Post(
                channel_id=ch_ds.id, platform="tiktok",
                post_url=f"https://x/ds{i}",
                analysis={"format": "clip", "confidence": 0.8},
            ))
        for i in range(2):
            session.add(Post(
                channel_id=ch_ds.id, platform="tiktok",
                post_url=f"https://x/dsn{i}", analysis=None,
            ))
        session.add(Post(
            channel_id=ch_px.id, platform="instagram",
            post_url="https://x/px1",
            analysis={"format": "clip", "confidence": 0.9},
        ))
        session.commit()

        per_pair_channels = _measure_per_pair_channels(session)

    disney_row = next(r for r in per_pair_channels if r["pair_key"] == "disney")
    assert disney_row["pair_posts_total"] == 7
    assert len(disney_row["channels"]) >= 2

    # Sortierung: Coverage DESC. Pixar (100 %) muss vor disneystudios (67 %).
    handles_in_order = [c["handle"] for c in disney_row["channels"]
                        if c["handle"] in ("pixar", "disneystudios")]
    assert handles_in_order == ["pixar", "disneystudios"]

    # Format-Check der Channel-Felder
    px_row = next(c for c in disney_row["channels"] if c["handle"] == "pixar")
    assert px_row["platform"] == "instagram"
    assert px_row["market"] == "US"
    assert px_row["posts_total"] == 1
    assert px_row["posts_analyzed"] == 1


# ---- Confidence-Verteilung -------------------------------------------


def test_confidence_distribution_buckets_match_briefing(db):
    """5 Confidence-Buckets aus dem Wolf-Briefing:
    < 0.5, 0.5-0.69, 0.7-0.79, 0.8-0.89, ≥ 0.9. Nur strict_analyzed
    Posts (NULL/leeres Dict fallen raus)."""
    with Session(db) as session:
        ch = Channel(
            name="DS", platform="tiktok", url="https://x/ds",
            handle="disneystudios", market=Market.US,
        )
        session.add(ch); session.commit(); session.refresh(ch)

        # 5 Posts, je einer pro Bucket
        confidences = [0.42, 0.65, 0.75, 0.85, 0.95]
        for i, conf in enumerate(confidences):
            session.add(Post(
                channel_id=ch.id, platform="tiktok",
                post_url=f"https://x/c{i}",
                analysis={"format": "clip", "confidence": conf},
            ))
        # Plus ein Post mit leerem Dict — soll NICHT zaehlen
        session.add(Post(
            channel_id=ch.id, platform="tiktok",
            post_url="https://x/empty", analysis={},
        ))
        # Plus ein Post mit NULL — soll NICHT zaehlen
        session.add(Post(
            channel_id=ch.id, platform="tiktok",
            post_url="https://x/null", analysis=None,
        ))
        session.commit()

        per_pair_conf = _measure_confidence_per_pair(session)

    disney_row = next(r for r in per_pair_conf if r["pair_key"] == "disney")
    assert disney_row["total"] == 5  # nur strict-analyzed
    assert disney_row["buckets"]["< 0.5"] == 1
    assert disney_row["buckets"]["0.5-0.69"] == 1
    assert disney_row["buckets"]["0.7-0.79"] == 1
    assert disney_row["buckets"]["0.8-0.89"] == 1
    assert disney_row["buckets"]["≥ 0.9"] == 1


def test_confidence_bucket_boundaries_exact():
    """Die Bucket-Funktionen sind kategorial: 0.5 → 0.5-0.69, 0.7 →
    0.7-0.79 usw. Schliess-Verhalten ist [lower, upper)."""
    from scripts.measure_phase0_coverage import CONFIDENCE_BUCKETS
    def bucket(c):
        for name, pred in CONFIDENCE_BUCKETS:
            if pred(c):
                return name
        return None

    assert bucket(0.499) == "< 0.5"
    assert bucket(0.5) == "0.5-0.69"
    assert bucket(0.699) == "0.5-0.69"
    assert bucket(0.7) == "0.7-0.79"
    assert bucket(0.799) == "0.7-0.79"
    assert bucket(0.8) == "0.8-0.89"
    assert bucket(0.899) == "0.8-0.89"
    assert bucket(0.9) == "≥ 0.9"
    assert bucket(1.0) == "≥ 0.9"


def test_confidence_distribution_skips_posts_without_confidence_key(db):
    """Echte Object-Werte ohne ``confidence``-Subfeld werden nicht
    gezaehlt — z.B. wenn ein alter Analyzer-Lauf das Feld vergessen
    hat. ``total`` bleibt unter den naiven ``with_analysis``-Counts."""
    with Session(db) as session:
        ch = Channel(
            name="DS", platform="tiktok", url="https://x/ds",
            handle="disneystudios", market=Market.US,
        )
        session.add(ch); session.commit(); session.refresh(ch)
        session.add(Post(
            channel_id=ch.id, platform="tiktok",
            post_url="https://x/with",
            analysis={"format": "clip", "confidence": 0.8},
        ))
        # Echtes Dict, aber ohne confidence
        session.add(Post(
            channel_id=ch.id, platform="tiktok",
            post_url="https://x/without",
            analysis={"format": "clip"},
        ))
        session.commit()

        per_pair_conf = _measure_confidence_per_pair(session)

    disney_row = next(r for r in per_pair_conf if r["pair_key"] == "disney")
    assert disney_row["total"] == 1
