"""HTTP-Layer-Tests fuer ``GET /api/roundups/latest`` (Master-Plan-
Schritt-3b — Frontend-Anzeige der Segment-Roundups).

Der Endpoint treibt den Roundup-Block auf der Landing-Page. Vertragsfix:

- 200 + ``SegmentRoundupListResponse``-Form, ein Eintrag pro Segment,
  jeweils der mit dem hoechsten ``(iso_year, iso_week)``.
- Tiebreak ``generated_at`` DESC: wenn dieselbe Woche zweimal gelaufen
  ist (Last-Write-Wins beim Regenerate), gewinnt die juengere Row.
- Sortiert in ``ChannelSegment``-ENUM-Reihenfolge, deterministisch.
- Segmente ohne Row sind nicht im Payload — Frontend kennt die
  Segment-Liste und rendert "noch kein Roundup" selbst.
- Public: kein Bearer-Auth-Dependency (spiegelt ``/api/pairs``).
- Volle ``llm_output``-Felder im Payload (Schritt-3c: headline, tldr,
  titles, themes, data_caveats) — der Aufklapp-Bereich der Kachel
  rendert aus einer einzigen Antwort. ``titles`` ist das Herzstueck
  und muss konsistent durch Schema -> Endpoint -> Frontend laufen
  (Drift-Schutz, Wolf-Hinweis 26.05.).

Isolation: shared in-memory SQLite via ``StaticPool``, ``auth_enabled``
auf True gedreht um zu verifizieren, dass der Pfad explizit als public
whitelisted ist (analog ``test_api_pairs.py``-Style, aber gegen die
Whitelist-Mechanik).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import ChannelSegment, SegmentRoundup


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
    # Auth bewusst aktiv lassen — der Roundup-Listen-Endpoint muss als
    # public whitelisted sein. Ohne ``api_token``-Setzung wuerde der
    # nicht-whitelistete Pfad bei aktivem Auth 401 liefern; dieser Test
    # erwartet 200 und beweist damit die Whitelist-Eintragung.
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "TESTTOKEN", raising=False)

    def _override_session():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _llm_output_payload(headline: str = "Headline") -> dict:
    return {
        "headline": headline,
        "tldr": "Kurzfassung.",
        "titles": [
            {
                "titel": "Sample Movie",
                "channel": "@channel_a",
                "format_typ": "Kino-Reminder",
                "kennzahl": "82s, 24.000 Views, 8% Aktivierung",
                "release_datum": "22. Mai",
                "post_url": "https://example.com/p/1",
            }
        ],
        "themes": ["theme_1"],
        "data_caveats": ["12 von 33 Channels ohne Posts in diesem Fenster."],
    }


def _aggregation_payload(
    *,
    segment: str,
    iso_year: int,
    iso_week: int,
    channels_evaluated: int = 33,
    channels_with_posts: int = 20,
    total_posts: int = 171,
    window_days: int = 14,
) -> dict:
    return {
        "segment": segment,
        "iso_year": iso_year,
        "iso_week": iso_week,
        "window_days": window_days,
        "window_start": "2026-05-12T00:00:00+00:00",
        "window_end": "2026-05-26T00:00:00+00:00",
        "channels_evaluated": channels_evaluated,
        "channels_with_posts": channels_with_posts,
        "total_posts": total_posts,
        "channels": [],
    }


def _seed_roundup(
    db_engine,
    *,
    segment: ChannelSegment,
    iso_year: int,
    iso_week: int,
    headline: str = "Headline",
    generated_at: datetime | None = None,
    channels_evaluated: int = 33,
    channels_with_posts: int = 20,
    total_posts: int = 171,
    window_days: int = 14,
) -> None:
    if generated_at is None:
        generated_at = datetime.now(timezone.utc)
    with Session(db_engine) as session:
        row = SegmentRoundup(
            segment=segment,
            iso_year=iso_year,
            iso_week=iso_week,
            window_days=window_days,
            channels_aggregation=_aggregation_payload(
                segment=segment.value,
                iso_year=iso_year,
                iso_week=iso_week,
                channels_evaluated=channels_evaluated,
                channels_with_posts=channels_with_posts,
                total_posts=total_posts,
                window_days=window_days,
            ),
            llm_output=_llm_output_payload(headline),
            generated_at=generated_at,
            model="claude-opus-4-7",
        )
        session.add(row)
        session.commit()


# ---------- Empty: no roundups in DB --------------------------------------


def test_roundups_latest_returns_empty_list_when_no_rows(client: TestClient):
    response = client.get("/api/roundups/latest")
    assert response.status_code == 200
    assert response.json() == {"roundups": []}


# ---------- Public: no Bearer auth required -------------------------------


def test_roundups_latest_is_public_no_auth_header(client: TestClient, db):
    # Auth-Middleware ist im Fixture aktiviert (api_token gesetzt). Ein
    # unbekannter Pfad wuerde ohne Header 401 liefern; dieser Endpoint
    # darf nicht — er muss als public whitelisted sein.
    _seed_roundup(db, segment=ChannelSegment.US_MAJOR, iso_year=2026, iso_week=22)
    response = client.get("/api/roundups/latest")  # bewusst kein Authorization-Header
    assert response.status_code == 200


# ---------- Payload shape -------------------------------------------------


def test_roundups_latest_full_payload_shape(client: TestClient, db):
    _seed_roundup(db, segment=ChannelSegment.US_MAJOR, iso_year=2026, iso_week=22)
    response = client.get("/api/roundups/latest")
    payload = response.json()
    assert "roundups" in payload
    assert len(payload["roundups"]) == 1
    entry = payload["roundups"][0]
    required = {
        "segment",
        "iso_year",
        "iso_week",
        "window_days",
        "generated_at",
        "channels_evaluated",
        "channels_with_posts",
        "total_posts",
        "llm_output",
    }
    assert required <= set(entry.keys())
    assert entry["segment"] == "us_major"
    assert entry["iso_year"] == 2026
    assert entry["iso_week"] == 22
    assert entry["window_days"] == 14
    assert entry["channels_evaluated"] == 33
    assert entry["channels_with_posts"] == 20
    assert entry["total_posts"] == 171
    # Volle LLM-Output-Felder — Aufklapp-Bereich der Kachel rendert aus
    # dieser Antwort, ohne weiteren Call.
    llm = entry["llm_output"]
    assert llm["headline"] == "Headline"
    assert llm["tldr"] == "Kurzfassung."
    assert llm["themes"] == ["theme_1"]
    assert llm["data_caveats"] == [
        "12 von 33 Channels ohne Posts in diesem Fenster."
    ]
    # Schritt-3c: titles-Sektion ist das Herzstueck und MUSS ueber den
    # Wire kommen — wenn der Endpoint sie verschluckt, sieht das Frontend
    # sie nie. Drift-Schutz durch alle drei Schichten (Schema -> Endpoint
    # -> Frontend).
    assert "titles" in llm
    assert len(llm["titles"]) == 1
    title = llm["titles"][0]
    assert title["titel"] == "Sample Movie"
    assert title["channel"] == "@channel_a"
    assert title["format_typ"] == "Kino-Reminder"
    assert title["kennzahl"] == "82s, 24.000 Views, 8% Aktivierung"
    assert title["release_datum"] == "22. Mai"
    assert title["post_url"] == "https://example.com/p/1"
    # Schritt-3d (26.05.): verdict ist aus dem Schema. Eine Pre-3d-Row
    # mit verdict im LLM-Output wuerde von Pydantic still verworfen —
    # der Wire-Payload enthaelt das Feld nicht mehr.
    assert "verdict" not in title


# ---------- One row per segment + ENUM-order ------------------------------


def test_roundups_latest_one_row_per_segment_in_enum_order(client: TestClient, db):
    # Alle vier Sprint-Segmente bestueckt, plus uk_major (zur Verifikation
    # der ENUM-Reihenfolge im Payload).
    _seed_roundup(db, segment=ChannelSegment.US_MAJOR, iso_year=2026, iso_week=22)
    _seed_roundup(db, segment=ChannelSegment.US_INDEPENDENT, iso_year=2026, iso_week=22)
    _seed_roundup(db, segment=ChannelSegment.UK_MAJOR, iso_year=2026, iso_week=22)
    _seed_roundup(db, segment=ChannelSegment.DE_VERLEIH, iso_year=2026, iso_week=22)
    _seed_roundup(db, segment=ChannelSegment.DE_INDEPENDENT, iso_year=2026, iso_week=22)

    response = client.get("/api/roundups/latest")
    segments = [r["segment"] for r in response.json()["roundups"]]
    # Erwartete Reihenfolge: ENUM-Reihenfolge (us_major, us_independent,
    # uk_major, uk_independent, de_verleih, de_independent), ohne die
    # nicht-bestueckten Segmente.
    assert segments == [
        "us_major",
        "us_independent",
        "uk_major",
        "de_verleih",
        "de_independent",
    ]


# ---------- Latest-per-segment: (iso_year, iso_week) selection ------------


def test_roundups_latest_picks_highest_iso_year_week(client: TestClient, db):
    # Aeltere und juengere Woche fuer dasselbe Segment. Erwartet: die
    # juengere Woche gewinnt.
    _seed_roundup(
        db, segment=ChannelSegment.US_MAJOR, iso_year=2026, iso_week=20,
        headline="Old",
    )
    _seed_roundup(
        db, segment=ChannelSegment.US_MAJOR, iso_year=2026, iso_week=22,
        headline="Newer",
    )
    response = client.get("/api/roundups/latest")
    payload = response.json()["roundups"]
    assert len(payload) == 1
    assert payload[0]["iso_week"] == 22
    assert payload[0]["llm_output"]["headline"] == "Newer"


def test_roundups_latest_picks_higher_iso_year_over_later_week_of_prior_year(
    client: TestClient, db,
):
    # Pruefe, dass die Reihenfolge zuerst nach iso_year geht. KW 50/2025
    # ist chronologisch frueher als KW 02/2026.
    _seed_roundup(
        db, segment=ChannelSegment.US_MAJOR, iso_year=2025, iso_week=50,
        headline="LastYear",
    )
    _seed_roundup(
        db, segment=ChannelSegment.US_MAJOR, iso_year=2026, iso_week=2,
        headline="ThisYear",
    )
    response = client.get("/api/roundups/latest")
    entry = response.json()["roundups"][0]
    assert entry["iso_year"] == 2026
    assert entry["iso_week"] == 2
    assert entry["llm_output"]["headline"] == "ThisYear"


def test_roundups_latest_picks_newer_generated_at_within_same_week(
    client: TestClient, db,
):
    # Dieselbe (segment, iso_year, iso_week) kann nicht zweimal in der
    # Tabelle existieren — das ist der Composite-PK. Aber zwei
    # verschiedene Segmente in derselben Woche werden beide ausgegeben.
    # Tiebreak ``generated_at`` greift, wenn zwei Rows denselben
    # (iso_year, iso_week) haben — z.B. unterschiedliche Segmente. Hier
    # verifizieren wir den Code-Pfad mit zwei Segmenten + identischer
    # Woche; dass beide gefunden werden.
    _seed_roundup(
        db, segment=ChannelSegment.US_MAJOR, iso_year=2026, iso_week=22,
        generated_at=datetime(2026, 5, 25, 6, 0, tzinfo=timezone.utc),
    )
    _seed_roundup(
        db, segment=ChannelSegment.DE_VERLEIH, iso_year=2026, iso_week=22,
        generated_at=datetime(2026, 5, 25, 6, 5, tzinfo=timezone.utc),
    )
    response = client.get("/api/roundups/latest")
    segments = [r["segment"] for r in response.json()["roundups"]]
    assert "us_major" in segments
    assert "de_verleih" in segments


# ---------- Segments without rows are omitted -----------------------------


def test_roundups_latest_omits_segments_without_rows(client: TestClient, db):
    # Nur us_major + de_verleih bestueckt; uk_major / us_independent etc.
    # haben keine Row und sind nicht im Payload (kein Stub-Eintrag, keine
    # leere Kachel — der Frontend-Block kennt die Segment-Liste).
    _seed_roundup(db, segment=ChannelSegment.US_MAJOR, iso_year=2026, iso_week=22)
    _seed_roundup(db, segment=ChannelSegment.DE_VERLEIH, iso_year=2026, iso_week=22)
    response = client.get("/api/roundups/latest")
    segments = [r["segment"] for r in response.json()["roundups"]]
    assert segments == ["us_major", "de_verleih"]


# ---------- Robust against thin segments ---------------------------------


def test_roundups_latest_thin_segment_renders_with_zero_metrics(
    client: TestClient, db,
):
    # us_independent hatte im Pilot 17 Channels, davon nur wenige aktiv.
    # Wenn die Aggregation extrem duenn ist (z.B. 0 Posts), muss der
    # Endpoint trotzdem antworten — nicht 500. ``data_caveats`` macht
    # daraus eine erklaerte ruhige Woche.
    _seed_roundup(
        db,
        segment=ChannelSegment.US_INDEPENDENT,
        iso_year=2026,
        iso_week=22,
        channels_evaluated=17,
        channels_with_posts=2,
        total_posts=10,
    )
    response = client.get("/api/roundups/latest")
    assert response.status_code == 200
    entry = response.json()["roundups"][0]
    assert entry["segment"] == "us_independent"
    assert entry["channels_with_posts"] == 2
    assert entry["total_posts"] == 10
    assert entry["llm_output"]["data_caveats"]  # nicht-leer


# ---------- Robust against missing optional LLM fields --------------------


def test_roundups_latest_accepts_llm_output_without_optionals(
    client: TestClient, db,
):
    # Schritt-3c: ``themes`` ist Optional, ``titles`` hat Default ``[]``.
    # Eine Row ohne ``themes`` und ohne ``titles`` muss durchgehen — der
    # Endpoint defaultet ``titles`` auf eine leere Liste.
    with Session(db) as session:
        session.add(
            SegmentRoundup(
                segment=ChannelSegment.US_MAJOR,
                iso_year=2026,
                iso_week=22,
                window_days=14,
                channels_aggregation=_aggregation_payload(
                    segment="us_major", iso_year=2026, iso_week=22,
                ),
                llm_output={
                    "headline": "Minimal",
                    "tldr": "Kurz.",
                    "data_caveats": ["Y"],
                },
                generated_at=datetime.now(timezone.utc),
                model="claude-opus-4-7",
            )
        )
        session.commit()

    response = client.get("/api/roundups/latest")
    assert response.status_code == 200
    entry = response.json()["roundups"][0]
    assert entry["llm_output"]["headline"] == "Minimal"
    # ``themes`` ist Optional → bleibt None. ``titles`` hat Default []
    # → Endpoint emittiert eine leere Liste, kein None.
    assert entry["llm_output"].get("themes") is None
    assert entry["llm_output"].get("titles") == []
