"""Projekt-One-Pager (Roadmap Schritt 4, 25.08.2026).

Der Export ist reine Komposition: Start-Brief, Countdown und Beweis
rechnen, das Modul rendert. Die Tests sichern die Render-Zusagen:

1. Alles Dynamische laeuft durch html.escape — ein Titel mit Markup
   darf im Dokument nie als Markup ankommen.
2. Sektionen erscheinen nur, wenn ihre Daten existieren: kein
   Countdown ohne Release-Datum, kein Beweis-Absatz ohne umgesetzte
   Empfehlung.
3. Bilder referenzieren den oeffentlichen Thumbnail-Proxy ABSOLUT
   (api_base) — die Datei wird ausserhalb des Dashboards geoeffnet,
   relative Pfade liefen ins Leere.
4. Der Dateiname ist ein gefahrloser ASCII-Slug fuer
   Content-Disposition.
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
from app.models.entities import Asset, Channel, Market, Post, Title
from app.services.projekt_export import dateiname_fuer, render_projekt_one_pager

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


def _channel(session: Session) -> Channel:
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}",
        handle=f"handle-{uuid4().hex[:6]}",
        platform="tiktok",
        url=f"https://x.test/{uuid4()}",
        market=Market.DE,
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
    analysis: dict | None = None,
) -> Post:
    p = Post(
        channel_id=channel.id,
        platform=channel.platform,
        post_url=f"https://x.test/p/{uuid4()}",
        caption="x",
        detected_at=NOW - timedelta(days=1),
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


def _projekt(
    session: Session, *, name: str = "Unser Film", release: bool = True
) -> Title:
    t = Title(
        title_original=name,
        genres=["Horror"],
        release_date_de=(NOW + timedelta(days=63)).date() if release else None,
        is_own_project=True,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _breakout_zelle(session: Session) -> None:
    """Sechs Kanaele mit Baseline plus je einem Humor-Breakout — ergibt
    eine belastbare over-Zelle (tone=humorous) mit Beispiel-Posts."""
    for _ in range(6):
        kanal = _channel(session)
        for _ in range(4):
            _post(session, kanal, likes=100)
        breakout = _post(
            session, kanal, likes=400,
            analysis={"tone": "humorous", "confidence": 0.9},
        )
        session.add(Asset(post_id=breakout.id, thumbnail_url="https://cdn.test/b.jpg"))
        session.commit()


def test_dateiname_ist_ein_gefahrloser_slug():
    assert dateiname_fuer("Steckerlfisch Fiasko") == "one-pager-steckerlfisch-fiasko.html"
    assert dateiname_fuer('Böse "Datei"/\\Name?') == "one-pager-b-se-datei-name.html"
    assert dateiname_fuer("???") == "one-pager-projekt.html"


def test_unbekannter_titel_wirft(session: Session):
    with pytest.raises(ValueError):
        render_projekt_one_pager(session, uuid4(), api_base="https://api.test/", now=NOW)


def test_dynamisches_wird_escaped(session: Session):
    projekt = _projekt(session, name='<script>alert("x")</script>')
    html, _ = render_projekt_one_pager(
        session, projekt.id, api_base="https://api.test/", now=NOW
    )
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_sektionen_nur_mit_daten(session: Session):
    ohne_release = _projekt(session, release=False)
    html, _ = render_projekt_one_pager(
        session, ohne_release.id, api_base="https://api.test/", now=NOW
    )
    assert "Wo die Kampagne steht" not in html
    assert "bewiesen" not in html  # kein Beweis-Absatz ohne Messwochen

    mit_release = _projekt(session, name="Zweiter Film")
    html, _ = render_projekt_one_pager(
        session, mit_release.id, api_base="https://api.test/", now=NOW
    )
    assert "Wo die Kampagne steht" in html
    assert "Pre-Launch" in html
    assert "Release in 9 Wochen" in html


def test_empfehlungen_mit_absoluten_bild_urls(session: Session):
    _breakout_zelle(session)
    projekt = _projekt(session)
    html, dateiname = render_projekt_one_pager(
        session, projekt.id, api_base="https://api.test/", now=NOW
    )
    assert "Was im Markt gerade überperformt" in html
    assert "humorous" in html
    assert 'src="https://api.test/api/thumbnails/' in html, (
        "Bilder muessen den Proxy ABSOLUT referenzieren — die Datei "
        "wird ausserhalb des Dashboards geoeffnet."
    )
    assert dateiname == "one-pager-unser-film.html"


# ---------- Endpoint ---------------------------------------------------


def test_endpoint_ist_ohne_flag_aus(client, monkeypatch):
    monkeypatch.delenv("FEATURE_PROJEKT_EXPORT_ENABLED", raising=False)
    antwort = client.get(f"/api/admin/projekt-export/{uuid4()}")
    assert antwort.status_code == 503
    assert "FEATURE_PROJEKT_EXPORT_ENABLED" in antwort.json()["detail"]


def test_endpoint_unbekannter_titel_404(client, monkeypatch):
    monkeypatch.setenv("FEATURE_PROJEKT_EXPORT_ENABLED", "true")
    antwort = client.get(f"/api/admin/projekt-export/{uuid4()}")
    assert antwort.status_code == 404


def test_endpoint_liefert_html_als_download(client, session, monkeypatch):
    monkeypatch.setenv("FEATURE_PROJEKT_EXPORT_ENABLED", "true")
    projekt = _projekt(session)
    antwort = client.get(f"/api/admin/projekt-export/{projekt.id}")
    assert antwort.status_code == 200
    assert antwort.headers["content-type"].startswith("text/html")
    assert (
        antwort.headers["content-disposition"]
        == 'attachment; filename="one-pager-unser-film.html"'
    )
    assert "Unser Film" in antwort.text
