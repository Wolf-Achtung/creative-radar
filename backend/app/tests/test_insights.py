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


def test_visual_analyzed_ignores_non_success_statuses():
    """W3 correction: 'analyzed' moved out of the ignore-list because the
    Production aggregate diagnosis showed it is the second-most-common
    success status. Pending / text_only / error / failure-statuses still
    don't count."""
    with _session() as session:
        _make_asset(session, visual_analysis_status="pending")
        _make_asset(session, visual_analysis_status="text_only")  # legacy, never set today
        _make_asset(session, visual_analysis_status="error")
        overview = build_overview(session)
        assert overview["visual_analyzed"] == 0


def test_visual_analyzed_ignores_new_w3_failure_statuses():
    """W3 Variante A: vision_empty/vision_timeout/vision_error/image_unreachable
    /image_invalid are honest failures and must NOT count as analyzed."""
    with _session() as session:
        _make_asset(session, visual_analysis_status="vision_empty")
        _make_asset(session, visual_analysis_status="vision_timeout")
        _make_asset(session, visual_analysis_status="vision_error")
        _make_asset(session, visual_analysis_status="image_unreachable")
        _make_asset(session, visual_analysis_status="image_invalid")
        overview = build_overview(session)
        assert overview["visual_analyzed"] == 0


def test_visual_analyzed_mixed_with_new_failure_statuses():
    """Done + text_fallback still count; new failure statuses don't."""
    with _session() as session:
        _make_asset(session, visual_analysis_status="done")
        _make_asset(session, visual_analysis_status="text_fallback")
        _make_asset(session, visual_analysis_status="vision_empty")
        _make_asset(session, visual_analysis_status="vision_timeout")
        overview = build_overview(session)
        assert overview["visual_analyzed"] == 2


def test_status_breakdown_includes_new_statuses():
    """visual_status_breakdown KPI surfaces every status the user might see in
    production, including the new W3 ones."""
    with _session() as session:
        _make_asset(session, visual_analysis_status="vision_empty")
        _make_asset(session, visual_analysis_status="image_unreachable")
        overview = build_overview(session)
        assert overview["visual_status_breakdown"].get("vision_empty") == 1
        assert overview["visual_status_breakdown"].get("image_unreachable") == 1


def test_visual_analyzed_counts_analyzed_status():
    """W3 correction: 'analyzed' is the production success status set by the
    creative_ai pipeline (not visual_analysis), seen in 4 of 20 prod samples.
    Counter must include it for honest visual_analyzed numbers."""
    with _session() as session:
        _make_asset(session, visual_analysis_status="analyzed")
        overview = build_overview(session)
        assert overview["visual_analyzed"] == 1


def test_visual_analyzed_counts_all_three_success_states():
    """Honest counter: done + analyzed + text_fallback all count."""
    with _session() as session:
        _make_asset(session, visual_analysis_status="done")
        _make_asset(session, visual_analysis_status="analyzed")
        _make_asset(session, visual_analysis_status="text_fallback")
        _make_asset(session, visual_analysis_status="error")  # legacy / failure
        overview = build_overview(session)
        assert overview["visual_analyzed"] == 3
