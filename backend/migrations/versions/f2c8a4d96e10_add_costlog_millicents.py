"""add cost_usd_millicents to costlog for sub-cent precision

Revision ID: f2c8a4d96e10
Revises: d8a2f5e914bc
Create Date: 2026-05-12 09:00:00.000000

Cost-Tracking-Fix Anthropic + OpenAI (2026-05-12) — additive sub-cent
precision column for ``creative_radar.costlog``.

Background: per-call OpenAI (gpt-4o-mini) and Anthropic-Haiku costs
land in the 0.03-0.06 cent range, which the existing
``cost_usd_cents INTEGER`` flattens to 0. F0.6 cost-summary therefore
sees provider buckets at $0 even after thousands of calls. The new
``cost_usd_millicents`` column (1 cent = 1000 millicents) stores the
unrounded integer so the cost-summary endpoint can aggregate without
losing the per-call signal.

``cost_usd_cents`` stays on the table for back-compat with historical
Apify rows and the consumer code paths that already key off it. Both
columns are written by ``services/cost_log._persist``; the millicent
column is the authoritative number going forward.

SQLite test path: ``SQLModel.metadata.create_all`` already creates the
column from the updated entity, so this migration is a Postgres-only
op-guarded ADD COLUMN. ``IF NOT EXISTS`` keeps it idempotent.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "f2c8a4d96e10"
down_revision: Union[str, Sequence[str], None] = "d8a2f5e914bc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.execute(
        f"ALTER TABLE {SCHEMA}.costlog "
        "ADD COLUMN IF NOT EXISTS cost_usd_millicents INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute(
        f"ALTER TABLE {SCHEMA}.costlog DROP COLUMN IF EXISTS cost_usd_millicents"
    )
