"""Tests for the cost-logging service (Phase 4 W4 Task 4.4 / F0.6).

Coverage:
- record_apify_run persists a row with the right USD/EUR conversion
  and metadata
- record_openai_call accepts both dataclass-style usage (SDK) and dict
- record_*-failures swallowed (DB error doesn't break the caller)
- _to_eur_cents uses the snapshot rate at logging time
- /api/admin/cost-summary aggregates correctly per group_by
- Cost-summary endpoint requires Bearer auth (via global middleware)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.main import app
from app.models.entities import CostLog
from app.services import cost_log as cost_log_module


def _shared_test_engine():
    """SQLite :memory: + StaticPool so all sessions in this process see the
    same in-memory DB. Without StaticPool, every new Session call opens a
    brand-new isolated DB and reads return 'no such table'."""
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


# ---------- _to_eur_cents ----------


def test_to_eur_cents_uses_settings_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "usd_to_eur_rate", 0.92, raising=False)
    assert cost_log_module._to_eur_cents(100) == 92  # 100 cents @ 0.92
    monkeypatch.setattr(settings, "usd_to_eur_rate", 1.0, raising=False)
    assert cost_log_module._to_eur_cents(100) == 100


def test_to_eur_cents_handles_zero_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defensive: if the rate is somehow 0/None, _to_eur_cents falls back
    to 0.92 (avoids dividing-by-zero downstream and matches W4 default)."""
    monkeypatch.setattr(settings, "usd_to_eur_rate", 0, raising=False)
    assert cost_log_module._to_eur_cents(100) == 92


# ---------- record_apify_run ----------


