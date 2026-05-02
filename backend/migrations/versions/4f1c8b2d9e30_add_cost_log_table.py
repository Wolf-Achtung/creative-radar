"""add cost_log table

Revision ID: 4f1c8b2d9e30
Revises: 857d9777a8d0
Create Date: 2026-05-02 12:30:00.000000

W4 Task 4.4 / F0.6 — adds the cost_log table to creative_radar schema.
Logging only, no hard cap. Postgres-only revision; SQLite test paths
bootstrap via SQLModel.metadata.create_all.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '4f1c8b2d9e30'
down_revision: Union[str, Sequence[str], None] = '857d9777a8d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def upgrade() -> None:
    op.create_table(
        "costlog",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("provider", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("operation", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("cost_usd_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_eur_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_meta", sa.JSON(), nullable=True),
        schema=SCHEMA,
        if_not_exists=True,
    )
    # Indexes are concurrently created (W4 Task 4.2 lesson — non-blocking on
    # a live system). autocommit_block is required because CONCURRENTLY
    # cannot run inside a transaction.
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_costlog_timestamp", "costlog", ["timestamp"],
            unique=False, if_not_exists=True, schema=SCHEMA,
            postgresql_concurrently=True,
        )
        op.create_index(
            "ix_costlog_provider", "costlog", ["provider"],
            unique=False, if_not_exists=True, schema=SCHEMA,
            postgresql_concurrently=True,
        )
        op.create_index(
            "ix_costlog_operation", "costlog", ["operation"],
            unique=False, if_not_exists=True, schema=SCHEMA,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("ix_costlog_operation", table_name="costlog",
                      if_exists=True, schema=SCHEMA, postgresql_concurrently=True)
        op.drop_index("ix_costlog_provider", table_name="costlog",
                      if_exists=True, schema=SCHEMA, postgresql_concurrently=True)
        op.drop_index("ix_costlog_timestamp", table_name="costlog",
                      if_exists=True, schema=SCHEMA, postgresql_concurrently=True)
    op.drop_table("costlog", schema=SCHEMA, if_exists=True)
