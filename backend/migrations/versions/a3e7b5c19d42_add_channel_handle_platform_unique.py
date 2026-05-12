"""add UNIQUE constraint on channel(handle, platform)

Revision ID: a3e7b5c19d42
Revises: f2c8a4d96e10
Create Date: 2026-05-12

Sprint Tech-Debt-1 — makes silent duplicate channels structurally
impossible. Two cases caught manually on 2026-05-12 (LionsgateMovies
on YouTube, ``starwars`` on Instagram) showed that the ``channel``
table has no DB-side guard against re-inserting the same
``(handle, platform)`` pair from a different import path; the only
defence was app-level pre-check, which various Perplexity-import and
inventory scripts bypass.

Pre-flight required: this migration FAILS HARD if duplicates exist
at upgrade time (Postgres rejects ``CREATE UNIQUE CONSTRAINT`` against
data that violates it). Wolf runs the audit query in the PR
description before merging — any survivors are deactivated via a
separate UPDATE migration first.

Schema-qualified on Postgres (``creative_radar.channel``), bare on
SQLite for the alembic-roundtrip tests. ``batch_alter_table`` is used
so the SQLite path recreates the table with the constraint (SQLite
cannot ALTER TABLE ADD CONSTRAINT).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a3e7b5c19d42"
down_revision: Union[str, Sequence[str], None] = "f2c8a4d96e10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"
CONSTRAINT_NAME = "channel_handle_platform_unique"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _table_schema() -> Union[str, None]:
    return SCHEMA if _is_postgres() else None


def upgrade() -> None:
    schema = _table_schema()
    with op.batch_alter_table("channel", schema=schema) as batch_op:
        batch_op.create_unique_constraint(
            CONSTRAINT_NAME,
            ["handle", "platform"],
        )


def downgrade() -> None:
    schema = _table_schema()
    with op.batch_alter_table("channel", schema=schema) as batch_op:
        batch_op.drop_constraint(CONSTRAINT_NAME, type_="unique")
