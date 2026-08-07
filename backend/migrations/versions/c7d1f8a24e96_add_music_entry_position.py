"""add music_entry_position to video_feature

Revision ID: c7d1f8a24e96
Revises: a9c3e7f21b58
Create Date: 2026-08-07 21:00:00.000000

Trailer-Intelligence Stufe 5, Plan B. Das Fundament-Dokument hat die
Erweiterung angekuendigt: menschlich annotierte Ereignisse wie der
Musikeinsatz brauchen ein eigenes Merkmal, bewusst getrennt von
``loudness_rise_position`` — eine Lautheitskurve kann Musik nicht von
Dialog unterscheiden, ein Ohr kann es. Die Spalte wird nur vom
Tap-Along-Annotationsweg befuellt, nie von ``extract_features``.

Rein additiv: eine NULL-bare Spalte, kein Backfill, kein Index (das
Merkmal wird gelesen, wenn die Zeile ohnehin geladen ist — es gibt
keine Abfrage nach ihm).

SQLite-Testpfad bootstrappt via ``SQLModel.metadata.create_all`` und
hat die Spalte bereits aus der Entity — dort ist die Migration ein
No-op, wie beim ``platform_channel_id``-Muster (d8a2f5e914bc).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "c7d1f8a24e96"
down_revision: Union[str, Sequence[str], None] = "a9c3e7f21b58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.execute(
        f"ALTER TABLE {SCHEMA}.video_feature "
        "ADD COLUMN IF NOT EXISTS music_entry_position DOUBLE PRECISION"
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute(
        f"ALTER TABLE {SCHEMA}.video_feature "
        "DROP COLUMN IF EXISTS music_entry_position"
    )
