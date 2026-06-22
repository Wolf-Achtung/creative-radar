"""HTTP-layer tests for ``GET /api/pairs`` (Sprint 2026-05-12 — landing-page sync).

Verifies the endpoint that drives the landing-page card grid on
``app.creative-radar.de``. Contract:

- 200 + ``PairsResponse`` shape for every enabled pair, in PAIRS-dict
  insertion order (Wolf's curated visual order, not alphabetical).
- Disabled pairs are excluded.
- Markets per pair are emitted in fixed DE → US → UK order
  (``MARKETS_DISPLAY_ORDER``), only including markets the pair actually
  covers (Lionsgate → ``["US", "UK"]`` because it has no DE channel).

Test isolation pattern mirrors ``test_api_insights_weekly.py`` — shared
in-memory SQLite via ``StaticPool`` and ``auth_enabled`` flipped off.
The endpoint does not touch the DB, but the test client still boots the
full app which expects a DB session dependency to exist.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.database import get_session
from app.main import app
from app.services import insight_engine
from app.services.insight_engine import INSIGHT_FREQUENCY_LABEL


def _shared_test_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def db():
    engine = _shared_test_engine()
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)

    def _override_session():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _expected_enabled_keys_in_order() -> list[str]:
    """Insertion-order enabled pair keys — what /api/pairs is expected to emit."""
    return [k for k, v in insight_engine.PAIRS.items() if v.get("enabled", False)]


# ---------- 200: payload shape -------------------------------------------------


def test_pairs_endpoint_returns_all_enabled(client: TestClient):
    response = client.get("/api/pairs")
    assert response.status_code == 200
    payload = response.json()
    assert "pairs" in payload
    returned_keys = [p["pair_key"] for p in payload["pairs"]]
    assert returned_keys == _expected_enabled_keys_in_order()


def test_pairs_endpoint_response_schema(client: TestClient):
    response = client.get("/api/pairs")
    assert response.status_code == 200
    pairs = response.json()["pairs"]
    assert pairs, "endpoint must return at least one pair on a healthy PAIRS dict"
    required_fields = {"pair_key", "display_name", "markets", "frequency_label", "enabled"}
    for pair in pairs:
        assert required_fields <= set(pair.keys()), (
            f"missing fields in {pair['pair_key']!r}: "
            f"{required_fields - set(pair.keys())}"
        )
        assert isinstance(pair["pair_key"], str) and pair["pair_key"]
        assert isinstance(pair["display_name"], str) and pair["display_name"]
        assert isinstance(pair["markets"], list) and pair["markets"]
        assert all(isinstance(m, str) for m in pair["markets"])
        assert pair["frequency_label"] == INSIGHT_FREQUENCY_LABEL
        assert pair["enabled"] is True


# ---------- Exclusion: disabled pairs -----------------------------------------


def test_pairs_endpoint_excludes_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Flip warnerbros to enabled=False and assert it disappears from
    the response. The other 8 stay. This guards the enabled-filter logic
    independent of which pairs happen to be disabled in production today
    (currently none)."""
    pair_def = insight_engine.PAIRS["warnerbros"]
    original_enabled = pair_def["enabled"]
    original_reason = pair_def.get("reason")
    pair_def["enabled"] = False
    pair_def["reason"] = "temporarily disabled for test"
    try:
        response = client.get("/api/pairs")
        assert response.status_code == 200
        returned_keys = [p["pair_key"] for p in response.json()["pairs"]]
        assert "warnerbros" not in returned_keys
        assert len(returned_keys) == sum(
            1
            for k, v in insight_engine.PAIRS.items()
            if v.get("enabled", False) and k != "warnerbros"
        )
    finally:
        pair_def["enabled"] = original_enabled
        pair_def["reason"] = original_reason


# ---------- Markets: stable display order + per-pair correctness --------------


def test_pairs_endpoint_lionsgate_markets_us_and_uk(client: TestClient):
    """Stand 27.05.2026 (PAIRS-markets-Update): Lionsgate ist US+UK —
    kein DE-Auftritt (Vertrieb via Leonine/Studiocanal), aber UK-Pool
    aktiv (@lionsgateuk auf IG+TT). Frueher hatte das Pair ``markets=["US"]``
    als bewusste X1-Behauptung, dass der Brief UK nicht abdeckt; UK-B1
    rendert die UK-Sektion aber automatisch via _format_channel_section,
    daher zieht der markets-Wert nun nach."""
    response = client.get("/api/pairs")
    assert response.status_code == 200
    lionsgate = next(
        (p for p in response.json()["pairs"] if p["pair_key"] == "lionsgate"),
        None,
    )
    assert lionsgate is not None, "lionsgate must be returned"
    assert lionsgate["markets"] == ["US", "UK"]


