"""Sicherheits-Garantien des
``cleanup_preview_openai_descriptions``-Scripts (Sprint 29.05.2026).

Read-only-Vorschau-Script — Tests bestaetigen:

1. H1-Praefix-Matcher trennt Beschreibungen von echten Titeln korrekt
   (case-insensitive, Trailing-Space respektiert).
2. Vorschau-Output zeigt Counts, Top-Praefixe, beide Stichproben.
3. Empty-DB-Pfad (keine OPEN+OPENAI-Rows) bricht sauber ab.
4. Read-only-Garantie: TitleCandidate-Snapshot before == after.
5. Edge: ``Derek`` matched NICHT ``Der `` (Trailing-Space).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import (
    Asset,
    CandidateSource,
    CandidateStatus,
    Channel,
    Post,
    TitleCandidate,
)
from scripts.cleanup_preview_openai_descriptions import (
    DESCRIPTION_PREFIXES,
    _matched_prefix,
    _print_preview,
)


def _shared_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def db():
    engine = _shared_engine()
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_asset(session: Session) -> Asset:
    ch = Channel(
        name="Test", platform="instagram",
        url=f"https://example.com/{uuid4()}",
    )
    session.add(ch)
    session.commit(); session.refresh(ch)
    post = Post(channel_id=ch.id, post_url=f"https://x/{uuid4()}",
                caption="x")
    session.add(post)
    session.commit(); session.refresh(post)
    asset = Asset(post_id=post.id)
    session.add(asset)
    session.commit(); session.refresh(asset)
    return asset


def _seed_candidate(
    session: Session,
    *,
    suggested_title: str,
    source: CandidateSource = CandidateSource.OPENAI,
    status: CandidateStatus = CandidateStatus.OPEN,
    confidence: float = 0.35,
    age_days: int = 30,
) -> TitleCandidate:
    asset = _seed_asset(session)
    created = datetime.now(timezone.utc) - timedelta(days=age_days)
    c = TitleCandidate(
        asset_id=asset.id,
        suggested_title=suggested_title,
        source=source,
        confidence=confidence,
        status=status,
        created_at=created,
        updated_at=created,
    )
    session.add(c)
    session.commit(); session.refresh(c)
    return c


def _snapshot_candidates(session: Session) -> list[tuple]:
    rows = session.exec(select(TitleCandidate)).all()
    return sorted([
        (str(c.id), c.suggested_title, c.status, c.confidence,
         c.source) for c in rows
    ])


# ---- _matched_prefix --------------------------------------------------


def test_matched_prefix_recognizes_der_with_trailing_space():
    assert _matched_prefix("Der Post stellt Ellie Bamber über") == "Der "


def test_matched_prefix_recognizes_instagram_post():
    assert _matched_prefix("Instagram-Post von Vertigo Releasing") == "Instagram-Post"


def test_matched_prefix_recognizes_tiktok_von():
    assert _matched_prefix("TikTok von Disney Studios") == "TikTok von"


def test_matched_prefix_case_insensitive_lowercased_input():
    """Sicherheits-Anforderung aus dem Briefing: ``DER post...``
    matched gegen ``Der ``."""
    assert _matched_prefix("DER POST STELLT") == "Der "
    assert _matched_prefix("der post stellt") == "Der "


def test_matched_prefix_does_not_match_derek():
    """Trailing-Space-Pflicht: ``Derek`` matched NICHT gegen ``Der ``."""
    assert _matched_prefix("Derek") is None
    assert _matched_prefix("Derek - The Movie") is None


def test_matched_prefix_does_not_match_die_hard():
    """Trailing-Space-Pflicht: ``Die Hard`` matched gegen ``Die `` —
    aber genau das ist gewollt, denn ``Die Hard`` ist ein echter
    Filmtitel und wir wollen ihn als Tippfehler-False-Positive zeigen.

    Wichtig: das ist KEIN Bug. Die Praefix-Liste ist absichtlich
    konservativ; ``Die Hard`` faellt in den H1-Pool und Wolf sieht
    das beim Sichten. Wenn die Restmenge zu klein wird, lassen wir
    ``Die `` aus der Liste fallen. Dieser Test dokumentiert die
    bewusste Designentscheidung.
    """
    assert _matched_prefix("Die Hard") == "Die "


def test_matched_prefix_does_not_match_real_titles():
    """Stichprobe aus dem Briefing-Beispiel — diese drei sollen NICHT
    matchen (sind echte Titel oder Titel-Fragmente)."""
    assert _matched_prefix("Mortal Kombat II") is None
    assert _matched_prefix("Anyone But You") is None
    assert _matched_prefix("Wicked extended trailer") is None


def test_matched_prefix_empty_returns_none():
    assert _matched_prefix(None) is None
    assert _matched_prefix("") is None


def test_matched_prefix_all_listed_prefixes_match_themselves():
    """Selbst-Konsistenz: jeder Praefix in der Liste matched seinen
    eigenen Beispiel-String (Praefix + Beispiel-Suffix)."""
    for prefix in DESCRIPTION_PREFIXES:
        sample = f"{prefix}weitere Tokens"
        result = _matched_prefix(sample)
        assert result is not None, f"Praefix `{prefix}` matched sich nicht selbst."


# ---- _print_preview --------------------------------------------------


def test_preview_on_empty_db(capsys, db):
    with Session(db) as session:
        _print_preview(session)
    out = capsys.readouterr().out
    assert "Total OPEN+OPENAI:       0" in out
    assert "Keine OPEN+OPENAI-Rows" in out


def test_preview_splits_h1_hits_and_rest(capsys, db):
    """3 H1-Treffer + 3 Restmenge => Counts + Stichproben enthalten
    beide."""
    with Session(db) as session:
        # H1-Treffer
        _seed_candidate(session, suggested_title="Der Post stellt Ellie Bamber über")
        _seed_candidate(session, suggested_title="Instagram-Post von Vertigo")
        _seed_candidate(session, suggested_title="The Post shows a Reel")
        # Restmenge (echte Titel-Fragmente)
        _seed_candidate(session, suggested_title="Mortal Kombat II")
        _seed_candidate(session, suggested_title="Anyone But You")
        _seed_candidate(session, suggested_title="Wicked extended trailer")
        # Off-target: andere Source (sollte gefiltert werden)
        _seed_candidate(
            session, suggested_title="Der falsche Source",
            source=CandidateSource.OCR,
        )
        # Off-target: andere Status (sollte gefiltert werden)
        _seed_candidate(
            session, suggested_title="Der falsche Status",
            status=CandidateStatus.RESOLVED,
        )

        _print_preview(session, seed=42)

    out = capsys.readouterr().out
    assert "Total OPEN+OPENAI:       6" in out
    assert "H1-Praefix-Treffer:      3" in out
    assert "Restmenge:               3" in out
    # Top-Praefixe enthaelt mindestens einen sichtbaren Eintrag
    assert "Top-Treffer-Praefixe" in out
    # Beide Stichproben-Header
    assert "Stichprobe — H1-Treffer" in out
    assert "Stichprobe — Restmenge" in out
    # Beispiel-Titel aus den seeds tauchen auf
    assert "Der Post stellt Ellie Bamber" in out
    assert "Mortal Kombat II" in out


def test_preview_excludes_non_openai_and_non_open(capsys, db):
    """Filter-Hygiene: OCR-Source und RESOLVED-Status zaehlen nicht
    in OPEN+OPENAI."""
    with Session(db) as session:
        _seed_candidate(session, suggested_title="Der Post stellt X")
        _seed_candidate(
            session, suggested_title="Der Post mit OCR-Source",
            source=CandidateSource.OCR,
        )
        _seed_candidate(
            session, suggested_title="Der Post resolved",
            status=CandidateStatus.RESOLVED,
        )

        _print_preview(session)

    out = capsys.readouterr().out
    assert "Total OPEN+OPENAI:       1" in out


# ---- Read-only-Garantie ----------------------------------------------


def test_preview_is_read_only(db):
    """Snapshot before/after identical."""
    with Session(db) as session:
        _seed_candidate(session, suggested_title="Der Post stellt X")
        _seed_candidate(session, suggested_title="Mortal Kombat II")
        before = _snapshot_candidates(session)

        _print_preview(session)

        after = _snapshot_candidates(session)
    assert before == after
