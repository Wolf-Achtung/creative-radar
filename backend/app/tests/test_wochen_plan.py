"""Wochen-Plan (Roadmap-Ausbau 2, 25.08.2026).

Aus dem Wochen-Bericht wird ein Plan. Die Tests sichern die
Kompositions-Entscheidungen:

1. Passt/Lieber-nicht kommt aus den Phasen-Mustern des Projekts —
   over wird Vorschlag, under wird Warnung, nichts wird vertauscht.
2. "Liegen geblieben" zaehlt NUR die juengste ABGESCHLOSSENE
   Messwoche und NUR nicht umgesetzte Empfehlungen — eine laufende
   Folgewoche ist kein Versaeumnis, eine umgesetzte Empfehlung auch
   nicht.
3. Ohne Wir-Projekte kommt die Countdown-Note durch, keine leeren
   Listen.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import (
    Channel,
    Market,
    Post,
    RecommendationSnapshot,
    Title,
)
from app.services.wochen_plan import compute_wochen_plan

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session(engine) -> Session:
    with Session(engine) as s:
        yield s


@pytest.fixture
def client(engine, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "user_auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "admin_auth_enabled", False, raising=False)

    def _override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _channel(session: Session, *, own: bool = False) -> Channel:
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}",
        platform="tiktok",
        url=f"https://x.test/{uuid4()}",
        market=Market.DE,
        is_own=own,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


def _post(
    session: Session,
    channel: Channel,
    *,
    likes: int = 100,
    days_ago: float = 1,
    analysis: dict | None = None,
) -> Post:
    p = Post(
        channel_id=channel.id,
        platform=channel.platform,
        post_url=f"https://x.test/p/{uuid4()}",
        caption="x",
        detected_at=NOW - timedelta(days=days_ago),
        visible_views=1000,
        visible_likes=likes,
        visible_comments=0,
        visible_bookmarks=0,
        raw_payload={},
        analysis=analysis,
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _projekt(session: Session, *, release_in_tagen: int = 63) -> Title:
    t = Title(
        title_original="Unser Film",
        release_date_de=(NOW + timedelta(days=release_in_tagen)).date(),
        is_own_project=True,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _phasen_korpus(session: Session) -> None:
    """Sechs Kanaele: Baseline plus je zwei humorvolle Pre-Launch-
    Breakouts und drei spannungsgetriebene Pre-Launch-Posts ohne
    Breakout — genug Masse, dass in pre_launch eine over- UND eine
    under-Zelle den z-Test bestehen."""
    for _ in range(6):
        kanal = _channel(session)
        for _ in range(4):
            _post(session, kanal)
        for _ in range(2):
            _post(
                session, kanal, likes=400,
                analysis={
                    "tone": "humorous", "lifecycle_stage": "pre_launch",
                    "confidence": 0.9,
                },
            )
        for _ in range(3):
            _post(
                session, kanal, likes=100,
                analysis={
                    "tone": "suspenseful", "lifecycle_stage": "pre_launch",
                    "confidence": 0.9,
                },
            )


def test_ohne_wir_projekte_kommt_die_note_durch(session: Session):
    ergebnis = compute_wochen_plan(session, now=NOW)
    assert "Wir-Projekt" in ergebnis["note"]
    assert ergebnis["projekte"] == []


def test_passt_und_lieber_nicht_aus_den_phasen_mustern(session: Session):
    _phasen_korpus(session)
    _projekt(session)
    ergebnis = compute_wochen_plan(session, now=NOW)
    projekt = ergebnis["projekte"][0]
    assert projekt["phase"] == "pre_launch"
    assert any("Humorvoll" in satz for satz in projekt["passt"]), projekt
    assert all("funktioniert in dieser Phase gerade." in s for s in projekt["passt"])
    assert any("Spannungsgetrieben" in satz for satz in projekt["lieber_nicht"]), projekt
    # Nichts vertauscht: der Breakout-Wert steht nie unter "lieber nicht".
    assert not any("Humorvoll" in satz for satz in projekt["lieber_nicht"])
    assert "Vergleichbare Kampagnen" in projekt["timing"] or "Release in" in projekt["timing"]


def test_liegengeblieben_nur_abgeschlossene_woche_und_nicht_umgesetzt(session: Session):
    _projekt(session)
    kanal = _channel(session, own=True)
    for i in range(4):
        _post(session, kanal, likes=(i + 1) * 100, days_ago=30 + i)
    # Snapshot W33: Folgewoche (17.-23.08.) ist am 25.08. abgeschlossen.
    # 'humorous' wurde umgesetzt (eigener Post in der Folgewoche),
    # 'mit_frage' nicht.
    session.add(RecommendationSnapshot(
        iso_year=2026, iso_week=33, window_days=90,
        cells=[
            {"dimension": "tone", "value": "humorous", "median_lift": 1.5},
            {"dimension": "caption_frage", "value": "mit_frage", "median_lift": 1.4},
        ],
    ))
    # Snapshot W34: Folgewoche laeuft noch — zaehlt nicht.
    session.add(RecommendationSnapshot(
        iso_year=2026, iso_week=34, window_days=90,
        cells=[{"dimension": "tone", "value": "edgy", "median_lift": 1.3}],
    ))
    session.commit()
    _post(
        session, kanal, likes=400, days_ago=6,  # 19.08. → Folgewoche von W33
        analysis={"tone": "humorous", "confidence": 0.9},
    )

    ergebnis = compute_wochen_plan(session, now=NOW)
    saetze = [z["satz"] for z in ergebnis["liegengeblieben"]]
    assert any("Caption mit Frage" in s and "2026-W33" in s for s in saetze), saetze
    assert not any("Humorvoll" in s for s in saetze), "umgesetzt zaehlt nicht"
    assert not any("Edgy" in s for s in saetze), "laufende Woche zaehlt nicht"


# ---------- Endpoint ---------------------------------------------------


def test_endpoint_ist_ohne_flag_aus(client, monkeypatch):
    monkeypatch.delenv("FEATURE_WOCHEN_PLAN_ENABLED", raising=False)
    antwort = client.get("/api/admin/wochen-plan")
    assert antwort.status_code == 503
    assert "FEATURE_WOCHEN_PLAN_ENABLED" in antwort.json()["detail"]


def test_endpoint_liefert_den_plan(client, session, monkeypatch):
    monkeypatch.setenv("FEATURE_WOCHEN_PLAN_ENABLED", "true")
    _projekt(session)
    antwort = client.get("/api/admin/wochen-plan")
    assert antwort.status_code == 200
    assert antwort.json()["projekte"][0]["titel"] == "Unser Film"
