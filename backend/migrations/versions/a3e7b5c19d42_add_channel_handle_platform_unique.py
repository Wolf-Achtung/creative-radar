"""add partial UNIQUE index on channel(handle, platform) WHERE active

Revision ID: a3e7b5c19d42
Revises: f2c8a4d96e10
Create Date: 2026-05-12

Sprint Tech-Debt-1 — makes silent duplicate channels structurally
impossible at the DB level while preserving the manual-cleanup
pattern (active=false, kept as audit trail with FK posts attached).

Two cases caught on 2026-05-12 (LionsgateMovies/YT, starwars/IG)
showed that "remember to pre-check before INSERT" doesn't scale
across the Perplexity-import script, the admin endpoint, and the
inventory-pass tooling. The cleanup for starwars/IG deactivated the
older INT row (active=false, mvp=false, ~23 FK-bound posts
preserved) and kept the newer US row active — a full UNIQUE
constraint over (handle, platform) would have rejected that state.

The PARTIAL index ``WHERE active = true`` (Postgres) / ``WHERE
active = 1`` (SQLite) reflects the actual business rule: no two
*active* channels may share a (handle, platform) pair. Deactivated
audit-trail rows remain legal duplicates, which is what the cleanup
flow produces. No data migration with handle-renames needed.

Postgres uses ``postgresql_where``; SQLite uses ``sqlite_where``
(partial indexes have been supported since SQLite 3.8.0). The
``upgrade()`` branches on dialect because the WHERE-predicate
literal differs — Postgres has ``true``/``false`` booleans,
SQLite stores them as integers and the corresponding literal is
``1``/``0``.

Downgrade drops the index. No data backup needed (the index
itself stores no data beyond pointers).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a3e7b5c19d42"
down_revision: Union[str, Sequence[str], None] = "f2c8a4d96e10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"
INDEX_NAME = "channel_handle_platform_unique"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _table_schema() -> Union[str, None]:
    return SCHEMA if _is_postgres() else None


def upgrade() -> None:
    schema = _table_schema()
    if _is_postgres():
        op.create_index(
            INDEX_NAME,
            "channel",
            ["handle", "platform"],
            unique=True,
            schema=schema,
            postgresql_where=sa.text("active = true"),
        )
    else:
        # SQLite stores booleans as integers; the partial-predicate literal
        # must be 1, not the unsupported ``true``. Both Postgres and SQLite
        # then evaluate it the same way against the boolean column.
        op.create_index(
            INDEX_NAME,
            "channel",
            ["handle", "platform"],
            unique=True,
            sqlite_where=sa.text("active = 1"),
        )


def downgrade() -> None:
    schema = _table_schema()
    op.drop_index(INDEX_NAME, table_name="channel", schema=schema)
