"""V3 Sprint 1, Commit 2 — deterministic title_id enrichment for
aktuell_im_fokus items. post_url -> Post -> Asset.title_id, never by name.
In-memory sqlite, no LLM."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Asset, AssetType, Channel, Market, Post, ReviewStatus, Title
from app.schemas.insights import TitelImFokus
from app.services.insight_engine import (
    _enrich_fokus_title_ids,
    _resolve_title_id_for_post_url,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _channel(session) -> Channel:
    ch = Channel(id=uuid4(), name="warner", handle="warner",
                 url="https://x/warner", platform="tiktok", market=Market.DE)
    session.add(ch)
    session.commit()
    return ch


def _post(session, channel, url) -> Post:
    post = Post(id=uuid4(), channel_id=channel.id, platform="tiktok", post_url=url)
    session.add(post)
    session.commit()
    return post


def _title(session, name) -> Title:
    t = Title(id=uuid4(), title_original=name, active=True)
    session.add(t)
    session.commit()
    return t


def _asset(session, post, title) -> Asset:
    a = Asset(id=uuid4(), post_id=post.id, title_id=title.id if title else None,
              asset_type=AssetType.UNKNOWN, review_status=ReviewStatus.NEW)
    session.add(a)
    session.commit()
    return a


def test_resolve_single_title_id(session):
    ch = _channel(session)
    post = _post(session, ch, "https://tt/p/1")
    title = _title(session, "Mortal Kombat II")
    _asset(session, post, title)
    assert _resolve_title_id_for_post_url(session, "https://tt/p/1") == str(title.id)


def test_resolve_multiple_assets_same_title(session):
    ch = _channel(session)
    post = _post(session, ch, "https://tt/p/2")
    title = _title(session, "Solo")
    _asset(session, post, title)
    _asset(session, post, title)  # two assets, same title -> distinct == 1
    assert _resolve_title_id_for_post_url(session, "https://tt/p/2") == str(title.id)


def test_resolve_two_distinct_titles_returns_none(session):
    ch = _channel(session)
    post = _post(session, ch, "https://tt/p/3")
    _asset(session, post, _title(session, "Film A"))
    _asset(session, post, _title(session, "Film B"))
    # >1 distinct title_id -> no guess
    assert _resolve_title_id_for_post_url(session, "https://tt/p/3") is None


def test_resolve_asset_without_title_returns_none(session):
    ch = _channel(session)
    post = _post(session, ch, "https://tt/p/4")
    _asset(session, post, None)  # matcher hasn't assigned
    assert _resolve_title_id_for_post_url(session, "https://tt/p/4") is None


def test_resolve_unknown_post_url_returns_none(session):
    assert _resolve_title_id_for_post_url(session, "https://tt/p/does-not-exist") is None


def test_resolve_empty_post_url_returns_none(session):
    assert _resolve_title_id_for_post_url(session, None) is None
    assert _resolve_title_id_for_post_url(session, "") is None


def _fokus(titel, post_url, title_id=None) -> TitelImFokus:
    return TitelImFokus(titel=titel, markt="DE", format_typ="clip",
                        kennzahl="x", post_url=post_url, title_id=title_id)


def test_enrich_sets_title_id_per_item(session):
    ch = _channel(session)
    post = _post(session, ch, "https://tt/p/10")
    title = _title(session, "Mortal Kombat II")
    _asset(session, post, title)

    item_hit = _fokus("Mortal Kombat II", "https://tt/p/10")
    item_miss = _fokus("Unknown Film", "https://tt/p/none")
    llm_output = SimpleNamespace(aktuell_im_fokus=[item_hit, item_miss])

    _enrich_fokus_title_ids(session, llm_output)

    assert item_hit.title_id == str(title.id)
    assert item_miss.title_id is None


def test_enrich_preserves_existing_title_id(session):
    ch = _channel(session)
    post = _post(session, ch, "https://tt/p/11")
    _asset(session, post, _title(session, "Solo"))

    item = _fokus("Solo", "https://tt/p/11", title_id="pre-set-keep-me")
    _enrich_fokus_title_ids(session, SimpleNamespace(aktuell_im_fokus=[item]))
    # idempotent: an already-set value is not overwritten
    assert item.title_id == "pre-set-keep-me"


def test_enrich_handles_none_and_empty(session):
    # llm_output None and empty list must not raise
    _enrich_fokus_title_ids(session, None)
    _enrich_fokus_title_ids(session, SimpleNamespace(aktuell_im_fokus=None))
    _enrich_fokus_title_ids(session, SimpleNamespace(aktuell_im_fokus=[]))
