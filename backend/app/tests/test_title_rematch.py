from sqlmodel import SQLModel, Session, create_engine, select

from app.models.entities import Asset, Channel, Post, Title, TitleCandidate
from app.services.title_rematch import rematch_unassigned_assets


def _session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_rematch_assigns_safe_whitelist_match():
    with _session() as session:
        title = Title(title_original="Euphoria", active=True)
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(title)
        session.add(channel)
        session.commit()
        session.refresh(channel)

        post = Post(channel_id=channel.id, post_url="https://example.com/post-1", caption="Official Trailer: Euphoria")
        session.add(post)
        session.commit()
        session.refresh(post)

        asset = Asset(post_id=post.id, title_id=None, ai_summary_de="Trailer zu Euphoria")
        session.add(asset)
        session.commit()
        session.refresh(asset)

        summary = rematch_unassigned_assets(session)
        refreshed = session.get(Asset, asset.id)

        assert summary.checked == 1
        assert summary.auto_matched == 1
        assert summary.candidates_created == 0
        assert summary.still_unmatched == 0
        assert refreshed is not None
        assert refreshed.title_id == title.id


def test_rematch_creates_candidate_for_unmatched_asset():
    with _session() as session:
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(channel)
        session.commit()
        session.refresh(channel)

        post = Post(channel_id=channel.id, post_url="https://example.com/post-2", caption="Unknown preview teaser")
        session.add(post)
        session.commit()
        session.refresh(post)

        asset = Asset(post_id=post.id, title_id=None, ai_summary_de="Kein bekannter Titel")
        session.add(asset)
        session.commit()
        session.refresh(asset)

        summary = rematch_unassigned_assets(session)
        candidates = session.exec(select(TitleCandidate)).all()

        assert summary.checked == 1
        assert summary.auto_matched == 0
        assert summary.candidates_created == 1
        assert summary.still_unmatched == 1
        assert len(candidates) == 1
        assert candidates[0].asset_id == asset.id
