"""Langform-Eingrenzung (Trailer-Intelligence Stufe 3, Schritt 1).

Das Modul beantwortet nicht, *warum* Langform gewinnt — es grenzt ein,
welche Erklaerungen es **nicht** sind. Die Tests sichern deshalb vor
allem die Faelle, in denen eine falsche Eingrenzung entstuende:

1. Ein Vorsprung, der in Wahrheit ein Plattform-Effekt ist, muss beim
   Schichten verschwinden — sonst wuerde das Modul dem Handwerk
   zuschreiben, was der Plattform gehoert.
2. Ein echter Vorsprung darf beim Schichten *nicht* verschwinden.
3. Eine Schicht mit einem duennen Arm ist kein bestandener Test und darf
   ``survives_in`` nicht hochzaehlen.
4. Ohne Gesamtvorsprung ist die ganze Schichtung gegenstandslos und muss
   als solche gemeldet werden.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Asset, Channel, Market, Post, Title
from app.services import langform_analysis as la


NOW = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)


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


def _channel(
    session: Session, *, platform: str = "youtube", market: Market = Market.US
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
    duration: int,
    views: int = 1000,
    likes: int = 100,
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
        raw_payload={},
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _cohort(
    session: Session,
    channel: Channel,
    *,
    duration: int,
    normal: int,
    breakouts: int,
) -> None:
    """``normal`` Posts auf Kanalniveau, ``breakouts`` mit 2,5-fachem Lift.

    Die Ausreisser bleiben immer in der Minderheit, damit sie den
    Kanal-Median nicht selbst anheben.
    """
    for _ in range(normal):
        _post(session, channel, duration=duration, likes=100)
    for _ in range(breakouts):
        _post(session, channel, duration=duration, likes=250)


def _row(rows: list[la.StratumComparison], name: str) -> la.StratumComparison:
    for r in rows:
        if r.stratum == name:
            return r
    raise AssertionError(f"Schicht {name!r} fehlt: {[r.stratum for r in rows]}")


# ---------- Statistik ----------------------------------------------------


def test_two_proportion_z_is_symmetric_and_scales(session: Session):
    z = la._two_proportion_z(20, 100, 10, 100)
    assert z is not None and z > 0
    # Umgekehrte Reihenfolge -> gleicher Betrag, anderes Vorzeichen.
    assert la._two_proportion_z(10, 100, 20, 100) == pytest.approx(-z)
    # Gleiche Differenz, zehnfache Stichprobe -> deutlich belastbarer.
    assert la._two_proportion_z(200, 1000, 100, 1000) > z


def test_two_proportion_z_guards_degenerate_input(session: Session):
    assert la._two_proportion_z(0, 0, 5, 10) is None
    assert la._two_proportion_z(5, 10, 0, 0) is None
    # Keine Treffer auf beiden Seiten: keine Varianz, kein z.
    assert la._two_proportion_z(0, 50, 0, 50) is None
    assert la._two_proportion_z(50, 50, 50, 50) is None


# ---------- Der Kern: erklaert eine Schicht den Vorsprung? ---------------


def test_platform_effect_is_unmasked_by_stratifying(session: Session):
    """Der Fall, wegen dem das Modul existiert.

    Zwei Plattformen mit sehr verschiedenem Trefferniveau. Innerhalb
    *jeder* Plattform laufen Lang- und Kurzform exakt gleich — aber
    Langform liegt ueberwiegend auf der starken Plattform. Der
    Gesamtvergleich zeigt deshalb einen Vorsprung, der keiner ist. Die
    Schichtung muss ihn aufloesen.
    """
    for _ in range(3):
        yt = _channel(session, platform="youtube")
        # YouTube: 40 % Treffer in beiden Klassen.
        _cohort(session, yt, duration=150, normal=36, breakouts=24)
        _cohort(session, yt, duration=30, normal=12, breakouts=8)
    for _ in range(3):
        tt = _channel(session, platform="tiktok")
        # TikTok: 10 % Treffer in beiden Klassen.
        _cohort(session, tt, duration=150, normal=18, breakouts=2)
        _cohort(session, tt, duration=30, normal=54, breakouts=6)

    report = la.compute_langform_report(session, now=NOW)

    # Korpusweit sieht Langform besser aus ...
    assert report.overall.gap_pp > 5
    # ... aber innerhalb jeder Plattform ist der Unterschied weg.
    for row in report.strata["platform"]:
        if row.verdict != "insufficient":
            assert row.verdict == "none", (row.stratum, row.gap_pp, row.gap_z)
    assert "platform" in report.explained_by
    assert any("verschwindet vollstaendig" in n for n in report.notes)


def test_real_advantage_survives_every_stratum(session: Session):
    """Gegenprobe: ein Vorsprung, der innerhalb jeder Plattform besteht,
    darf durch die Schichtung nicht verschwinden. Sonst wuerde das Modul
    echte Befunde wegerklaeren."""
    for platform in ("youtube", "tiktok"):
        for _ in range(3):
            ch = _channel(session, platform=platform)
            _cohort(session, ch, duration=150, normal=20, breakouts=20)
            _cohort(session, ch, duration=30, normal=54, breakouts=6)

    report = la.compute_langform_report(session, now=NOW)

    assert report.overall.verdict == "advantage"
    for row in report.strata["platform"]:
        assert row.verdict == "advantage", (row.stratum, row.gap_z)
    assert report.explained_by == []
    assert report.survives_in == report.tested_strata > 0
    assert any("haelt in allen" in n for n in report.notes)


def test_thin_arm_is_not_a_passed_test(session: Session):
    """Eine Schicht mit zu duennem Arm darf ``survives_in`` nicht
    hochzaehlen — sonst waere die Bilanz geschoent."""
    for _ in range(3):
        yt = _channel(session, platform="youtube")
        _cohort(session, yt, duration=150, normal=20, breakouts=20)
        _cohort(session, yt, duration=30, normal=54, breakouts=6)
    # Eine zweite Plattform mit nur einer Handvoll Posts.
    tt = _channel(session, platform="tiktok")
    _cohort(session, tt, duration=150, normal=3, breakouts=2)
    _cohort(session, tt, duration=30, normal=3, breakouts=2)

    report = la.compute_langform_report(session, now=NOW)
    tiktok = _row(report.strata["platform"], "tiktok")

    assert tiktok.verdict == "insufficient"
    assert "mindestens" in tiktok.reason
    # Die Rohwerte werden trotzdem gemeldet, nur eben ohne Urteil.
    assert tiktok.langform_posts == 5
    assert tiktok.gap_z is None
    # Und die duenne Schicht zaehlt in keiner Richtung mit.
    assert all(
        c.verdict != "insufficient"
        for rows in report.strata.values()
        for c in rows
        if c.stratum == "tiktok" and c.verdict == "advantage"
    )


def test_no_overall_advantage_makes_strata_moot(session: Session):
    """Ohne Gesamtvorsprung ist die Schichtung gegenstandslos. Das muss
    dastehen, sonst liest jemand die Schicht-Tabelle als Befund."""
    for _ in range(3):
        ch = _channel(session)
        _cohort(session, ch, duration=150, normal=36, breakouts=4)
        _cohort(session, ch, duration=30, normal=36, breakouts=4)

    report = la.compute_langform_report(session, now=NOW)

    assert report.overall.verdict == "none"
    assert any("ohne Aussagekraft" in n for n in report.notes)


# ---------- Kohorten-Bildung ---------------------------------------------


def test_grey_zone_is_excluded_from_both_arms(session: Session):
    """Die Uebergangszone liegt statistisch in der Mitte und wuerde beide
    Arme verwaessern — sie wird gezaehlt, aber nicht verglichen."""
    for _ in range(3):
        ch = _channel(session)
        _cohort(session, ch, duration=150, normal=20, breakouts=10)
        _cohort(session, ch, duration=75, normal=20, breakouts=10)
        _cohort(session, ch, duration=30, normal=20, breakouts=10)

    report = la.compute_langform_report(session, now=NOW)

    assert report.langform_posts == 90
    assert report.uebergang_posts == 90
    assert report.kurzform_posts == 90
    # Die 90 Grauzonen-Posts tauchen in keinem Arm auf.
    assert report.overall.langform_posts == 90
    assert report.overall.kurzform_posts == 90
    assert any("Uebergangszone" in n for n in report.notes)


@pytest.mark.parametrize(
    "seconds, expected",
    [(59, "kurz"), (60, "grau"), (89, "grau"), (90, "lang"), (240, "lang")],
)
def test_cohort_boundaries_match_stage_one(session: Session, seconds, expected):
    """Dieselben Grenzen wie ``format_class`` in Stufe 1 — sonst waeren
    die Zahlen der beiden Stufen nicht vergleichbar."""
    ch = _channel(session)
    for _ in range(6):
        _post(session, ch, duration=seconds)

    report = la.compute_langform_report(session, now=NOW)
    got = {
        "lang": report.langform_posts,
        "grau": report.uebergang_posts,
        "kurz": report.kurzform_posts,
    }
    assert got[expected] == 6
    assert sum(got.values()) == 6


def test_posts_without_duration_are_reported_not_dropped(session: Session):
    ch = _channel(session)
    for _ in range(5):
        _post(session, ch, duration=150)
    for _ in range(4):
        _post(session, ch, duration=None)

    report = la.compute_langform_report(session, now=NOW)
    assert report.langform_posts == 5
    assert any("ohne Dauer-Angabe" in n for n in report.notes)


# ---------- Dauer-Gradient -----------------------------------------------


def test_duration_gradient_separates_threshold_from_slope(session: Session):
    """Waechst der Vorsprung mit der Laenge weiter, oder springt er bei
    90 Sekunden und bleibt flach? Der Gradient trennt die beiden
    Lesarten — hier mit steigendem Effekt gebaut."""
    for _ in range(3):
        ch = _channel(session)
        _cohort(session, ch, duration=100, normal=27, breakouts=3)   # 10 %
        _cohort(session, ch, duration=150, normal=21, breakouts=9)   # 30 %
        _cohort(session, ch, duration=240, normal=15, breakouts=15)  # 50 %
        _cohort(session, ch, duration=30, normal=108, breakouts=12)  # 10 %

    report = la.compute_langform_report(session, now=NOW)
    bands = {c.stratum: c for c in report.duration_gradient}

    assert bands["90-120s"].verdict == "none"
    assert bands["120-180s"].verdict == "advantage"
    assert bands["180-300s"].verdict == "advantage"
    assert bands["180-300s"].gap_pp > bands["120-180s"].gap_pp
    # Der Gradient beantwortet eine andere Frage und darf die Bilanz
    # der Erklaerungs-Schichten nicht beeinflussen.
    assert report.tested_strata == sum(
        1 for rows in report.strata.values() for c in rows if c.verdict != "insufficient"
    )


# ---------- Release-Fenster und Titel-Match ------------------------------


def test_title_match_stratum_splits_matched_and_unmatched(session: Session):
    ch = _channel(session)
    matched = [_post(session, ch, duration=150) for _ in range(20)]
    for _ in range(20):
        _post(session, ch, duration=150)
    for _ in range(40):
        _post(session, ch, duration=30)

    title = Title(title_original="X", content_type="Film")
    session.add(title)
    session.commit()
    session.refresh(title)
    for p in matched:
        session.add(Asset(post_id=p.id, title_id=title.id))
    session.commit()

    report = la.compute_langform_report(session, now=NOW, min_posts_per_arm=5)
    names = {r.stratum for r in report.strata["title_match"]}
    assert names == {"gematcht", "ohne Titel"}
    assert _row(report.strata["title_match"], "gematcht").langform_posts == 20


def test_days_to_release_uses_the_shared_bucket_logic(session: Session):
    """Der Release-Bucket kommt aus derselben Klassifikation wie der
    Empfehlungs-Baustein — nur die Titel-Aufloesung ist eigen, weil die
    Fassung im insight_engine ein nie gesetztes Attribut liest."""
    ch = _channel(session, market=Market.US)
    title = Title(
        title_original="X",
        content_type="Film",
        release_date_us=(NOW + timedelta(days=60)).date(),
    )
    session.add(title)
    session.commit()
    session.refresh(title)

    p = _post(session, ch, duration=150, days_ago=1)
    session.add(Asset(post_id=p.id, title_id=title.id))
    session.commit()

    bucket = la._days_to_release_stratum(
        p, {p.id: title.id}, {title.id: title}, "US"
    )
    # 61 Tage vor Release -> weit vor dem Start.
    assert bucket == ">4w_pre"

    # Ohne Titel bleibt es unknown statt zu raten.
    assert la._days_to_release_stratum(p, {}, {}, "US") == "unknown"


# ---------- Ausgabe -------------------------------------------------------


def test_report_is_json_serialisable(session: Session):
    import json

    for _ in range(3):
        ch = _channel(session)
        _cohort(session, ch, duration=150, normal=20, breakouts=20)
        _cohort(session, ch, duration=30, normal=54, breakouts=6)

    report = la.compute_langform_report(session, now=NOW)
    payload = json.loads(json.dumps(report.to_dict()))
    assert payload["overall"]["verdict"] == "advantage"
    assert payload["survives_in"] >= 1
    assert "platform" in payload["strata"]


def test_empty_corpus_returns_explanatory_report(session: Session):
    report = la.compute_langform_report(session, market="DE", now=NOW)
    assert report.langform_posts == 0
    assert report.overall is None
    assert report.notes
    assert report.to_dict()["strata"] == {}


# ---------- Endpunkt ------------------------------------------------------


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
    for _ in range(3):
        ch = _channel(session)
        _cohort(session, ch, duration=150, normal=20, breakouts=20)
        _cohort(session, ch, duration=30, normal=54, breakouts=6)

    resp = client.get("/api/admin/langform?window_days=90")
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall"]["verdict"] == "advantage"
    assert body["tested_strata"] >= 1
    assert body["explained_by"] == []


def test_endpoint_rejects_out_of_range_window(client):
    assert client.get("/api/admin/langform?window_days=5").status_code == 422
    assert client.get("/api/admin/langform?min_posts_per_arm=1").status_code == 422


def test_localized_advantage_is_not_the_same_as_explained(session: Session):
    """Der Fall aus dem Produktionslauf vom 07.08.2026.

    Langform gewinnt auf einer Plattform klar und auf der anderen gar
    nicht. Das ist weder "die Plattform erklaert alles" noch "haelt
    ueberall" — es sagt, wo der Hebel greift. Die binaere Fassung haette
    daraus faelschlich Schwaeche gelesen.
    """
    for _ in range(3):
        yt = _channel(session, platform="youtube")
        _cohort(session, yt, duration=150, normal=20, breakouts=20)   # 50 %
        _cohort(session, yt, duration=30, normal=54, breakouts=6)     # 10 %
    for _ in range(3):
        tt = _channel(session, platform="tiktok")
        _cohort(session, tt, duration=150, normal=36, breakouts=4)    # 10 %
        _cohort(session, tt, duration=30, normal=54, breakouts=6)     # 10 %

    report = la.compute_langform_report(session, now=NOW)

    assert _row(report.strata["platform"], "youtube").verdict == "advantage"
    assert _row(report.strata["platform"], "tiktok").verdict == "none"

    # Weder wegerklaert ...
    assert "platform" not in report.explained_by
    # ... noch universell — sondern lokalisiert.
    assert "platform" in report.localized_in
    assert any("wo der Hebel greift" in n for n in report.notes)
    # Das Grobmass allein wuerde hier in die Irre fuehren.
    assert 0 < report.survives_in < report.tested_strata


def test_universal_advantage_is_not_reported_as_localized(session: Session):
    """Gegenprobe: haelt der Vorsprung in jeder Schicht, darf die
    Dimension nicht als lokalisiert erscheinen."""
    for platform in ("youtube", "tiktok"):
        for _ in range(3):
            ch = _channel(session, platform=platform)
            _cohort(session, ch, duration=150, normal=20, breakouts=20)
            _cohort(session, ch, duration=30, normal=54, breakouts=6)

    report = la.compute_langform_report(session, now=NOW)
    assert report.localized_in == []
    assert report.explained_by == []