def test_record_apify_run_legacy_compute_units_path_falls_back_to_zero(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pay-per-Event-Pricing-Fix: das ``compute_units * apify_compute_unit_usd``-
    Modell ist tot. Wenn ein Run-Response NUR die alte ``usage``-Struktur
    trägt (z. B. ein Free-Tier-Owner-Run oder ein Mock aus alter Test-
    Fixture) und ``usageTotalUsd`` fehlt, schreibt das Cost-Log eine
    0-cent-Row + WARN. Die Compute-Unit-Zahl bleibt im ``cost_meta`` als
    Audit-Anker erhalten — beide Garantien sind nötig, damit
    Pre-Pay-per-Event-Captures rückverfolgbar bleiben."""
    monkeypatch.setattr(settings, "usd_to_eur_rate", 0.92, raising=False)

    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_apify_run(
            run_data={
                "id": "run-123",
                "actId": "apify~instagram-scraper",
                "usage": {"ACTOR_COMPUTE_UNITS": 0.5},
            },
            items_count=12,
            operation="actor:apify~instagram-scraper",
        )

    rows = session.exec(select(CostLog)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.provider == "apify"
    assert row.operation == "actor:apify~instagram-scraper"
    # usageTotalUsd fehlt → 0 cents, kein Versuch der CU-Hochrechnung.
    assert row.cost_usd_cents == 0
    assert row.cost_eur_cents == 0
    # Audit-Anker bleibt — der Drill-Down kann später die CU-Zahl lesen.
    assert row.cost_meta["compute_units"] == 0.5
    assert row.cost_meta["items_count"] == 12
    assert row.cost_meta["run_id"] == "run-123"
    assert row.cost_meta["usage_total_usd"] is None


def test_record_apify_run_handles_missing_usage(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Some Apify run responses omit the usage dict (failed runs, very old
    actors). Cost log still writes a 0-cost row for the audit trail."""
    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_apify_run(
            run_data={"id": "run-no-usage"},
            items_count=0,
            operation="actor:foo",
        )

    rows = session.exec(select(CostLog)).all()
    assert len(rows) == 1
    assert rows[0].cost_usd_cents == 0
    assert rows[0].cost_eur_cents == 0
    assert rows[0].cost_meta["compute_units"] == 0.0


# ---------- Pay-per-Event Cost-Tracking (Apify-Pricing-Migration 2025) ----


def test_record_apify_run_uses_usage_total_usd_when_present_pay_per_event(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Authoritative-Read: bei Pay-per-Event-Actors trägt Apify die
    Run-Kosten in ``usageTotalUsd`` ein (server-seitig über
    ``chargedEventCounts × Event-Preise`` berechnet). Wir lesen direkt
    diese Zahl, runden auf Cent — keine eigene Hochrechnung, kein
    Drift zwischen unseren Hardcoded-Preisen und Apifys
    Pricing-Engine."""
    monkeypatch.setattr(settings, "usd_to_eur_rate", 0.92, raising=False)

    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_apify_run(
            run_data={
                "id": "ig-run-1",
                "actId": "apify~instagram-api-scraper",
                # 280 result-events × $0.002025 = $0.567 (server-seitig
                # bereits aggregiert in usageTotalUsd)
                "usageTotalUsd": 0.567,
                "usage": {"ACTOR_COMPUTE_UNITS": 0.0},  # PPE → CU=0
                "usageUsd": {
                    "ACTOR_COMPUTE_UNITS": 0.0,
                    "EVENT_RESULT": 0.567,
                },
                "chargedEventCounts": {
                    "actor-start": 1,
                    "result-event": 280,
                },
                "pricingInfo": {"pricingModel": "PAY_PER_EVENT"},
            },
            items_count=280,
            operation="actor:apify~instagram-api-scraper",
        )

    rows = session.exec(select(CostLog)).all()
    assert len(rows) == 1
    row = rows[0]
    # 0.567 USD → 56.7 cents → int(round(56.7)) = 57
    assert row.cost_usd_cents == 57
    # 57 cents × 0.92 = 52.44 → 52
    assert row.cost_eur_cents == 52


def test_record_apify_run_uses_usage_total_usd_when_present_tt(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TT-Scraper-Variante: identisches Cost-Modell mit anderem Event-
    Preis (Clockworks $0.003 statt Apifys $0.002025). Test sichert,
    dass die Authoritative-Read-Logik plattform-agnostisch ist."""
    monkeypatch.setattr(settings, "usd_to_eur_rate", 0.92, raising=False)

    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_apify_run(
            run_data={
                "id": "tt-run-1",
                "actId": "clockworks~tiktok-scraper",
                # 86 result-events × $0.003 + 1 actor-start × $0.001 = $0.259
                "usageTotalUsd": 0.259,
                "chargedEventCounts": {
                    "actor-start": 1,
                    "result-event": 86,
                },
                "pricingInfo": {"pricingModel": "PAY_PER_EVENT"},
            },
            items_count=86,
            operation="actor:clockworks~tiktok-scraper",
        )

    rows = session.exec(select(CostLog)).all()
    assert len(rows) == 1
    # 0.259 USD → 25.9 cents → int(round(25.9)) = 26
    assert rows[0].cost_usd_cents == 26


def test_record_apify_run_cost_meta_includes_event_counts_and_pricing_model(
    session: Session,
) -> None:
    """Drill-Down-Audit-Trail: ``chargedEventCounts``, ``usage_usd`` und
    ``pricing_model`` landen vollständig in ``cost_meta``, damit eine
    spätere Analyse rekonstruieren kann, wofür Apify abgerechnet hat —
    actor-start vs result-event vs künftige Event-Typen — ohne dass die
    Cost-Berechnungs-Logik selber dieses Wissen halten muss."""
    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_apify_run(
            run_data={
                "id": "ig-run-meta",
                "actId": "apify~instagram-api-scraper",
                "usageTotalUsd": 0.30,
                "usageUsd": {
                    "ACTOR_COMPUTE_UNITS": 0.0,
                    "EVENT_RESULT": 0.30,
                    "DATA_TRANSFER_INTERNAL_GBYTES": 0.0,
                },
                "chargedEventCounts": {
                    "actor-start": 1,
                    "result-event": 148,
                },
                "pricingInfo": {"pricingModel": "PAY_PER_EVENT"},
            },
            items_count=148,
            operation="actor:apify~instagram-api-scraper",
        )

    rows = session.exec(select(CostLog)).all()
    assert len(rows) == 1
    meta = rows[0].cost_meta
    assert meta["usage_total_usd"] == 0.30
    assert meta["pricing_model"] == "PAY_PER_EVENT"
    assert meta["charged_event_counts"] == {
        "actor-start": 1,
        "result-event": 148,
    }
    assert meta["usage_usd"]["EVENT_RESULT"] == 0.30
    # Backward-Compat-Audit-Felder bleiben befüllt.
    assert meta["compute_units"] == 0.0
    assert meta["items_count"] == 148
    assert meta["actor_id"] == "apify~instagram-api-scraper"
    assert meta["run_id"] == "ig-run-meta"


def test_record_apify_run_handles_string_usage_total_usd_defensively(
    session: Session,
) -> None:
    """Apify's HTTP-JSON ist normalerweise floats, aber wir absichern
    defensiv gegen String-Werte (z. B. proxy-mangling, alte SDK-
    Versionen, Test-Fixtures). ``int(round(float("0.42") * 100))`` = 42."""
    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_apify_run(
            run_data={
                "id": "ig-run-str",
                "usageTotalUsd": "0.42",  # ← string, kein float
            },
            items_count=42,
            operation="actor:apify~instagram-api-scraper",
        )

    rows = session.exec(select(CostLog)).all()
    assert len(rows) == 1
    assert rows[0].cost_usd_cents == 42


def test_record_apify_run_swallows_db_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed cost-log write must NOT propagate — the actor run that
    triggered the log already returned data we shouldn't lose."""
    class FailingSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def add(self, *a): raise RuntimeError("DB down")
        def commit(self): pass

    with patch.object(cost_log_module, "Session", lambda _engine: FailingSession()):
        # Must not raise
        cost_log_module.record_apify_run(
            run_data={"usage": {"COMPUTE_UNITS": 1.0}},
            items_count=5,
            operation="actor:foo",
        )


# ---------- record_openai_call ----------


def test_record_openai_call_with_dict_usage(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openai_input_per_1k_usd", 0.000150, raising=False)
    monkeypatch.setattr(settings, "openai_output_per_1k_usd", 0.000600, raising=False)
    monkeypatch.setattr(settings, "usd_to_eur_rate", 0.92, raising=False)

    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_openai_call(
            usage={"prompt_tokens": 1000, "completion_tokens": 500},
            operation="vision_call",
            meta={"asset_id": "abc-123"},
        )

    rows = session.exec(select(CostLog)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.provider == "openai"
    assert row.operation == "vision_call"
    # 1k input @ 0.00015 + 0.5k output @ 0.0006 = 0.00015 + 0.0003 = 0.00045 USD
    # = 0.045 cents -> rounded to 0
    assert row.cost_usd_cents == 0
    assert row.cost_meta["input_tokens"] == 1000
    assert row.cost_meta["output_tokens"] == 500
    assert row.cost_meta["asset_id"] == "abc-123"


def test_record_openai_call_with_object_usage(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenAI SDK returns a CompletionUsage object with attribute access."""
    monkeypatch.setattr(settings, "openai_input_per_1k_usd", 0.000150, raising=False)
    monkeypatch.setattr(settings, "openai_output_per_1k_usd", 0.000600, raising=False)

    class FakeUsage:
        prompt_tokens = 100000
        completion_tokens = 50000

    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_openai_call(
            FakeUsage(), operation="chat_completion"
        )

    rows = session.exec(select(CostLog)).all()
    assert len(rows) == 1
    # 100k @ 0.00015 + 50k @ 0.0006 = 0.015 + 0.03 = 0.045 USD = 4.5 cents.
    # Python's int(round(4.5)) yields 4 (banker's rounding) — we accept that
    # since cost_log is best-effort accounting, not financial reporting.
    assert rows[0].cost_usd_cents in (4, 5)
    assert rows[0].cost_meta["input_tokens"] == 100000
    assert rows[0].cost_meta["output_tokens"] == 50000


def test_record_openai_call_handles_none_usage(
    session: Session,
) -> None:
    """If the SDK returned no usage info, log a 0-cost row anyway."""
    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_openai_call(None, operation="vision_call")

    rows = session.exec(select(CostLog)).all()
    assert len(rows) == 1
    assert rows[0].cost_usd_cents == 0
    assert rows[0].cost_meta["input_tokens"] == 0


def test_record_openai_call_uses_millicents_for_sub_cent_precision(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Precision-loss-fix regression-guard. A typical gpt-4o-mini vision
    call (~965 input + ~250 output tokens) costs ~0.03 cents — well below
    the 0.5-cent rounding floor. Before the fix every such call landed
    with cost_usd_cents=0, making aggregation worthless. The millicent
    column stores the unrounded integer (1 cent = 1000 millicents) so the
    sub-cent signal survives."""
    monkeypatch.setattr(settings, "openai_input_per_1k_usd", 0.000150, raising=False)
    monkeypatch.setattr(settings, "openai_output_per_1k_usd", 0.000600, raising=False)

    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_openai_call(
            usage={"prompt_tokens": 965, "completion_tokens": 250},
            operation="vision_call",
        )

    row = session.exec(select(CostLog)).one()
    # input_usd  = 965/1000 * 0.000150 = 0.00014475
    # output_usd = 250/1000 * 0.000600 = 0.00015000
    # total      = 0.00029475 USD = 0.029475 cents = 29.475 millicents
    # int(round(...)) -> 29 millicents (the 0 from cents is the bug we fix)
    assert row.cost_usd_cents == 0
    assert row.cost_usd_millicents == 29
    assert row.cost_meta["cost_usd_millicents"] == 29


# ---------- record_anthropic_call ----------


def test_record_anthropic_call_opus_pricing(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opus 4.7 must route to ``anthropic_opus`` with the configured
    $15/$75-per-Mtok rates. Before the fix, ``claude-opus-*`` fell
    through to the generic ``anthropic`` bucket with rate=0 and the
    weekly-brief cost was silently 0 cents."""
    monkeypatch.setattr(settings, "anthropic_opus_input_per_1k_usd", 0.015, raising=False)
    monkeypatch.setattr(settings, "anthropic_opus_output_per_1k_usd", 0.075, raising=False)

    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_anthropic_call(
            usage={"input_tokens": 10_000, "output_tokens": 2_000},
            model="claude-opus-4-7",
            operation="weekly_brief",
            meta={"pair_key": "disney"},
        )

    row = session.exec(select(CostLog)).one()
    # input_usd  = 10000/1000 * 0.015 = 0.15
    # output_usd = 2000/1000  * 0.075 = 0.15
    # total      = 0.30 USD = 30 cents = 30000 millicents
    assert row.provider == "anthropic_opus"
    assert row.operation == "weekly_brief"
    assert row.cost_usd_cents == 30
    assert row.cost_usd_millicents == 30_000
    assert row.cost_meta["model"] == "claude-opus-4-7"
    assert row.cost_meta["pair_key"] == "disney"


def test_record_anthropic_call_bills_cache_tokens(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cache-Token muessen in die Input-Kosten eingehen.

    Vorher rechnete ``input_usd`` nur mit ``input_tokens`` — bei aktivem
    Prompt-Caching zaehlt das aber nur die Token NACH dem letzten
    Breakpoint. Der gecachte Anteil (Write + Read) fiel komplett aus der
    Rechnung, die Kosten wurden unterschaetzt und der Monats-Cap griff zu
    spaet. Gleiches ``input_tokens`` wie im Opus-Test oben, aber zusaetzlich
    Cache-Verkehr → die Gesamtkosten muessen hoeher liegen.
    """
    monkeypatch.setattr(settings, "anthropic_opus_input_per_1k_usd", 0.015, raising=False)
    monkeypatch.setattr(settings, "anthropic_opus_output_per_1k_usd", 0.075, raising=False)

    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_anthropic_call(
            usage={
                "input_tokens": 10_000,
                "output_tokens": 2_000,
                "cache_creation_input_tokens": 4_000,
                "cache_read_input_tokens": 20_000,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 4_000,
                    "ephemeral_1h_input_tokens": 0,
                },
            },
            model="claude-opus-4-8",
            operation="weekly_brief",
        )

    row = session.exec(select(CostLog)).one()
    # billable input = 10000 + 4000*1.25 + 0*2.00 + 20000*0.10 = 17000
    # input_usd      = 17000/1000 * 0.015 = 0.255
    # output_usd     = 2000/1000  * 0.075 = 0.150
    # total          = 0.405 USD = 40500 millicents (40 cents)
    assert row.cost_usd_millicents == 40_500
    assert row.cost_usd_cents == 40
    # Ohne den Fix waeren es 30_000 millicents gewesen (nur input_tokens).
    assert row.cost_usd_millicents > 30_000
    assert row.cost_meta["cache_creation_input_tokens"] == 4_000
    assert row.cost_meta["cache_read_input_tokens"] == 20_000
    assert row.cost_meta["cache_creation_5m"] == 4_000
    assert row.cost_meta["cache_creation_1h"] == 0
    assert row.cost_meta["prompt_tokens_total"] == 34_000


def test_record_anthropic_call_cache_write_without_split_bills_1h(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fehlt der 5m/1h-Split, wird der gesamte Write-Anteil zum teureren
    1h-Satz gerechnet — bewusst konservativ, damit der Budget-Cap eher zu
    frueh als zu spaet greift."""
    monkeypatch.setattr(settings, "anthropic_opus_input_per_1k_usd", 0.015, raising=False)
    monkeypatch.setattr(settings, "anthropic_opus_output_per_1k_usd", 0.0, raising=False)

    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_anthropic_call(
            usage={
                "input_tokens": 0,
                "output_tokens": 0,
                # kein ``cache_creation``-Split vorhanden
                "cache_creation_input_tokens": 4_000,
                "cache_read_input_tokens": 0,
            },
            model="claude-opus-4-8",
            operation="weekly_brief",
        )

    row = session.exec(select(CostLog)).one()
    # 4000 * 2.00 / 1000 * 0.015 = 0.12 USD = 12000 millicents
    assert row.cost_usd_millicents == 12_000
    assert row.cost_meta["cache_creation_1h"] == 4_000
    assert row.cost_meta["cache_creation_5m"] == 0


def test_record_anthropic_call_ignores_inconsistent_cache_split(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passt die Summe des Splits nicht zu ``cache_creation_input_tokens``,
    ist der Split nicht vertrauenswuerdig → ebenfalls konservativ 1h."""
    monkeypatch.setattr(settings, "anthropic_opus_input_per_1k_usd", 0.015, raising=False)
    monkeypatch.setattr(settings, "anthropic_opus_output_per_1k_usd", 0.0, raising=False)

    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_anthropic_call(
            usage={
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 4_000,
                "cache_read_input_tokens": 0,
                # 1000 + 1000 != 4000 → verworfen
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 1_000,
                    "ephemeral_1h_input_tokens": 1_000,
                },
            },
            model="claude-opus-4-8",
            operation="weekly_brief",
        )

    row = session.exec(select(CostLog)).one()
    assert row.cost_usd_millicents == 12_000  # 4000 * 2.00, nicht der Split
    assert row.cost_meta["cache_creation_1h"] == 4_000


def test_record_anthropic_call_without_cache_fields_unchanged(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: ``usage`` ohne Cache-Felder muss exakt dasselbe Ergebnis
    liefern wie vor dem Cache-Fix (Opus-Referenzwert: 30_000 millicents)."""
    monkeypatch.setattr(settings, "anthropic_opus_input_per_1k_usd", 0.015, raising=False)
    monkeypatch.setattr(settings, "anthropic_opus_output_per_1k_usd", 0.075, raising=False)

    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_anthropic_call(
            usage={"input_tokens": 10_000, "output_tokens": 2_000},
            model="claude-opus-4-8",
            operation="weekly_brief",
        )

    row = session.exec(select(CostLog)).one()
    assert row.cost_usd_millicents == 30_000
    assert row.cost_usd_cents == 30
    assert row.cost_meta["cache_creation_input_tokens"] == 0
    assert row.cost_meta["cache_read_input_tokens"] == 0
    assert row.cost_meta["prompt_tokens_total"] == 10_000


def test_record_anthropic_call_handles_unknown_model(
    session: Session,
) -> None:
    """A model string outside haiku/sonnet/opus must still produce a
    persisted row (auditable) rather than blowing up. Cost is 0 because
    we have no rate to apply — the cost_meta keeps the model name so
    Wolf can spot the unknown-model and ship a config update."""
    test_engine = session.get_bind()
    with patch.object(cost_log_module, "engine", test_engine):
        cost_log_module.record_anthropic_call(
            usage={"input_tokens": 1000, "output_tokens": 500},
            model="claude-future-9000",
            operation="weekly_brief",
        )

    row = session.exec(select(CostLog)).one()
    assert row.provider == "anthropic"
    assert row.cost_usd_cents == 0
    assert row.cost_usd_millicents == 0
    assert row.cost_meta["model"] == "claude-future-9000"


# ---------- /api/admin/cost-summary endpoint ----------


@pytest.fixture
def auth_off_client(monkeypatch: pytest.MonkeyPatch):
    """Endpoint runs through the global Bearer-auth middleware. We test the
    cost-summary logic itself with auth off (it's tested separately in
    test_auth_middleware.py).

    SQLite ':memory:' is per-connection-isolated, so TestClient's request-
    scoped Session does not see tables created by an outer setup engine.
    Override get_session with a Session bound to a single shared in-memory
    engine — the Session yielded to the endpoint and the Session we seed
    rows from share the same in-memory DB.
    """
    from app.database import get_session  # noqa: PLC0415

    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)

    test_engine = _shared_test_engine()
    SQLModel.metadata.create_all(test_engine)

    def _override_session():
        with Session(test_engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app), test_engine
    finally:
        app.dependency_overrides.pop(get_session, None)


def _seed_cost_log(session: Session, *, provider: str, operation: str,
                   eur_cents: int, usd_cents: int, when: datetime) -> None:
    session.add(
        CostLog(
            id=uuid4(),
            timestamp=when,
            provider=provider,
            operation=operation,
            cost_usd_cents=usd_cents,
            cost_eur_cents=eur_cents,
            cost_meta={},
        )
    )
    session.commit()


def test_cost_summary_groups_by_provider_default(auth_off_client) -> None:
    """Smoke test: empty DB returns an empty buckets list with totals=0."""
    client, _engine = auth_off_client
    response = client.get("/api/admin/cost-summary")
    assert response.status_code == 200
    body = response.json()
    assert body["group_by"] == "provider"
    assert body["total_count"] == 0
    assert body["total_cost_eur_cents"] == 0
    assert body["buckets"] == []


def test_cost_summary_aggregates_by_provider(auth_off_client) -> None:
    """Seed two apify rows + one openai row in the same in-memory engine the
    endpoint will read from, then check the buckets sum correctly."""
    client, test_engine = auth_off_client
    now = datetime.now(timezone.utc)
    with Session(test_engine) as s:
        _seed_cost_log(s, provider="apify", operation="actor:foo",
                       eur_cents=20, usd_cents=22, when=now)
        _seed_cost_log(s, provider="apify", operation="actor:bar",
                       eur_cents=10, usd_cents=11, when=now)
        _seed_cost_log(s, provider="openai", operation="vision_call",
                       eur_cents=4, usd_cents=5, when=now)

    response = client.get("/api/admin/cost-summary")
    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 3
    assert body["total_cost_eur_cents"] == 34  # 20+10+4
    assert body["total_cost_usd_cents"] == 38  # 22+11+5

    buckets_by_key = {b["key"]: b for b in body["buckets"]}
    assert buckets_by_key["apify"]["count"] == 2
    assert buckets_by_key["apify"]["cost_eur_cents"] == 30
    assert buckets_by_key["openai"]["count"] == 1


def test_cost_summary_rejects_invalid_dates(auth_off_client) -> None:
    client, _engine = auth_off_client
    response = client.get("/api/admin/cost-summary?from_date=not-a-date")
    assert response.status_code == 400


def test_cost_summary_requires_auth_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Endpoint must be gated by the global Bearer middleware — no special
    cost-summary token. Without a header (and auth on) we get 401."""
    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "valid-token", raising=False)

    client = TestClient(app)
    response = client.get("/api/admin/cost-summary")
    assert response.status_code == 401


def test_cost_summary_passes_with_correct_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auth-on smoke. Reuses the dependency-override pattern so we don't
    drag a real :memory: engine into this test — we only care that auth
    didn't reject the request, not the body content."""
    from app.database import get_session  # noqa: PLC0415

    monkeypatch.setattr(settings, "auth_enabled", True, raising=False)
    monkeypatch.setattr(settings, "api_token", "valid-token", raising=False)

    test_engine = _shared_test_engine()
    SQLModel.metadata.create_all(test_engine)

    def _override_session():
        with Session(test_engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    try:
        client = TestClient(app)
        response = client.get(
            "/api/admin/cost-summary",
            headers={"Authorization": "Bearer valid-token"},
        )
        assert response.status_code == 200
    finally:
        app.dependency_overrides.pop(get_session, None)
