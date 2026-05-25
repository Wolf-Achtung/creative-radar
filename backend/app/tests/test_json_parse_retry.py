"""Sprint M2 — JSON-Parse-Retry-Logic für die Insight-Engine.

Hintergrund: Der intermittierende Anthropic-200-mit-invalidem-JSON-Glitch
(17.05. Disney, 25.05. warnerbros) führte zu stillen Persist-Skips ohne
Re-Call-Versuch. M2 baut eine zweistufige Recovery: lenient Substring-
Extraktion (A, kostenlos) und bis zu zwei frische ``messages_create_text``-
Calls (B). Tests hier verifizieren, dass jede Stufe wie erwartet greift
und der Cron-Lauf bei totalem Fehlschlag sauber überspringt statt zu
crashen.

Test-Pattern: monkeypatch auf ``messages_create_text`` mit
``side_effect``-Listen oder Custom-Funktionen, damit pro Pair-Generation
unterschiedliche Anthropic-Responses simuliert werden können.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.services import insight_engine as engine_module
from app.schemas.insights import (
    ChannelStats,
    LLMReport,
    PairAggregation,
    PlatformAggregation,
    RankedPost,
    TitleCoverage,
)


# ---------------------------------------------------------------------------
# Shared helpers — minimal copies from test_brief_previous_context.py so the
# M2-Suite kein Cross-File-Import auf private Test-Helper braucht. Bewusst
# duplizierter Aggregation-Builder: die zwei Test-Dateien dürfen unabhängig
# voneinander refaktorierbar bleiben.
# ---------------------------------------------------------------------------


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_m2_retry_", suffix=".db")
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


def _empty_title_coverage() -> TitleCoverage:
    return TitleCoverage.model_construct(
        titles_in_both_markets=[],
        de_only_titles=[],
        us_only_titles=[],
        de_assets_with_title=0,
        de_assets_total=0,
        us_assets_with_title=0,
        us_assets_total=0,
        uk_only_titles=[],
        uk_assets_with_title=0,
        uk_assets_total=0,
        overall_coverage_pct=0.0,
    )


def _channel(handle: str, market: str) -> ChannelStats:
    return ChannelStats.model_construct(
        handle=handle,
        market=market,
        channel_id=f"{handle}-id",
        channel_found=True,
        posts_count=0,
        assets_count=0,
        coverage_pct=0.0,
        top_hashtags=[],
        avg_caption_length=0.0,
        avg_duration_seconds=None,
        duration_buckets={},
        top_posts=[],
        avg_engagement=0.0,
        avg_activation_rate=0.0,
        historical_top_posts=[],
        ranked_posts=[],
    )


def _build_aggregation(pair_key: str, iso_year: int, iso_week: int) -> PairAggregation:
    de = _channel("test_de", "DE")
    us = _channel("test_us", "US")
    platform_agg = PlatformAggregation.model_construct(
        platform="tiktok",
        de_channel=de,
        us_channel=us,
        uk_channel=None,
        cross_market_matches=[],
        title_coverage=_empty_title_coverage(),
        notes=[],
    )
    now = datetime(iso_year, 5, 17, tzinfo=timezone.utc)
    return PairAggregation.model_construct(
        pair_key=pair_key,
        pair_label=f"{pair_key} test",
        platform="tiktok",
        window_days=30,
        window_start=now - timedelta(days=30),
        window_end=now,
        iso_week=iso_week,
        iso_year=iso_year,
        de_channel=de,
        us_channel=us,
        uk_channel=None,
        cross_market_matches=[],
        title_coverage=_empty_title_coverage(),
        notes=[],
        per_platform=[platform_agg],
    )


def _seed_pair_in_pairs(monkeypatch, pair_key: str) -> None:
    monkeypatch.setitem(engine_module.PAIRS, pair_key, {
        "display_name": pair_key,
        "markets": ["DE", "US"],
        "label": f"{pair_key} test",
        "platforms": {"tiktok": [{"handle": pair_key, "market": "DE"}]},
        "platform": "tiktok",
        "channels": [],
        "enabled": True,
        "reason": None,
    })


def _valid_llm_body() -> dict:
    """Minimal-valides ``LLMReport``-Dict — alle required fields gesetzt."""
    return LLMReport.model_validate({
        "headline": "M2-retry-recovered",
        "tldr": "valider Recovery-Body",
        "trends": [],
        "actions": [],
        "cross_market_insight": {
            "de_vs_us": "x",
            "transfer_opportunity": "y",
        },
        "risks": [],
        "data_caveats": [],
    }).model_dump(mode="json")


def _msg(text: str, *, input_tokens: int = 8000, output_tokens: int = 3000):
    """Baut ein SDK-Message-Shape (content + usage) für Mock-Returns."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _patch_engine_basics(monkeypatch, pair_key: str) -> None:
    """Mockt aggregate_pair + is_anthropic_configured, lässt
    ``messages_create_text`` und ``record_anthropic_call`` frei für den
    Test, damit jede Test-Funktion sie individuell konfigurieren kann."""
    _seed_pair_in_pairs(monkeypatch, pair_key)
    monkeypatch.setattr(engine_module, "is_anthropic_configured", lambda: True)

    def fake_aggregate_pair(session, pk, *, window_days=30, now=None):
        return _build_aggregation(pk, 2026, 21)

    monkeypatch.setattr(engine_module, "aggregate_pair", fake_aggregate_pair)


