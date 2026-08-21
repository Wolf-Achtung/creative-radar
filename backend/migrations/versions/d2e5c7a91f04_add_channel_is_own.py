"""add is_own to channel (Wir-Segment Schritt 1)

Revision ID: d2e5c7a91f04
Revises: b6d3f8a1c5e7
Create Date: 2026-08-21 20:00:00.000000

Wir-Segment (21.08.2026): ``channel.is_own`` kennzeichnet Kanaele, die
das eigene Team betreut — die Grundlage fuer die Auswertung
"empfohlen → gemacht → gewirkt" (services/wir_segment.py). Wolf setzt
das Flag selbst ueber die Checkliste in Admin → Quellen; per Default
ist KEIN Kanal "wir".

NOT NULL DEFAULT false wie ``monitoring_enabled`` daneben: die
Auswertung soll nicht gegen NULL verteidigen muessen.

SQLite-Testpfad bootstrappt via ``SQLModel.metadata.create_all`` und
hat die Spalte bereits aus der Entity — dort ist die Migration ein
No-op, wie beim ``title.genres``-Muster (a4b7c2e9d1f3).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "d2e5c7a91f04"
down_revision: Union[str, Sequence[str], None] = "b6d3f8a1c5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.execute(
        f"ALTER TABLE {SCHEMA}.channel "
        "ADD COLUMN IF NOT EXISTS is_own BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute(f"ALTER TABLE {SCHEMA}.channel DROP COLUMN IF EXISTS is_own")
