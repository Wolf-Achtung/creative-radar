"""Sprint Match-Key-Konsistenz — assert all four write paths land in
slug form.

Pre-Sprint there were four writers:

- ``services.visual_analysis._heuristic_analysis`` — slug ✓ (regression-
  guarded by ``test_visual_analysis.py``; not re-tested here).
- ``services.visual_analysis.analyze_asset_visual`` parser — slug ✓ (same).
- ``services.title_rematch.rematch_unassigned_assets`` — was raw, now slug.
- ``api.assets.update_asset_review`` — was raw, now slug.

The bottom two are covered here. The shared helper itself
(``slugify_match_key``) gets a small unit-test block so the algorithm
behaviour is pinned independently of any caller.
"""
from __future__ import annotations

from uuid import uuid4

from sqlmodel import Session, SQLModel, create_engine

from app.api.assets import update_asset_review
from app.models.entities import Asset, Channel, Post, ReviewStatus, Title
from app.schemas.dto import AssetReviewUpdate
from app.services.match_key import slugify_match_key
from app.services.title_rematch import rematch_unassigned_assets


def _session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


# --------------------------------------------------------------------------
# Unit tests for slugify_match_key
# --------------------------------------------------------------------------


def test_slugify_basic_franchise_slug():
    assert slugify_match_key("Avatar: The Way of Water") == "avatar-the-way-of-water"


def test_slugify_preserves_german_umlauts():
    # Pinned: '_slug' historically kept ä/ö/ü/ß; the migration's regex
    # mirrors that. Drift here would also drift the migration's
    # backfill output.
    assert slugify_match_key("Über uns") == "über-uns"
    assert slugify_match_key("Größenwahn") == "größenwahn"


def test_slugify_collapses_separator_runs():
    assert slugify_match_key("foo / bar :: baz") == "foo-bar-baz"


def test_slugify_strips_trailing_dashes():
    assert slugify_match_key("--Hello, World!--") == "hello-world"


def test_slugify_returns_none_for_empty_or_separator_only():
    assert slugify_match_key(None) is None
    assert slugify_match_key("") is None
    assert slugify_match_key("   ") is None
    assert slugify_match_key("---") is None


# --------------------------------------------------------------------------
# title_rematch write-path
# --------------------------------------------------------------------------


def test_title_rematch_writes_slug_form():
    """Replaces the pre-Sprint raw-form behaviour: when a safe whitelist
    match is found, ``rematch_unassigned_assets`` must store the slugged
    franchise/title, not the bare string."""
    with _session() as session:
        title = Title(title_original="The Way of Water", franchise="Avatar", active=True)
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(title)
        session.add(channel)
        session.commit()
        session.refresh(channel)

        post = Post(
            channel_id=channel.id,
            post_url="https://example.com/p/avatar-1",
            caption="Avatar: The Way of Water — Official Trailer",
        )
        session.add(post)
        session.commit()
        session.refresh(post)

        asset = Asset(post_id=post.id, title_id=None, ai_summary_de="Trailer Avatar")
        session.add(asset)
        session.commit()
        session.refresh(asset)

        rematch_unassigned_assets(session)
        refreshed = session.get(Asset, asset.id)

        assert refreshed is not None
        assert refreshed.title_id == title.id
        # Franchise wins over title_original in the writer; slugified.
        assert refreshed.de_us_match_key == "avatar"


# --------------------------------------------------------------------------
# /api/assets/{asset_id}/review write-path
# --------------------------------------------------------------------------


def test_review_assets_api_writes_slug_form():
    """Replaces the pre-Sprint raw-form behaviour in
    ``update_asset_review``: assigning a title via PATCH must store the
    slug form so the value lines up with the vision-pipeline output for
    the same title."""
    with _session() as session:
        title = Title(
            title_original="Mother Mary",
            franchise=None,
            active=True,
        )
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(title)
        session.add(channel)
        session.commit()
        session.refresh(title)
        session.refresh(channel)

        post = Post(channel_id=channel.id, post_url="https://example.com/p/mother-mary-1")
        session.add(post)
        session.commit()
        session.refresh(post)

        asset = Asset(post_id=post.id, title_id=None)
        session.add(asset)
        session.commit()
        session.refresh(asset)

        payload = AssetReviewUpdate(
            review_status=ReviewStatus.APPROVED,
            include_in_report=True,
            is_highlight=False,
            title_id=title.id,
        )
        update_asset_review(asset_id=asset.id, payload=payload, session=session)

        refreshed = session.get(Asset, asset.id)
        assert refreshed is not None
        assert refreshed.title_id == title.id
        # Title.franchise is None -> falls back to title_original "Mother Mary",
        # slugified to "mother-mary".
        assert refreshed.de_us_match_key == "mother-mary"


def test_review_assets_api_slug_with_special_chars():
    """End-to-end check that special-char franchises survive the API
    write path identically to a direct slugify call."""
    with _session() as session:
        title = Title(
            title_original="Generic Title",
            franchise="Foo / Bar : Baz",
            active=True,
        )
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(title)
        session.add(channel)
        session.commit()
        session.refresh(title)
        session.refresh(channel)

        post = Post(channel_id=channel.id, post_url="https://example.com/p/foo-bar-baz")
        session.add(post)
        session.commit()
        session.refresh(post)

        asset = Asset(post_id=post.id, title_id=None)
        session.add(asset)
        session.commit()
        session.refresh(asset)

        update_asset_review(
            asset_id=asset.id,
            payload=AssetReviewUpdate(
                review_status=ReviewStatus.APPROVED,
                include_in_report=False,
                is_highlight=False,
                title_id=title.id,
            ),
            session=session,
        )

        refreshed = session.get(Asset, asset.id)
        assert refreshed is not None
        assert refreshed.de_us_match_key == "foo-bar-baz"
        assert refreshed.de_us_match_key == slugify_match_key("Foo / Bar : Baz")
