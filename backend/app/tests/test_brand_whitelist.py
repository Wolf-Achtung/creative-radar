"""Sprint 9 H3 tests — brand-whitelist loader & studio filter.

Covers schema-load, hashtag-/substring-match, studio scoping, and the
cross-studio (studio=null) escape hatch.
"""
from __future__ import annotations

import pytest

from app.services.brand_whitelist_loader import (
    BrandEntry,
    find_brand_match,
    load_brand_whitelist,
)


@pytest.fixture(autouse=True)
def _clear_loader_cache():
    """Each test re-reads the YAML so a flaky entry can't leak across tests."""
    load_brand_whitelist(force_reload=True)
    yield
    load_brand_whitelist(force_reload=True)


def test_brand_whitelist_yaml_loads_with_valid_schema():
    """Schema-validation: every YAML entry must conform to BrandEntry."""
    entries = load_brand_whitelist(force_reload=True)
    assert len(entries) >= 10, "expect ≥10 curated initial entries"
    assert all(isinstance(entry, BrandEntry) for entry in entries)
    valid_types = {"brand_spot", "event", "platform", "studio_brand", "franchise_brand"}
    for entry in entries:
        assert entry.type in valid_types, f"unknown type {entry.type!r} on {entry.title!r}"


def test_brand_match_disney_brand_in_disney_pair():
    """A Disney brand-spot must match when called with studio='disney'."""
    match = find_brand_match("New Make-A-Wish spot #drawntoyou", studio="disney")
    assert match is not None
    assert match.title == "Drawn to You"
    assert match.studio == "disney"


def test_brand_match_disney_brand_does_not_leak_into_sony_pair():
    """Studio scoping: Disney's Drawn to You must NOT match in a Sony pair."""
    match = find_brand_match("#drawntoyou some sony post", studio="sonypictures")
    assert match is None


def test_brand_match_cross_studio_event_matches_in_any_pair():
    """studio=null on the entry → matches regardless of caller-side studio."""
    disney = find_brand_match("CinemaCon trailer drop #cinemacon", studio="disney")
    sony = find_brand_match("CinemaCon trailer drop #cinemacon", studio="sonypictures")
    assert disney is not None and disney.title == "Cinemacon"
    assert sony is not None and sony.title == "Cinemacon"


def test_brand_match_disney_plus_platform_alias():
    """Hashtag #disneyplus resolves to the Disney+ platform entry."""
    match = find_brand_match("Streaming today #disneyplus", studio="disney")
    assert match is not None
    assert match.title == "Disney Plus Streaming"
    assert match.type == "platform"


def test_brand_match_returns_none_for_unrelated_caption():
    """Captions without any aliases must produce no brand match."""
    match = find_brand_match("just a generic caption with nothing to match", studio="disney")
    assert match is None


def test_brand_match_handles_empty_text():
    """Empty / None text safely returns None."""
    assert find_brand_match("", studio="disney") is None
    assert find_brand_match(None, studio="disney") is None
