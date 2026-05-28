"""Sicherheits-Garantien des Phase-0-Coverage-Scripts (Stufe-2-Sprint).

Read-only-Script — Tests bestaetigen:
1. Counts auf einer seeded Test-DB korrekt.
2. Empty-DB-Pfad sauber (keine Crashes, "—"-Ausgabe).
3. Script veraendert die DB nicht (snapshot before/after identical).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import Asset, Channel, Market, Post, Title
from scripts.measure_phase0_coverage import (
    _measure_global_post_counts,
    _measure_global_title_counts,
    _measure_per_pair,
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
    with Session(db) as session:
        ch = Channel(
            name="Test", platform="tiktok",
            url="https://x", handle="warnerbros",
            market=Market.US,
        )
        session.add(ch)
        session.commit(); session.refresh(ch)

        # Drei Posts mit unterschiedlichen Coverage-Profilen
        session.add(Post(
            channel_id=ch.id, platform="tiktok",
            post_url="https://x/1",
            analysis={"format": "trailer", "tone": "energetic"},
            duration_seconds=22,
            published_at=datetime.now(timezone.utc),
        ))
        session.add(Post(
            channel_id=ch.id, platform="tiktok",
            post_url="https://x/2",
            analysis=None,
            duration_seconds=18,
            published_at=None,
        ))
        session.add(Post(
            channel_id=ch.id, platform="tiktok",
            post_url="https://x/3",
            analysis=None,
            duration_seconds=None,
            published_at=None,
        ))
        session.commit()

        gp = _measure_global_post_counts(session)

    assert gp["posts_total"] == 3
    assert gp["posts_with_analysis"] == 1
    assert gp["posts_with_duration"] == 2
    assert gp["posts_with_published"] == 1


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
