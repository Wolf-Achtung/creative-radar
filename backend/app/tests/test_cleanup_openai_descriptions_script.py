"""Sicherheits-Garantien des
``cleanup_openai_descriptions``-Scripts (Sprint 29.05.2026).

Analog #201-Pattern (``test_cleanup_script.py``), aber gegen OPENAI-
spezifischen WHERE-Filter:

1. Vorschau-Modus aendert nichts.
2. ``_apply`` (mit ``skip_confirmation=True``) trifft NUR Rows mit
   ``status=OPEN`` UND ``source=OPENAI``. RESOLVED, IGNORED, MATCHER,
   OCR, HASHTAG, TEXT, PERPLEXITY bleiben unangetastet.
3. ``_undo`` setzt NUR Rows zurueck, die zum Run-Timestamp UND zur
   ``source=OPENAI`` gehoeren — fremde IGNORED-Rows (z.B. aus #201
   mit source HASHTAG/TEXT) bleiben unangetastet.
4. Interaktive Bestaetigung blockt ohne ``--yes``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import (
    Asset,
    CandidateSource,
    CandidateStatus,
    Channel,
    Post,
    TitleCandidate,
)
from scripts.cleanup_openai_descriptions import (
    _apply,
    _print_preview,
    _undo,
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


def _seed_asset(session: Session) -> Asset:
    ch = Channel(
        name="Test", platform="instagram",
        url=f"https://example.com/{uuid4()}",
    )
    session.add(ch)
    session.commit(); session.refresh(ch)
    post = Post(channel_id=ch.id, post_url=f"https://x/{uuid4()}",
                caption="x")
    session.add(post)
    session.commit(); session.refresh(post)
    asset = Asset(post_id=post.id)
    session.add(asset)
    session.commit(); session.refresh(asset)
    return asset


def _seed_candidate(
    session: Session,
    *,
    source: CandidateSource,
    status: CandidateStatus = CandidateStatus.OPEN,
    confidence: float = 0.4,
    age_days: int = 30,
    suggested_title: str = "Der Post stellt X über",
) -> TitleCandidate:
    asset = _seed_asset(session)
    created = datetime.now(timezone.utc) - timedelta(days=age_days)
    c = TitleCandidate(
        asset_id=asset.id,
        suggested_title=suggested_title,
        source=source,
        confidence=confidence,
        status=status,
        created_at=created,
        updated_at=created,
    )
    session.add(c)
    session.commit(); session.refresh(c)
    return c


def _naive(ts: datetime | None) -> datetime | None:
    """SQLite vergisst TZ beim Read — Vergleiche immer auf naive Form."""
    if ts is None:
        return None
    return ts.replace(tzinfo=None)


def _snapshot(session: Session) -> list[tuple]:
    rows = session.exec(select(TitleCandidate)).all()
    return sorted([
        (str(c.id), c.status, c.source, c.confidence, c.updated_at)
        for c in rows
    ])


# ---- Vorschau aendert nichts -----------------------------------------


def test_preview_does_not_modify_anything(db):
    with Session(db) as session:
        _seed_candidate(session, source=CandidateSource.OPENAI)
        _seed_candidate(session, source=CandidateSource.OCR)
        before = _snapshot(session)

        n = _print_preview(session)

        after = _snapshot(session)
    assert before == after
    assert n == 1  # nur die OPENAI-Row


def test_preview_empty_db_zero(db, capsys):
    with Session(db) as session:
        n = _print_preview(session)
    assert n == 0
    out = capsys.readouterr().out
    assert "Erfasst vom Filter:      0" in out
    assert "nichts zu tun" in out


def test_preview_shows_confidence_and_age_buckets(db, capsys):
    """Erwartungs-Pattern: Confidence-Bucket 0.30-0.50 dominant
    (Briefing: alle 1.771 bei conf 0.4)."""
    with Session(db) as session:
        _seed_candidate(session, source=CandidateSource.OPENAI,
                        confidence=0.4, age_days=20)
        _seed_candidate(session, source=CandidateSource.OPENAI,
                        confidence=0.4, age_days=50)
        _seed_candidate(session, source=CandidateSource.OPENAI,
                        confidence=0.2, age_days=100)

        _print_preview(session)

    out = capsys.readouterr().out
    assert "Nach Confidence-Bucket" in out
    assert "0.30-0.50" in out and "2" in out
    assert "0.10-0.30" in out
    assert "Nach Alter" in out
    assert "14-29d" in out
    assert "30-59d" in out
    assert "90d+" in out


# ---- Apply trifft nur OPEN+OPENAI ------------------------------------


def test_apply_targets_only_open_openai_rows(db):
    """End-to-End-Exclusion-Stichprobe: nur die OPENAI+OPEN-Row darf
    nach Apply auf IGNORED stehen, alle anderen bleiben unveraendert."""
    with Session(db) as session:
        target = _seed_candidate(session, source=CandidateSource.OPENAI,
                                 status=CandidateStatus.OPEN)
        # Exclusion-Stichproben
        keep_resolved = _seed_candidate(
            session, source=CandidateSource.OPENAI,
            status=CandidateStatus.RESOLVED,
        )
        keep_already_ignored = _seed_candidate(
            session, source=CandidateSource.OPENAI,
            status=CandidateStatus.IGNORED,
        )
        keep_ocr = _seed_candidate(
            session, source=CandidateSource.OCR,
            status=CandidateStatus.OPEN,
        )
        keep_hashtag = _seed_candidate(
            session, source=CandidateSource.HASHTAG,
            status=CandidateStatus.OPEN,
        )
        keep_text = _seed_candidate(
            session, source=CandidateSource.TEXT,
            status=CandidateStatus.OPEN,
        )
        keep_perplexity = _seed_candidate(
            session, source=CandidateSource.PERPLEXITY,
            status=CandidateStatus.OPEN,
        )

        result = _apply(session, skip_confirmation=True)
        assert result is not None
        n_updated, run_ts = result
        assert n_updated == 1

        for tc in (
            keep_resolved, keep_already_ignored, keep_ocr,
            keep_hashtag, keep_text, keep_perplexity,
        ):
            session.refresh(tc)
        session.refresh(target)

    assert target.status == CandidateStatus.IGNORED
    # SQLite vergisst TZ beim Read — Vergleich auf naive Form normalisieren.
    assert _naive(target.updated_at) == _naive(run_ts)

    assert keep_resolved.status == CandidateStatus.RESOLVED
    # Already-ignored ist nicht geaendert worden — pre-existing IGNORED
    # darf NICHT den neuen Run-Timestamp tragen, sonst rollt --undo
    # die fremde Row mit zurueck.
    assert keep_already_ignored.status == CandidateStatus.IGNORED
    assert _naive(keep_already_ignored.updated_at) != _naive(run_ts)

    assert keep_ocr.status == CandidateStatus.OPEN
    assert keep_hashtag.status == CandidateStatus.OPEN
    assert keep_text.status == CandidateStatus.OPEN
    assert keep_perplexity.status == CandidateStatus.OPEN


def test_apply_zero_rows_returns_none(db):
    with Session(db) as session:
        _seed_candidate(session, source=CandidateSource.OCR)
        result = _apply(session, skip_confirmation=True)
    assert result is None


# ---- Apply: interaktive Bestaetigung --------------------------------


def test_apply_blocks_without_yes_when_user_declines(db):
    """Ohne ``--yes`` ruft Apply ``_confirm_interactively`` auf — wir
    mocken das auf ``False`` und erwarten Abbruch, KEINE Status-
    Aenderung."""
    with Session(db) as session:
        target = _seed_candidate(session, source=CandidateSource.OPENAI)
        with patch(
            "scripts.cleanup_openai_descriptions._confirm_interactively",
            return_value=False,
        ):
            result = _apply(session, skip_confirmation=False)
        session.refresh(target)
    assert result is None
    assert target.status == CandidateStatus.OPEN


def test_apply_proceeds_without_yes_when_user_confirms(db):
    with Session(db) as session:
        target = _seed_candidate(session, source=CandidateSource.OPENAI)
        with patch(
            "scripts.cleanup_openai_descriptions._confirm_interactively",
            return_value=True,
        ):
            result = _apply(session, skip_confirmation=False)
        session.refresh(target)
    assert result is not None
    n, _ts = result
    assert n == 1
    assert target.status == CandidateStatus.IGNORED


# ---- Undo: Run-Timestamp + Source-Doppel-Check -----------------------


def test_undo_restores_only_own_run(db):
    """Klassisches Round-Trip: Apply -> Undo -> Status = OPEN, nur fuer
    Rows mit dem eigenen Run-Timestamp."""
    with Session(db) as session:
        target = _seed_candidate(session, source=CandidateSource.OPENAI)
        result = _apply(session, skip_confirmation=True)
        assert result is not None
        n_apply, run_ts = result
        assert n_apply == 1
        session.refresh(target)
        assert target.status == CandidateStatus.IGNORED

        n_undo = _undo(session, run_ts)
        session.refresh(target)

    assert n_undo == 1
    assert target.status == CandidateStatus.OPEN


def test_undo_ignores_unrelated_ignored_rows(db):
    """Wichtigster Sicherheits-Test: eine fremde IGNORED-Row (z.B. aus
    #201 mit source=HASHTAG, anderer Timestamp) darf von --undo NICHT
    auf OPEN gesetzt werden."""
    with Session(db) as session:
        # Zielmenge fuer den eigenen Apply-Lauf
        own = _seed_candidate(session, source=CandidateSource.OPENAI)
        # Fremde IGNORED-Row, anderes Source (simuliert #201-Run)
        foreign_201 = _seed_candidate(
            session, source=CandidateSource.HASHTAG,
            status=CandidateStatus.IGNORED,
        )
        foreign_201_ts = foreign_201.updated_at

        result = _apply(session, skip_confirmation=True)
        assert result is not None
        n_apply, run_ts = result
        assert n_apply == 1

        # Undo des EIGENEN Laufs — fremde Rows nicht beruehrt.
        n_undo = _undo(session, run_ts)

        session.refresh(own)
        session.refresh(foreign_201)
    assert n_undo == 1
    assert own.status == CandidateStatus.OPEN
    assert foreign_201.status == CandidateStatus.IGNORED
    assert _naive(foreign_201.updated_at) == _naive(foreign_201_ts)


def test_undo_ignores_foreign_openai_ignored_with_different_timestamp(db):
    """Edge-Fall: eine OPENAI+IGNORED-Row mit anderem Timestamp (z.B.
    aus einem frueheren Apply-Lauf) bleibt unangetastet."""
    with Session(db) as session:
        old_run_ts = datetime.now(timezone.utc) - timedelta(days=2)
        # Manuelle Row, die wirkt wie ein frueherer Apply-Lauf
        asset = _seed_asset(session)
        old = TitleCandidate(
            asset_id=asset.id,
            suggested_title="alter Apply",
            source=CandidateSource.OPENAI,
            confidence=0.4,
            status=CandidateStatus.IGNORED,
            created_at=old_run_ts - timedelta(days=10),
            updated_at=old_run_ts,
        )
        session.add(old)
        # Neue OPEN-Row fuer den heutigen Apply-Lauf
        new_target = _seed_candidate(session, source=CandidateSource.OPENAI)
        session.commit()
        session.refresh(old)

        result = _apply(session, skip_confirmation=True)
        assert result is not None
        _n, run_ts = result

        n_undo = _undo(session, run_ts)

        session.refresh(old)
        session.refresh(new_target)
    assert n_undo == 1
    assert new_target.status == CandidateStatus.OPEN
    # Fremde Row bleibt IGNORED mit ihrem alten Timestamp.
    assert old.status == CandidateStatus.IGNORED
    assert _naive(old.updated_at) == _naive(old_run_ts)


def test_undo_zero_matches_for_unknown_timestamp(db):
    """Tippfehler-Timestamp -> 0 Matches, kein Crash."""
    with Session(db) as session:
        _seed_candidate(session, source=CandidateSource.OPENAI,
                        status=CandidateStatus.IGNORED)
        bogus_ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
        n_undo = _undo(session, bogus_ts)
    assert n_undo == 0
