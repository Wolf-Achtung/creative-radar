"""add genres to title (Trailer-Intelligence Stufe 1)

Revision ID: a4b7c2e9d1f3
Revises: c7d1f8a24e96
Create Date: 2026-08-20 14:00:00.000000

Trailer-Intelligence Stufe 1 (20.08.2026): ``title.genres`` — TMDb-
Genres als JSON-Liste in TMDb-Reihenfolge (erstes = primaeres Genre).
Befuellt vom Title-Sync aus den discover-Antworten; die Muster-
Aggregation (services/trailer_patterns.py) gruppiert danach.

DEFAULT '[]' NOT NULL: Bestandszeilen tragen eine leere Liste, nicht
NULL — der Python-Default greift nur fuer neue Rows, und die
Aggregation soll nicht gegen None verteidigen muessen. JSON wie
``title.aliases`` daneben, kein JSONB (Projekt-Konvention).

SQLite-Testpfad bootstrappt via ``SQLModel.metadata.create_all`` und
hat die Spalte bereits aus der Entity — dort ist die Migration ein
No-op, wie beim ``music_entry_position``-Muster (c7d1f8a24e96).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "a4b7c2e9d1f3"
down_revision: Union[str, Sequence[str], None] = "c7d1f8a24e96"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.execute(
        f"ALTER TABLE {SCHEMA}.title "
        "ADD COLUMN IF NOT EXISTS genres JSON NOT NULL DEFAULT '[]'"
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute(
        f"ALTER TABLE {SCHEMA}.title "
        "DROP COLUMN IF EXISTS genres"
    )
