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


# ----------------------------------------- token-prefilter (perf) regression ---


def test_build_token_index_buckets_by_token():
    from app.services.whitelist_matcher import build_normalized_index, build_token_index
    from app.models.entities import Title
    bundle = [
        (Title(title_original="Mortal Kombat II"), {"exact": ["Mortal Kombat II"], "local": [], "alias": [], "weak": []}),
        (Title(title_original="Moana"), {"exact": ["Moana"], "local": [], "alias": [], "weak": []}),
    ]
    idx = build_normalized_index(bundle)
    tok = build_token_index(idx)
    assert "mortal" in tok and "mortal kombat ii" in tok["mortal"]
    assert "moana" in tok and tok["moana"] == {"moana"}
    # short / numeric tokens are excluded
    assert "ii" not in tok


def test_token_prefilter_preserves_multiword_substring():
    """A multi-word title is still found via substring when its words appear in
    the caption — the token prefilter must not drop it."""
    from app.services.whitelist_matcher import find_best_title_match, is_safe_auto_match
    with _session() as session:  # type: ignore[name-defined]
        session.add(Title(title_original="Mortal Kombat II", active=True))
        session.commit()
        m = find_best_title_match(session, "Mortal Kombat II - Baraka vs Johnny Cage")
        assert m.title is not None and m.title.title_original == "Mortal Kombat II"
        assert is_safe_auto_match(m)


def test_large_catalog_match_is_correct_and_does_not_hang():
    """~5000 generic single-word titles + one real multi-word title. The match
    must return the real title (token prefilter scopes the work) and complete
    quickly — guards against the O(assets x catalog) SequenceMatcher regression."""
    import time
    from app.services.whitelist_matcher import find_best_title_match, is_safe_auto_match
    with _session() as session:  # type: ignore[name-defined]
        for i in range(5000):
            session.add(Title(title_original=f"Genericword{i}aaa", active=True))
        session.add(Title(title_original="Mortal Kombat II", active=True))
        session.commit()
        started = time.monotonic()
        m = find_best_title_match(session, "New look at Mortal Kombat II today")
        elapsed = time.monotonic() - started
        assert m.title is not None and m.title.title_original == "Mortal Kombat II"
        assert is_safe_auto_match(m)
        # Generous bound: token-prefiltered match is milliseconds; the old
        # full-scan-per-field would be far slower at 5k titles.
        assert elapsed < 2.0, f"match too slow ({elapsed:.2f}s) — token prefilter regressed?"
