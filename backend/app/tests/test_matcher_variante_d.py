"""Variante D — deterministische Strong-Hit-Disambiguierung in
``find_best_title_match``: Spezifität (längster matched_text) → Zeit-Tiebreak
(Release-Nähe zum Post-Datum, gleiche Franchise) → OPEN (im Zweifel nichts
zuweisen). In-memory sqlite, kein LLM."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Title
from app.services.whitelist_matcher import find_best_title_match, is_safe_auto_match


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_specificity_longest_match_wins(session: Session):
    """Text trifft 'Mortal Kombat' UND 'Mortal Kombat II' als Phrase →
    der längste matched_text (das Sequel) gewinnt, statt pauschal ambiguous."""
    session.add(Title(title_original="Mortal Kombat", franchise="Mortal Kombat", active=True))
    session.add(Title(title_original="Mortal Kombat II", franchise="Mortal Kombat", active=True))
    session.commit()

    match = find_best_title_match(session, "Mortal Kombat II – Baraka vs Johnny Cage")

    assert match.title is not None
    assert match.title.title_original == "Mortal Kombat II"
    assert is_safe_auto_match(match)


def test_specificity_parent_still_wins_for_exact_franchise_text(session: Session):
    """Nur 'Mortal Kombat' (ohne 'II') im Text → einziger Strong-Hit ist der
    Eltern-Titel → deterministisch der 2021er, kein Tiebreak nötig."""
    session.add(Title(title_original="Mortal Kombat", franchise="Mortal Kombat", active=True))
    session.add(Title(title_original="Mortal Kombat II", franchise="Mortal Kombat", active=True))
    session.commit()

    match = find_best_title_match(session, "Mortal Kombat")

    assert match.title is not None
    assert match.title.title_original == "Mortal Kombat"


def test_time_tiebreak_picks_nearest_release(session: Session):
    """Zwei gleich lang matchende Titel derselben Franchise (shared alias) →
    der Titel mit dem Release-Datum am nächsten an published_at gewinnt."""
    session.add(Title(title_original="Alpha One", franchise="Saga",
                      release_date_de=date(2020, 1, 1), aliases=["Saga Collection"], active=True))
    session.add(Title(title_original="Alpha Two", franchise="Saga",
                      release_date_de=date(2026, 1, 1), aliases=["Saga Collection"], active=True))
    session.commit()

    pub = datetime(2026, 2, 1, tzinfo=timezone.utc)
    match = find_best_title_match(session, "Behind the Saga Collection shoot", published_at=pub)

    assert match.title is not None
    assert match.title.title_original == "Alpha Two"
    assert is_safe_auto_match(match)


def test_time_tiebreak_prefers_us_when_de_missing(session: Session):
    """Release-Anker fällt auf release_date_us zurück, wenn _de fehlt."""
    session.add(Title(title_original="Beta One", franchise="Beta",
                      release_date_us=date(2019, 6, 1), aliases=["Beta Reihe"], active=True))
    session.add(Title(title_original="Beta Two", franchise="Beta",
                      release_date_us=date(2026, 6, 1), aliases=["Beta Reihe"], active=True))
    session.commit()

    pub = datetime(2026, 5, 20, tzinfo=timezone.utc)
    match = find_best_title_match(session, "neuer clip Beta Reihe", published_at=pub)

    assert match.title is not None
    assert match.title.title_original == "Beta Two"


def test_open_fallback_when_no_release_data(session: Session):
    """Gleich lang, gleiche Franchise, aber KEIN release_date → Zeit-Tiebreak
    greift nicht → title=None / ambiguous (OPEN), keine Zuweisung."""
    session.add(Title(title_original="Alpha One", franchise="Saga", aliases=["Saga Collection"], active=True))
    session.add(Title(title_original="Alpha Two", franchise="Saga", aliases=["Saga Collection"], active=True))
    session.commit()

    pub = datetime(2026, 2, 1, tzinfo=timezone.utc)
    match = find_best_title_match(session, "Behind the Saga Collection shoot", published_at=pub)

    assert match.title is None
    assert match.source == "ambiguous"
    assert not is_safe_auto_match(match)


def test_open_fallback_when_different_franchise(session: Session):
    """Gleich lang, aber unterschiedliche Franchise → kein Zeit-Tiebreak,
    selbst mit release_date → OPEN."""
    session.add(Title(title_original="Alpha One", franchise="SagaA",
                      release_date_de=date(2020, 1, 1), aliases=["Common Phrase Here"], active=True))
    session.add(Title(title_original="Alpha Two", franchise="SagaB",
                      release_date_de=date(2026, 1, 1), aliases=["Common Phrase Here"], active=True))
    session.commit()

    pub = datetime(2026, 2, 1, tzinfo=timezone.utc)
    match = find_best_title_match(session, "see Common Phrase Here now", published_at=pub)

    assert match.title is None
    assert match.source == "ambiguous"


def test_no_tiebreak_without_published_at(session: Session):
    """Ohne published_at greift der Zeit-Tiebreak nicht → OPEN statt Rateschuss."""
    session.add(Title(title_original="Alpha One", franchise="Saga",
                      release_date_de=date(2020, 1, 1), aliases=["Saga Collection"], active=True))
    session.add(Title(title_original="Alpha Two", franchise="Saga",
                      release_date_de=date(2026, 1, 1), aliases=["Saga Collection"], active=True))
    session.commit()

    match = find_best_title_match(session, "Behind the Saga Collection shoot")

    assert match.title is None
    assert match.source == "ambiguous"


def test_single_title_still_assigned(session: Session):
    """Regression: eindeutiger Einzeltitel bleibt sicher zugewiesen."""
    session.add(Title(title_original="Wednesday", active=True))
    session.commit()

    match = find_best_title_match(session, "Official Trailer: Wednesday")

    assert match.title is not None
    assert match.title.title_original == "Wednesday"
    assert is_safe_auto_match(match)
