"""Cron-Stage fuer die Post-Klassifikation (Trailer-Intelligence Stufe 1).

Hintergrund: Die Inventur vom 06.08.2026 fand nur 920 von 7.623 Posts
klassifiziert (12 %). Ursache war nicht ein kaputter Analyzer, sondern
eine fehlende Automatisierung — ``analyze_post`` hing ausschliesslich am
manuellen Admin-Endpunkt. ``_run_post_analysis_backlog`` haengt ihn in den
Wochen-Cron, nach dem Muster der beiden Vision-Stages.

Getestet wird hier die Stage-Mechanik, nicht der Analyzer selbst (der hat
seine eigene Suite in test_post_analyzer.py):

1. Cap begrenzt die Anzahl verarbeiteter Posts.
2. Auswahl ist newest-first (der Empfehlungs-Baustein liest ein 7d-Fenster).
3. Bereits analysierte Posts (``last_analyzed_at``) werden nicht angefasst.
4. Ein Fehler bei einem Post stoppt die Stage nicht.
5. Ein Auth-Fehler stoppt die Stage sofort (waere sonst fuer jeden
   Folge-Post die gleiche sichere Fehlschlag-Ausgabe).
6. cap=0 und fehlender Anthropic-Key deaktivieren sauber statt zu crashen.
7. ``skip_vision`` reicht bis in ``analyze_post`` durch und schlaegt sich
   in der Kostenschaetzung nieder.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import cron as cron_module
from app.models.entities import (
    AcquisitionStrategy,
    Channel,
    Market,
    Post,
    Priority,
    QualityTier,
)


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


def _make_channel(session: Session) -> Channel:
    ch = Channel(
        name="TestChannel",
        platform="youtube",
        url="https://example.test/ch",
        market=Market.INT,
        priority=Priority.B,
        quality_tier=QualityTier.P1,
        acquisition_strategy=AcquisitionStrategy.YOUTUBE_API,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


def _make_post(
    session: Session,
    channel: Channel,
    *,
    detected_at: datetime,
    last_analyzed_at: datetime | None = None,
) -> Post:
    post = Post(
        channel_id=channel.id,
        platform="youtube",
        post_url=f"https://example.test/p/{uuid4()}",
        caption="Official trailer is here.",
        raw_payload={},
        detected_at=detected_at,
        last_analyzed_at=last_analyzed_at,
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    return post


def _ok_result(post_id, *, asset_created: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        post_id=post_id, status="analyzed", asset_created=asset_created,
        errors=[], calls={"haiku": 1, "sonnet": 1, "sonnet_vision": 0},
    )


def _patch_analyzer(analyze_side_effect, *, configured: bool = True):
    """Patch the lazily-imported analyzer trio inside the stage.

    ``_run_post_analysis_backlog`` imports from app.services.post_analyzer
    and app.services.anthropic_client at call time (SDK is optional), so
    the patch has to target those modules, not the cron module.
    """
    import app.services.anthropic_client as ac
    import app.services.post_analyzer as pa

    return (
        patch.object(pa, "analyze_post", side_effect=analyze_side_effect),
        patch.object(ac, "is_anthropic_configured", return_value=configured),
    )


# ---------- Cap + Auswahlreihenfolge ----------------------------------


def test_cap_limits_number_of_posts(session: Session):
    ch = _make_channel(session)
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for i in range(5):
        _make_post(session, ch, detected_at=base + timedelta(hours=i))

    seen = []

    def fake(sess, post, *, skip_vision=False):
        seen.append(post.id)
        return _ok_result(post.id)

    p_analyze, p_conf = _patch_analyzer(fake)
    with p_analyze, p_conf:
        out = cron_module._run_post_analysis_backlog(session, 2, skip_vision=True)

    assert out["enabled"] is True
    assert out["selected"] == 2
    assert out["attempted"] == 2
    assert out["analyzed"] == 2
    assert len(seen) == 2


def test_selection_is_newest_first(session: Session):
    """Der Empfehlungs-Baustein aggregiert ein 7d-Fenster. Oldest-first
    wuerde den Cap auf 90 Tage alte Zeilen verbrauchen und die aktuelle
    Woche dauerhaft unklassifiziert lassen."""
    ch = _make_channel(session)
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    oldest = _make_post(session, ch, detected_at=base)
    middle = _make_post(session, ch, detected_at=base + timedelta(days=5))
    newest = _make_post(session, ch, detected_at=base + timedelta(days=10))

    seen = []

    def fake(sess, post, *, skip_vision=False):
        seen.append(post.id)
        return _ok_result(post.id)

    p_analyze, p_conf = _patch_analyzer(fake)
    with p_analyze, p_conf:
        cron_module._run_post_analysis_backlog(session, 2, skip_vision=True)

    assert seen == [newest.id, middle.id]
    assert oldest.id not in seen


def test_already_analyzed_posts_are_skipped(session: Session):
    ch = _make_channel(session)
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    done = _make_post(
        session, ch, detected_at=base + timedelta(days=9),
        last_analyzed_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )
    todo = _make_post(session, ch, detected_at=base)

    seen = []

    def fake(sess, post, *, skip_vision=False):
        seen.append(post.id)
        return _ok_result(post.id)

    p_analyze, p_conf = _patch_analyzer(fake)
    with p_analyze, p_conf:
        out = cron_module._run_post_analysis_backlog(session, 50, skip_vision=True)

    assert seen == [todo.id]
    assert done.id not in seen
    assert out["selected"] == 1


# ---------- Fehler-Isolation ------------------------------------------


def test_single_post_failure_does_not_stop_the_stage(session: Session):
    ch = _make_channel(session)
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for i in range(3):
        _make_post(session, ch, detected_at=base + timedelta(hours=i))

    calls = {"n": 0}

    def fake(sess, post, *, skip_vision=False):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return _ok_result(post.id)

    p_analyze, p_conf = _patch_analyzer(fake)
    with p_analyze, p_conf:
        out = cron_module._run_post_analysis_backlog(session, 10, skip_vision=True)

    assert out["attempted"] == 3
    assert out["analyzed"] == 2
    assert out["errors"] == 1
    assert len(out["error_samples"]) == 1
    assert "RuntimeError" in out["error_samples"][0]


def test_analyzer_error_status_counts_as_error(session: Session):
    """analyze_post gibt bei zweimal invalidem Classifier-JSON
    status='error' zurueck, ohne etwas zu schreiben — das darf nicht als
    Erfolg durchgehen."""
    ch = _make_channel(session)
    _make_post(session, ch, detected_at=datetime(2026, 8, 1, tzinfo=timezone.utc))

    def fake(sess, post, *, skip_vision=False):
        return SimpleNamespace(
            post_id=post.id, status="error", asset_created=False,
            errors=["haiku:invalid json twice"], calls={},
        )

    p_analyze, p_conf = _patch_analyzer(fake)
    with p_analyze, p_conf:
        out = cron_module._run_post_analysis_backlog(session, 10, skip_vision=True)

    assert out["analyzed"] == 0
    assert out["errors"] == 1


def test_auth_error_stops_stage_immediately(session: Session):
    """Auth ist ein Konfigurationsfehler — er wiederholt sich fuer jeden
    Folge-Post. Die Stage bricht ab, statt den Cap leerzulaufen."""
    from app.services.anthropic_client import AnthropicAuthError

    ch = _make_channel(session)
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for i in range(4):
        _make_post(session, ch, detected_at=base + timedelta(hours=i))

    calls = {"n": 0}

    def fake(sess, post, *, skip_vision=False):
        calls["n"] += 1
        if calls["n"] == 2:
            raise AnthropicAuthError("bad key")
        return _ok_result(post.id)

    p_analyze, p_conf = _patch_analyzer(fake)
    with p_analyze, p_conf:
        out = cron_module._run_post_analysis_backlog(session, 10, skip_vision=True)

    assert out["auth_failed"] is True
    assert calls["n"] == 2  # nach dem Auth-Fehler kein weiterer Versuch
    assert out["analyzed"] == 1


# ---------- Deaktivierte Pfade ----------------------------------------


def test_cap_zero_disables_stage(session: Session):
    out = cron_module._run_post_analysis_backlog(session, 0, skip_vision=True)
    assert out == {"enabled": False, "cap": 0}


def test_missing_anthropic_key_disables_stage_without_error(session: Session):
    """Staging laeuft bewusst ohne Anthropic-Key — das ist ein normaler
    Skip, kein Fehler."""
    ch = _make_channel(session)
    _make_post(session, ch, detected_at=datetime(2026, 8, 1, tzinfo=timezone.utc))

    p_analyze, p_conf = _patch_analyzer(lambda *a, **k: None, configured=False)
    with p_analyze, p_conf:
        out = cron_module._run_post_analysis_backlog(session, 10, skip_vision=True)

    assert out["enabled"] is False
    assert out["reason"] == "anthropic_not_configured"


# ---------- skip_vision ------------------------------------------------


@pytest.mark.parametrize("skip_vision", [True, False])
def test_skip_vision_is_passed_through(session: Session, skip_vision: bool):
    ch = _make_channel(session)
    _make_post(session, ch, detected_at=datetime(2026, 8, 1, tzinfo=timezone.utc))

    received = {}

    def fake(sess, post, *, skip_vision=False):
        received["skip_vision"] = skip_vision
        return _ok_result(post.id)

    p_analyze, p_conf = _patch_analyzer(fake)
    with p_analyze, p_conf:
        out = cron_module._run_post_analysis_backlog(
            session, 10, skip_vision=skip_vision
        )

    assert received["skip_vision"] is skip_vision
    assert out["skip_vision"] is skip_vision


def test_text_only_cost_estimate_is_lower_than_full(session: Session):
    """Der Vision-Call dominiert die Kosten — die Schaetzung im Summary
    muss das abbilden, sonst liest Wolf im CronRun eine falsche Zahl."""
    ch = _make_channel(session)
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for i in range(2):
        _make_post(session, ch, detected_at=base + timedelta(hours=i))

    def fake(sess, post, *, skip_vision=False):
        return _ok_result(post.id)

    costs = {}
    for flag in (True, False):
        # Posts wieder auf unanalysiert zuruecksetzen fuer den zweiten Lauf
        for p in session.exec(select(Post)).all():
            p.last_analyzed_at = None
            session.add(p)
        session.commit()

        p_analyze, p_conf = _patch_analyzer(fake)
        with p_analyze, p_conf:
            out = cron_module._run_post_analysis_backlog(session, 10, skip_vision=flag)
        costs[flag] = out["estimated_cost_usd"]

    assert costs[True] < costs[False]
    # Vision ist ~72 % der Kosten -> text-only liegt bei grob einem Drittel
    assert costs[True] == pytest.approx(costs[False] * 0.287, rel=0.05)


# ---------- Zeitbudget der Stage (Vorfall 10.08.2026) ------------------
#
# Am 10.08. hat diese Stage mit cap=2500 den ganzen Cron-Lauf ins
# Gesamt-Timeout gezogen: ~3,7s pro Post ergeben rund 2,5 Stunden, die zur
# Grundlaufzeit von ~7.200s dazukamen; abgeschnitten wurde bei 9.000s.
# Scrape war fertig, aber Briefs, Roundups und Wochenbriefings fehlten —
# sie stehen hinter dieser Stage. Ein Cap in *Posts* schuetzt nicht, weil
# er nichts ueber Zeit aussagt. Diese Tests decken den Zeitdeckel ab.


def test_budget_stops_the_loop_between_posts(session: Session):
    """Ist das Budget aufgebraucht, bricht die Stage geordnet ab."""
    ch = _make_channel(session)
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for i in range(10):
        _make_post(session, ch, detected_at=base + timedelta(hours=i))

    clock = iter([0.0] + [float(i) for i in range(1, 60)])
    processed = []

    def fake(sess, post, *, skip_vision=False):
        processed.append(post.id)
        return _ok_result(post.id)

    p_analyze, p_conf = _patch_analyzer(fake)
    with p_analyze, p_conf, patch.object(
        cron_module.time, "monotonic", side_effect=lambda: next(clock)
    ):
        result = cron_module._run_post_analysis_backlog(
            session, 10, skip_vision=True, budget_seconds=3.0
        )

    assert result["timed_out"] is True
    # Nicht alle zehn — der Deckel hat gegriffen.
    assert result["attempted"] < 10
    assert result["remaining"] == 10 - result["attempted"]
    assert len(processed) == result["attempted"]


def test_work_done_before_the_budget_ran_out_is_kept(session: Session):
    """Der Abbruch verwirft nichts: analysierte Posts bleiben analysiert.

    Das ist die Eigenschaft, die den kooperativen Abbruch ueberhaupt
    zulaessig macht — die Schleife committet nach jedem Post.
    """
    ch = _make_channel(session)
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for i in range(6):
        _make_post(session, ch, detected_at=base + timedelta(hours=i))

    clock = iter([0.0] + [float(i) for i in range(1, 40)])

    def fake(sess, post, *, skip_vision=False):
        post.last_analyzed_at = datetime.now(timezone.utc)
        sess.add(post)
        return _ok_result(post.id)

    p_analyze, p_conf = _patch_analyzer(fake)
    with p_analyze, p_conf, patch.object(
        cron_module.time, "monotonic", side_effect=lambda: next(clock)
    ):
        result = cron_module._run_post_analysis_backlog(
            session, 6, skip_vision=True, budget_seconds=2.0
        )

    persisted = len(
        session.exec(select(Post).where(Post.last_analyzed_at.is_not(None))).all()
    )
    assert result["analyzed"] == persisted
    assert persisted > 0
    # Und der Rest ist nicht verloren, nur verschoben.
    assert result["remaining"] > 0


def test_without_budget_nothing_changes(session: Session):
    """Ohne ``budget_seconds`` verhaelt sich die Stage wie bisher."""
    ch = _make_channel(session)
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for i in range(4):
        _make_post(session, ch, detected_at=base + timedelta(hours=i))

    p_analyze, p_conf = _patch_analyzer(lambda s, p, **k: _ok_result(p.id))
    with p_analyze, p_conf:
        result = cron_module._run_post_analysis_backlog(session, 4, skip_vision=True)

    assert result["timed_out"] is False
    assert result["attempted"] == 4
    assert result["remaining"] == 0
    assert result["budget_seconds"] is None


def test_generous_budget_processes_everything(session: Session):
    ch = _make_channel(session)
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for i in range(4):
        _make_post(session, ch, detected_at=base + timedelta(hours=i))

    p_analyze, p_conf = _patch_analyzer(lambda s, p, **k: _ok_result(p.id))
    with p_analyze, p_conf:
        result = cron_module._run_post_analysis_backlog(
            session, 4, skip_vision=True, budget_seconds=3600
        )

    assert result["timed_out"] is False
    assert result["attempted"] == 4
    assert result["remaining"] == 0


# ---------- Der Default des Zeitbudgets --------------------------------


def test_stage_timeout_default_fits_under_the_total_timeout(monkeypatch):
    """1800s sind auch beim Code-Default von 9.000s noch sicher.

    Gemessene Grundlaufzeit ohne diese Stage: ~7.200s. 7.200 + 1.800 =
    9.000 — der Wert passt also selbst dann, wenn eine Umgebung
    ``CRON_TOTAL_RUN_TIMEOUT_SECONDS`` nie angehoben hat.
    """
    monkeypatch.delenv("CRON_POST_ANALYSIS_STAGE_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("CRON_TOTAL_RUN_TIMEOUT_SECONDS", raising=False)

    stage = cron_module._post_analysis_stage_timeout_seconds()
    total = cron_module._cron_total_timeout_seconds()

    assert stage == 1800
    assert stage <= total - 7200


def test_stage_timeout_is_configurable_and_survives_garbage(monkeypatch):
    monkeypatch.setenv("CRON_POST_ANALYSIS_STAGE_TIMEOUT_SECONDS", "2700")
    assert cron_module._post_analysis_stage_timeout_seconds() == 2700

    monkeypatch.setenv("CRON_POST_ANALYSIS_STAGE_TIMEOUT_SECONDS", "keine-zahl")
    assert cron_module._post_analysis_stage_timeout_seconds() == 1800


def test_cron_call_site_actually_passes_the_budget():
    """Die Verdrahtung ist der eigentliche Schutz — also wird sie geprueft.

    Ein Zeitbudget, das die Stage kann, aber der Cron nicht mitgibt, haette
    den 10.08. nicht verhindert. Geprueft wird per AST statt per Textsuche,
    damit Umformatierung den Test nicht bricht.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(cron_module))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and any(
            isinstance(a, ast.Name) and a.id == "_run_post_analysis_backlog"
            for a in node.args
        )
    ]
    assert calls, "Aufruf von _run_post_analysis_backlog nicht gefunden"
    for call in calls:
        keywords = {kw.arg for kw in call.keywords}
        assert "budget_seconds" in keywords, (
            "Der Cron ruft die Post-Analyse ohne budget_seconds auf — genau "
            "diese Luecke hat am 10.08. den ganzen Lauf gerissen."
        )
