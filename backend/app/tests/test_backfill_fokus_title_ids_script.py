"""V3 Sprint 1, Commit 4 — backfill script for fokus title_id.
Verifies dry-run writes nothing, --apply sets title_id over the deterministic
chain, the rest of llm_output is preserved, and re-runs are idempotent.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import (
    Asset, AssetType, Channel, InsightReport as InsightReportRow,
    Market, Post, ReviewStatus, Title,
)
from scripts.backfill_fokus_title_ids import run


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


def _seed(engine) -> tuple[str, str]:
    """One brief with a two-item aktuell_im_fokus: one resolvable post_url,
    one unknown. Returns (resolvable_title_id, brief_key)."""
    with Session(engine) as s:
        title = Title(id=uuid4(), title_original="Mortal Kombat II", active=True)
        ch = Channel(id=uuid4(), name="warnerde", handle="warnerde",
                     url="https://x/warnerde", platform="tiktok", market=Market.DE)
        s.add(title)
        s.add(ch)
        s.commit()
        post = Post(id=uuid4(), channel_id=ch.id, platform="tiktok",
                    post_url="https://d/known")
        s.add(post)
        s.commit()
        s.add(Asset(id=uuid4(), post_id=post.id, title_id=title.id,
                    asset_type=AssetType.UNKNOWN, review_status=ReviewStatus.NEW))
        s.commit()

        row = InsightReportRow(
            pair_key="warnerbros", iso_year=2026, iso_week=21,
            generated_at=__import__("datetime").datetime(2026, 5, 25, tzinfo=__import__("datetime").timezone.utc),
            aggregation={},
            llm_output={
                "headline": "keep me",
                "aktuell_im_fokus": [
                    {"titel": "Mortal Kombat II", "markt": "DE", "format_typ": "clip",
                     "kennzahl": "x", "post_url": "https://d/known"},
                    {"titel": "Unknown", "markt": "US", "format_typ": "clip",
                     "kennzahl": "y", "post_url": "https://d/missing"},
                ],
            },
            model="test-model",
        )
        s.add(row)
        s.commit()
        return str(title.id)


def test_dry_run_writes_nothing(engine):
    title_id = _seed(engine)
    with Session(engine) as s:
        summary = run(s, apply=False)
    assert summary["items_set"] == 1
    assert summary["items_stay_none"] == 1
    assert summary["briefs_changed"] == 0
    # DB unchanged: no title_id persisted.
    with Session(engine) as s:
        row = s.exec(__import__("sqlmodel").select(InsightReportRow)).first()
        assert "title_id" not in row.llm_output["aktuell_im_fokus"][0]


def test_apply_sets_title_id_and_preserves_rest(engine):
    title_id = _seed(engine)
    with Session(engine) as s:
        summary = run(s, apply=True)
    assert summary["items_set"] == 1
    assert summary["briefs_changed"] == 1
    with Session(engine) as s:
        row = s.exec(__import__("sqlmodel").select(InsightReportRow)).first()
        fokus = row.llm_output["aktuell_im_fokus"]
        assert fokus[0]["title_id"] == title_id          # resolvable -> set
        assert fokus[1].get("title_id") is None           # unknown -> stays None/absent
        # rest of the blob untouched
        assert row.llm_output["headline"] == "keep me"
        assert fokus[0]["titel"] == "Mortal Kombat II"


def test_apply_is_idempotent(engine):
    _seed(engine)
    with Session(engine) as s:
        run(s, apply=True)
    with Session(engine) as s:
        summary2 = run(s, apply=True)
    # Second pass: the already-set item is skipped, nothing rewritten.
    assert summary2["items_already"] == 1
    assert summary2["items_set"] == 0
    assert summary2["briefs_changed"] == 0
