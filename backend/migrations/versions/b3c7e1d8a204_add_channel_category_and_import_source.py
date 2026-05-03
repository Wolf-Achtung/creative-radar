"""add channel category and import_source audit fields

Revision ID: b3c7e1d8a204
Revises: 9a2e7c4f5b18
Create Date: 2026-05-03 17:00:00.000000

Sprint 5.3.X Mini-Run 1 — additive audit columns for the Perplexity-seed
bulk-import. Both columns nullable VARCHAR, no defaults, no indexes:

- creative_radar.channel: + category, + import_source

`category` carries the Verleih-Klassifikation aus der Recherche-Liste
(z.B. ``streamer``, ``major_studio``, ``arthouse_distributor``). Reines
Audit-Feld, von der Anwendung heute nicht ausgewertet.

`import_source` tracks which research-batch a channel came from. The
import script writes a value like ``perplexity_2026_05_03``; manuell
angelegte Channels lassen das Feld NULL. Das Datum steckt im Wert,
nicht im Spalten-Namen — folgende Imports schreiben einfach einen neuen
Wert (``perplexity_2026_07_xx`` etc.).

Postgres-path: schema-qualified `creative_radar.channel`, plain VARCHAR.
SQLite-path: bare `channel` table, identical column shape — the
alembic-roundtrip-test in test_migration_channel_audit_fields.py
exercises this branch.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3c7e1d8a204"
down_revision: Union[str, Sequence[str], None] = "9a2e7c4f5b18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _table_schema() -> Union[str, None]:
    return SCHEMA if _is_postgres() else None


def upgrade() -> None:
    schema = _table_schema()

    with op.batch_alter_table("channel", schema=schema) as batch_op:
        batch_op.add_column(sa.Column("category", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("import_source", sa.String(), nullable=True))


def downgrade() -> None:
    schema = _table_schema()

    with op.batch_alter_table("channel", schema=schema) as batch_op:
        batch_op.drop_column("import_source")
        batch_op.drop_column("category")
