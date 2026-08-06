"""Sicherheits-Garantien des Post-Analyzer-Backfill-Scripts.

Tests bestaetigen:

1. Dry-Run aendert nichts (read-only).
2. Apply skippt bereits analysierte Posts (idempotent).
3. Pair-Filter wirkt — andere Pairs werden nicht angefasst.
4. Pro-Pair-Counts korrekt.
5. Resume-Safety: ein Crash mid-Batch laesst bereits-fertige Posts
   intakt; das ist Konvention von ``analyze_post``, hier testen wir den
   Loop-Wrapper.
6. Auth-Error bricht ab, aber rollt nicht zurueck (bereits-fertig bleibt
   erhalten).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import Channel, Market, Post
from scripts.backfill_post_analyzer import (
    _apply_backfill,
    _channels_for_pair_keys,
    _count_unanalyzed_per_pair,
    _enabled_pair_handles,
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


def _seed_channel(session: Session, handle: str, *, platform: str = "tiktok") -> Channel:
    ch = Channel(
        name=f"ch-{handle}", platform=platform,
        url=f"https://x.com/{uuid4()}",
        handle=handle, market=Market.US,
    )
    session.add(ch); session.commit(); session.refresh(ch)
    return ch


def _seed_post(
    session: Session, channel: Channel, *,
    analyzed: bool = False, url_suffix: str = "",
) -> Post:
    p = Post(
        channel_id=channel.id, platform=channel.platform,
        post_url=f"https://x.com/{channel.handle}/{url_suffix or uuid4()}",
        caption="test",
        published_at=datetime.now(timezone.utc) - timedelta(days=1),
        detected_at=datetime.now(timezone.utc) - timedelta(days=1),
        last_analyzed_at=datetime.now(timezone.utc) if analyzed else None,
    )
    session.add(p); session.commit(); session.refresh(p)
    return p


# ---- Phase-0-like counts (Dry-Run) -----------------------------------


def test_dry_run_counts_unanalyzed_posts(db):
    with Session(db) as session:
        ch = _seed_channel(session, "warnerbros")
        _seed_post(session, ch, analyzed=False, url_suffix="a")
        _seed_post(session, ch, analyzed=False, url_suffix="b")
        _seed_post(session, ch, analyzed=True, url_suffix="c")

        channels = _channels_for_pair_keys(session, ["warnerbros"])
        counts = _count_unanalyzed_per_pair(session, channels)

    unanal, total = counts["warnerbros"]
    assert unanal == 2
    assert total == 3


def test_dry_run_does_not_modify(db):
    with Session(db) as session:
        ch = _seed_channel(session, "warnerbros")
        post = _seed_post(session, ch, analyzed=False, url_suffix="d")
        before_count = len(list(session.exec(select(Post)).all()))
        before_updated = post.updated_at

        channels = _channels_for_pair_keys(session, ["warnerbros"])
        _count_unanalyzed_per_pair(session, channels)

        session.refresh(post)
        after_count = len(list(session.exec(select(Post)).all()))

    assert after_count == before_count
    assert post.updated_at == before_updated


# ---- Apply (mit gemocktem analyze_post) -------------------------------


@pytest.fixture
def mock_analyzer(monkeypatch):
    """Tauscht ``_import_analyzer`` so aus, dass ``analyze_post`` einen
    minimalen Erfolgs-State produziert ohne echte API-Calls."""
    from app.services import post_analyzer as real_pa

    def fake_analyze(session, post, *, skip_vision=False):
        post.analysis = {"format": "clip", "tone": "energetic",
                         "purpose": "ongoing_promotion",
                         "lifecycle_stage": "post_launch", "confidence": 0.7}
        post.last_analyzed_at = datetime.now(timezone.utc)
        session.add(post)
        return SimpleNamespace(status="analyzed", post_id=post.id)

    class FakeAuthError(Exception):
        pass

    import scripts.backfill_post_analyzer as bf
    monkeypatch.setattr(
        bf, "_import_analyzer",
        lambda: (fake_analyze, FakeAuthError, lambda: True),
    )
    return fake_analyze


def test_apply_analyzes_unanalyzed_only(db, mock_analyzer):
    with Session(db) as session:
        ch = _seed_channel(session, "warnerbros")
        p1 = _seed_post(session, ch, analyzed=False, url_suffix="a1")
        p2 = _seed_post(session, ch, analyzed=False, url_suffix="a2")
        # Bereits analysiert — soll nicht erneut angefasst werden
        p3 = _seed_post(session, ch, analyzed=True, url_suffix="a3")
        before_last_analyzed_p3 = p3.last_analyzed_at

        channels = _channels_for_pair_keys(session, ["warnerbros"])
        stats = _apply_backfill(session, channels, skip_confirmation=True)

        session.refresh(p1); session.refresh(p2); session.refresh(p3)

    assert stats["warnerbros"]["analyzed"] == 2
    assert stats["warnerbros"]["errors"] == 0
    assert p1.last_analyzed_at is not None
    assert p2.last_analyzed_at is not None
    # p3 bleibt unangetastet
    assert p3.last_analyzed_at == before_last_analyzed_p3


def test_apply_is_idempotent_on_second_run(db, mock_analyzer):
    with Session(db) as session:
        ch = _seed_channel(session, "warnerbros")
        _seed_post(session, ch, analyzed=False, url_suffix="i1")

        channels = _channels_for_pair_keys(session, ["warnerbros"])
        stats1 = _apply_backfill(session, channels, skip_confirmation=True)
        assert stats1["warnerbros"]["analyzed"] == 1

        # Zweiter Run: nichts mehr zu tun
        stats2 = _apply_backfill(session, channels, skip_confirmation=True)
    # Zweiter Run gibt kein "warnerbros"-Pair zurueck, weil keine Posts
    # unanalysiert sind — frueh return.
    assert "warnerbros" not in stats2


def test_apply_pair_filter_does_not_touch_other_pairs(db, mock_analyzer):
    with Session(db) as session:
        ch_wb = _seed_channel(session, "warnerbros")
        ch_sony = _seed_channel(session, "sonypictures")
        post_wb = _seed_post(session, ch_wb, analyzed=False, url_suffix="wb1")
        post_sony = _seed_post(session, ch_sony, analyzed=False, url_suffix="sony1")

        # Filter: nur warnerbros
        channels = _channels_for_pair_keys(session, ["warnerbros"])
        stats = _apply_backfill(session, channels, skip_confirmation=True)

        session.refresh(post_wb); session.refresh(post_sony)

    assert stats["warnerbros"]["analyzed"] == 1
    assert post_wb.last_analyzed_at is not None
    # Sony darf NICHT angefasst worden sein
    assert post_sony.last_analyzed_at is None


def test_per_post_error_does_not_break_batch(db, monkeypatch):
    """Wenn ein einzelner Post crasht, wird er als Error gezaehlt und
    der Loop laeuft weiter."""
    call_state = {"count": 0}

    def flaky_analyze(session, post, *, skip_vision=False):
        call_state["count"] += 1
        if call_state["count"] == 2:
            raise RuntimeError("simulated per-post crash")
        post.analysis = {"format": "clip"}
        post.last_analyzed_at = datetime.now(timezone.utc)
        session.add(post)
        return SimpleNamespace(status="analyzed", post_id=post.id)

    class FakeAuthError(Exception):
        pass

    import scripts.backfill_post_analyzer as bf
    monkeypatch.setattr(
        bf, "_import_analyzer",
        lambda: (flaky_analyze, FakeAuthError, lambda: True),
    )

    with Session(db) as session:
        ch = _seed_channel(session, "warnerbros")
        _seed_post(session, ch, analyzed=False, url_suffix="b1")
        _seed_post(session, ch, analyzed=False, url_suffix="b2")
        _seed_post(session, ch, analyzed=False, url_suffix="b3")

        channels = _channels_for_pair_keys(session, ["warnerbros"])
        stats = _apply_backfill(session, channels, skip_confirmation=True)

    assert stats["warnerbros"]["analyzed"] == 2
    assert stats["warnerbros"]["errors"] == 1


def test_apply_auth_error_aborts_but_keeps_done_work(db, monkeypatch):
    """Resume-Safety: nach AuthError ist genau der erste verarbeitete
    Post persistiert (commit ist durch), die folgenden sind unangetastet.
    Wir verifizieren das in einer FRISCHEN Session, damit der ORM-
    Identity-Map-State der ersten Session den Befund nicht maskiert."""
    call_state = {"count": 0}

    class FakeAuthError(Exception):
        pass

    def flaky_analyze(session, post, *, skip_vision=False):
        call_state["count"] += 1
        if call_state["count"] == 2:
            raise FakeAuthError("ANTHROPIC_API_KEY invalid")
        post.analysis = {"format": "clip"}
        post.last_analyzed_at = datetime.now(timezone.utc)
        session.add(post)
        return SimpleNamespace(status="analyzed", post_id=post.id)

    import scripts.backfill_post_analyzer as bf
    monkeypatch.setattr(
        bf, "_import_analyzer",
        lambda: (flaky_analyze, FakeAuthError, lambda: True),
    )

    with Session(db) as session:
        ch = _seed_channel(session, "warnerbros")
        # Drei Posts mit gestaffeltem detected_at, damit die DESC-
        # Order-by-Loop-Reihenfolge deterministisch ist.
        now = datetime.now(timezone.utc)
        for i in range(3):
            p = Post(
                channel_id=ch.id, platform=ch.platform,
                post_url=f"https://x.com/{ch.handle}/auth-err-{i}",
                caption="test",
                published_at=now - timedelta(hours=i),
                detected_at=now - timedelta(hours=i),
            )
            session.add(p)
        session.commit()

        channels = _channels_for_pair_keys(session, ["warnerbros"])
        stats = _apply_backfill(session, channels, skip_confirmation=True)

    # AuthError war beim ZWEITEN Call (count==2). Heisst: erster Post
    # ist erfolgreich + commited; zweiter hat Error → frueh return.
    # Dritter Post nicht angefasst.
    assert stats["warnerbros"]["analyzed"] == 1

    # In FRISCHER Session pruefen, was die DB persistiert hat.
    with Session(db) as fresh:
        analyzed_count = len(list(fresh.exec(
            select(Post).where(Post.last_analyzed_at.is_not(None))
        ).all()))
    # Genau ein Post hat last_analyzed_at — der erste, vor dem Auth-Crash.
    assert analyzed_count == 1


# ---- Apply without Anthropic-Key configured --------------------------


def test_apply_aborts_when_anthropic_unconfigured(db, monkeypatch):
    """Wenn ``is_anthropic_configured()`` False ist, wird der Backfill
    nicht gestartet — wir wollen kein halbes Ergebnis."""
    def fake_analyze(session, post, *, skip_vision=False):  # darf nie aufgerufen werden
        raise AssertionError("analyze_post should not be called")

    class FakeAuthError(Exception):
        pass

    import scripts.backfill_post_analyzer as bf
    monkeypatch.setattr(
        bf, "_import_analyzer",
        lambda: (fake_analyze, FakeAuthError, lambda: False),
    )

    with Session(db) as session:
        ch = _seed_channel(session, "warnerbros")
        _seed_post(session, ch, analyzed=False, url_suffix="uc1")

        channels = _channels_for_pair_keys(session, ["warnerbros"])
        stats = _apply_backfill(session, channels, skip_confirmation=True)

    # Kein Pair-Stat-Eintrag, weil frueh return
    assert stats == {}


# ---- Channel-Resolution ----------------------------------------------


def test_channels_for_pair_keys_resolves_warnerbros(db):
    """Pair "warnerbros" listet u.a. den Handle "warnerbros" — seeden
    wir genau diesen, sollte _channels_for_pair_keys eine non-leere
    Liste liefern. Sonst weichen wir vom Aggregator-Pattern ab."""
    with Session(db) as session:
        ch = _seed_channel(session, "warnerbros")
        result = _channels_for_pair_keys(session, ["warnerbros"])

    assert "warnerbros" in result
    assert ch.id in result["warnerbros"]


def test_enabled_pair_handles_contains_known_pairs():
    """Sanity-Check, dass die PAIRS-Konstante noch dieselben Pair-Keys
    enthaelt wie in der Phase-0-Coverage. Wenn dieser Test bricht,
    hat jemand Pairs deaktiviert oder umbenannt — Test muss dann
    angepasst werden."""
    handles = _enabled_pair_handles()
    assert "warnerbros" in handles
    assert "disney" in handles
