from datetime import datetime, timezone

from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Asset, Channel, Market, Post
from app.services.insights import build_overview


def _session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


_counter = {"n": 0}


def _make_asset(session: Session, *, visual_analysis_status: str) -> Asset:
    _counter["n"] += 1
    n = _counter["n"]
    channel = Channel(name=f"Test {n}", platform="instagram", url=f"https://example.com/c{n}", market=Market.US)
    session.add(channel)
    session.commit()
    session.refresh(channel)

    post = Post(
        channel_id=channel.id,
        post_url=f"https://example.com/post/{n}",
        detected_at=datetime.now(timezone.utc),
    )
    session.add(post)
    session.commit()
    session.refresh(post)

    asset = Asset(
        post_id=post.id,
        visual_analysis_status=visual_analysis_status,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def test_visual_analyzed_counts_done_status():
    with _session() as session:
        _make_asset(session, visual_analysis_status="done")
        overview = build_overview(session)
        assert overview["visual_analyzed"] == 1


def test_visual_analyzed_counts_text_fallback_status():
    with _session() as session:
        _make_asset(session, visual_analysis_status="text_fallback")
        overview = build_overview(session)
        assert overview["visual_analyzed"] == 1


def test_visual_analyzed_ignores_pending_and_legacy_statuses():
    with _session() as session:
        _make_asset(session, visual_analysis_status="pending")
        _make_asset(session, visual_analysis_status="analyzed")
        _make_asset(session, visual_analysis_status="text_only")
        _make_asset(session, visual_analysis_status="error")
        overview = build_overview(session)
        assert overview["visual_analyzed"] == 0
