"""add insight_report table for briefing persistence

Revision ID: a7f2c4b831e9
Revises: e5d8f1a36b40
Create Date: 2026-05-08 16:30:00.000000

Sprint 1 (Persistenz + ASCII-Keys + Disney-Fix) — adds the
``creative_radar.insight_report`` table that caches one weekly briefing
per (pair_key, iso_year, iso_week). Composite primary key matches the
natural lookup; ``generated_at`` index supports admin-overview queries
("last N persisted reports").

Cache semantics live in the API layer (`api/insights.py`):
- GET ``/api/insights/weekly?pair=X`` returns the persisted row if one
  exists for the current ISO week, otherwise generates + persists a new
  one.
- ``?force=true`` skips the cache lookup but still persists
  (Last-Write-Wins on the composite PK).
- ``?dry_run=true`` continues to skip both LLM and persistence.

Column-type notes:
- ``aggregation`` and ``llm_output`` use ``sa.JSON()`` (not JSONB) to
  stay consistent with ``cron_run.summary_json`` and ``post.raw_payload``.
  SQLite tests bootstrap from ``SQLModel.metadata.create_all`` which maps
  ``JSON`` to ``TEXT`` on SQLite; on Postgres ``sa.JSON()`` resolves to
  the ``json`` type. A future Sprint can switch to JSONB if we need
  GIN-indexed payload queries — for the current PK-only lookup pattern
  it makes no difference.
- ``cost_usd_cents``/``input_tokens``/``output_tokens`` are nullable so
  a defensive parse-fallback can still persist the report when the
  Anthropic SDK doesn't surface usage metadata.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7f2c4b831e9"
down_revision: Union[str, Sequence[str], None] = "e5d8f1a36b40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _table_schema() -> Union[str, None]:
    return SCHEMA if _is_postgres() else None


def upgrade() -> None:
    schema = _table_schema()

    # ``if_not_exists=True`` matches the cron_run-migration pattern: SQLite
    # test paths bootstrap via ``SQLModel.metadata.create_all`` so the table
    # may already exist; Postgres production runs against a fresh schema.
    op.create_table(
        "insight_report",
        sa.Column("pair_key", sa.String(length=64), nullable=False),
        sa.Column("iso_year", sa.Integer(), nullable=False),
        sa.Column("iso_week", sa.Integer(), nullable=False),
        sa.Column("aggregation", sa.JSON(), nullable=False),
        sa.Column("llm_output", sa.JSON(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("cost_usd_cents", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("pair_key", "iso_year", "iso_week"),
        schema=schema,
        if_not_exists=True,
    )

    # generated_at supports admin-overview queries; the composite PK
    # already covers the per-pair / per-week lookup that the GET endpoint
    # uses, no extra index needed there. Plain ascending B-tree — Postgres
    # serves ``ORDER BY generated_at DESC`` from it via a backward scan.
    op.create_index(
        "ix_insight_report_generated_at",
        "insight_report",
        ["generated_at"],
        unique=False,
        schema=schema,
        if_not_exists=True,
    )


def downgrade() -> None:
    schema = _table_schema()
    op.drop_index(
        "ix_insight_report_generated_at",
        table_name="insight_report",
        schema=schema,
        if_exists=True,
    )
    op.drop_table("insight_report", schema=schema, if_exists=True)
