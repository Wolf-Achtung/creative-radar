"""Sprint Candidate-Insert-Filter — Rausch-Quellen in Auto-Pfaden unterdrücken.

Verifiziert den erweiterten Guard in ``create_candidate_from_asset``: in den
automatischen Zuflusspfaden (``skip_if_guess_only=True``) werden Match-Quellen
ohne Auto-Match-Nutzen NICHT zu Candidates:

    match.source in ("none", "ambiguous", "brand_whitelist", "empty") → return None

``empty`` ist ein Factory-internes Fallback-Label: feuert der Zweit-Call auf
leeren kinetic_text/placement_title_text, wäscht es sonst einen geblockten
Erst-Treffer (ambiguous ohne kinetic/placement) an diesem Filter vorbei.

Echte Whitelist-Treffer (exact, hashtag, unique_text, fuzzy) bleiben erhalten.
User-getriebene Calls (``skip_if_guess_only=False``) sind vom Filter unberührt.

WICHTIG: Der Filter keyed auf ``match.source``, NICHT auf ``confidence`` und
NICHT auf ``candidate.source`` — die ~2113 echten OPENAI-source/0.35-Candidates
dürfen nicht mitgefiltert werden (Regressionstest #4 / #8).
"""
from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import (
    Asset,
    CandidateSource,
    CandidateStatus,
    Channel,
    Post,
    TitleCandidate,
)
from app.services import title_candidates as tc_module
from app.services.title_candidates import create_candidate_from_asset
from app.services.whitelist_matcher import MatchResult


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


_post_seq = 0


def _seed_asset(session: Session, *, ai_summary_de: str = "irgendein Text") -> Asset:
    global _post_seq
    _post_seq += 1
    channel = Channel(name="Test", platform="instagram", url="https://example.com")
    session.add(channel)
    session.commit()
    session.refresh(channel)
    post = Post(channel_id=channel.id, post_url=f"https://example.com/p-{_post_seq}", caption="cap")
    session.add(post)
    session.commit()
    session.refresh(post)
    asset = Asset(post_id=post.id, title_id=None, ai_summary_de=ai_summary_de)
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def _patch_match(monkeypatch, result: MatchResult) -> None:
    """``find_best_title_match`` im title_candidates-Modul fest verdrahten —
    isoliert die Factory-Filter-Logik von der Matcher-Erkennung."""
    monkeypatch.setattr(tc_module, "find_best_title_match", lambda *a, **k: result)


# 1. ambiguous, skip=True (Auto) → kein Candidate
def test_ambiguous_auto_path_creates_no_candidate(monkeypatch):
    with _session() as session:
        asset = _seed_asset(session)
        _patch_match(monkeypatch, MatchResult(title=None, confidence=0.0, source="ambiguous", suggested_title=None))

        result = create_candidate_from_asset(session, asset.id)  # skip default True

        assert result is None
        assert session.exec(select(TitleCandidate)).all() == []


# 2. brand_whitelist, skip=True (Auto) → kein Candidate
def test_brand_whitelist_auto_path_creates_no_candidate(monkeypatch):
    with _session() as session:
        asset = _seed_asset(session)
        _patch_match(monkeypatch, MatchResult(title=None, confidence=0.85, source="brand_whitelist", suggested_title="Netflix Originals"))

        result = create_candidate_from_asset(session, asset.id)

        assert result is None
        assert session.exec(select(TitleCandidate)).all() == []


# 2b. empty, skip=True (Auto) → kein Candidate (Factory-internes Fallback-Label)
def test_empty_source_auto_path_creates_no_candidate(monkeypatch):
    with _session() as session:
        asset = _seed_asset(session)
        _patch_match(monkeypatch, MatchResult(title=None, confidence=0.0, source="empty", suggested_title=None))

        result = create_candidate_from_asset(session, asset.id)

        assert result is None
        assert session.exec(select(TitleCandidate)).all() == []


# 2c. Laundering-Pfad: Erst-Call ambiguous (suggested_title=None) → Factory
#     feuert den Fallback-Call (kinetic/placement leer) → zweiter Call liefert
#     "empty". Ohne "empty" in der Blockliste würde hier ein Candidate
#     entstehen ("Unklarer Titel"/0.35) — genau der reale Rematch-Fall.
def test_ambiguous_laundered_to_empty_is_still_blocked(monkeypatch):
    with _session() as session:
        asset = _seed_asset(session)
        results = iter([
            MatchResult(title=None, confidence=0.0, source="ambiguous", suggested_title=None),
            MatchResult(title=None, confidence=0.0, source="empty", suggested_title=None),
        ])
        monkeypatch.setattr(tc_module, "find_best_title_match", lambda *a, **k: next(results))

        result = create_candidate_from_asset(session, asset.id)

        assert result is None
        assert session.exec(select(TitleCandidate)).all() == []


