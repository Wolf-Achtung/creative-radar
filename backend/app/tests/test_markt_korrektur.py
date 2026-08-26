"""Markt-Korrektur der Muster-Statistik (26.08.2026).

Anlass (Wolf): "macht es Sinn, Posts aus voellig unterschiedlichen
Maerkten, Kanaelen und Zielgruppen miteinander zu vergleichen?"
Kanalgroesse und Plattform waren schon herausgerechnet — der Markt
nicht. Ein Merkmal, dessen Posts zufaellig in einem Markt mit hoher
Grundquote liegen, sah damit besser aus, als es ist.

Drei Zusagen, jede mit eigenem Test:

1. Reine Markt-Zusammensetzung ist kein Muster mehr — die erwartete
   Quote kommt je Post aus seinem (Plattform, Markt)-Stratum.
2. Ein echter Effekt innerhalb eines Marktes spricht weiter an
   (die Korrektur senkt nicht einfach die Empfindlichkeit).
3. Jede belastbare over/under-Zelle traegt einen Klartext-Satz,
   welche Maerkte den Befund tragen (``market_note``) — der Bericht
   mischt die Maerkte weiter bewusst, aber sichtbar.

Dazu: der ``market``-Parameter an ``GET /api/insights/patterns`` und
``.../patterns/examples`` (Schritt 3 — Markt-Filter im Panel).
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
from app.models.entities import Channel, Market, Post
from app.services import trailer_patterns as tp

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


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
    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")

    def _override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _channel(
    session: Session, *, market: Market = Market.US, platform: str = "instagram"
) -> Channel:
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}",
        platform=platform,
        url=f"https://x.test/{uuid4()}",
        market=market,
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
    return p


def _analysis(fmt: str) -> dict:
    return {"format": fmt, "confidence": 0.9}


def _cell(report: tp.TrailerPatternReport, dim: str, value: str) -> tp.PatternCell:
    for c in report.dimensions[dim]:
        if c.value == value:
            return c
    raise AssertionError(f"Zelle {dim}={value} fehlt: {report.dimensions[dim]}")


def _skewed_markets(session: Session) -> None:
    """Eine Plattform, zwei Maerkte mit stark unterschiedlicher Quote.

    Spiegel von ``_skewed_platforms`` aus test_trailer_patterns.py, nur
    entlang der Markt-Achse: US 3 Kanaele x 30 Posts mit 40 % Treffern
    (haelftig format_a/format_b), DE 3 Kanaele x 30 Posts mit 1/30 —
    alles format_b.
    """
    for _ in range(3):
        ch = _channel(session, market=Market.US)
        for fmt in ("format_a", "format_b"):
            for _ in range(9):
                _post(session, ch, likes=100, analysis=_analysis(fmt))
            for _ in range(6):
                _post(session, ch, likes=250, analysis=_analysis(fmt))
    for _ in range(3):
        ch = _channel(session, market=Market.DE)
        for _ in range(29):
            _post(session, ch, likes=100, analysis=_analysis("format_b"))
        _post(session, ch, likes=250, analysis=_analysis("format_b"))


# ---------- 1. Markt-Zusammensetzung allein ist kein Muster --------------


def test_market_composition_alone_is_not_a_pattern(session: Session):
    """``format_a`` liegt ausschliesslich im US-Markt und hat dort exakt
    die uebliche US-Trefferquote — inhaltlich kein Befund. Gegen die
    reine Plattform-Quote gemessen (alles Instagram) saehe es nach einem
    starken Muster aus; gegen das eigene (Plattform, Markt)-Stratum
    faellt es korrekt auf neutral."""
    _skewed_markets(session)
    report = tp.compute_trailer_patterns(session, now=NOW)
    a = _cell(report, "format", "format_a")

    assert a.market_mix == {"US": 45}
    assert a.breakout_rate == pytest.approx(0.4)
    assert a.expected_breakout_rate == pytest.approx(0.4)
    assert a.breakout_verdict == "neutral"

    # Gegenprobe: mit der Plattform-Quote als Referenz (der Stand vor
    # der Markt-Korrektur) waere dieselbe Zelle ein klarer Befund.
    platform_rate = 39 / 180  # US 36 + DE 3 Treffer auf 180 Instagram-Posts
    naive_z = tp._breakout_z(a.breakout_rate, platform_rate, a.sample_size)
    assert naive_z is not None and naive_z > tp.BREAKOUT_Z_THRESHOLD


def test_real_effect_survives_the_market_correction(session: Session):
    """Gegenprobe: ein Merkmal, das *innerhalb* seines Marktes deutlich
    ueber der Stratum-Erwartung liegt, muss weiter ansprechen."""
    for _ in range(3):
        ch = _channel(session, market=Market.US)
        for _ in range(20):
            _post(session, ch, likes=100, analysis=_analysis("clip"))
        for _ in range(2):
            _post(session, ch, likes=100, analysis=_analysis("trailer"))
        for _ in range(8):
            _post(session, ch, likes=250, analysis=_analysis("trailer"))
    for _ in range(3):
        ch = _channel(session, market=Market.DE)
        for _ in range(30):
            _post(session, ch, likes=100, analysis=_analysis("clip"))

    report = tp.compute_trailer_patterns(session, now=NOW)
    trailer = _cell(report, "format", "trailer")

    assert trailer.breakout_rate == pytest.approx(0.8)
    # Erwartung ist die (instagram, US)-Stratum-Quote, nicht die
    # verduennte Plattform-Quote ueber beide Maerkte.
    assert trailer.expected_breakout_rate == pytest.approx(24 / 90)
    assert trailer.breakout_verdict == "over"


def test_thin_stratum_falls_back_to_the_platform_rate(session: Session):
    """Ein (Plattform, Markt)-Stratum unter der Mindest-Postzahl bekommt
    keine eigene Quote — sonst wuerde jede Zelle darin gegen sich selbst
    geprueft. Es faellt auf die Plattform-Quote zurueck, nicht auf die
    Korpus-Quote (die hier durch TikTok verzerrt waere)."""
    for _ in range(3):
        ch = _channel(session, market=Market.US)
        for _ in range(18):
            _post(session, ch, likes=100, analysis=_analysis("clip"))
        for _ in range(12):
            _post(session, ch, likes=250, analysis=_analysis("clip"))
    # DE auf Instagram: nur 10 Posts — unter MIN_POSTS_PER_PLATFORM_BASELINE.
    de = _channel(session, market=Market.DE)
    for _ in range(10):
        _post(session, de, likes=100, analysis=_analysis("teaser"))
    # Zweite Plattform, damit Korpus- und Plattform-Quote auseinanderliegen.
    for _ in range(3):
        ch = _channel(session, market=Market.US, platform="tiktok")
        for _ in range(30):
            _post(session, ch, likes=100, analysis=_analysis("clip"))

    report = tp.compute_trailer_patterns(session, now=NOW)
    teaser = _cell(report, "format", "teaser")

    instagram_rate = 36 / 100
    assert teaser.expected_breakout_rate == pytest.approx(instagram_rate)
    assert teaser.expected_breakout_rate != pytest.approx(
        report.baseline_breakout_rate
    )


# ---------- 2. Markt-Ausweis an der Zelle --------------------------------


def _effekt_in(session: Session, market: Market, *, trailer_posts: int = 10) -> None:
    """3 Kanaele im Markt: clips normal, Trailer klar ueber Schnitt."""
    for _ in range(3):
        ch = _channel(session, market=market)
        for _ in range(20):
            _post(session, ch, likes=100, analysis=_analysis("clip"))
        breakouts = int(trailer_posts * 0.8)
        for _ in range(trailer_posts - breakouts):
            _post(session, ch, likes=100, analysis=_analysis("trailer"))
        for _ in range(breakouts):
            _post(session, ch, likes=250, analysis=_analysis("trailer"))


def test_market_note_names_all_carrying_markets(session: Session):
    """Traegt der Befund in beiden Maerkten, sagt der Satz das — in der
    Produkt-Reihenfolge DE vor US."""
    _effekt_in(session, Market.US)
    _effekt_in(session, Market.DE)

    report = tp.compute_trailer_patterns(session, now=NOW)
    trailer = _cell(report, "format", "trailer")

    assert trailer.breakout_verdict == "over"
    assert trailer.market_mix == {"DE": 30, "US": 30}
    assert trailer.market_note == "Gilt in DE und US."
    assert trailer.to_dict()["market_note"] == "Gilt in DE und US."


def test_market_note_names_the_carrying_market(session: Session):
    """Traegt nur ein Markt und der andere ist zu duenn fuer eine eigene
    Aussage, steht genau das im Satz."""
    _effekt_in(session, Market.US)
    # DE: ein Kanal, 4 Trailer-Posts (unter MIN_SAMPLE_PER_CELL) plus
    # Grundrauschen fuer die Kanal-Baseline.
    de = _channel(session, market=Market.DE)
    for _ in range(16):
        _post(session, de, likes=100, analysis=_analysis("clip"))
    for _ in range(4):
        _post(session, de, likes=100, analysis=_analysis("trailer"))

    report = tp.compute_trailer_patterns(session, now=NOW)
    trailer = _cell(report, "format", "trailer")

    assert trailer.breakout_verdict == "over"
    assert trailer.market_note == "Gilt vor allem in US — für DE zu wenig Daten."


def test_single_market_cell_has_no_note(session: Session):
    """Nur ein Markt in der Zelle: nichts zu unterscheiden, kein Satz."""
    _effekt_in(session, Market.US)

    report = tp.compute_trailer_patterns(session, now=NOW)
    trailer = _cell(report, "format", "trailer")

    assert trailer.breakout_verdict == "over"
    assert trailer.market_mix == {"US": 30}
    assert trailer.market_note is None
    assert "market_note" not in trailer.to_dict()


def test_neutral_cell_has_no_note(session: Session):
    """Der Satz haengt am Befund — eine neutrale Zelle traegt keinen."""
    _skewed_markets(session)
    report = tp.compute_trailer_patterns(session, now=NOW)
    a = _cell(report, "format", "format_a")
    assert a.breakout_verdict == "neutral"
    assert a.market_note is None


def test_markt_liste_ordnung():
    assert tp._markt_liste(["US", "DE"]) == "DE und US"
    assert tp._markt_liste(["UK", "US", "DE"]) == "DE, US und UK"
    assert tp._markt_liste(["INT", "DE"]) == "DE und INT"
    assert tp._markt_liste(["US"]) == "US"


# ---------- 3. Markt-Filter an den Endpoints -----------------------------


def test_patterns_endpoint_filtert_auf_den_markt(client, session):
    """?market=DE rechnet nur ueber DE-Kanaele — der US-Effekt darf den
    gefilterten Bericht nicht erreichen."""
    _effekt_in(session, Market.US)
    de = _channel(session, market=Market.DE)
    for _ in range(20):
        _post(session, de, likes=100, analysis=_analysis("clip"))

    alles = client.get("/api/insights/patterns").json()
    nur_de = client.get("/api/insights/patterns", params={"market": "DE"}).json()

    assert alles["market"] is None
    assert nur_de["market"] == "DE"
    werte = [c["value"] for c in nur_de["dimensions"].get("format", [])]
    assert "trailer" not in werte
    assert nur_de["posts_with_baseline"] == 20


def test_examples_endpoint_respektiert_den_markt(client, session):
    """Die Beispiele zu einer gefilterten Zelle duerfen nur Posts aus
    dem gefilterten Markt zeigen — sonst belegen sie eine andere Zahl
    als die, die daneben steht."""
    _effekt_in(session, Market.US)
    _effekt_in(session, Market.DE)

    r = client.get(
        "/api/insights/patterns/examples",
        params={"dimension": "format", "value": "trailer", "market": "DE"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["cell_size"] == 30
    ohne_filter = client.get(
        "/api/insights/patterns/examples",
        params={"dimension": "format", "value": "trailer"},
    ).json()
    assert ohne_filter["cell_size"] == 60