# ---------------------------------------------------------------------------
# Test 1 — Codefence-Wrapping wird vom Bestands-``_strip_codefence``
# gerettet (Fall (a)). Regressionstest: kein Re-Call, parse_path=strict
# wegen Strip-First-Reihenfolge.
# ---------------------------------------------------------------------------

def test_codefence_wrapped_json_parses_without_recall(db, monkeypatch, caplog):
    _patch_engine_basics(monkeypatch, "test_pair_codefence")
    body = _valid_llm_body()
    wrapped = "```json\n" + json.dumps(body) + "\n```"
    anthropic_mock = MagicMock(return_value=_msg(wrapped))
    record_mock = MagicMock()
    monkeypatch.setattr(engine_module, "messages_create_text", anthropic_mock)
    monkeypatch.setattr(engine_module, "record_anthropic_call", record_mock)

    with caplog.at_level(logging.WARNING, logger="app.services.insight_engine"):
        with Session(db) as session:
            report = engine_module.generate_weekly_report(
                session, "test_pair_codefence", window_days=30,
            )

    assert report.llm_output is not None
    assert report.llm_output.headline == "M2-retry-recovered"
    assert anthropic_mock.call_count == 1
    assert record_mock.call_count == 1
    # Kein Retry-Log, kein Final-Failure-Log
    retry_records = [r for r in caplog.records if "json-parse-retry" in r.getMessage()]
    failure_records = [r for r in caplog.records if "json-parse-failed" in r.getMessage()]
    assert retry_records == []
    assert failure_records == []


# ---------------------------------------------------------------------------
# Test 2 — Preamble + JSON-Body: Lenient-Parsing (A) rettet ohne Re-Call.
# Deckt Fall (b) aus dem M2-Briefing — Modell schreibt "Here is the analysis:"
# vor dem JSON-Objekt.
# ---------------------------------------------------------------------------

def test_preamble_before_json_rescued_by_lenient_parse_without_recall(
    db, monkeypatch, caplog,
):
    _patch_engine_basics(monkeypatch, "test_pair_preamble")
    body = _valid_llm_body()
    preamble_text = "Hier ist die geforderte Analyse:\n\n" + json.dumps(body)
    anthropic_mock = MagicMock(return_value=_msg(preamble_text))
    record_mock = MagicMock()
    monkeypatch.setattr(engine_module, "messages_create_text", anthropic_mock)
    monkeypatch.setattr(engine_module, "record_anthropic_call", record_mock)

    with caplog.at_level(logging.INFO, logger="app.services.insight_engine"):
        with Session(db) as session:
            report = engine_module.generate_weekly_report(
                session, "test_pair_preamble", window_days=30,
            )

    assert report.llm_output is not None
    assert report.llm_output.headline == "M2-retry-recovered"
    # Nur EIN Anthropic-Call, kein Re-Call nötig
    assert anthropic_mock.call_count == 1
    assert record_mock.call_count == 1
    recovered_records = [
        r for r in caplog.records
        if "insight-engine-json-parse-recovered" in r.getMessage()
    ]
    assert len(recovered_records) == 1
    rec = recovered_records[0]
    assert rec.parse_path == "lenient"
    assert rec.anthropic_calls == 1
    assert rec.recall_count == 0


