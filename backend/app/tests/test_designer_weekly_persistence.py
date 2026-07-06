"""Tests für die Persistenz des Designer-Wochenbriefings.

Mirror von ``test_cutter_weekly_persistence.py``.

Kernpunkte:
- Roundtrip: Row trägt evidence + llm_output + Kosten/Token, PK
  (iso_year, iso_week), Last-Write-Wins beim Regenerate.
- BEWUSSTE Abweichung von der Roundup-Konvention (identisch zu Cutter):
  llm_output=None wird TROTZDEM persistiert (Evidence-Blob =
  Kalibrierungs-Produkt), raw_llm_text trägt die verworfene Antwort.
- Leerlauf-Woche (model='none') persistiert mit drei Code-Blöcken.
- Re-Hydrierung der Blobs über die Pydantic-Schemas.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import DesignerWeeklyBriefing
from app.schemas.insights import (
    DesignerPlatformBlock,
    DesignerWeeklyLLMReport,
    DesignerWeeklyReport,
    WeeklyBriefingEvidence,
    WeeklyBriefingParams,
    WeeklyBriefingSources,
    WeeklyEvidencePost,
    WeeklyPlatformEvidence,
)
from app.services import designer_weekly
from app.services.weekly_briefing_evidence import week_bounds


ANCHOR = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
ISO_YEAR, ISO_WEEK = ANCHOR.isocalendar().year, ANCHOR.isocalendar().week
WEEK_START, WEEK_END = week_bounds(ISO_YEAR, ISO_WEEK)


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(prefix="cr_designer_persist_", suffix=".db")
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


def _evidence() -> WeeklyBriefingEvidence:
    post = WeeklyEvidencePost(
        post_url="https://ig.example/1",
        platform="instagram",
        er=0.2,
        views=1000,
        likes=200,
        comments=0,
        engagement_sum=200,
        distinct_key="Film A",
        source="pair:disney",
        title_original="Film A",
        published_at=WEEK_START + timedelta(days=1),
    )
    return WeeklyBriefingEvidence(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        week_start=WEEK_START,
        week_end=WEEK_END,
        params=WeeklyBriefingParams(
            min_posts=5, min_distinct_keys=3, p75_window_weeks=8, p75_min_sample=30
        ),
        sources=WeeklyBriefingSources(pair_briefs=["disney"]),
        platforms=[
            WeeklyPlatformEvidence(
                platform="instagram",
                status="pattern_released",
                p75_er=0.15,
                p75_sample_size=40,
                week_posts_total=5,
                candidates_above_p75=5,
                distinct_keys=["Film A"],
                supporting_posts=[post],
            ),
            WeeklyPlatformEvidence(
                platform="tiktok", status="no_pattern", reason="zu wenig", p75_er=0.1,
            ),
            WeeklyPlatformEvidence(
                platform="youtube", status="no_threshold", reason="kein n",
            ),
        ],
        week_posts_total=5,
        title_key_share=1.0,
    )


def _llm_output() -> DesignerWeeklyLLMReport:
    return DesignerWeeklyLLMReport(
        bloecke=[
            DesignerPlatformBlock(
                platform="instagram",
                beobachtung="Grossflaechige Text-Overlays trugen die Woche.",
                cited_post_ids=["https://ig.example/1"],
                generated_by="llm",
            ),
            DesignerPlatformBlock(
                platform="tiktok",
                beobachtung="Kein klares Muster diese Woche auf TikTok: zu wenig",
                generated_by="code",
            ),
            DesignerPlatformBlock(
                platform="youtube",
                beobachtung="Keine belastbare Vergleichsbasis fuer YouTube diese Woche: kein n",
                generated_by="code",
            ),
        ],
        data_caveats=["Dünne Basis."],
    )


def _report(**overrides) -> DesignerWeeklyReport:
    fields = dict(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        generated_at=ANCHOR,
        model="claude-opus-4-7",
        evidence=_evidence(),
        llm_output=_llm_output(),
        cost_usd_estimate=0.42,
        input_tokens=1000,
        output_tokens=300,
    )
    fields.update(overrides)
    return DesignerWeeklyReport(**fields)


def test_persist_roundtrip_and_rehydration(db):
    with Session(db) as session:
        designer_weekly._persist_designer_weekly(session, _report())
        row = session.get(DesignerWeeklyBriefing, (ISO_YEAR, ISO_WEEK))
        assert row is not None
        assert row.model == "claude-opus-4-7"
        assert row.cost_usd_cents == 42
        assert row.input_tokens == 1000
        # Re-Hydrierung über die Pydantic-Schemas — der Lese-Pfad der
        # Kalibrierung (Admin/DB) bekommt validierende Modelle.
        evidence = WeeklyBriefingEvidence.model_validate(row.evidence)
        assert evidence.platforms[0].status == "pattern_released"
        assert evidence.platforms[0].supporting_posts[0].post_url == "https://ig.example/1"
        assert evidence.title_key_share == pytest.approx(1.0)
        llm = DesignerWeeklyLLMReport.model_validate(row.llm_output)
        assert [b.generated_by for b in llm.bloecke] == ["llm", "code", "code"]


def test_persist_is_last_write_wins(db):
    with Session(db) as session:
        designer_weekly._persist_designer_weekly(session, _report(model="first"))
        designer_weekly._persist_designer_weekly(session, _report(model="second"))
        rows = session.exec(select(DesignerWeeklyBriefing)).all()
        assert len(rows) == 1
        assert rows[0].model == "second"


def test_persist_keeps_row_when_llm_output_is_none(db):
    """Die bewusste Abweichung: Evidence ist das Produkt — eine Woche mit
    verworfener LLM-Synthese wird trotzdem persistiert."""
    report = _report(
        llm_output=None,
        raw_llm_text='{"bloecke": "verworfen wegen Citation-Verstoss"}',
        cost_usd_estimate=0.10,
    )
    with Session(db) as session:
        designer_weekly._persist_designer_weekly(session, report)
        row = session.get(DesignerWeeklyBriefing, (ISO_YEAR, ISO_WEEK))
        assert row is not None
        assert row.llm_output is None
        assert "verworfen" in row.raw_llm_text
        evidence = WeeklyBriefingEvidence.model_validate(row.evidence)
        assert evidence.platforms[0].status == "pattern_released"


def test_generate_and_persist_no_pattern_week(db, monkeypatch):
    """Leerlauf-Woche End-to-End: kein LLM-Call, model='none', Row mit
    drei Code-Blöcken und vollem Evidence-Blob."""
    idle_evidence = _evidence()
    for p in idle_evidence.platforms:
        p.status = "no_pattern" if p.platform != "youtube" else "no_threshold"
        p.reason = p.reason or "unter Schwelle"
        p.supporting_posts = []
    monkeypatch.setattr(
        designer_weekly, "build_weekly_evidence",
        lambda session, now=None: idle_evidence,
    )
    monkeypatch.setattr(
        designer_weekly, "collect_forecast_signals", lambda session: []
    )

    with Session(db) as session:
        report = designer_weekly.generate_and_persist_designer_weekly(
            session, now=ANCHOR
        )
        row = session.get(DesignerWeeklyBriefing, (ISO_YEAR, ISO_WEEK))

    assert report.model == "none"
    assert row is not None
    assert row.model == "none"
    assert row.cost_usd_cents is None
    llm = DesignerWeeklyLLMReport.model_validate(row.llm_output)
    assert all(b.generated_by == "code" for b in llm.bloecke)
