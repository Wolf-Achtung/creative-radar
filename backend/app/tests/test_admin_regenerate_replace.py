"""Force-Regenerate-Bug-Fix Sprint (PR #150) — Verifikation des neuen
``replace`` Query-Params an ``POST /api/admin/insights/regenerate``.

Hintergrund: Sprint 3c (2026-05-12) hat ``generate_and_persist_report``
so verschärft, dass ``force=True`` bei existierender PK-Row immer den
gecachten Brief zurückliefert — Sicherheit gegen den Sprint-3b-Race, in
dem zwei parallele ``force=true``-Curls beide Opus aufgerufen haben. Das
hat ``POST /admin/insights/regenerate`` zu einem No-Op gegen
existierende Briefs gemacht. PR #150 schliesst diese Lücke nicht durch
Aufheben von Sprint 3c, sondern durch einen neuen, expliziten
``replace=true``-Pfad.

Test-Kontrakt:
1. ``force=true`` allein returnt den gecachten Brief — Sprint-3c bleibt.
2. ``replace=true`` bypasst Composite-PK-Check, ruft den LLM-Pfad und
   überschreibt die Row.
3. ``pair=all&replace=true`` propagiert ``replace`` an jeden iterierten
   Pair — alle ``enabled=True``-Pairs werden neu generiert.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

import app.api.cron as cron_module
from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import InsightReport as InsightReportRow
from app.services import insight_engine as engine_module
from app.services.insight_engine import PAIRS
from app.schemas.insights import (
    InsightReport,
    LLMReport,
    PairAggregation,
    TitleCoverage,
)


def _engine_for_path(path: str):
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_regenerate_", suffix=".db")
    os.close(fd)
    engine = _engine_for_path(path)
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.fixture
def client(db, monkeypatch: pytest.MonkeyPatch):
    # Auth disabled keeps the test focused on the route logic. The
    # /admin/insights/regenerate route otherwise sits behind the same
    # Bearer middleware as the rest of the admin namespace.
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)

    def _override_session():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _utc_naive(dt: datetime) -> datetime:
    """Drop tzinfo for comparison — SQLite strips tzinfo on persist, so
    a tz-aware ``datetime(..., tzinfo=utc)`` round-trips back as naive.
    Postgres preserves the timezone in production; this normaliser is
    a test-only convenience."""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _current_iso_year_week() -> tuple[int, int]:
    """Heutige ISO (year, week) — die ``/api/admin/insights/regenerate``-
    Route reicht kein ``now``-Argument an ``generate_and_persist_report``
    durch, daher fällt ``aggregate_pair`` auf ``datetime.now(timezone.utc)``
    zurück und der Composite-PK-Lookup geht gegen die heutige ISO-Woche.
    Tests müssen ihren Seed an dieselbe Woche binden, sonst kippt der
    ``existing is not None``-Pfad sobald der Wandkalender umblättert
    (genau das ist mit PR #150 zwischen 2026-05-17 und 2026-05-18 passiert)."""
    iso = datetime.now(timezone.utc).isocalendar()
    return iso.year, iso.week


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


def _build_synthetic_report(
    pair_key: str,
    iso_year: int,
    iso_week: int,
    *,
    generated_at: datetime,
    cost_cents: int = 150,
) -> InsightReport:
    """Build a minimal-valid InsightReport for mocking ``generate_weekly_report``.

    Uses ``model_construct`` to skip Pydantic validation on deeply nested
    fields — the persistence layer only reads ``llm_output`` and
    ``aggregation`` via ``model_dump(mode='json')``, which serialises
    whatever shape we hand it.
    """
    pair_label = f"{pair_key} test"
    llm = LLMReport.model_validate({
        "headline": "test-headline",
        "tldr": "test-tldr",
        "trends": [],
        "actions": [],
        "cross_market_insight": {
            "de_vs_us": "test-de-vs-us",
            "transfer_opportunity": "test-transfer",
        },
        "risks": [],
        "data_caveats": [],
    })
    agg = PairAggregation.model_construct(
        pair_key=pair_key,
        pair_label=pair_label,
        platform="tiktok",
        window_days=30,
        window_start=generated_at - timedelta(days=30),
        window_end=generated_at,
        iso_week=iso_week,
        iso_year=iso_year,
        de_channel=None,
        us_channel=None,
        uk_channel=None,
        cross_market_matches=[],
        title_coverage=_empty_title_coverage(),
        notes=[],
        per_platform=[],
    )
    return InsightReport.model_construct(
        pair_key=pair_key,
        pair_label=pair_label,
        iso_week=iso_week,
        iso_year=iso_year,
        window_days=30,
        coverage_pct=0.0,
        generated_at=generated_at,
        model="claude-opus-4-7",
        dry_run=False,
        llm_output=llm,
        aggregation=agg,
        cost_usd_estimate=cost_cents / 100.0,
        input_tokens=1000,
        output_tokens=2000,
    )


def _seed_existing_brief(
    db_engine,
    pair_key: str,
    iso_year: int,
    iso_week: int,
    *,
    generated_at: datetime,
    cost_cents: int = 99,
) -> None:
    """Insert a synthetic InsightReport row matching the Composite-PK
    shape that the Sprint-1 persistence layer writes."""
    pair_label = f"{pair_key} test"
    aggregation_json = PairAggregation.model_construct(
        pair_key=pair_key,
        pair_label=pair_label,
        platform="tiktok",
        window_days=30,
        window_start=generated_at - timedelta(days=30),
        window_end=generated_at,
        iso_week=iso_week,
        iso_year=iso_year,
        de_channel=None,
        us_channel=None,
        uk_channel=None,
        cross_market_matches=[],
        title_coverage=_empty_title_coverage(),
        notes=[],
        per_platform=[],
    ).model_dump(mode="json")
    llm_json = LLMReport.model_validate({
        "headline": "stale-headline",
        "tldr": "stale-tldr",
        "trends": [],
        "actions": [],
        "cross_market_insight": {
            "de_vs_us": "stale-de-vs-us",
            "transfer_opportunity": "stale-transfer",
        },
        "risks": [],
        "data_caveats": [],
    }).model_dump(mode="json")
    row = InsightReportRow(
        pair_key=pair_key,
        iso_year=iso_year,
        iso_week=iso_week,
        aggregation=aggregation_json,
        llm_output=llm_json,
        generated_at=generated_at,
        model="claude-opus-4-7",
        cost_usd_cents=cost_cents,
    )
    with Session(db_engine) as session:
        session.add(row)
        session.commit()


def _enabled_pair_count() -> int:
    return sum(1 for v in PAIRS.values() if v.get("enabled", False))


def test_regenerate_with_force_alone_returns_cached_brief(client, db, monkeypatch):
    """Sprint-3c-Vertrag muss erhalten bleiben: ``force=true`` ohne
    ``replace=true`` triggert keinen LLM-Call und returnt den existierenden
    Brief."""
    pair = "disney"
    iso_year, iso_week = _current_iso_year_week()
    old_generated_at = datetime(2026, 5, 13, 8, 31, 0, tzinfo=timezone.utc)
    _seed_existing_brief(
        db, pair, iso_year=iso_year, iso_week=iso_week,
        generated_at=old_generated_at, cost_cents=99,
    )

    call_counter = {"count": 0}

    def _spy_generate_weekly_report(*args, **kwargs):
        call_counter["count"] += 1
        raise AssertionError(
            "generate_weekly_report must NOT be called when force=true "
            "without replace=true — Sprint-3c contract broken."
        )

    monkeypatch.setattr(
        engine_module, "generate_weekly_report",
        _spy_generate_weekly_report,
    )

    response = client.post(
        "/api/admin/insights/regenerate",
        params={"pair": pair, "force": "true"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["results"][0]["pair"] == pair
    assert body["results"][0]["status"] == "ok"
    assert call_counter["count"] == 0

    # Row in DB unchanged.
    with Session(db) as session:
        row = session.get(InsightReportRow, (pair, iso_year, iso_week))
        assert row is not None
        assert _utc_naive(row.generated_at) == _utc_naive(old_generated_at)
        assert row.cost_usd_cents == 99


def test_regenerate_with_replace_triggers_new_generation(client, db, monkeypatch):
    """``replace=true`` bypasst den Composite-PK-Check, ruft
    ``generate_weekly_report`` und schreibt die Row neu (UPSERT via
    ``_persist_report``-delete-then-insert)."""
    pair = "disney"
    iso_year, iso_week = _current_iso_year_week()
    old_generated_at = datetime(2026, 5, 13, 8, 31, 0, tzinfo=timezone.utc)
    _seed_existing_brief(
        db, pair, iso_year=iso_year, iso_week=iso_week,
        generated_at=old_generated_at, cost_cents=99,
    )

    # Freezed "new" timestamp so the test is deterministic.
    new_generated_at = datetime(2026, 5, 17, 14, 0, 0, tzinfo=timezone.utc)
    fresh_report = _build_synthetic_report(
        pair, iso_year, iso_week,
        generated_at=new_generated_at, cost_cents=187,
    )
    call_counter = {"count": 0, "kwargs": []}

    def _mock_generate_weekly_report(session, pair_key, **kwargs):
        call_counter["count"] += 1
        call_counter["kwargs"].append({"pair_key": pair_key, **kwargs})
        return fresh_report

    monkeypatch.setattr(
        engine_module, "generate_weekly_report",
        _mock_generate_weekly_report,
    )

    response = client.post(
        "/api/admin/insights/regenerate",
        params={"pair": pair, "replace": "true"},
    )
    assert response.status_code == 200, response.text

    assert call_counter["count"] == 1, (
        "generate_weekly_report should run exactly once when replace=true "
        "for an existing row."
    )

    body = response.json()
    assert body["results"][0]["status"] == "ok"
    assert body["total_cost_cents"] == 187

    with Session(db) as session:
        row = session.get(InsightReportRow, (pair, iso_year, iso_week))
        assert row is not None
        # Row wurde überschrieben — neue generated_at, neue cost.
        # SQLite stripped tzinfo on persist; compare as naive UTC.
        assert _utc_naive(row.generated_at) == _utc_naive(new_generated_at)
        assert row.cost_usd_cents == 187
        # Es gibt weiterhin genau eine Row (UPSERT, kein Duplicate).
        all_rows = session.exec(
            __import__("sqlmodel").select(InsightReportRow).where(
                InsightReportRow.pair_key == pair,
                InsightReportRow.iso_year == iso_year,
                InsightReportRow.iso_week == iso_week,
            )
        ).all()
        assert len(all_rows) == 1


def test_regenerate_all_with_replace_triggers_all_enabled_pairs(client, db, monkeypatch):
    """``pair=all&replace=true`` propagiert ``replace`` an jeden Pair.
    Jeder enabled Pair triggert einen LLM-Call."""
    n_pairs = _enabled_pair_count()
    iso_year, iso_week = _current_iso_year_week()
    old_generated_at = datetime(2026, 5, 13, 9, 0, 0, tzinfo=timezone.utc)
    for pair_key, pair_def in PAIRS.items():
        if pair_def.get("enabled", False):
            _seed_existing_brief(
                db, pair_key, iso_year=iso_year, iso_week=iso_week,
                generated_at=old_generated_at, cost_cents=99,
            )

    new_generated_at = datetime(2026, 5, 17, 14, 0, 0, tzinfo=timezone.utc)
    call_counter = {"count": 0, "pairs": []}

    def _mock_generate_weekly_report(session, pair_key, **kwargs):
        call_counter["count"] += 1
        call_counter["pairs"].append(pair_key)
        return _build_synthetic_report(
            pair_key, iso_year, iso_week,
            generated_at=new_generated_at, cost_cents=150,
        )

    monkeypatch.setattr(
        engine_module, "generate_weekly_report",
        _mock_generate_weekly_report,
    )

    response = client.post(
        "/api/admin/insights/regenerate",
        params={"pair": "all", "replace": "true"},
    )
    assert response.status_code == 200, response.text

    assert call_counter["count"] == n_pairs
    # Jeder enabled Pair wurde genau einmal angefasst (keine Dupletten,
    # kein Pair vergessen).
    assert sorted(call_counter["pairs"]) == sorted(
        k for k, v in PAIRS.items() if v.get("enabled", False)
    )

    body = response.json()
    assert len(body["results"]) == n_pairs
    assert all(r["status"] == "ok" for r in body["results"])
    assert body["total_cost_cents"] == n_pairs * 150

    # Alle Rows wurden überschrieben — neue generated_at überall.
    with Session(db) as session:
        for pair_key, pair_def in PAIRS.items():
            if not pair_def.get("enabled", False):
                continue
            row = session.get(InsightReportRow, (pair_key, iso_year, iso_week))
            assert row is not None
            assert _utc_naive(row.generated_at) == _utc_naive(new_generated_at), (
                f"pair {pair_key} should have new generated_at after "
                f"pair=all&replace=true"
            )


def test_regenerate_surfaces_raw_llm_text_on_generation_failure(client, db, monkeypatch):
    """B2-Diagnose-Surface: ein no_llm_output-Report (Parse-/Schema-/Citation-
    Fail) kommt mit ``llm_output=None`` zurueck und wurde bisher als
    ``status="ok"`` mit verworfenem Rohtext maskiert. Der Endpoint liefert
    jetzt ``status="generation_failed"`` + ``raw_llm_text`` inline (spiegelt
    ``/insights/title/regenerate``). Nicht-destruktiv: ``_persist_report``
    skippt den Write bei ``llm_output=None``, es landet keine Row."""
    pair = "disney"
    iso_year, iso_week = _current_iso_year_week()
    # Roh-Output, dem das Pflichtfeld ``transfer_opportunity`` fehlt — genau
    # die Form, die LLMReport.model_validate als Schema-Fail verwirft.
    raw = '{"headline":"x","cross_market_insight":{"de_vs_us":"a"}}'
    failed_report = InsightReport.model_construct(
        pair_key=pair,
        pair_label=f"{pair} test",
        iso_week=iso_week,
        iso_year=iso_year,
        window_days=30,
        coverage_pct=0.0,
        generated_at=datetime(2026, 6, 15, 8, 0, 0, tzinfo=timezone.utc),
        model="claude-opus-4-7",
        dry_run=False,
        llm_output=None,
        aggregation=None,
        cost_usd_estimate=0.0,
        raw_llm_text=raw,
    )

    def _mock_generate_weekly_report(session, pair_key, **kwargs):
        return failed_report

    monkeypatch.setattr(
        engine_module, "generate_weekly_report",
        _mock_generate_weekly_report,
    )

    response = client.post(
        "/api/admin/insights/regenerate",
        params={"pair": pair, "replace": "false"},
    )
    assert response.status_code == 200, response.text

    result = response.json()["results"][0]
    assert result["pair"] == pair
    assert result["status"] == "generation_failed"
    assert result["raw_llm_text"] == raw

    # Nicht-destruktiv: kein Write bei llm_output=None.
    with Session(db) as session:
        assert session.get(InsightReportRow, (pair, iso_year, iso_week)) is None


def test_cron_brief_gen_still_respects_cache_hit_after_pr150(db, monkeypatch):
    """Regressions-Schutz für PR #149: der Cron-Pfad ruft
    ``generate_and_persist_report`` weiterhin OHNE ``replace``-kwarg auf
    (Default False), also bleibt das Cache-Hit-Verhalten erhalten. Direkter
    Bruch-Test wenn jemand den Cron-Pfad versehentlich auf ``replace=True``
    umstellt."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from uuid import UUID
    from app.models.entities import CronRun

    monkeypatch.setenv("ENABLE_BRIEF_GEN_IN_CRON", "true")
    monkeypatch.setattr("app.api.cron.engine", db)
    from app.config import settings as _settings
    monkeypatch.setattr(_settings, "cron_vision_backlog_max_assets_per_run", 0, raising=False)
    monkeypatch.setenv("ENABLE_TITLE_SYNC_IN_CRON", "false")
    monkeypatch.setattr(
        cron_module, "compute_apify_monthly_spend",
        lambda session: SimpleNamespace(
            hard_cap_exceeded=False, enforced=False, soft_warn_exceeded=False,
            to_dict=lambda: {}, pct_used=0.0,
            spent_usd_cents=0, budget_usd_cents=50000,
        ),
    )
    monkeypatch.setattr(
        cron_module, "_execute_platform_sync",
        AsyncMock(return_value=({}, [])),
    )
    monkeypatch.setattr(cron_module, "_run_rematch_after_sync", AsyncMock(return_value={}))
    monkeypatch.setattr(
        cron_module, "aggregate_apify_costs_since",
        lambda s, since: {"estimated_cost_usd": 0.0},
    )
    monkeypatch.setattr(
        cron_module, "aggregate_anthropic_costs_since",
        lambda s, since: {"estimated_cost_usd": 0.0},
    )
    monkeypatch.setattr(
        cron_module, "aggregate_openai_costs_since",
        lambda s, since: {"estimated_cost_usd": 0.0},
    )

    captured_kwargs: list[dict] = []

    def _spy(session, pair_key, **kwargs):
        captured_kwargs.append(kwargs)
        return SimpleNamespace(cost_usd_estimate=0.0)

    monkeypatch.setattr(cron_module, "generate_and_persist_report", _spy)

    with Session(db) as session:
        run = CronRun()
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id: UUID = run.id

    import asyncio
    asyncio.run(cron_module._run_cron_sync_background(run_id, run_index=0))

    assert len(captured_kwargs) == _enabled_pair_count()
    # Cron darf NIE replace=True senden — sonst frisst er bei jedem
    # Montag-Cron 9 frische Opus-Calls, obwohl Briefs schon im Cache liegen.
    for kw in captured_kwargs:
        assert kw.get("replace", False) is False, (
            f"Cron must not pass replace=True. Got kwargs={kw}"
        )