# ---------------------------------------------------------------------------
# Test 3 — Erster Call invalides JSON (Fall (d)), zweiter Call sauber:
# B-Call-1 (=erster Retry) greift, Brief persistiert. Cost-Erfassung muss
# pro Call separat feuern.
# ---------------------------------------------------------------------------

def test_recall_after_invalid_json_recovers_and_persists(db, monkeypatch, caplog):
    _patch_engine_basics(monkeypatch, "test_pair_recall_b1")
    body = _valid_llm_body()
    # Erster Call: mid-document Syntaxfehler (zwei Objekte ohne Comma —
    # exakt das Disney-17:19-Pattern aus dem PR-#154-Kommentar).
    bad_text = '{"headline": "valid prefix",\n  "tldr": "first"}\n{"second": "object"}'
    good_text = json.dumps(body)
    anthropic_mock = MagicMock(side_effect=[_msg(bad_text), _msg(good_text)])
    record_mock = MagicMock()
    monkeypatch.setattr(engine_module, "messages_create_text", anthropic_mock)
    monkeypatch.setattr(engine_module, "record_anthropic_call", record_mock)

    with caplog.at_level(logging.INFO, logger="app.services.insight_engine"):
        with Session(db) as session:
            report = engine_module.generate_weekly_report(
                session, "test_pair_recall_b1", window_days=30,
            )

    assert report.llm_output is not None
    assert report.llm_output.headline == "M2-retry-recovered"
    # Zwei Calls: initial + ein Retry
    assert anthropic_mock.call_count == 2
    # Cost-Erfassung pro Call → F0.7-Cap sieht beide Spends
    assert record_mock.call_count == 2
    # Token-Aggregation: 2× (8000 in, 3000 out)
    assert report.input_tokens == 16000
    assert report.output_tokens == 6000
    # Retry-Log: ein Eintrag für attempt=1
    retry_records = [
        r for r in caplog.records
        if r.levelname == "WARNING"
        and "insight-engine-json-parse-retry" in r.getMessage()
    ]
    assert len(retry_records) == 1
    assert retry_records[0].attempt == 1
    assert retry_records[0].error_type == "JSONDecodeError"
    # Recovered-Log: parse_path=strict (zweiter Call lieferte sauberes JSON),
    # recall_count=1
    recovered_records = [
        r for r in caplog.records
        if "insight-engine-json-parse-recovered" in r.getMessage()
    ]
    assert len(recovered_records) == 1
    assert recovered_records[0].recall_count == 1


# ---------------------------------------------------------------------------
# Test 4 — Alle drei Versuche scheitern (1 initial + 2 Re-Calls):
# llm_output bleibt None, _persist_report skippt, finales Diagnostik-Log
# fired einmalig mit char_position + raw_response_first_500 +
# anthropic_calls=3. KEIN Crash, Caller kriegt sauberen Report zurück.
# ---------------------------------------------------------------------------

def test_all_attempts_fail_returns_skip_marker_and_logs_diagnostic(
    db, monkeypatch, caplog,
):
    _patch_engine_basics(monkeypatch, "test_pair_total_fail")
    bad_text = '{"headline": "x", "tldr": "y"}\n{"second": "object"}'
    anthropic_mock = MagicMock(return_value=_msg(bad_text))
    record_mock = MagicMock()
    monkeypatch.setattr(engine_module, "messages_create_text", anthropic_mock)
    monkeypatch.setattr(engine_module, "record_anthropic_call", record_mock)

    with caplog.at_level(logging.WARNING, logger="app.services.insight_engine"):
        with Session(db) as session:
            report = engine_module.generate_weekly_report(
                session, "test_pair_total_fail", window_days=30,
            )

    # Caller bekommt einen Report-Stub mit llm_output=None — kein Crash
    assert report.llm_output is None
    assert report.raw_llm_text is not None  # raw_text wird surfacing
    # 3 Calls: initial + 2 Re-Calls
    assert anthropic_mock.call_count == 3
    assert record_mock.call_count == 3
    # Cost-Aggregation spiegelt bezahlten Leerlauf
    assert report.input_tokens == 24000
    assert report.output_tokens == 9000
    # Zwei Retry-Warnings (attempt=1 und attempt=2)
    retry_records = sorted(
        [r for r in caplog.records
         if r.levelname == "WARNING"
         and "insight-engine-json-parse-retry" in r.getMessage()],
        key=lambda r: r.attempt,
    )
    assert [r.attempt for r in retry_records] == [1, 2]
    # Finales Diagnostik-Log (ERROR-Level, einmalig, mit char_position +
    # raw_response_first_500 + anthropic_calls=3)
    failure_records = [
        r for r in caplog.records
        if r.levelname == "ERROR"
        and "insight-engine-json-parse-failed" in r.getMessage()
    ]
    assert len(failure_records) == 1
    rec = failure_records[0]
    assert isinstance(rec.char_position, int)
    assert rec.char_position > 0
    assert rec.raw_response_first_500.startswith('{"headline"')
    assert rec.anthropic_calls == 3
    assert rec.recall_count == 2


