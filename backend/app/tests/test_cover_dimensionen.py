"""Hook-Intelligence Teil 2 (20.08.2026) — Cover-Dimensionen aus der
persistierten Vision-Analyse.

Die OpenAI-Vision-Stufe extrahiert ``has_title_placement`` /
``has_kinetic`` / ``kinetic_type`` laengst strukturiert je Asset —
dieser Durchgang macht sie erstmals als Muster-Dimensionen sichtbar.
Kein neuer LLM-Call: die Extraktoren lesen nur, was schon in der
Datenbank liegt.

Die zwei Wachsamkeits-Regeln, die hier getestet werden:
- Selbst-Gate ueber ``visual_confidence_score`` >= 0.7 — Heuristik-
  Zeilen (Score ~0.35, kein echter Vision-Call) duerfen keine Zelle
  fuellen.
- ``has_kinetic=True`` ohne brauchbaren ``kinetic_type`` ist
  widerspruechlich → keine Zelle statt geratenem Wert.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Asset, Channel, Market, Post
from app.services import trailer_patterns as tp

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def session():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        with Session(eng) as s:
            yield s
    finally:
        eng.dispose()


def _kanal(session: Session) -> Channel:
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}",
        platform="tiktok",
        url=f"https://x.test/{uuid4()}",
        market=Market.US,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


def _post(session: Session, kanal: Channel) -> Post:
    p = Post(
        channel_id=kanal.id,
        platform=kanal.platform,
        post_url=f"https://x.test/p/{uuid4()}",
        caption="x",
        detected_at=NOW - timedelta(days=1),
        visible_views=1000,
        visible_likes=100,
        visible_comments=0,
        visible_bookmarks=0,
        raw_payload={},
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _asset(
    session: Session,
    post: Post,
    *,
    status: str = "analyzed",
    score: float = 0.9,
    titel: bool = True,
    kinetic: bool = False,
    kinetic_type: str | None = None,
) -> Asset:
    a = Asset(
        post_id=post.id,
        visual_analysis_status=status,
        visual_confidence_score=score,
        has_title_placement=titel,
        has_kinetic=kinetic,
        kinetic_type=kinetic_type,
    )
    session.add(a)
    session.commit()
    return a


def _zellen(session: Session, name: str) -> dict[str, int]:
    report = tp.compute_trailer_patterns(session, window_days=30, now=NOW)
    return {c.value: c.sample_size for c in report.dimensions.get(name, [])}


def test_cover_titel_liest_die_persistierte_vision_analyse(session):
    kanal = _kanal(session)
    posts = [_post(session, kanal) for _ in range(6)]
    for p in posts[:4]:
        _asset(session, p, titel=True)
    for p in posts[4:]:
        _asset(session, p, titel=False)

    assert _zellen(session, "cover_titel") == {"mit_titel": 4, "ohne_titel": 2}


def test_niedrige_vision_konfidenz_fuellt_keine_zelle(session):
    """Heuristik-Zeilen (Score ~0.35) stammen aus dem Fallback ohne
    echten Vision-Call — sie duerfen die Cover-Zellen nicht fuellen."""
    kanal = _kanal(session)
    posts = [_post(session, kanal) for _ in range(6)]
    for p in posts[:3]:
        _asset(session, p, titel=True, score=0.9)
    for p in posts[3:]:
        _asset(session, p, titel=True, score=0.35)

    assert _zellen(session, "cover_titel") == {"mit_titel": 3}


def test_nicht_analysierte_assets_zaehlen_nicht(session):
    kanal = _kanal(session)
    posts = [_post(session, kanal) for _ in range(6)]
    _asset(session, posts[0], titel=True, status="pending")
    _asset(session, posts[1], titel=True, status="fetch_failed")

    assert _zellen(session, "cover_titel") == {}


def test_kinetik_werte_und_widerspruch(session):
    kanal = _kanal(session)
    posts = [_post(session, kanal) for _ in range(6)]
    _asset(session, posts[0], kinetic=True, kinetic_type="title_card")
    _asset(session, posts[1], kinetic=True, kinetic_type="text_overlay")
    _asset(session, posts[2], kinetic=False)
    # has_kinetic=True, aber kein brauchbarer Typ → keine Zelle.
    _asset(session, posts[3], kinetic=True, kinetic_type="unknown")
    _asset(session, posts[4], kinetic=True, kinetic_type=None)

    assert _zellen(session, "cover_kinetik") == {
        "title_card": 1,
        "text_overlay": 1,
        "ohne_kinetik": 1,
    }


def test_niedrige_cover_abdeckung_steht_als_note_im_bericht(session):
    kanal = _kanal(session)
    posts = [_post(session, kanal) for _ in range(6)]
    _asset(session, posts[0], titel=True)

    report = tp.compute_trailer_patterns(session, window_days=30, now=NOW)

    assert any("Cover-Merkmale" in note for note in report.notes), (
        "Bei 1/6 Cover-Abdeckung muss der Bericht das ausweisen — die "
        "Cover-Zellen beschreiben sonst unbemerkt nur einen Ausschnitt."
    )


def test_beispiel_posts_folgen_derselben_zugehoerigkeit(session):
    """``posts_for_cell`` muss fuer Cover-Dimensionen dieselben Regeln
    anwenden wie die Zellen-Zaehlung — inklusive Konfidenz-Gate. Sonst
    zeigt der Beispiel-Endpoint Posts, die in der Zelle nicht stecken."""
    kanal = _kanal(session)
    posts = [_post(session, kanal) for _ in range(6)]
    for p in posts[:2]:
        _asset(session, p, titel=True, score=0.9)
    _asset(session, posts[2], titel=True, score=0.35)

    ctx = tp.build_lift_context(session, window_days=30, now=NOW)
    members = tp.posts_for_cell(session, ctx, "cover_titel", "mit_titel")

    assert {p.id for p in members} == {p.id for p in posts[:2]}
