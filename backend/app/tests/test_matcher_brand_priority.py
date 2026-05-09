"""Sprint 9 9.3 — integration: TMDb-vs-brand-whitelist match priority.

Pins the contract that ``find_best_title_match`` prefers a TMDb-backed
title hit over a brand-whitelist fallback when both could fire, and
that the brand fallback only kicks in when the TMDb pool is silent.
"""
from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Title
from app.services.brand_whitelist_loader import load_brand_whitelist
from app.services.whitelist_matcher import find_best_title_match


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def _reload_brand_whitelist():
    load_brand_whitelist(force_reload=True)
    yield
    load_brand_whitelist(force_reload=True)


def test_tmdb_match_wins_over_brand_whitelist(session: Session):
    """Caption hits both a TMDb title and a brand alias → TMDb wins.

    Uses a title hashtag that survives the matcher's CamelCase-split
    (``#Zootopia`` → ``zootopia``) so the strong-hit branch fires before
    the brand fallback can be evaluated.
    """
    session.add(
        Title(
            tmdb_id=999001,
            title_original="Zootopia",
            aliases=["Zootopia"],
            active=True,
        )
    )
    session.commit()

    caption = "Watch the new clip! #Zootopia #disneyplus"
    match = find_best_title_match(session, caption, studio="disney")

    assert match.title is not None, "expected TMDb-backed title to win"
    assert match.title.title_original == "Zootopia"
    assert match.source != "brand_whitelist"


def test_brand_whitelist_kicks_in_when_no_tmdb_signal(session: Session):
    """No TMDb pool signal → brand whitelist serves the suggested title."""
    # Empty title pool — only brand-whitelist should be able to fire.
    caption = "Behind the scenes #drawntoyou"
    match = find_best_title_match(session, caption, studio="disney")

    assert match.title is None, "brand-whitelist hits do not carry a Title row"
    assert match.source == "brand_whitelist"
    assert match.suggested_title == "Drawn to You"
    assert match.confidence == pytest.approx(0.85)


def test_brand_whitelist_respects_studio_filter_in_full_matcher(session: Session):
    """Disney-scoped brand entry must NOT surface for Sony pairs end-to-end."""
    caption = "#drawntoyou some sony post"
    match = find_best_title_match(session, caption, studio="sonypictures")

    assert match.title is None
    assert match.source != "brand_whitelist"