def test_pairs_endpoint_paramountplus_markets_de_us_uk(client: TestClient):
    """Stand 27.05.2026: Paramount+ deckt DE+US+UK ab — Pool und
    markets-Wert stimmen wieder ueberein (frueher absichtlich auf
    DE+US gekappt, weil der UK-Brief-Render-Pfad noch nicht stand;
    UK-B1 hat das geloest)."""
    response = client.get("/api/pairs")
    paramountplus = next(
        (p for p in response.json()["pairs"] if p["pair_key"] == "paramountplus"),
        None,
    )
    assert paramountplus is not None
    assert paramountplus["markets"] == ["DE", "US", "UK"]


def test_pairs_endpoint_markets_always_in_fixed_display_order(client: TestClient):
    """Whichever markets a pair covers, the emitted order is the
    intersection with DE → US → UK. Guards against the markets-order
    drifting back into channels-insertion order."""
    response = client.get("/api/pairs")
    fixed_order = ["DE", "US", "UK"]
    for pair in response.json()["pairs"]:
        markets = pair["markets"]
        # Each market should be at its expected index in the fixed order.
        indices = [fixed_order.index(m) for m in markets]
        assert indices == sorted(indices), (
            f"{pair['pair_key']!r} emitted markets {markets} not in "
            f"DE → US → UK order"
        )


# ---------- X1 surface override --------------------------------------------


def test_pairs_endpoint_emits_markets_field_verbatim(client: TestClient):
    """Endpoint emittiert das ``markets``-Feld exakt so wie in der
    PAIRS-Registry gepflegt — der Pool wird nicht heuristisch
    drueberberechnet, der explicit-Branch in _markets_for_pair gewinnt.
    Stand 27.05.2026 stimmen Pool und markets fuer warnerbros wieder
    ueberein (beide DE+US+UK); der Test sichert dennoch die
    Override-Mechanik fuer den Fall, dass jemand das markets-Feld
    spaeter wieder enger pflegen will (z.B. um einen Pool-Markt
    bewusst nicht zu surfacen)."""
    response = client.get("/api/pairs")
    warnerbros = next(
        (p for p in response.json()["pairs"] if p["pair_key"] == "warnerbros"),
        None,
    )
    assert warnerbros is not None
    assert warnerbros["markets"] == ["DE", "US", "UK"]


