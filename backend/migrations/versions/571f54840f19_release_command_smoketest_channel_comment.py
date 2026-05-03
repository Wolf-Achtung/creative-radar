"""release_command smoketest: COMMENT on creative_radar.channel

Revision ID: 571f54840f19
Revises: 7e3b2c4a8f51
Create Date: 2026-05-03 12:30:00.000000

Sprint 5.2.2-Hygiene Mini-Run 1 — no-op smoketest for the auto-migration
release_command (railway.json `preDeployCommand`). Sets a Postgres COMMENT
on the creative_radar.channel table; harmless, idempotent, and leaves a
visible trace in pg_description so the production deploy proves the
auto-migration ran end-to-end. SQLite has no per-table COMMENT, so the
SQLite path is a deliberate no-op (the alembic_version bump is the proof
in either dialect).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "571f54840f19"
down_revision: Union[str, Sequence[str], None] = "7e3b2c4a8f51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"
TABLE = "channel"
COMMENT_TEXT = (
    "Channel-Registry — see Sprint 5.2.1 PRs #48/#51 + "
    "release_command smoketest 5.2.2-hygiene"
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _set_comment(value: str | None) -> None:
    # Postgres COMMENT ON ... IS NULL clears the comment; non-null sets it.
    # Single-quote escape via doubling — defensive even though COMMENT_TEXT
    # has none today.
    if value is None:
        op.execute(f"COMMENT ON TABLE {SCHEMA}.{TABLE} IS NULL")
        return
    escaped = value.replace("'", "''")
    op.execute(f"COMMENT ON TABLE {SCHEMA}.{TABLE} IS '{escaped}'")


def upgrade() -> None:
    if _is_postgres():
        _set_comment(COMMENT_TEXT)


def downgrade() -> None:
    if _is_postgres():
        _set_comment(None)
