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


def test_rematch_does_not_create_candidate_for_guess_only_match():
    """Sprint 28.05.2026 (Variante D): wenn der Whitelist-Matcher
    KEINEN Treffer findet (``match.source == "none"``) und nur ein
    6-Token-Guess als ``suggested_title`` produziert, wird KEIN
    Candidate angelegt — das war der Hauptproduzent des Rauschens
    ("Rivals" fuer UEFA-Posts).

    Vorher (vor Variante D) hat dieser Test ``candidates_created == 1``
    erwartet; das ALTE Verhalten ist absichtlich abgeschaltet.
    ``still_unmatched`` zaehlt weiter, sodass die Summary trotzdem
    sichtbar macht, dass Assets ohne Title-Zuordnung herumliegen.
    """
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
        assert summary.candidates_created == 0
        assert summary.still_unmatched == 1
        assert candidates == []


def test_rematch_creates_candidate_for_fuzzy_whitelist_match():
    """Sprint 28.05.2026 (Variante D, Gegenprobe): echte Whitelist-
    Matches mit Confidence < 0.95 bleiben als Candidate erhalten — das
    sind legitime Review-Faelle (Fuzzy-Hit, Brand-Whitelist,
    ambiguous-Multi-Match). Hier provoziert eine Caption mit einem
    Tippfehler im Whitelist-Titel den Fuzzy-Pfad
    (``SequenceMatcher.ratio > 0.72`` aber < 0.95, sodass
    ``is_safe_auto_match`` NICHT greift)."""
    with _session() as session:
        title = Title(title_original="Drawn to You", active=True)
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(title)
        session.add(channel)
        session.commit()
        session.refresh(channel)

        # 1-Buchstabe-Drop ("Drawn to Yu") liegt bei SequenceMatcher.ratio
        # ~0.91 — ueber der 0.72-Fuzzy-Schwelle, unter der 0.95-Safe-Bar.
        post = Post(
            channel_id=channel.id,
            post_url="https://example.com/post-fuzzy",
            caption="Drawn to Yu",
        )
        session.add(post)
        session.commit()
        session.refresh(post)

        asset = Asset(
            post_id=post.id, title_id=None,
            ai_summary_de="drawn to yu",
        )
        session.add(asset)
        session.commit()
        session.refresh(asset)

        summary = rematch_unassigned_assets(session)
        candidates = session.exec(select(TitleCandidate)).all()

        # Kein safe-auto-Match (Confidence < 0.95), aber der Fuzzy-
        # Match liefert einen Whitelist-Treffer → Candidate wird
        # angelegt.
        assert summary.checked == 1
        assert summary.auto_matched == 0
        assert summary.candidates_created == 1
        assert len(candidates) == 1
        assert candidates[0].asset_id == asset.id


def test_rematch_open_fallback_for_ambiguous_no_release():
    """Variante D Teil 3 + Candidate-Insert-Filter-Sprint: matcht der Text
    zwei gleich spezifische Titel derselben Franchise ohne Release-Datum,
    löst weder Spezifität noch Zeit eindeutig auf → KEINE Zuweisung
    (title_id bleibt None).

    Verhaltensänderung (Candidate-Insert-Filter): ``source=="ambiguous"``
    zählt jetzt als Rausch-Quelle ohne Auto-Match-Nutzen und erzeugt im
    Auto-Pfad (rematch, ``skip_if_guess_only=True``) KEINEN Candidate mehr.
    Vorher legte dieser Fall einen OPEN-Kandidaten zur Review an; das war
    eine der unterdrückten Rausch-Quellen. ``still_unmatched`` zählt weiter,
    sodass die Summary das unzugeordnete Asset weiterhin sichtbar macht."""
    with _session() as session:
        a = Title(title_original="Alpha One", franchise="Saga", aliases=["Saga Collection"], active=True)
        b = Title(title_original="Alpha Two", franchise="Saga", aliases=["Saga Collection"], active=True)
        channel = Channel(name="Test", platform="instagram", url="https://example.com")
        session.add(a)
        session.add(b)
        session.add(channel)
        session.commit()
        session.refresh(channel)

        post = Post(channel_id=channel.id, post_url="https://example.com/post-ambig",
                    caption="Behind the Saga Collection shoot")
        session.add(post)
        session.commit()
        session.refresh(post)

        asset = Asset(post_id=post.id, title_id=None, ai_summary_de="Saga Collection feature")
        session.add(asset)
        session.commit()
        session.refresh(asset)

        summary = rematch_unassigned_assets(session)
        refreshed = session.get(Asset, asset.id)
        candidates = session.exec(select(TitleCandidate)).all()

        assert summary.auto_matched == 0
        assert refreshed is not None
        assert refreshed.title_id is None          # im Zweifel NICHTS zuweisen
        assert summary.still_unmatched == 1
        assert summary.candidates_created == 0     # ambiguous → kein Candidate mehr
        assert candidates == []


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
