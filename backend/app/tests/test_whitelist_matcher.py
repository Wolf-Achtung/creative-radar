"""Sprint 10g: cache-API tests for whitelist_matcher.

Verifies that ``find_best_title_match`` returns identical results whether the
title bundle is loaded ad-hoc or passed in as ``cached_bundle`` /
``cached_normalized_index`` — the same equivalence the cron auto-rematch
batch relies on.
"""

from sqlmodel import SQLModel, Session, create_engine

from app.models.entities import Title, TitleKeyword
from app.services.whitelist_matcher import (
    build_normalized_index,
    find_best_title_match,
    load_title_bundle,
)


def _session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_load_title_bundle_returns_active_titles_with_keywords():
    with _session() as session:
        title_a = Title(title_original="Euphoria", active=True)
        title_b = Title(title_original="Inactive Show", active=False)
        session.add(title_a)
        session.add(title_b)
        session.commit()
        session.refresh(title_a)
        session.add(TitleKeyword(title_id=title_a.id, keyword="euphoria-hbo"))
        session.commit()

        bundle = load_title_bundle(session)

        assert len(bundle) == 1
        title, candidates = bundle[0]
        assert title.id == title_a.id
        assert "Euphoria" in candidates["exact"]
        # ``weak`` includes franchise + keyword strings
        assert "euphoria-hbo" in candidates["weak"]


def test_find_best_title_match_with_cached_bundle_matches_uncached():
    with _session() as session:
        session.add(Title(title_original="Euphoria", active=True))
        session.add(Title(title_original="Wednesday", active=True))
        session.commit()

        bundle = load_title_bundle(session)
        index = build_normalized_index(bundle)

        cached = find_best_title_match(
            session,
            "Official Trailer: Euphoria",
            cached_bundle=bundle,
            cached_normalized_index=index,
        )
        uncached = find_best_title_match(session, "Official Trailer: Euphoria")

        assert cached.title is not None
        assert uncached.title is not None
        assert cached.title.id == uncached.title.id
        assert cached.source == uncached.source
        assert cached.confidence == uncached.confidence
