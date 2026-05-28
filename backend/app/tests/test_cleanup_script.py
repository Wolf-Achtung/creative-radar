"""Sprint 28.05.2026 — Sicherheits-Garantien des Backlog-Aufraeumer-Scripts.

Tests laufen gegen eine Test-DB (StaticPool sqlite). Geprueft werden:

1. Vorschau-Modus aendert nichts.
2. ``_apply`` (mit ``skip_confirmation=True``) trifft NUR Rows, die alle
   vier WHERE-Bedingungen erfuellen — keine OCR-Source, keine
   Confidence >= 0.40, keine jungen Rows, keine bereits IGNORED-Rows.
3. ``_undo`` setzt NUR Rows zurueck, die zum gleichen Run-Timestamp
   gehoeren — nicht eine zufaellig parallel manuell auf IGNORED
   gesetzte Row.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
from scripts.cleanup_open_title_candidates import _apply, _print_preview, _undo


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
    post = Post(channel_id=ch.id, post_url=f"https://x/{uuid4()}", caption="x")
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
    confidence: float,
    status: CandidateStatus = CandidateStatus.OPEN,
    age_days: int = 30,
) -> TitleCandidate:
    asset = _seed_asset(session)
    created = datetime.now(timezone.utc) - timedelta(days=age_days)
    c = TitleCandidate(
        asset_id=asset.id,
        suggested_title="Whatever",
        source=source,
        confidence=confidence,
        status=status,
        created_at=created,
        updated_at=created,
    )
    session.add(c)
    session.commit(); session.refresh(c)
    return c


# ---- Vorschau aendert nichts -----------------------------------------


def test_preview_does_not_modify_anything(db):
    with Session(db) as session:
        c = _seed_candidate(
            session,
            source=CandidateSource.HASHTAG, confidence=0.30, age_days=30,
        )
        before_status = c.status
        before_updated = c.updated_at

        _print_preview(session)

        session.refresh(c)
        assert c.status == before_status
        assert c.updated_at == before_updated


# ---- Apply trifft nur die richtigen Rows -----------------------------


def test_apply_targets_only_eligible_rows(db):
    """Sechs Stichproben — eine pro Ausschluss-Bedingung — werden NICHT
    angefasst, plus eine eligible Row die WIRD angefasst."""
    with Session(db) as session:
        eligible = _seed_candidate(
            session,
            source=CandidateSource.HASHTAG, confidence=0.30, age_days=30,
        )
        not_eligible = [
            # 1. Falsche Source (OCR statt hashtag/text)
            _seed_candidate(
                session,
                source=CandidateSource.OCR, confidence=0.30, age_days=30,
            ),
            # 2. Falsche Source (OpenAI)
            _seed_candidate(
                session,
                source=CandidateSource.OPENAI, confidence=0.30, age_days=30,
            ),
            # 3. Confidence >= 0.40
            _seed_candidate(
                session,
                source=CandidateSource.HASHTAG, confidence=0.45, age_days=30,
            ),
            # 4. Junge Row (< 14 Tage)
            _seed_candidate(
                session,
                source=CandidateSource.HASHTAG, confidence=0.30, age_days=5,
            ),
            # 5. Bereits RESOLVED
            _seed_candidate(
                session,
                source=CandidateSource.HASHTAG, confidence=0.30, age_days=30,
                status=CandidateStatus.RESOLVED,
            ),
            # 6. Bereits IGNORED
            _seed_candidate(
                session,
                source=CandidateSource.HASHTAG, confidence=0.30, age_days=30,
                status=CandidateStatus.IGNORED,
            ),
        ]

        result = _apply(session, skip_confirmation=True)
        assert result is not None
        n_updated, run_ts = result
        assert n_updated == 1

        session.refresh(eligible)
        assert eligible.status == CandidateStatus.IGNORED
        # SQLite-Storage strippt tz; gegen naive UTC vergleichen.
        run_ts_naive = run_ts.replace(tzinfo=None) if run_ts.tzinfo else run_ts
        assert eligible.updated_at == run_ts_naive

        # Alle nicht-eligible Rows unangetastet (Status wie geseedet).
        expected_statuses = [
            CandidateStatus.OPEN, CandidateStatus.OPEN, CandidateStatus.OPEN,
            CandidateStatus.OPEN, CandidateStatus.RESOLVED, CandidateStatus.IGNORED,
        ]
        for c, expected in zip(not_eligible, expected_statuses):
            session.refresh(c)
            assert c.status == expected


# ---- Apply schreibt nichts, wenn nichts erfasst wird -----------------


def test_apply_returns_none_when_nothing_eligible(db):
    with Session(db) as session:
        # Nur eine nicht-eligible Row (Confidence zu hoch)
        c = _seed_candidate(
            session,
            source=CandidateSource.HASHTAG, confidence=0.50, age_days=30,
        )
        result = _apply(session, skip_confirmation=True)
        assert result is None
        session.refresh(c)
        assert c.status == CandidateStatus.OPEN


# ---- Undo: nur eigene Rows -------------------------------------------


def test_undo_restores_only_own_run(db):
    """Zwei IGNORED-Rows mit unterschiedlichen Timestamps; der Undo
    eines Timestamps darf nur EINE der beiden zurueckkippen."""
    with Session(db) as session:
        a = _seed_candidate(
            session,
            source=CandidateSource.HASHTAG, confidence=0.30, age_days=30,
        )
        b = _seed_candidate(
            session,
            source=CandidateSource.HASHTAG, confidence=0.20, age_days=40,
        )

        # Apply schreibt beide auf IGNORED mit Lauf-Timestamp lauf_ts.
        result = _apply(session, skip_confirmation=True)
        assert result is not None
        n_updated, lauf_ts = result
        assert n_updated == 2

        # Simuliere: ein Admin setzt manuell eine DRITTE Row auf
        # IGNORED mit anderem updated_at (manueller Workflow).
        manual = _seed_candidate(
            session,
            source=CandidateSource.HASHTAG, confidence=0.30, age_days=30,
        )
        # Manuell auf IGNORED setzen mit anderem Timestamp:
        manual.status = CandidateStatus.IGNORED
        manual.updated_at = datetime.now(timezone.utc) + timedelta(hours=1)
        session.add(manual); session.commit(); session.refresh(manual)
        manual_ts = manual.updated_at

        # Undo des urspruenglichen Laufs
        n_restored = _undo(session, lauf_ts)
        assert n_restored == 2

        session.refresh(a); session.refresh(b); session.refresh(manual)
        assert a.status == CandidateStatus.OPEN
        assert b.status == CandidateStatus.OPEN
        # Manuelle Aussortierung bleibt IGNORED — undo durfte sie
        # NICHT anfassen.
        assert manual.status == CandidateStatus.IGNORED
        assert manual.updated_at == manual_ts


def test_undo_with_no_matching_rows_returns_zero(db):
    with Session(db) as session:
        # Eine Row existiert, aber kein passendes Apply lief
        _seed_candidate(
            session,
            source=CandidateSource.HASHTAG, confidence=0.30, age_days=30,
        )
        bogus_ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
        n = _undo(session, bogus_ts)
        assert n == 0
