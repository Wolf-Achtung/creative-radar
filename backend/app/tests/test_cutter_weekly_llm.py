"""Tests für die LLM-Synthese des Cutter-Wochenbriefings (Commit B).

Kern-Disziplin unter Test: das LLM formuliert nur, was die Code-Prüfung
freigegeben hat — Citation strict von Tag 1 (Wolf-Entscheidung 3).

Abgedeckt:
- Leerlauf-Woche → KEIN LLM-Call, drei deterministische Code-Blöcke.
- Freigegebenes Muster → LLM-Block übernommen, Leerlauf-Plattformen
  bleiben Code-Blöcke, Asymmetrie-Caveat kommt vom Code.
- Citation-Verstoß (fremde URL) → Antwort verworfen, zweiter Anlauf
  rettet; beide Anläufe landen im Costlog.
- Beide Anläufe verworfen → ``llm_output=None`` + ``raw_llm_text``,
  Evidence bleibt vollständig (Kalibrierungs-Produkt).
- Validator-Einzelfälle: Plattform-Mismatch, <2 Belege, Quer-Muster
  ohne Zwei-Plattform-Deckung, markt_signal_notiz ohne Signale.
- Forecast-Signale: nur status=ok-Märkte werden Signale.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.schemas.insights import (
    CutterEvidencePost,
    CutterForecastSignal,
    CutterPlatformBlock,
    CutterPlatformEvidence,
    CutterWeeklyEvidence,
    CutterWeeklyLLMReport,
    CutterWeeklyParams,
    CutterWeeklySources,
)
from app.services import cutter_weekly
from app.services.anthropic_client import JsonRetryResult


ANCHOR = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
ISO_YEAR, ISO_WEEK = ANCHOR.isocalendar().year, ANCHOR.isocalendar().week
WEEK_START, WEEK_END = cutter_weekly.week_bounds(ISO_YEAR, ISO_WEEK)


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_cutter_llm_", suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}
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


# ---------------------------------------------------------------------------
# Evidence-Fixtures (direkt konstruiert — die deterministische Schicht
# ist in test_cutter_weekly_evidence.py abgedeckt)
# ---------------------------------------------------------------------------


def _evidence_post(url: str, key: str = "Film A") -> CutterEvidencePost:
    return CutterEvidencePost(
        post_url=url,
        platform="instagram",
        er=0.2,
        views=1000,
        likes=200,
        comments=0,
        engagement_sum=200,
        distinct_key=key,
        source="pair:disney",
        title_original=key,
        published_at=WEEK_START + timedelta(days=1),
    )


def _released_ig(urls: list[str]) -> CutterPlatformEvidence:
    posts = [
        _evidence_post(u, key=f"Film {chr(65 + i % 3)}") for i, u in enumerate(urls)
    ]
    return CutterPlatformEvidence(
        platform="instagram",
        status="pattern_released",
        p75_er=0.15,
        p75_sample_size=40,
        week_posts_total=len(posts),
        candidates_above_p75=len(posts),
        distinct_keys=sorted({p.distinct_key for p in posts}),
        supporting_posts=posts,
    )


def _idle(platform: str, status: str = "no_pattern") -> CutterPlatformEvidence:
    return CutterPlatformEvidence(
        platform=platform,
        status=status,
        reason="2 Post(s) mit ER >= p75 (0.1000) über 1 Distinct-Key(s) — "
        "Schwelle verlangt >= 5 Posts über >= 3 Keys."
        if status == "no_pattern"
        else "p75 nicht definiert: nur 4 Posts mit views>0 im Rollfenster (Mindest-n 30).",
        p75_er=0.10 if status == "no_pattern" else None,
        p75_sample_size=40 if status == "no_pattern" else 4,
    )


def _evidence(
    platforms: list[CutterPlatformEvidence],
    signals: Optional[list[CutterForecastSignal]] = None,
) -> CutterWeeklyEvidence:
    return CutterWeeklyEvidence(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        week_start=WEEK_START,
        week_end=WEEK_END,
        params=CutterWeeklyParams(
            min_posts=5, min_distinct_keys=3, p75_window_weeks=8, p75_min_sample=30
        ),
        sources=CutterWeeklySources(pair_briefs=["disney"]),
        platforms=platforms,
        week_posts_total=sum(p.week_posts_total for p in platforms),
        forecast_signals=signals or [],
    )


IG_URLS = [f"https://ig.example/{i}" for i in range(5)]


def _valid_llm_payload(**overrides) -> dict:
    payload = {
        "bloecke": [
            {
                "platform": "instagram",
                "beobachtung": "Die starken Posts dieser Woche sind kurze Clips.",
                "schnitt_impuls": None,
                "cited_post_ids": IG_URLS[:2],
            }
        ],
        "quer_muster": None,
        "quer_cited_post_ids": [],
        "markt_signal_notiz": None,
        "data_caveats": ["Dünne Wochen-Basis auf TikTok."],
    }
    payload.update(overrides)
    return payload


def _retry_result(parsed, *, in_tokens: int = 1000, out_tokens: int = 300) -> JsonRetryResult:
    usage = SimpleNamespace(input_tokens=in_tokens, output_tokens=out_tokens)
    msg = SimpleNamespace(usage=usage)
    return JsonRetryResult(
        parsed=parsed,
        call_attempts=[(msg, "raw-text" if parsed is None else "{}")],
        parse_path="strict" if parsed is not None else "",
    )


@pytest.fixture
def costlog_spy(monkeypatch):
    calls: list[dict] = []

    def _spy(usage, *, model, operation, meta=None):
        calls.append({"model": model, "operation": operation, "meta": meta})

    # Lazy-Import im Service → am Quellmodul patchen.
    monkeypatch.setattr(
        "app.services.cost_log.record_anthropic_call", _spy
    )
    return calls


@pytest.fixture
def no_forecast_signals(monkeypatch):
    monkeypatch.setattr(
        cutter_weekly, "collect_forecast_signals", lambda session: []
    )


# ---------------------------------------------------------------------------
# Leerlauf-Woche: kein LLM-Call
# ---------------------------------------------------------------------------


def test_no_pattern_week_skips_llm_call(db, monkeypatch, no_forecast_signals):
    evidence = _evidence([_idle("instagram"), _idle("tiktok"), _idle("youtube", "no_threshold")])
    monkeypatch.setattr(
        cutter_weekly, "build_weekly_evidence", lambda session, now=None: evidence
    )

    def _must_not_be_called(**kwargs):
        raise AssertionError("LLM darf in einer Leerlauf-Woche nicht gerufen werden")

    monkeypatch.setattr(
        "app.services.anthropic_client.call_with_json_retry", _must_not_be_called
    )

    with Session(db) as session:
        report = cutter_weekly.generate_cutter_weekly(session, now=ANCHOR)

    assert report.model == "none"
    assert report.cost_usd_estimate is None
    assert report.llm_output is not None
    assert len(report.llm_output.bloecke) == 3
    assert all(b.generated_by == "code" for b in report.llm_output.bloecke)
    assert "Kein klares Muster" in report.llm_output.bloecke[0].beobachtung
    assert "Keine belastbare Vergleichsbasis" in report.llm_output.bloecke[2].beobachtung
    assert all(b.cited_post_ids == [] for b in report.llm_output.bloecke)


# ---------------------------------------------------------------------------
# Happy Path: freigegebenes Muster → LLM-Block + Code-Leerläufe
# ---------------------------------------------------------------------------


def test_released_pattern_uses_llm_block_and_code_idle(
    db, monkeypatch, costlog_spy, no_forecast_signals
):
    evidence = _evidence([_released_ig(IG_URLS), _idle("tiktok"), _idle("youtube")])
    monkeypatch.setattr(
        cutter_weekly, "build_weekly_evidence", lambda session, now=None: evidence
    )
    monkeypatch.setattr(
        "app.services.anthropic_client.call_with_json_retry",
        lambda **kwargs: _retry_result(_valid_llm_payload()),
    )
    monkeypatch.setattr(
        "app.services.anthropic_client.is_anthropic_configured", lambda: True
    )

    with Session(db) as session:
        report = cutter_weekly.generate_cutter_weekly(session, now=ANCHOR)

    assert report.llm_output is not None
    blocks = {b.platform: b for b in report.llm_output.bloecke}
    assert blocks["instagram"].generated_by == "llm"
    assert blocks["instagram"].cited_post_ids == IG_URLS[:2]
    assert blocks["tiktok"].generated_by == "code"
    assert blocks["youtube"].generated_by == "code"
    # Reihenfolge fest: IG → TT → YT.
    assert [b.platform for b in report.llm_output.bloecke] == [
        "instagram", "tiktok", "youtube",
    ]
    assert report.raw_llm_text is None
    assert report.input_tokens == 1000 and report.output_tokens == 300
    assert report.cost_usd_estimate is not None
    assert len(costlog_spy) == 1
    assert costlog_spy[0]["operation"] == "cutter_weekly"
    assert costlog_spy[0]["meta"] == {"iso_year": ISO_YEAR, "iso_week": ISO_WEEK}


# ---------------------------------------------------------------------------
# Citation strict: Verstoß → verwerfen, zweiter Anlauf rettet
# ---------------------------------------------------------------------------


def test_citation_violation_triggers_one_retry_then_succeeds(
    db, monkeypatch, costlog_spy, no_forecast_signals
):
    evidence = _evidence([_released_ig(IG_URLS), _idle("tiktok"), _idle("youtube")])
    monkeypatch.setattr(
        cutter_weekly, "build_weekly_evidence", lambda session, now=None: evidence
    )
    bad = _valid_llm_payload(
        bloecke=[{
            "platform": "instagram",
            "beobachtung": "x",
            "cited_post_ids": ["https://fremd.example/nicht-im-set", IG_URLS[0]],
        }]
    )
    results = [_retry_result(bad), _retry_result(_valid_llm_payload())]
    calls = {"n": 0}

    def _fake_call(**kwargs):
        result = results[calls["n"]]
        calls["n"] += 1
        return result

    monkeypatch.setattr(
        "app.services.anthropic_client.call_with_json_retry", _fake_call
    )
    monkeypatch.setattr(
        "app.services.anthropic_client.is_anthropic_configured", lambda: True
    )

    with Session(db) as session:
        report = cutter_weekly.generate_cutter_weekly(session, now=ANCHOR)

    assert calls["n"] == 2
    assert report.llm_output is not None
    assert report.llm_output.bloecke[0].generated_by == "llm"
    # Beide bezahlten Anläufe im Costlog + Token-Summe über beide.
    assert len(costlog_spy) == 2
    assert report.input_tokens == 2000


def test_both_attempts_rejected_yields_none_llm_output(
    db, monkeypatch, costlog_spy, no_forecast_signals
):
    evidence = _evidence([_released_ig(IG_URLS), _idle("tiktok"), _idle("youtube")])
    monkeypatch.setattr(
        cutter_weekly, "build_weekly_evidence", lambda session, now=None: evidence
    )
    bad = _valid_llm_payload(
        bloecke=[{
            "platform": "instagram",
            "beobachtung": "x",
            "cited_post_ids": ["https://fremd.example/a", "https://fremd.example/b"],
        }]
    )
    monkeypatch.setattr(
        "app.services.anthropic_client.call_with_json_retry",
        lambda **kwargs: _retry_result(bad),
    )
    monkeypatch.setattr(
        "app.services.anthropic_client.is_anthropic_configured", lambda: True
    )

    with Session(db) as session:
        report = cutter_weekly.generate_cutter_weekly(session, now=ANCHOR)

    # Antwort verworfen — aber der Evidence-Blob (Kalibrierungs-Produkt)
    # ist vollständig da, inklusive der freigegebenen Muster.
    assert report.llm_output is None
    assert report.raw_llm_text is not None
    assert report.evidence.platforms[0].status == "pattern_released"
    assert len(costlog_spy) == 2  # bezahlte Fehlversuche bleiben sichtbar


# ---------------------------------------------------------------------------
# Validator-Einzelfälle
# ---------------------------------------------------------------------------


def _validate(payload_overrides: dict, signals=None) -> list[str]:
    evidence = _evidence(
        [_released_ig(IG_URLS), _idle("tiktok"), _idle("youtube")],
        signals=signals,
    )
    report = CutterWeeklyLLMReport.model_validate(_valid_llm_payload(**payload_overrides))
    return cutter_weekly._validate_llm_report(report, evidence, signals or [])


def test_validator_rejects_platform_mismatch():
    problems = _validate({
        "bloecke": [
            {"platform": "instagram", "beobachtung": "x", "cited_post_ids": IG_URLS[:2]},
            {"platform": "tiktok", "beobachtung": "erfunden", "cited_post_ids": IG_URLS[:2]},
        ]
    })
    assert any("freigegeben sind exakt" in p for p in problems)


def test_validator_rejects_too_few_citations():
    problems = _validate({
        "bloecke": [
            {"platform": "instagram", "beobachtung": "x", "cited_post_ids": [IG_URLS[0]]},
        ]
    })
    assert any("mindestens 2 Belege" in p for p in problems)


def test_validator_rejects_quer_muster_on_single_platform():
    problems = _validate({
        "quer_muster": "Überall kurze Clips.",
        "quer_cited_post_ids": IG_URLS[:2],  # nur IG — kein Quer-Beleg
    })
    assert any("keine zwei Plattformen" in p for p in problems)


def test_validator_rejects_signal_note_without_signals():
    problems = _validate({"markt_signal_notiz": "ER bei Disney steigend."})
    assert any("keine ok-Signale" in p for p in problems)


def test_validator_accepts_signal_note_with_signals():
    signals = [
        CutterForecastSignal(pair_key="disney", market="DE", direction="steigend", n_points=6)
    ]
    problems = _validate({"markt_signal_notiz": "ER-Trend bei Disney DE steigend — hinschauen."}, signals=signals)
    assert problems == []


def test_assemble_report_stamps_asymmetry_caveat():
    signals = [
        CutterForecastSignal(pair_key="disney", market="DE", direction="steigend", n_points=6)
    ]
    evidence = _evidence(
        [_released_ig(IG_URLS), _idle("tiktok"), _idle("youtube")], signals=signals
    )
    llm_report = CutterWeeklyLLMReport.model_validate(_valid_llm_payload())
    assembled = cutter_weekly._assemble_report(evidence, llm_report)
    assert any("kein Forecast-Pendant" in c for c in assembled.data_caveats)
    # LLM-Caveats bleiben erhalten.
    assert any("TikTok" in c for c in assembled.data_caveats)


# ---------------------------------------------------------------------------
# Forecast-Signale: nur ok-Status wird Signal
# ---------------------------------------------------------------------------


def test_collect_forecast_signals_only_ok_markets(db, monkeypatch):
    def _fake_forecast(session, pair_key, pair_def, *, apply_gate):
        assert apply_gate is True
        return {
            "markets": {
                "DE": {"status": "ok", "direction": "steigend", "n_points": 6},
                "US": {"status": "too_volatile", "n_points": 5, "r2": 0.2},
                "UK": {"status": "insufficient_data", "n_points": 1},
            }
        }

    monkeypatch.setattr(
        "app.services.forecast.generate_er_forecast", _fake_forecast
    )
    monkeypatch.setattr(
        "app.services.insight_engine.PAIRS",
        {"disney": {"enabled": True}, "off": {"enabled": False}},
    )

    with Session(db) as session:
        signals = cutter_weekly.collect_forecast_signals(session)

    assert len(signals) == 1
    assert signals[0].pair_key == "disney"
    assert signals[0].market == "DE"
    assert signals[0].direction == "steigend"


def test_collect_forecast_signals_isolates_pair_failure(db, monkeypatch):
    def _fake_forecast(session, pair_key, pair_def, *, apply_gate):
        if pair_key == "broken":
            raise RuntimeError("boom")
        return {"markets": {"DE": {"status": "ok", "direction": "fallend", "n_points": 4}}}

    monkeypatch.setattr(
        "app.services.forecast.generate_er_forecast", _fake_forecast
    )
    monkeypatch.setattr(
        "app.services.insight_engine.PAIRS",
        {"broken": {"enabled": True}, "disney": {"enabled": True}},
    )

    with Session(db) as session:
        signals = cutter_weekly.collect_forecast_signals(session)

    assert [s.pair_key for s in signals] == ["disney"]
