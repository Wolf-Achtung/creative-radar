from sqlmodel import SQLModel, Session, create_engine, select

from app.models.entities import Asset, Channel, Post, Title, TitleCandidate
from app.services import title_rematch as title_rematch_module
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


def test_rematch_batches_commits_for_many_auto_matches(monkeypatch):
    """Sprint 10g: with ``commit_batch_size=10`` and 25 matched assets we
    expect 3 batched commits inside the loop (2 full batches × 10 + 1
    flush of the remaining 5 at the end). All 25 assets must be assigned.
    """
    with _session() as session:
        title = Title(title_original="Euphoria", active=True)
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(title)
        session.add(channel)
        session.commit()
        session.refresh(channel)
        session.refresh(title)

        asset_ids = []
        for i in range(25):
            post = Post(
                channel_id=channel.id,
                post_url=f"https://example.com/p-{i}",
                caption="Official Trailer: Euphoria",
            )
            session.add(post)
            session.commit()
            session.refresh(post)
            asset = Asset(post_id=post.id, title_id=None, ai_summary_de="Euphoria trailer")
            session.add(asset)
            session.commit()
            session.refresh(asset)
            asset_ids.append(asset.id)

        commit_calls = {"n": 0}
        original_commit = Session.commit

        def counting_commit(self):
            commit_calls["n"] += 1
            return original_commit(self)

        monkeypatch.setattr(Session, "commit", counting_commit)

        summary = rematch_unassigned_assets(session, commit_batch_size=10)

        assert summary.checked == 25
        assert summary.auto_matched == 25
        assert summary.still_unmatched == 0

        for aid in asset_ids:
            refreshed = session.get(Asset, aid)
            assert refreshed.title_id == title.id

        # At least 3 batched commits inside rematch_unassigned_assets
        # (10, 10, 5 flush). ``resolve_open_candidates_for_asset`` skips
        # commits when no open candidate exists, so it doesn't inflate the
        # count for this scenario.
        assert commit_calls["n"] >= 3