# 3. echter Whitelist-Treffer (retained source) → Candidate wie bisher
def test_real_whitelist_match_still_creates_candidate(monkeypatch):
    with _session() as session:
        asset = _seed_asset(session)
        _patch_match(monkeypatch, MatchResult(title=None, confidence=0.97, source="unique_text", suggested_title="Echter Titel"))

        result = create_candidate_from_asset(session, asset.id)

        assert result is not None
        assert result.source == CandidateSource.MATCHER
        assert result.suggested_title == "Echter Titel"
        candidates = session.exec(select(TitleCandidate)).all()
        assert len(candidates) == 1


# 4. KRITISCHER REGRESSIONSTEST: OPENAI-source conf 0.35 echt → Candidate BLEIBT.
#    candidate.source == OPENAI entsteht über match.source=="none" + ai_summary;
#    dieser Pfad läuft nur unter skip=False (User-API) durch. Der Filter darf
#    ihn NICHT anfassen.
def test_openai_source_low_confidence_candidate_is_preserved(monkeypatch):
    with _session() as session:
        asset = _seed_asset(session, ai_summary_de="echte KI-Zusammenfassung")
        _patch_match(monkeypatch, MatchResult(title=None, confidence=0.0, source="none", suggested_title=None))

        result = create_candidate_from_asset(session, asset.id, skip_if_guess_only=False)

        assert result is not None
        assert result.source == CandidateSource.OPENAI   # _candidate_source_from_text
        assert result.confidence == 0.35                 # 0 → 0.35-Floor
        assert len(session.exec(select(TitleCandidate)).all()) == 1


# 5. source=="none" + skip=True → weiterhin None (bestehender Variante-D-Guard)
def test_none_source_auto_path_still_skipped(monkeypatch):
    with _session() as session:
        asset = _seed_asset(session)
        _patch_match(monkeypatch, MatchResult(title=None, confidence=0.0, source="none", suggested_title="rivals"))

        result = create_candidate_from_asset(session, asset.id)

        assert result is None
        assert session.exec(select(TitleCandidate)).all() == []


# 6. User-API skip=False → ambiguous + brand_whitelist werden ANGELEGT (Override)
def test_user_override_creates_ambiguous_and_brand_candidates(monkeypatch):
    with _session() as session:
        asset_a = _seed_asset(session)
        _patch_match(monkeypatch, MatchResult(title=None, confidence=0.0, source="ambiguous", suggested_title=None))
        amb = create_candidate_from_asset(session, asset_a.id, skip_if_guess_only=False)
        assert amb is not None
        assert amb.suggested_title == "Unklarer Titel"   # None → Default
        assert amb.confidence == 0.35

        asset_b = _seed_asset(session)
        _patch_match(monkeypatch, MatchResult(title=None, confidence=0.85, source="brand_whitelist", suggested_title="Netflix Originals"))
        brand = create_candidate_from_asset(session, asset_b.id, skip_if_guess_only=False)
        assert brand is not None
        assert brand.suggested_title == "Netflix Originals"
        assert brand.confidence == 0.85

        asset_c = _seed_asset(session)
        _patch_match(monkeypatch, MatchResult(title=None, confidence=0.0, source="empty", suggested_title=None))
        empty = create_candidate_from_asset(session, asset_c.id, skip_if_guess_only=False)
        assert empty is not None
        assert empty.suggested_title == "Unklarer Titel"

        assert len(session.exec(select(TitleCandidate)).all()) == 3


# 7. Dedup pro asset_id → weiterhin funktional (OPEN-Candidate wird zurückgegeben)
def test_dedup_returns_existing_open_candidate(monkeypatch):
    with _session() as session:
        asset = _seed_asset(session)
        _patch_match(monkeypatch, MatchResult(title=None, confidence=0.97, source="unique_text", suggested_title="Echter Titel"))

        first = create_candidate_from_asset(session, asset.id)
        second = create_candidate_from_asset(session, asset.id)

        assert first is not None
        assert second is not None
        assert second.id == first.id
        assert len(session.exec(select(TitleCandidate)).all()) == 1


# 8. Beleg: Filter keyed auf source, NICHT auf confidence. Ein retained-source-
#    Match mit confidence 0.35 erzeugt weiterhin einen Candidate.
def test_filter_keys_on_source_not_confidence(monkeypatch):
    with _session() as session:
        asset = _seed_asset(session)
        _patch_match(monkeypatch, MatchResult(title=None, confidence=0.35, source="fuzzy", suggested_title="Fuzzy Titel"))

        result = create_candidate_from_asset(session, asset.id)

        assert result is not None
        assert result.confidence == 0.35
        assert len(session.exec(select(TitleCandidate)).all()) == 1
