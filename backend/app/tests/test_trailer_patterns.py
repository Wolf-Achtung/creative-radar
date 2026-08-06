"""Korpusweite Muster-Aggregation (Trailer-Intelligence Stufe 1, Schritt 2).

Der Kern dieses Moduls ist nicht das Zaehlen, sondern die Methodik. Die
Tests sichern genau die Entscheidungen, die ein falsches Ergebnis
produzieren wuerden, wenn man sie anders traefe:

1. Kanal-Normierung — ein grosser Kanal darf ein Muster nicht allein
   durch seine Groesse erzeugen.
2. Mindest-Kanalzahl — ein Vielposter darf es auch nicht durch Menge.
3. Konfidenz-Filter nur auf modell-erzeugten Dimensionen.
4. Zellen unter der Schwelle verschwinden nicht, sie werden als
   ``insufficient`` gemeldet.
5. Musik-Extraktion ist defensiv gegen unklare Apify-Feldtypen.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Channel, Market, Post
from app.services import trailer_patterns as tp


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _channel(session: Session, *, market: Market = Market.US, platform: str = "tiktok") -> Channel:
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
    views: int = 1000,
    likes: int = 100,
    duration: int | None = None,
    analysis: dict | None = None,
    raw_payload: dict | None = None,
    days_ago: int = 1,
) -> Post:
    p = Post(
        channel_id=channel.id,
        platform=channel.platform,
        post_url=f"https://x.test/p/{uuid4()}",
        caption="x",
        detected_at=NOW - timedelta(days=days_ago),
        visible_views=views,
        visible_likes=likes,
        visible_comments=0,
        visible_bookmarks=0,
        duration_seconds=duration,
        analysis=analysis,
        raw_payload=raw_payload or {},
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _analysis(fmt: str = "trailer", *, confidence: float = 0.9, tone: str = "suspenseful") -> dict:
    return {
        "format": fmt,
        "tone": tone,
        "purpose": "release_week",
        "lifecycle_stage": "launch",
        "confidence": confidence,
    }


def _cell(report: tp.TrailerPatternReport, dim: str, value: str) -> tp.PatternCell:
    for c in report.dimensions[dim]:
        if c.value == value:
            return c
    raise AssertionError(f"Zelle {dim}={value} fehlt: {report.dimensions[dim]}")


# ---------- Kanal-Normierung -------------------------------------------


def test_channel_normalisation_removes_channel_size_effect(session: Session):
    """Der eigentliche Zweck des Moduls.

    Zwei Kanaele, 100-fach unterschiedliche Reichweite, aber identisches
    *relatives* Muster: Trailer laufen im eigenen Kanal doppelt so gut
    wie der Rest. Ohne Normierung wuerde der grosse Kanal das Ergebnis
    diktieren; mit Normierung muss bei beiden derselbe Lift herauskommen.
    """
    # Drei Kanaele mit 100-fach gestaffelter Reichweite, aber identischen
    # Raten: Grundrauschen 10 %, Trailer 20 %. Trailer sind bewusst die
    # Minderheit (3 von 9) — siehe test_dominant_format_defines_its_own_baseline.
    for views, likes_base in ((1_000_000, 100_000), (10_000, 1_000), (50_000, 5_000)):
        ch = _channel(session)
        for _ in range(6):
            _post(session, ch, views=views, likes=likes_base, analysis=_analysis("clip"))
        for _ in range(3):
            _post(session, ch, views=views, likes=likes_base * 2, analysis=_analysis("trailer"))

    report = tp.compute_trailer_patterns(session, now=NOW)

    trailer = _cell(report, "format", "trailer")
    clip = _cell(report, "format", "clip")

    # Der Lift ist in allen drei Kanaelen derselbe, obwohl die absolute
    # Reichweite um Faktor 100 auseinanderliegt.
    assert trailer.median_lift == pytest.approx(2.0)
    assert clip.median_lift == pytest.approx(1.0)
    assert trailer.verdict == "over"
    assert clip.verdict == "neutral"
    assert trailer.channel_count == 3


def test_dominant_format_defines_its_own_baseline(session: Session):
    """Interpretationsfalle, bewusst festgehalten.

    Der Lift misst gegen den Median des Kanals — also gegen dessen
    eigenen Output-Mix. Macht ein Format die Mehrheit der Posts eines
    Kanals aus, bestimmt es den Median selbst und kann rechnerisch nicht
    darueber liegen. Das Signal erscheint dann gespiegelt: nicht
    "Trailer over", sondern "der Rest under".

    Das ist kein Bug, sondern die Definition von Median-Normierung. Wer
    die Ausgabe liest, muss es wissen — deshalb ein eigener Test statt
    nur ein Kommentar.
    """
    for _ in range(3):
        ch = _channel(session)
        # Trailer sind hier die Mehrheit (5 von 9).
        for _ in range(4):
            _post(session, ch, views=1000, likes=100, analysis=_analysis("clip"))
        for _ in range(5):
            _post(session, ch, views=1000, likes=200, analysis=_analysis("trailer"))

    report = tp.compute_trailer_patterns(session, now=NOW)

    trailer = _cell(report, "format", "trailer")
    clip = _cell(report, "format", "clip")

    assert trailer.median_lift == pytest.approx(1.0)
    assert trailer.verdict == "neutral"
    # Dasselbe Verhaeltnis, nur andersherum sichtbar.
    assert clip.median_lift == pytest.approx(0.5)
    assert clip.verdict == "under"


def test_absolute_reach_does_not_drive_the_verdict(session: Session):
    """Gegenprobe: ein Kanal mit riesiger Reichweite, dessen Trailer im
    eigenen Vergleich UNTERdurchschnittlich sind, darf kein 'over'
    erzeugen."""
    channels = [_channel(session) for _ in range(3)]
    for ch in channels:
        # Baseline hoch (20 %), Trailer schwach (5 %) — trotz Millionen Views.
        for _ in range(5):
            _post(session, ch, views=5_000_000, likes=1_000_000, analysis=_analysis("clip"))
        for _ in range(5):
            _post(session, ch, views=5_000_000, likes=250_000, analysis=_analysis("trailer"))

    report = tp.compute_trailer_patterns(session, now=NOW)
    trailer = _cell(report, "format", "trailer")

    assert trailer.median_lift < 1.0
    assert trailer.verdict == "under"


# ---------- Ehrlichkeits-Regeln ----------------------------------------


def test_single_prolific_channel_cannot_create_a_pattern(session: Session):
    """Die korpusweit neue Regel: Stichprobe gross genug, aber alles aus
    einem Kanal -> kein Muster, sondern ein Hinweis auf die Luecke."""
    solo = _channel(session)
    for _ in range(5):
        _post(session, solo, views=1000, likes=100, analysis=_analysis("clip"))
    for _ in range(20):
        _post(session, solo, views=1000, likes=900, analysis=_analysis("compilation"))

    # Zwei weitere Kanaele, damit ueberhaupt Baselines existieren.
    for _ in range(2):
        ch = _channel(session)
        for _ in range(5):
            _post(session, ch, views=1000, likes=100, analysis=_analysis("clip"))

    report = tp.compute_trailer_patterns(session, now=NOW)
    comp = _cell(report, "format", "compilation")

    assert comp.sample_size == 20  # Stichprobe waere gross genug
    assert comp.channel_count == 1
    assert comp.verdict == "insufficient"
    assert "Kanaele" in (comp.reason or "")


def test_thin_cells_are_reported_not_hidden(session: Session):
    """Eine Luecke ist ein Befund. Wer sie wegfiltert, laesst den Bestand
    besser aussehen, als er ist."""
    channels = [_channel(session) for _ in range(3)]
    for ch in channels:
        for _ in range(5):
            _post(session, ch, views=1000, likes=100, analysis=_analysis("clip"))
    # Genau zwei Teaser, verteilt auf zwei Kanaele.
    _post(session, channels[0], views=1000, likes=500, analysis=_analysis("teaser"))
    _post(session, channels[1], views=1000, likes=500, analysis=_analysis("teaser"))

    report = tp.compute_trailer_patterns(session, now=NOW)
    teaser = _cell(report, "format", "teaser")

    assert teaser.sample_size == 2
    assert teaser.verdict == "insufficient"
    assert "Minimum" in (teaser.reason or "")


def test_insufficient_cells_sort_after_solid_ones(session: Session):
    channels = [_channel(session) for _ in range(3)]
    for ch in channels:
        for _ in range(5):
            _post(session, ch, views=1000, likes=100, analysis=_analysis("clip"))
    _post(session, channels[0], views=1000, likes=900, analysis=_analysis("teaser"))

    report = tp.compute_trailer_patterns(session, now=NOW)
    verdicts = [c.verdict for c in report.dimensions["format"]]
    assert verdicts[-1] == "insufficient"


# ---------- Konfidenz-Filter greift dimensionsabhaengig -----------------


def test_low_confidence_excluded_from_model_dimensions(session: Session):
    channels = [_channel(session) for _ in range(3)]
    for ch in channels:
        for _ in range(5):
            _post(session, ch, views=1000, likes=100,
                  analysis=_analysis("clip", confidence=0.3), duration=20)

    report = tp.compute_trailer_patterns(session, now=NOW)

    # format ist modell-erzeugt -> alles rausgefiltert
    assert report.dimensions["format"] == []


def test_measured_dimensions_ignore_the_confidence_filter(session: Session):
    """Dauer ist gemessen, nicht klassifiziert. Sie durch den
    Konfidenz-Filter zu schicken wuerde bei niedriger
    Klassifikations-Abdeckung brauchbare Daten wegwerfen."""
    channels = [_channel(session) for _ in range(3)]
    for ch in channels:
        for _ in range(5):
            _post(session, ch, views=1000, likes=100,
                  analysis=_analysis("clip", confidence=0.3), duration=20)

    report = tp.compute_trailer_patterns(session, now=NOW)

    bucket = _cell(report, "duration_bucket", "15-30s")
    assert bucket.sample_size == 15
    assert bucket.verdict != "insufficient"


def test_unanalysed_posts_still_feed_duration(session: Session):
    """Posts ohne jede Analyse (analysis is None) duerfen die gemessenen
    Dimensionen nicht blockieren — das ist der Normalfall bei 12 %
    Klassifikations-Abdeckung."""
    channels = [_channel(session) for _ in range(3)]
    for ch in channels:
        for _ in range(5):
            _post(session, ch, views=1000, likes=100, analysis=None, duration=45)

    report = tp.compute_trailer_patterns(session, now=NOW)

    assert report.dimensions["format"] == []
    assert _cell(report, "duration_bucket", "30-60s").sample_size == 15
    assert report.analysis_coverage == 0.0


# ---------- Musik --------------------------------------------------------


@pytest.mark.parametrize(
    "raw_music,expected",
    [
        ({"musicOriginal": True}, "original_sound"),
        ({"musicOriginal": False}, "licensed_track"),
        ({"musicOriginal": "true"}, "original_sound"),
        ({"musicOriginal": "False"}, "licensed_track"),
        ({"musicOriginal": None}, "unknown"),
        ({"musicName": "Song"}, "unknown"),
    ],
)
def test_music_extraction_is_defensive(session: Session, raw_music, expected):
    """Apify sichert den Feldtyp nicht zu. Unklares wird ``unknown``
    statt geraten — eine falsch einsortierte Zelle waere schlimmer als
    eine fehlende."""
    ch = _channel(session)
    post = _post(session, ch, raw_payload={"_creative_radar_music": raw_music})
    assert tp._extract_music_kind(post) == expected


def test_music_absent_yields_no_cell(session: Session):
    ch = _channel(session)
    post = _post(session, ch, raw_payload={})
    assert tp._extract_music_kind(post) is None


# ---------- Baseline-Schutz ---------------------------------------------


def test_channels_with_too_few_posts_are_skipped(session: Session):
    """Ein Kanal mit zwei Posts hat keinen belastbaren Median. Sein Lift
    wuerde jede Zelle verzerren, in die er einfliesst."""
    thin = _channel(session)
    _post(session, thin, views=1000, likes=900, analysis=_analysis("trailer"))
    _post(session, thin, views=1000, likes=900, analysis=_analysis("trailer"))

    report = tp.compute_trailer_patterns(session, now=NOW)

    assert report.posts_with_baseline == 0
    assert any("Baseline" in n or "Kanaele" in n for n in report.notes)


def test_channel_without_views_does_not_divide_by_zero(session: Session):
    ch = _channel(session)
    for _ in range(5):
        _post(session, ch, views=0, likes=0, analysis=_analysis("clip"))

    report = tp.compute_trailer_patterns(session, now=NOW)
    assert report.posts_with_baseline == 0


# ---------- Fenster + Markt ---------------------------------------------


def test_window_excludes_older_posts(session: Session):
    ch = _channel(session)
    for _ in range(5):
        _post(session, ch, views=1000, likes=100, days_ago=5, analysis=_analysis("clip"))
    for _ in range(5):
        _post(session, ch, views=1000, likes=100, days_ago=200, analysis=_analysis("clip"))

    report = tp.compute_trailer_patterns(session, window_days=90, now=NOW)
    assert report.posts_in_window == 5


def test_market_filter_restricts_to_matching_channels(session: Session):
    de = _channel(session, market=Market.DE)
    us = _channel(session, market=Market.US)
    for _ in range(5):
        _post(session, de, views=1000, likes=100, analysis=_analysis("clip"))
        _post(session, us, views=1000, likes=100, analysis=_analysis("clip"))

    report = tp.compute_trailer_patterns(session, market="DE", now=NOW)
    assert report.posts_in_window == 5
    assert report.market == "DE"


def test_empty_corpus_returns_explanatory_report(session: Session):
    report = tp.compute_trailer_patterns(session, market="DE", now=NOW)
    assert report.posts_in_window == 0
    assert report.notes
    assert report.to_dict()["dimensions"] == {}


def test_to_dict_is_json_serialisable(session: Session):
    import json

    channels = [_channel(session) for _ in range(3)]
    for ch in channels:
        for _ in range(5):
            _post(session, ch, views=1000, likes=100, duration=20, analysis=_analysis("clip"))

    report = tp.compute_trailer_patterns(session, now=NOW)
    json.dumps(report.to_dict())  # darf nicht werfen


# ---------- Endpunkt ----------------------------------------------------


@pytest.fixture
def client(session: Session, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.database import get_session
    from app.main import app

    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)

    def _override_session():
        yield session

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_endpoint_returns_report(client, session: Session):
    channels = [_channel(session) for _ in range(3)]
    for ch in channels:
        for _ in range(6):
            _post(session, ch, views=1000, likes=100, duration=20, analysis=_analysis("clip"))
        for _ in range(3):
            _post(session, ch, views=1000, likes=200, duration=20, analysis=_analysis("trailer"))

    resp = client.get("/api/admin/trailer-patterns?window_days=90")
    assert resp.status_code == 200
    body = resp.json()

    assert body["window_days"] == 90
    assert body["posts_with_baseline"] == 27
    assert body["channels_covered"] == 3

    fmt = {c["value"]: c for c in body["dimensions"]["format"]}
    assert fmt["trailer"]["verdict"] == "over"
    assert fmt["trailer"]["median_lift"] == pytest.approx(2.0)


def test_endpoint_rejects_out_of_range_window(client):
    assert client.get("/api/admin/trailer-patterns?window_days=5").status_code == 422
    assert client.get("/api/admin/trailer-patterns?window_days=400").status_code == 422


def test_endpoint_passes_market_filter(client, session: Session):
    de = _channel(session, market=Market.DE)
    us = _channel(session, market=Market.US)
    for _ in range(5):
        _post(session, de, views=1000, likes=100, analysis=_analysis("clip"))
        _post(session, us, views=1000, likes=100, analysis=_analysis("clip"))

    body = client.get("/api/admin/trailer-patterns?market=DE").json()
    assert body["market"] == "DE"
    assert body["posts_in_window"] == 5
