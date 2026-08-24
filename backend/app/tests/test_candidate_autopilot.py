"""Kandidaten-Autopilot (Sprint Review-Automatisierung 2026-07-20).

Abgedeckt:
- Exakt-Treffer (title_original + Alias, case-/whitespace-insensitiv)
  mit Confidence >= Schwelle -> Asset zugeordnet (title_id +
  de_us_match_key wie beim manuellen Bestaetigen), Kandidat resolved.
- Unter der Schwelle / kein Exakt-Treffer / mehrdeutiger Name -> bleibt
  OPEN (Menschensache).
- Bereits zugeordnetes Asset -> offener Kandidat wird nur geschlossen.
- Karteileichen (alt UND schwach) -> IGNORED; alte STARKE Kandidaten
  bleiben OPEN.
- Idempotenz: zweiter Lauf findet nichts Neues.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.models.entities import (
    Asset,
    CandidateStatus,
    Channel,
    Post,
    Title,
    TitleCandidate,
    utc_now,
)
from app.services.candidate_autopilot import run_candidate_autopilot


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session


def _make_asset(session: Session, suffix: str) -> Asset:
    channel = Channel(name=f"ch-{suffix}", url=f"https://example.com/{suffix}")
    session.add(channel)
    session.commit()
    post = Post(channel_id=channel.id, post_url=f"https://example.com/{suffix}/post")
    session.add(post)
    session.commit()
    asset = Asset(post_id=post.id)
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def _make_candidate(session: Session, asset: Asset, suggested: str, confidence: float,
                    age_days: int = 0) -> TitleCandidate:
    candidate = TitleCandidate(
        asset_id=asset.id,
        suggested_title=suggested,
        confidence=confidence,
        status=CandidateStatus.OPEN,
        created_at=utc_now() - timedelta(days=age_days),
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return candidate


def _make_title(session: Session, name: str, **kwargs) -> Title:
    title = Title(title_original=name, **kwargs)
    session.add(title)
    session.commit()
    session.refresh(title)
    return title


def test_autopilot_assigns_exact_match_above_threshold(session):
    # Confidence 24.08.2026 von 0.9 auf 0.97 gehoben: "Fatherhood" ist ein
    # EIN-WORT-Titel, und die brauchen seit dem Vorfall vom 24.08. die
    # Safe-Marke des Matchers (0.95). 0.9 ist dort die Substring-
    # Confidence ("needs corroboration") — genau die Klasse, die 83
    # Fehlzuordnungen erzeugt hat. Der Test-Zweck (Exakt-Treffer wird
    # zugeordnet, match_key gesetzt, Kandidat resolved) bleibt unberuehrt;
    # die Ein-Wort-Grenze selbst hat ihre eigenen Tests in
    # test_autopilot_ein_wort_schutz.py.
    title = _make_title(session, "Fatherhood", franchise=None)
    asset = _make_asset(session, "a")
    _make_candidate(session, asset, "  fatherhood ", confidence=0.97)

    summary = run_candidate_autopilot(session)

    assert summary.auto_assigned == 1
    session.refresh(asset)
    assert asset.title_id == title.id
    assert asset.de_us_match_key  # match_key wie beim manuellen Bestaetigen
    candidate = session.exec(select(TitleCandidate)).first()
    assert candidate.status == CandidateStatus.RESOLVED


def test_autopilot_matches_aliases(session):
    title = _make_title(session, "The Odyssey", aliases=["Odyssee"])
    asset = _make_asset(session, "b")
    _make_candidate(session, asset, "Odyssee", confidence=0.95)

    summary = run_candidate_autopilot(session)
    assert summary.auto_assigned == 1
    session.refresh(asset)
    assert asset.title_id == title.id


def test_autopilot_skips_below_threshold(session):
    _make_title(session, "Fatherhood")
    asset = _make_asset(session, "c")
    candidate = _make_candidate(session, asset, "Fatherhood", confidence=0.5)

    summary = run_candidate_autopilot(session)
    assert summary.auto_assigned == 0
    assert summary.skipped_low_confidence == 1
    session.refresh(asset)
    session.refresh(candidate)
    assert asset.title_id is None
    assert candidate.status == CandidateStatus.OPEN


def test_autopilot_skips_without_exact_match(session):
    _make_title(session, "Fatherhood")
    asset = _make_asset(session, "d")
    _make_candidate(session, asset, "Fatherhod", confidence=0.99)  # Tippfehler

    summary = run_candidate_autopilot(session)
    assert summary.auto_assigned == 0
    assert summary.skipped_no_exact_match == 1


def test_autopilot_skips_ambiguous_titles(session):
    # Zwei aktive Titel mit demselben Namen (Remake-Fall) -> Menschensache.
    _make_title(session, "Boo")
    _make_title(session, "Boo")
    asset = _make_asset(session, "e")
    candidate = _make_candidate(session, asset, "Boo", confidence=0.95)

    summary = run_candidate_autopilot(session)
    assert summary.auto_assigned == 0
    assert summary.skipped_ambiguous == 1
    session.refresh(candidate)
    assert candidate.status == CandidateStatus.OPEN


def test_autopilot_resolves_candidates_of_already_assigned_assets(session):
    title = _make_title(session, "Fatherhood")
    asset = _make_asset(session, "f")
    asset.title_id = title.id
    session.add(asset)
    session.commit()
    candidate = _make_candidate(session, asset, "irgendwas", confidence=0.4)

    summary = run_candidate_autopilot(session)
    assert summary.resolved_already_assigned == 1
    session.refresh(candidate)
    assert candidate.status == CandidateStatus.RESOLVED


def test_autopilot_ignores_stale_weak_candidates(session):
    asset = _make_asset(session, "g")
    stale_weak = _make_candidate(session, asset, "Unbekannt", confidence=0.35, age_days=60)
    asset2 = _make_asset(session, "h")
    old_strong = _make_candidate(session, asset2, "Auch unbekannt", confidence=0.9, age_days=60)

    summary = run_candidate_autopilot(session)
    assert summary.ignored_stale == 1
    session.refresh(stale_weak)
    session.refresh(old_strong)
    assert stale_weak.status == CandidateStatus.IGNORED
    # Alt, aber stark: bleibt zur menschlichen Pruefung offen.
    assert old_strong.status == CandidateStatus.OPEN


def test_autopilot_is_idempotent(session):
    _make_title(session, "Fatherhood")
    asset = _make_asset(session, "i")
    # 0.97 statt 0.9 — Ein-Wort-Titel, s. Kommentar oben.
    _make_candidate(session, asset, "Fatherhood", confidence=0.97)

    first = run_candidate_autopilot(session)
    second = run_candidate_autopilot(session)
    assert first.auto_assigned == 1
    assert second.checked == 0
    assert second.auto_assigned == 0


def test_autopilot_respects_threshold_setting(session, monkeypatch):
    monkeypatch.setattr(settings, "candidate_autopilot_min_confidence", 0.99, raising=False)
    _make_title(session, "Fatherhood")
    asset = _make_asset(session, "j")
    _make_candidate(session, asset, "Fatherhood", confidence=0.9)

    summary = run_candidate_autopilot(session)
    assert summary.auto_assigned == 0
    assert summary.skipped_low_confidence == 1
