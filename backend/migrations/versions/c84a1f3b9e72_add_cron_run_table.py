"""add cron_run table

Revision ID: c84a1f3b9e72
Revises: b3c7e1d8a204
Create Date: 2026-05-04 12:30:00.000000

Sprint Cron-Background-Task — log table for cron-sync invocations.
Additive: a single new table ``creative_radar.cron_run`` with two indexes
(started_at desc for the GET /runs query, status for the running-row
lookup). No changes to existing tables.

The Asset/Post pipeline is untouched; this table only carries operational
metadata about background sync jobs (start/end timestamps, status, summary
payload, optional error message).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c84a1f3b9e72"
down_revision: Union[str, Sequence[str], None] = "b3c7e1d8a204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _table_schema() -> Union[str, None]:
    return SCHEMA if _is_postgres() else None


def upgrade() -> None:
    schema = _table_schema()

    # ``if_not_exists=True`` mirrors the costlog-migration pattern: SQLite
    # test paths bootstrap via ``SQLModel.metadata.create_all`` first, so
    # the table may already exist when the alembic chain runs. Postgres
    # production runs the migration on a fresh schema where the table is
    # absent — both paths converge.
    op.create_table(
        "cron_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("run_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=schema,
        if_not_exists=True,
    )
    op.create_index(
        "ix_cron_run_started_at",
        "cron_run",
        ["started_at"],
        unique=False,
        schema=schema,
        if_not_exists=True,
    )
    op.create_index(
        "ix_cron_run_status",
        "cron_run",
        ["status"],
        unique=False,
        schema=schema,
        if_not_exists=True,
    )


def downgrade() -> None:
    schema = _table_schema()
    op.drop_index("ix_cron_run_status", table_name="cron_run", schema=schema, if_exists=True)
    op.drop_index("ix_cron_run_started_at", table_name="cron_run", schema=schema, if_exists=True)
    op.drop_table("cron_run", schema=schema, if_exists=True)
