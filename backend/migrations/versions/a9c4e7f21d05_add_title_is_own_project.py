"""add is_own_project to title (Wir-Projekte)

Revision ID: a9c4e7f21d05
Revises: e7f3a9c258d1
Create Date: 2026-08-22 14:00:00.000000

Wir-Projekte (22.08.2026): Trailerhaus arbeitet projektweise, nicht
kanalweise — auf den Verleih-Kanaelen ist ein Post von ihnen und
zwanzig nicht. ``title.is_own_project`` markiert deshalb den TITEL als
eigenes Projekt; ``services/wir_segment.py`` zaehlt Posts ueber die
Titel-Zuordnung ODER (weiterhin) ueber ``channel.is_own``. Per Default
ist KEIN Titel "wir".

NOT NULL DEFAULT false wie ``channel.is_own`` (d2e5c7a91f04): die
Auswertung soll nicht gegen NULL verteidigen muessen.

SQLite-Testpfad bootstrappt via ``SQLModel.metadata.create_all`` und
hat die Spalte bereits aus der Entity — dort ist die Migration ein
No-op, wie beim ``title.genres``-Muster (a4b7c2e9d1f3).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "a9c4e7f21d05"
down_revision: Union[str, Sequence[str], None] = "e7f3a9c258d1"
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
        "ADD COLUMN IF NOT EXISTS is_own_project BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute(f"ALTER TABLE {SCHEMA}.title DROP COLUMN IF EXISTS is_own_project")