# ---------------------------------------------------------------------------
# Test 5 — Cron-Resilienz: Per-Pair-Persist-Skip darf nachfolgende Pairs
# NICHT abbrechen. Wir rufen ``generate_weekly_report`` zweimal direkt
# hintereinander — pair1 fällt durch (alle Retries scheitern), pair2
# bekommt sauberes JSON und muss sauber durchlaufen.
# ---------------------------------------------------------------------------

def test_failed_pair_does_not_abort_subsequent_pair(db, monkeypatch):
    _patch_engine_basics(monkeypatch, "pair_fail_first")
    _seed_pair_in_pairs(monkeypatch, "pair_succeed_second")
    body = _valid_llm_body()
    bad_text = '{"a": "b"}\n{"c": "d"}'
    good_text = json.dumps(body)

    # Erste Pair-Generation: 3× bad. Zweite: 1× good. Insgesamt 4 Calls.
    anthropic_mock = MagicMock(side_effect=[
        _msg(bad_text), _msg(bad_text), _msg(bad_text),   # pair_fail_first
        _msg(good_text),                                  # pair_succeed_second
    ])
    monkeypatch.setattr(engine_module, "messages_create_text", anthropic_mock)
    monkeypatch.setattr(
        engine_module, "record_anthropic_call",
        lambda *a, **kw: None,
    )

    with Session(db) as session:
        report1 = engine_module.generate_weekly_report(
            session, "pair_fail_first", window_days=30,
        )
        report2 = engine_module.generate_weekly_report(
            session, "pair_succeed_second", window_days=30,
        )

    assert report1.llm_output is None
    assert report2.llm_output is not None
    assert report2.llm_output.headline == "M2-retry-recovered"
    assert anthropic_mock.call_count == 4


# ---------------------------------------------------------------------------
# Test 6 — Lenient-Parsing Unit-Test: direkter Check des Helpers, damit
# die Drei-Pfad-Semantik (strict / lenient / "") explizit dokumentiert
# und gegen versehentliche Refactors abgesichert ist.
# ---------------------------------------------------------------------------

def test_try_parse_llm_json_strict_path_for_clean_input():
    text = '{"a": 1}'
    parsed, error, path = engine_module._try_parse_llm_json(text)
    assert parsed == {"a": 1}
    assert error is None
    assert path == "strict"


def test_try_parse_llm_json_lenient_path_for_preamble():
    text = 'Hier kommt der Report:\n{"a": 1, "b": 2}\nEnde.'
    parsed, error, path = engine_module._try_parse_llm_json(text)
    assert parsed == {"a": 1, "b": 2}
    assert error is None
    assert path == "lenient"


def test_try_parse_llm_json_unrescuable_returns_error_and_empty_path():
    text = '{"a": 1,\n  "b": 2}\n{"second": "object"}'
    parsed, error, path = engine_module._try_parse_llm_json(text)
    assert parsed is None
    assert error is not None
    assert path == ""
    # ``.pos`` muss gegen das gestrippte ``cleaned`` zeigen — der Test in
    # Test 4 verwendet diese Eigenschaft als Diagnostik-Anker.
    assert error.pos > 0
