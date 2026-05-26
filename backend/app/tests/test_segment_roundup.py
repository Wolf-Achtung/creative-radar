"""Tests fuer den Segment-Roundup-Generator (Master-Plan-Schritt-3).

Mockt die Anthropic-Schicht und seedet ein synthetisches Channel-/Post-
Inventar. Verifiziert:

1. Channel-Selection: nur Channels mit dem angefragten Segment kommen
   durch (Pair-Pool-Channels mit ``segment = NULL`` werden ignoriert).
2. Per-Channel-Aggregation: Top-N-Posts (Default 5), Hashtag-Counter,
   Posts-Count im Zeitfenster.
3. Segment-Aggregation: channels_evaluated, channels_with_posts,
   total_posts.
4. Prompt-Aufbau: knapper Markdown, kein JSON-Anhang.
5. LLM-Antwort wird in ``SegmentRoundupLLMReport`` geparst und persistiert.
6. Idempotenz: Re-Run mit gleichem (segment, year, week) ueberschreibt
   die existierende Row (Last-Write-Wins).
7. Disjunkt: Pair-Pool-Channel mit ``segment = NULL`` taucht NICHT auf.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import (
    Channel,
    ChannelSegment,
    Post,
    SegmentRoundup as SegmentRoundupRow,
)
from app.services import anthropic_client as anthropic_module
from app.services import segment_roundup as roundup_module
from app.services.segment_roundup import (
    _select_channels_for_segment,
    aggregate_segment,
    generate_and_persist_roundup,
)


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_roundup_", suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        try:
            os.unlink(path)
        except OSError:
            pass


def _seed_channel(db_engine, *, handle: str, platform: str, segment: ChannelSegment | None,
                  market: str = "US", active: bool = True) -> Channel:
    with Session(db_engine) as session:
        ch = Channel(
            id=uuid4(),
            name=handle,
            handle=handle,
            url=f"https://www.{platform}.com/{handle}",
            platform=platform,
            market=market,
            active=active,
            mvp=True,
            segment=segment,
        )
        session.add(ch)
        session.commit()
        session.refresh(ch)
        return ch


def _seed_post(
    db_engine,
    channel_id,
    *,
    days_ago: int,
    engagement: int,
    caption: str = "test caption #tag",
    views: int = 0,
) -> None:
    with Session(db_engine) as session:
        # Engagement = likes + comments + bookmarks + shares — wir
        # legen alles auf likes, damit die Sortierung deterministisch ist.
        post = Post(
            id=uuid4(),
            channel_id=channel_id,
            post_url=f"https://example.com/{uuid4().hex[:8]}",
            platform="instagram",
            published_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
            detected_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
            caption=caption,
            visible_likes=engagement,
            visible_comments=0,
            visible_bookmarks=0,
            visible_shares=0,
            visible_views=views,
            duration_seconds=20,
            raw_payload={},
        )
        session.add(post)
        session.commit()


def _mock_anthropic_response(monkeypatch, body: dict, usage: dict | None = None) -> MagicMock:
    """Patches messages_create_text + record_anthropic_call. Returns the mock.

    Schritt-4-Hinweis: ``messages_create_text`` wird ab Commit 2/N vom
    ``call_with_json_retry``-Helper im ``anthropic_client``-Modul
    aufgerufen — Patch muss am defining-Modul ansetzen, sonst trifft der
    Mock den Helper-Call nicht.
    """
    usage_ns = SimpleNamespace(
        input_tokens=(usage or {}).get("input_tokens", 1000),
        output_tokens=(usage or {}).get("output_tokens", 300),
    )
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(body))],
        usage=usage_ns,
    )
    mock = MagicMock(return_value=message)
    monkeypatch.setattr(anthropic_module, "messages_create_text", mock)
    monkeypatch.setattr(roundup_module, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(
        roundup_module, "record_anthropic_call",
        lambda *args, **kwargs: None,
    )
    return mock


def _minimal_llm_body() -> dict:
    return {
        "headline": "Roundup headline",
        "tldr": "Drei Sätze über das Segment.",
        "titles": [
            {
                "titel": "Sample Title",
                "channel": "@a24",
                "format_typ": "BTS",
                "kennzahl": "24s, 5.000 Reaktionen",
            }
        ],
        "themes": ["Indie-Releases", "Festival-Vorbereitung"],
        "data_caveats": ["2 von 5 Channels ohne Posts im Fenster"],
    }


# ---------------------------------------------------------------------------
# Test 1 — Channel-Selection: filtert auf Segment, ignoriert NULL + andere
# ---------------------------------------------------------------------------

def test_select_channels_filters_by_segment_only(db):
    _seed_channel(db, handle="a24", platform="instagram",
                  segment=ChannelSegment.US_INDEPENDENT)
    _seed_channel(db, handle="neonrated", platform="instagram",
                  segment=ChannelSegment.US_INDEPENDENT)
    # us_major — anderes Segment, darf nicht durchkommen
    _seed_channel(db, handle="hbo", platform="instagram",
                  segment=ChannelSegment.US_MAJOR)
    # Pair-Pool-Channel — segment NULL, disjunkt
    _seed_channel(db, handle="warnerbros", platform="instagram",
                  segment=None)
    # Inactive Channel, gleiches Segment — darf nicht
    _seed_channel(db, handle="oldindie", platform="instagram",
                  segment=ChannelSegment.US_INDEPENDENT, active=False)

    with Session(db) as session:
        results = _select_channels_for_segment(session, ChannelSegment.US_INDEPENDENT)

    handles = sorted(c.handle for c in results)
    assert handles == ["a24", "neonrated"]


# ---------------------------------------------------------------------------
# Test 2 — Per-Channel-Aggregation respektiert Top-N und Zeitfenster
# ---------------------------------------------------------------------------

def test_aggregate_segment_picks_top_n_within_window(db):
    ch = _seed_channel(db, handle="a24", platform="instagram",
                       segment=ChannelSegment.US_INDEPENDENT)
    # Im Fenster (14d) — 7 Posts mit absteigendem engagement
    for i in range(7):
        _seed_post(db, ch.id, days_ago=i, engagement=1000 - i * 100,
                   caption=f"in-window {i} #cinema")
    # Ausserhalb Fenster (20 Tage alt) — darf nicht
    _seed_post(db, ch.id, days_ago=20, engagement=99999, caption="too old")

    with Session(db) as session:
        agg = aggregate_segment(
            session, ChannelSegment.US_INDEPENDENT,
            window_days=14, top_posts_n=5,
        )

    assert agg.segment == "us_independent"
    assert agg.window_days == 14
    assert agg.channels_evaluated == 1
    assert agg.channels_with_posts == 1
    assert agg.total_posts == 7  # 7 im Fenster, 1 ausserhalb ignoriert
    assert len(agg.channels) == 1
    stats = agg.channels[0]
    assert stats.posts_count == 7
    assert len(stats.top_posts) == 5  # Top-5 trotz 7 verfuegbar
    # Sortierung absteigend nach engagement
    engs = [p.engagement_sum for p in stats.top_posts]
    assert engs == sorted(engs, reverse=True)
    # Hashtag-Counter
    assert any(h.tag == "cinema" for h in stats.top_hashtags)


# ---------------------------------------------------------------------------
# Test 3 — Channel ohne Posts im Fenster: erscheint mit posts_count=0
# ---------------------------------------------------------------------------

def test_aggregate_segment_handles_silent_channel(db):
    ch_active = _seed_channel(db, handle="active", platform="instagram",
                              segment=ChannelSegment.US_MAJOR)
    ch_silent = _seed_channel(db, handle="silent", platform="instagram",
                              segment=ChannelSegment.US_MAJOR)
    _seed_post(db, ch_active.id, days_ago=3, engagement=500)

    with Session(db) as session:
        agg = aggregate_segment(session, ChannelSegment.US_MAJOR, window_days=14)

    assert agg.channels_evaluated == 2
    assert agg.channels_with_posts == 1
    assert agg.total_posts == 1
    silent_stats = next(c for c in agg.channels if c.handle == "silent")
    assert silent_stats.posts_count == 0
    assert silent_stats.top_posts == []


# ---------------------------------------------------------------------------
# Test 4 — End-to-end-Generator + Persist (Anthropic gemockt)
# ---------------------------------------------------------------------------

def test_generate_and_persist_roundup_writes_row(db, monkeypatch):
    ch = _seed_channel(db, handle="a24", platform="instagram",
                       segment=ChannelSegment.US_INDEPENDENT)
    _seed_post(db, ch.id, days_ago=3, engagement=500)
    mock = _mock_anthropic_response(monkeypatch, _minimal_llm_body())

    with Session(db) as session:
        report = generate_and_persist_roundup(
            session, ChannelSegment.US_INDEPENDENT, window_days=14,
        )

    assert report.llm_output is not None
    assert report.llm_output.headline == "Roundup headline"
    assert mock.call_count == 1

    # Row in DB persistiert
    with Session(db) as session:
        row = session.get(
            SegmentRoundupRow,
            (ChannelSegment.US_INDEPENDENT, report.iso_year, report.iso_week),
        )
        assert row is not None
        assert row.segment == ChannelSegment.US_INDEPENDENT
        assert row.window_days == 14
        assert row.llm_output["headline"] == "Roundup headline"


# ---------------------------------------------------------------------------
# Test 5 — Idempotenz: Re-Run ueberschreibt die Row (Last-Write-Wins)
# ---------------------------------------------------------------------------

def test_generate_and_persist_roundup_is_last_write_wins(db, monkeypatch):
    ch = _seed_channel(db, handle="a24", platform="instagram",
                       segment=ChannelSegment.US_INDEPENDENT)
    _seed_post(db, ch.id, days_ago=3, engagement=500)
    _mock_anthropic_response(monkeypatch, _minimal_llm_body())

    with Session(db) as session:
        first = generate_and_persist_roundup(
            session, ChannelSegment.US_INDEPENDENT, window_days=14,
        )

    # Zweite Runde mit anderem headline
    second_body = _minimal_llm_body()
    second_body["headline"] = "Second-run headline"
    _mock_anthropic_response(monkeypatch, second_body)

    with Session(db) as session:
        second = generate_and_persist_roundup(
            session, ChannelSegment.US_INDEPENDENT, window_days=14,
        )

    assert second.llm_output.headline == "Second-run headline"
    # Genau eine Row in DB, nicht zwei
    with Session(db) as session:
        rows = list(session.exec(
            __import__("sqlmodel").select(SegmentRoundupRow)
            .where(SegmentRoundupRow.segment == ChannelSegment.US_INDEPENDENT)
        ).all())
        assert len(rows) == 1
        assert rows[0].llm_output["headline"] == "Second-run headline"


# ---------------------------------------------------------------------------
# Test 6 — Disjunkt: Pair-Pool-Channel (segment=NULL) wird nie aggregiert
# ---------------------------------------------------------------------------

def test_parse_cron_roundup_segments_default(caplog):
    """Default-CSV (alle vier produktiven Segmente) → vier ChannelSegment-
    Werte in der CSV-Reihenfolge."""
    from app.services.segment_roundup import parse_cron_roundup_segments
    result = parse_cron_roundup_segments(
        "us_major,us_independent,de_verleih,de_independent"
    )
    assert result == [
        ChannelSegment.US_MAJOR,
        ChannelSegment.US_INDEPENDENT,
        ChannelSegment.DE_VERLEIH,
        ChannelSegment.DE_INDEPENDENT,
    ]


def test_parse_cron_roundup_segments_whitespace_and_trailing_comma(caplog):
    """Tolerant fuer Whitespace und trailing comma (analog
    is_uk_enabled_for_pair-Parsing)."""
    from app.services.segment_roundup import parse_cron_roundup_segments
    result = parse_cron_roundup_segments("  us_major , de_verleih ,")
    assert result == [ChannelSegment.US_MAJOR, ChannelSegment.DE_VERLEIH]


def test_parse_cron_roundup_segments_unknown_token_warns_and_skips(monkeypatch):
    """Unbekannter Token → Warning-Log + skip, gueltige Tokens kommen
    durch.

    Direkter Logger-Mock statt caplog: andere Tests in der Full-Suite
    koennen propagate/handler-State des Loggers manipulieren und
    vergessen zurueckzubauen, dann sieht caplog nichts. Direct-Mock auf
    ``logger.warning`` ist immun dagegen.
    """
    from unittest.mock import MagicMock
    from app.services import segment_roundup as srm
    mock_warning = MagicMock()
    monkeypatch.setattr(srm.logger, "warning", mock_warning)

    result = srm.parse_cron_roundup_segments("us_major,fr_major,de_verleih")

    assert result == [ChannelSegment.US_MAJOR, ChannelSegment.DE_VERLEIH]
    # genau ein Warning fuer den unknown token
    assert mock_warning.call_count == 1
    args, kwargs = mock_warning.call_args
    assert args[0] == "cron_roundup_segments_unknown_value"
    assert kwargs["extra"]["token"] == "fr_major"


def test_parse_cron_roundup_segments_empty_value_errors(monkeypatch):
    """Wolf-Festlegung: leerer Gesamtwert darf NICHT still in
    'keine Roundups' kippen — ERROR-Log und leere Liste."""
    from unittest.mock import MagicMock
    from app.services import segment_roundup as srm
    mock_error = MagicMock()
    monkeypatch.setattr(srm.logger, "error", mock_error)

    result = srm.parse_cron_roundup_segments("")

    assert result == []
    assert mock_error.call_count == 1
    args, _ = mock_error.call_args
    assert args[0] == "cron_roundup_segments_empty"


def test_parse_cron_roundup_segments_all_unknown_errors(monkeypatch):
    """Wenn ALLE Tokens unbekannt sind, ist das ebenfalls ein
    leerer/unparsebarer Gesamtwert — ERROR-Log."""
    from unittest.mock import MagicMock
    from app.services import segment_roundup as srm
    mock_error = MagicMock()
    monkeypatch.setattr(srm.logger, "error", mock_error)
    # warning silenced damit es keine token-Warnings stoert (wir checken
    # nur den finalen Error)
    monkeypatch.setattr(srm.logger, "warning", MagicMock())

    result = srm.parse_cron_roundup_segments("fr_major,it_major")

    assert result == []
    # finaler "alle Tokens unbekannt"-Error fired
    error_messages = [call.args[0] for call in mock_error.call_args_list]
    assert "cron_roundup_segments_empty" in error_messages


def test_silent_channel_list_uses_platform_suffix_for_multi_platform_handles(db):
    """Schritt-4 Dedupe-Fix: Multi-Plattform-Handles im Silent-Block muessen
    ``@handle (platform)`` zeigen — ohne Plattform-Suffix erscheinen sie
    mehrfach als ``@disney`` und der LLM-Prompt verliert die
    Plattform-Unterscheidung."""
    from app.services.segment_roundup import _build_user_prompt
    # Gleicher Handle auf drei Plattformen, alle silent
    _seed_channel(db, handle="disney", platform="instagram",
                  segment=ChannelSegment.US_MAJOR)
    _seed_channel(db, handle="disney", platform="tiktok",
                  segment=ChannelSegment.US_MAJOR)
    _seed_channel(db, handle="disney", platform="youtube",
                  segment=ChannelSegment.US_MAJOR)

    with Session(db) as session:
        agg = aggregate_segment(session, ChannelSegment.US_MAJOR, window_days=14)
    prompt = _build_user_prompt(agg)

    assert "@disney (instagram)" in prompt
    assert "@disney (tiktok)" in prompt
    assert "@disney (youtube)" in prompt
    # Kein bare "@disney," ohne Plattform-Suffix
    assert "@disney," not in prompt
    assert "ohne Posts im Fenster (3)" in prompt


def test_pair_pool_channel_segment_null_is_disjoint(db, monkeypatch):
    pair_ch = _seed_channel(db, handle="warnerbros", platform="instagram",
                            segment=None)  # Pair-Pool
    _seed_post(db, pair_ch.id, days_ago=3, engagement=999)
    # Einen us_major-Channel daneben, sonst keine Posts → leere Aggregation
    _seed_channel(db, handle="hbo", platform="instagram",
                  segment=ChannelSegment.US_MAJOR)
    _mock_anthropic_response(monkeypatch, _minimal_llm_body())

    with Session(db) as session:
        agg = aggregate_segment(session, ChannelSegment.US_MAJOR, window_days=14)

    # warnerbros darf nicht in der us_major-Aggregation auftauchen
    handles = [c.handle for c in agg.channels]
    assert "warnerbros" not in handles
    assert handles == ["hbo"]
    assert agg.total_posts == 0  # hbo hat keine Posts, warnerbros wird ignoriert


# ---------------------------------------------------------------------------
# Test 7 — Bad-JSON-Antwort: llm_output bleibt None, kein Persist
# ---------------------------------------------------------------------------

def test_bad_json_response_persists_nothing(db, monkeypatch):
    ch = _seed_channel(db, handle="a24", platform="instagram",
                       segment=ChannelSegment.US_INDEPENDENT)
    _seed_post(db, ch.id, days_ago=3, engagement=500)

    # Mock returns malformed JSON — Helper repeats the call up to 2 times
    # (M2-retry pattern), all attempts return the same broken text, so
    # ``parsed`` stays None and persist-skip greift.
    text_block = SimpleNamespace(type="text", text='{"headline": "broken')
    usage_ns = SimpleNamespace(input_tokens=1000, output_tokens=100)
    message = SimpleNamespace(content=[text_block], usage=usage_ns)
    monkeypatch.setattr(anthropic_module, "messages_create_text",
                        MagicMock(return_value=message))
    monkeypatch.setattr(roundup_module, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(roundup_module, "record_anthropic_call",
                        lambda *a, **kw: None)

    with Session(db) as session:
        report = generate_and_persist_roundup(
            session, ChannelSegment.US_INDEPENDENT, window_days=14,
        )

    assert report.llm_output is None
    assert report.raw_llm_text is not None
    # Keine Row in DB
    with Session(db) as session:
        rows = list(session.exec(
            __import__("sqlmodel").select(SegmentRoundupRow)
        ).all())
        assert rows == []


# ---------------------------------------------------------------------------
# Schritt-3c (26.05.) — Generator-Erweiterungen: reichere Post-Metriken
# im Prompt, System-Prompt erzwingt titles + verdict, Top-N default 8.
# ---------------------------------------------------------------------------


def test_default_top_posts_n_is_eight():
    """Schritt-3c Wolf-Ping 1 (b): Top-N von 5 auf 8 erhoeht — der LLM
    braucht mehr Material fuer aussagekraeftige Titel-Bloecke."""
    from app.services.segment_roundup import ROUNDUP_DEFAULT_TOP_POSTS_N
    assert ROUNDUP_DEFAULT_TOP_POSTS_N == 8


def test_format_post_line_includes_rich_metrics():
    """Schritt-3c: Post-Zeile muss views, likes, activation-rate, duration
    fuehren — damit das LLM echte Zahlen in ``titles[*].kennzahl`` zitieren
    kann. Vorher: nur engagement_sum + views."""
    from app.schemas.insights import RankedPost
    from app.services.segment_roundup import _format_post_line

    p = RankedPost(
        post_url="https://example.com/p/1",
        caption_excerpt="Reveal trailer",
        views=24_000, likes=1_800, comments=120, saves=40, shares=15,
        engagement_sum=1_975, activation_rate=0.082,
        duration_seconds=82, title_local="Mandalorian", platform="instagram",
    )
    line = _format_post_line(1, p)
    # Pair-Brief-Stil-Anker: views/likes/akt./Sek./Titel-Marker
    assert "24,000 views" in line
    assert "1,800 likes" in line
    assert "8.2% akt." in line
    assert "82s" in line
    assert "[*Mandalorian*]" in line
    assert "Reveal trailer" in line
    assert "https://example.com/p/1" in line


def test_format_post_line_image_post_no_views_only_likes():
    """Daten-Hygiene-Sprint, A1 (Wolf 26.05.): Bild-Posts und Carousels
    landen mit ``views = None``/``0`` in der DB — Instagram liefert
    fuer Foto-Posts keine View-Zahl, Apify mapped die video-only-Felder.

    Erwartung: bei ``views == 0 && likes > 0`` faellt die Post-Zeile auf
    eine Like-only-Form zurueck. Kein '0 views', kein '0,0% akt.', kein
    irrefuehrender Schwaechen-Eindruck — die Like-Zahl ist der
    Daten-Anker, den das LLM als ``kennzahl`` uebernimmt.
    """
    from app.schemas.insights import RankedPost
    from app.services.segment_roundup import _format_post_line

    p = RankedPost(
        post_url="https://example.com/p/img",
        caption_excerpt="The End of Oak Street",
        views=0, likes=2_710, comments=85,
        engagement_sum=2_795, activation_rate=0.0,
        # duration_seconds bewusst None — Bild-Post hat keine Laufzeit.
        title_local="The End of Oak Street", platform="instagram",
    )
    line = _format_post_line(1, p)
    # Like-only-Form: keine views, keine akt.-Prozent, keine Sekunden.
    assert "0 views" not in line
    assert "0.0% akt." not in line
    assert "0% akt." not in line
    # Likes erscheinen mit Tausender-Komma und 'likes'-Suffix.
    assert "2,710 likes" in line
    # Titel-Marker bleibt erhalten.
    assert "[*The End of Oak Street*]" in line
    # Caption + URL bleiben unveraendert nachgezogen.
    assert "The End of Oak Street" in line
    assert "https://example.com/p/img" in line


def test_format_post_line_video_post_keeps_full_metrics():
    """Komplement zum Bild-Post-Branch: Video-Posts mit echten Views
    behalten die volle Form (views, likes, akt., Sekunden). Sicherheits-
    netz gegen einen ueberbreiten Branch-Match."""
    from app.schemas.insights import RankedPost
    from app.services.segment_roundup import _format_post_line

    p = RankedPost(
        views=12_000, likes=800, engagement_sum=900, activation_rate=0.075,
        duration_seconds=45, title_local="Trailer Drop",
        platform="instagram",
    )
    line = _format_post_line(1, p)
    assert "12,000 views" in line
    assert "800 likes" in line
    assert "7.5% akt." in line
    assert "45s" in line


def test_format_post_line_dead_post_with_zero_likes_uses_default_branch():
    """Edge-Case: views=0 UND likes=0 (kompletter toter Post).
    Soll den DEFAULT-Branch nehmen, nicht den Bild-Post-Branch — der
    Bild-Post-Branch ist gezielt fuer ``views == 0 && likes > 0``."""
    from app.schemas.insights import RankedPost
    from app.services.segment_roundup import _format_post_line

    p = RankedPost(
        views=0, likes=0, engagement_sum=0, activation_rate=0.0,
        platform="instagram",
    )
    line = _format_post_line(1, p)
    # Default-Branch zeigt das volle Null-Bild — bewusst, weil dort
    # nichts passiert ist.
    assert "0 views" in line
    assert "0 likes" in line


def test_system_prompt_explains_image_post_no_views():
    """Schritt-Daten-Hygiene-A1 (Wolf 26.05.): der System-Prompt MUSS
    dem LLM erklaeren, dass Bild-Post-Zeilen ohne Views korrekt sind
    (nicht 'kommt nicht an'). Sonst fehlt der semantische Hintergrund
    zur neuen Like-only-Post-Form."""
    from app.services.segment_roundup import ROUNDUP_SYSTEM_PROMPT
    assert "Bild-Posts" in ROUNDUP_SYSTEM_PROMPT
    assert "Carousels" in ROUNDUP_SYSTEM_PROMPT
    # Klartext-Hinweis, dass das LLM die Like-Zahl als Kennzahl nehmen soll
    assert "Like-Zahl als Kennzahl" in ROUNDUP_SYSTEM_PROMPT


def test_format_post_line_marks_series_content_type():
    """Schritt-3c uebernimmt den Pair-Brief-Marker: bei content_type
    'Series' wird der Titel als ``[*Title* — Serie]`` markiert, damit
    der LLM-Roundup Theatrical von Streaming-Serien trennen kann."""
    from app.schemas.insights import RankedPost
    from app.services.segment_roundup import _format_post_line

    p = RankedPost(
        views=10, likes=2, engagement_sum=12, activation_rate=0.05,
        title_local="The Last of Us", content_type="Series",
        platform="instagram",
    )
    line = _format_post_line(1, p)
    assert "[*The Last of Us* — Serie]" in line


def test_user_prompt_contains_per_post_metrics(db):
    """Schritt-3c: der gesamte User-Prompt muss die reicheren Metriken
    enthalten — Sanity-Check ueber die _build_user_prompt-Schicht, sodass
    keine spaetere Refactor-Aktion sie wieder herausfiltert."""
    from app.services.segment_roundup import _build_user_prompt

    ch = _seed_channel(db, handle="warnerbros", platform="instagram",
                       segment=ChannelSegment.US_MAJOR)
    # views=10_000 erzwingt den vollen Metrik-Branch der Post-Zeile;
    # mit dem Default views=0 wuerde das Like-only-Branch greifen
    # (Daten-Hygiene-A1, Wolf 26.05.).
    _seed_post(db, ch.id, days_ago=2, engagement=1200, views=10_000,
               caption="reveal trailer dropping today")

    with Session(db) as session:
        agg = aggregate_segment(session, ChannelSegment.US_MAJOR, window_days=14)
    prompt = _build_user_prompt(agg)

    # Channel-Header zeigt avg activation in Prozent
    assert "avg activation" in prompt
    # Mindestens ein Top-Post zitiert views/likes/akt./Sekunden-Form
    assert "views" in prompt
    assert "likes" in prompt
    assert "akt." in prompt


def test_system_prompt_forces_titles_schema_without_verdict():
    """Drift-Schutz fuer den Prompt-Vertrag (Wolf 26.05., Schritt 3d):
    Der System-Prompt MUSS das ``titles``-Schema mit den Kern-Feldern
    benennen (titel, channel, format_typ, kennzahl). Wenn jemand das
    Schema-Beispiel umschreibt und ein Pflichtfeld verliert, schlaegt
    dieser Test an.

    Schritt 3d: ``verdict`` darf NICHT mehr im Prompt-Schema stehen —
    das Schema hat das Feld verloren, der Prompt darf es nicht mehr
    anfordern (sonst produziert das LLM ein Feld, das Pydantic
    anschliessend stillschweigend verwirft).
    """
    from app.services.segment_roundup import ROUNDUP_SYSTEM_PROMPT
    # Schema-Felder im Beispiel-JSON
    assert '"titles":' in ROUNDUP_SYSTEM_PROMPT
    assert '"channel":' in ROUNDUP_SYSTEM_PROMPT
    assert '"format_typ":' in ROUNDUP_SYSTEM_PROMPT
    assert '"kennzahl":' in ROUNDUP_SYSTEM_PROMPT
    # Schritt 3d: verdict raus aus Schema und Vokabular-Sektion.
    assert '"verdict":' not in ROUNDUP_SYSTEM_PROMPT
    assert "VERDICT-VOKABULAR" not in ROUNDUP_SYSTEM_PROMPT


def test_system_prompt_drops_pre_3c_bewertungs_verbot():
    """Wolf-Festlegung 26.05. (Schritt 3c): Der alte Verbots-Satz
    ('KEINE Empfehlungen', '... nicht im Sinne von Bewertung') muss
    raus.

    Schritt 3d: Bewertung wird vom LLM nicht mehr in ein Etikett
    gegossen, aber Konkretheit (Titel, Channel, Kennzahl, Headline mit
    Haltung) bleibt das Soll — also weiter kein Verbots-Satz aus 3c."""
    from app.services.segment_roundup import ROUNDUP_SYSTEM_PROMPT
    assert "KEINE Empfehlungen" not in ROUNDUP_SYSTEM_PROMPT
    assert "nicht im Sinne von" not in ROUNDUP_SYSTEM_PROMPT


def test_system_prompt_keeps_no_market_comparison_anchor():
    """Wolf-Festlegung: kein Markt-Vergleich, kein Cross-Segment —
    DAS bleibt. Nur das Bewertungsverbot fliegt raus."""
    from app.services.segment_roundup import ROUNDUP_SYSTEM_PROMPT
    assert "KEIN Markt-Vergleich" in ROUNDUP_SYSTEM_PROMPT
    assert "Cross-Segment" in ROUNDUP_SYSTEM_PROMPT


def test_system_prompt_keeps_concrete_titles_anchor():
    """Schritt 3d (Wolf 26.05.): das Verdict-Etikett ist raus, aber die
    Konkretheits-Anker bleiben. Der Prompt MUSS weiter benennen, dass
    Filme/Serien und Channels namentlich + mit Kennzahlen erscheinen
    sollen. Sonst kippt der Roundup-Output zurueck in die nuechterne
    Aktivitaets-Aufzaehlung von Schritt 3."""
    from app.services.segment_roundup import ROUNDUP_SYSTEM_PROMPT
    # Konkretheit
    assert "Konkret und namentlich" in ROUNDUP_SYSTEM_PROMPT
    assert "Filme/Serien" in ROUNDUP_SYSTEM_PROMPT
    # Headline mit Haltung
    assert "Headline mit Pointe" in ROUNDUP_SYSTEM_PROMPT
    # Kennzahlen-Sektion
    assert "KENNZAHLEN" in ROUNDUP_SYSTEM_PROMPT