def test_pairs_endpoint_falls_back_to_pool_when_markets_field_absent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Backwards-compat: a pair entered before the X1 ``markets``
    convention catches up should fall back to the channel-pool union
    (the pre-X1 derivation). Verified by stripping the field from
    one pair in-place and asserting the endpoint still returns a
    sensible markets list derived from its channels."""
    pair_def = insight_engine.PAIRS["warnerbros"]
    original_markets = pair_def.pop("markets")
    try:
        response = client.get("/api/pairs")
        assert response.status_code == 200
        warnerbros = next(
            (p for p in response.json()["pairs"] if p["pair_key"] == "warnerbros"),
            None,
        )
        assert warnerbros is not None
        # Pool union for warnerbros covers DE/US/UK (Phase A), emitted
        # in fixed display order.
        assert warnerbros["markets"] == ["DE", "US", "UK"]
    finally:
        pair_def["markets"] = original_markets


# ---------- Sprint 28.05.2026 Studio-Kennzahl ----------------------------


from datetime import datetime, timedelta, timezone
from app.models.entities import Channel, InsightReport as InsightReportRow, Market, Post


def _seed_channel(session: Session, *, handle: str, market: str = "US",
                  platform: str = "tiktok") -> Channel:
    ch = Channel(
        name=f"Channel {handle}",
        platform=platform,
        url=f"https://x.example/{handle}",
        handle=handle,
        market=Market(market),
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


def _seed_post(session: Session, channel: Channel, *,
               published_at: datetime | None,
               detected_at: datetime | None,
               url_suffix: str = "p") -> Post:
    post = Post(
        channel_id=channel.id,
        platform=channel.platform,
        post_url=f"https://x/{channel.handle}/{url_suffix}",
        caption="test",
        published_at=published_at,
        detected_at=detected_at or datetime.now(timezone.utc),
        raw_payload={},
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    return post


def test_pairs_endpoint_includes_kennzahl_fields_with_defaults(client: TestClient):
    """Bei leerer DB: Felder kommen mit ``posts_count_completed_week=0``
    und ``last_generated_at=None`` zurueck. Default-Verhalten ist
    Pflicht damit der Frontend-Code nicht null-checken muss."""
    response = client.get("/api/pairs")
    assert response.status_code == 200
    for pair in response.json()["pairs"]:
        assert "posts_count_completed_week" in pair
        assert pair["posts_count_completed_week"] == 0
        assert "last_generated_at" in pair
        assert pair["last_generated_at"] is None


def _completed_week_start():
    """Montag 00:00 UTC der abgeschlossenen ISO-Woche — dieselbe
    Ableitung wie der Endpoint (last_completed_iso_week_anchor →
    _iso_week_start_utc)."""
    from app.api.insights import _iso_week_start_utc
    from app.services.insight_engine import last_completed_iso_week_anchor
    return _iso_week_start_utc(last_completed_iso_week_anchor())


def test_pairs_endpoint_counts_posts_in_completed_iso_week(client: TestClient, db):
    """Sprint Studio-Kachel-Vorwoche (2026-06-22): Posts mit
    ``published_at`` in der ABGESCHLOSSENEN ISO-Woche (KW-1) zaehlen;
    Posts der LAUFENDEN Woche und aeltere Posts NICHT. ``iso_week`` /
    ``iso_year`` tragen die KW-Kennung der abgeschlossenen Woche."""
    cws = _completed_week_start()
    with Session(db) as session:
        # Channel-Handle aus warnerbros-PAIRS (US-Seite)
        ch = _seed_channel(session, handle="warnerbros", market="US")
        # Drei Posts in der abgeschlossenen Woche
        for i in range(3):
            _seed_post(session, ch,
                       published_at=cws + timedelta(hours=12 + i),
                       detected_at=None, url_suffix=f"w{i}")
        # Ein Post in der LAUFENDEN Woche (>= week_end) → soll NICHT zaehlen
        _seed_post(session, ch,
                   published_at=cws + timedelta(days=7, hours=12),
                   detected_at=None, url_suffix="current")
        # Ein Post zwei Wochen alt (vor week_start) → soll NICHT zaehlen
        _seed_post(session, ch,
                   published_at=cws - timedelta(days=2),
                   detected_at=None, url_suffix="old")

    response = client.get("/api/pairs")
    assert response.status_code == 200
    pair = next(p for p in response.json()["pairs"] if p["pair_key"] == "warnerbros")
    assert pair["posts_count_completed_week"] == 3
    assert pair["iso_week"] == cws.isocalendar()[1]
    assert pair["iso_year"] == cws.isocalendar()[0]


def test_pairs_endpoint_falls_back_to_detected_at_when_published_at_null(
    client: TestClient, db,
):
    """published_at NULL → detected_at uebernimmt die Zeitfilterung
    (gleicher Fallback wie #190 und _channel_stats), ebenfalls beidseitig
    auf die abgeschlossene Woche gebounded."""
    cws = _completed_week_start()
    with Session(db) as session:
        ch = _seed_channel(session, handle="warnerbros", market="US")
        # Post ohne published_at, aber detected_at in der abgeschlossenen Woche
        _seed_post(session, ch,
                   published_at=None,
                   detected_at=cws + timedelta(hours=6),
                   url_suffix="nopub")
        # Post ohne published_at, detected_at in der LAUFENDEN Woche → NICHT zaehlen
        _seed_post(session, ch,
                   published_at=None,
                   detected_at=cws + timedelta(days=7, hours=6),
                   url_suffix="current-nopub")
        # Post ohne published_at, detected_at zwei Wochen alt → NICHT zaehlen
        _seed_post(session, ch,
                   published_at=None,
                   detected_at=cws - timedelta(days=3),
                   url_suffix="old-nopub")

    response = client.get("/api/pairs")
    pair = next(p for p in response.json()["pairs"] if p["pair_key"] == "warnerbros")
    assert pair["posts_count_completed_week"] == 1


def test_pairs_endpoint_returns_last_generated_at(client: TestClient, db):
    """``last_generated_at`` = MAX(generated_at) ueber alle Briefe des
    Pairs. Aelterer Brief wird nicht zurueckgegeben."""
    with Session(db) as session:
        older = datetime(2026, 5, 18, 6, 0, tzinfo=timezone.utc)
        newer = datetime(2026, 5, 25, 6, 0, tzinfo=timezone.utc)
        for ts, week in ((older, 20), (newer, 21)):
            session.add(InsightReportRow(
                pair_key="warnerbros",
                iso_year=2026,
                iso_week=week,
                generated_at=ts,
                aggregation={},
                llm_output={},
                model="test-model",
            ))
        session.commit()

    response = client.get("/api/pairs")
    pair = next(p for p in response.json()["pairs"] if p["pair_key"] == "warnerbros")
    assert pair["last_generated_at"] is not None
    # Pydantic gibt ``datetime``-ISO als String mit "+00:00"-Offset
    assert "2026-05-25T06:00:00" in pair["last_generated_at"]


def test_pairs_endpoint_pair_without_channels_returns_zero(client: TestClient, db):
    """Pair ohne onboarded Channels → ``posts_count_completed_week=0``,
    ``last_generated_at=None``. Empty-State ist ehrlich, nicht
    kaputt."""
    # Keine Channels geseedet → kein Channel in der DB. Der Endpoint
    # muss trotzdem alle Pairs liefern, mit 0 Posts.
    response = client.get("/api/pairs")
    assert response.status_code == 200
    for pair in response.json()["pairs"]:
        assert pair["posts_count_completed_week"] == 0
        assert pair["last_generated_at"] is None


def test_pairs_endpoint_query_count_does_not_grow_with_pairs(
    client: TestClient, db,
):
    """Audit: drei Pair-Aggregat-Queries (Channels + Posts + Last-Gen),
    UNABHAENGIG von der Anzahl der Pairs. Schuetzt vor versehentlichem
    N+1-Drift, wenn jemand spaeter pro Pair eine Sub-Query macht."""
    from sqlalchemy import event
    query_log: list[str] = []

    @event.listens_for(db, "before_cursor_execute")
    def _log_query(conn, cursor, statement, parameters, context, executemany):
        # Nur SELECTs auf die drei interessanten Tabellen mitzaehlen —
        # CREATE TABLE etc. von Pytest-Setup-Pfaden ignorieren.
        lowered = statement.lower()
        if lowered.startswith("select") and (
            " from channel" in lowered
            or " from post" in lowered
            or " from insightreport" in lowered
        ):
            query_log.append(statement)

    try:
        response = client.get("/api/pairs")
        assert response.status_code == 200
        # Max 3 Aggregat-Queries pro Endpoint-Call. ``len(query_log)``
        # zaehlt JEDEN Call ueber das ganze Setup — wir akzeptieren also
        # eine Obergrenze von 3 (1 fuer Channels, 1 fuer Posts, 1 fuer
        # InsightReport), unabhaengig vom Pair-Count.
        assert len(query_log) <= 3, (
            f"erwartet maximal 3 Aggregat-Queries, gesehen "
            f"{len(query_log)}:\n" + "\n---\n".join(query_log)
        )
    finally:
        event.remove(db, "before_cursor_execute", _log_query)


# ---------- Sprint 28.05.2026 (Option B) - Headline pro Kachel ----------


def test_pairs_endpoint_includes_headline_and_has_brief_defaults(client: TestClient):
    """Bei leerer DB: ``headline=None`` und ``has_brief=False`` fuer
    jeden Pair. Default-Verhalten ist Pflicht."""
    response = client.get("/api/pairs")
    assert response.status_code == 200
    for pair in response.json()["pairs"]:
        assert "headline" in pair
        assert pair["headline"] is None
        assert "has_brief" in pair
        assert pair["has_brief"] is False


def test_pairs_endpoint_returns_headline_from_latest_brief(client: TestClient, db):
    """Headline kommt vom Brief mit dem juengsten generated_at. Aelterer
    Brief soll NICHT zurueckgegeben werden."""
    with Session(db) as session:
        older = datetime(2026, 5, 18, 6, 0, tzinfo=timezone.utc)
        newer = datetime(2026, 5, 25, 6, 0, tzinfo=timezone.utc)
        session.add(InsightReportRow(
            pair_key="warnerbros", iso_year=2026, iso_week=20,
            generated_at=older, aggregation={},
            llm_output={"headline": "Warner Vorwoche-Headline"},
            model="test-model",
        ))
        session.add(InsightReportRow(
            pair_key="warnerbros", iso_year=2026, iso_week=21,
            generated_at=newer, aggregation={},
            llm_output={"headline": "Warner aktuelle Headline"},
            model="test-model",
        ))
        session.commit()

    response = client.get("/api/pairs")
    pair = next(p for p in response.json()["pairs"] if p["pair_key"] == "warnerbros")
    assert pair["headline"] == "Warner aktuelle Headline"
    assert pair["has_brief"] is True


def test_pairs_endpoint_robust_against_missing_headline_field(
    client: TestClient, db,
):
    """Brief existiert, aber ``llm_output`` traegt KEIN ``headline``-Feld
    (Schema-Drift, alter Brief vor Headline-Feld, oder leeres dict).
    Frontend soll ``headline=None`` aber ``has_brief=True`` bekommen —
    "es gibt einen Brief, nur ohne Headline"."""
    with Session(db) as session:
        ts = datetime(2026, 5, 25, 6, 0, tzinfo=timezone.utc)
        # Variant 1: llm_output leer
        session.add(InsightReportRow(
            pair_key="warnerbros", iso_year=2026, iso_week=21,
            generated_at=ts, aggregation={},
            llm_output={},
            model="test-model",
        ))
        # Variant 2: headline ist leerer String
        session.add(InsightReportRow(
            pair_key="disney", iso_year=2026, iso_week=21,
            generated_at=ts, aggregation={},
            llm_output={"headline": "  "},  # leer nach strip
            model="test-model",
        ))
        # Variant 3: headline ist None
        session.add(InsightReportRow(
            pair_key="universal", iso_year=2026, iso_week=21,
            generated_at=ts, aggregation={},
            llm_output={"headline": None},
            model="test-model",
        ))
        session.commit()

    payload = response_json = client.get("/api/pairs").json()
    by_key = {p["pair_key"]: p for p in payload["pairs"]}
    for pkey in ("warnerbros", "disney", "universal"):
        if pkey not in by_key:
            # Pair ist evtl. nicht in der enabled-Liste — skipp den Fall
            continue
        assert by_key[pkey]["has_brief"] is True
        assert by_key[pkey]["headline"] is None


def test_pairs_endpoint_query_count_does_not_grow_with_headline_join(
    client: TestClient, db,
):
    """Audit (analog #195): Trotz neuem Brief-Join bleibt der Endpoint
    bei max 3 Aggregat-Queries. Subquery-Join zaehlt als 1 — sonst
    waere der #195-Audit-Test verletzt."""
    from sqlalchemy import event

    # Seed: ein Brief mit Headline, damit der Join nicht trivial leer
    # ist und der Subquery-Pfad wirklich ausgefuehrt wird.
    with Session(db) as session:
        session.add(InsightReportRow(
            pair_key="warnerbros", iso_year=2026, iso_week=21,
            generated_at=datetime(2026, 5, 25, 6, 0, tzinfo=timezone.utc),
            aggregation={},
            llm_output={"headline": "test"},
            model="test-model",
        ))
        session.commit()

    query_log: list[str] = []

    @event.listens_for(db, "before_cursor_execute")
    def _log_query(conn, cursor, statement, parameters, context, executemany):
        lowered = statement.lower()
        if lowered.startswith("select") and (
            " from channel" in lowered
            or " from post" in lowered
            or " from insightreport" in lowered
            or " from insight_report" in lowered  # SQLite uses underscore
        ):
            query_log.append(statement)

    try:
        response = client.get("/api/pairs")
        assert response.status_code == 200
        assert len(query_log) <= 3, (
            f"erwartet maximal 3 Aggregat-Queries trotz Headline-Join, "
            f"gesehen {len(query_log)}:\n" + "\n---\n".join(query_log)
        )
    finally:
        event.remove(db, "before_cursor_execute", _log_query)
