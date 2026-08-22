"""add recommendation_snapshot table (Empfehlungs-Snapshots)

Revision ID: f3b8d1c47a92
Revises: a9c4e7f21d05
Create Date: 2026-08-22 19:00:00.000000

Empfehlungs-Snapshots (22.08.2026): das Vorher/Nachher-Design der
Wir-Schleife braucht eingefrorene Empfehlungs-Zeitpunkte. Der Cron
schreibt jede Woche die ``over``-Zellen des Muster-Berichts (dieselbe
MACHEN-Auswahl wie Playbook und Wir-Segment) als JSON-Liste weg —
eine Row pro ISO-Woche, Last-Write-Wins beim Force-Re-Run.

JSON-Blob wie ``insight_report``/``title_insight_report``: Felder in
den Zellen koennen dazukommen, ohne dass es eine Migration braucht.

SQLite-Testpfad bootstrappt via ``SQLModel.metadata.create_all`` und
hat die Tabelle bereits aus der Entity; diese Migration ist der
Prod-Eingriff (Muster d4f1a2b6c8e3).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3b8d1c47a92"
down_revision: Union[str, Sequence[str], None] = "a9c4e7f21d05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.create_table(
        "recommendation_snapshot",
        sa.Column("iso_year", sa.Integer(), nullable=False),
        sa.Column("iso_week", sa.Integer(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("cells", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("iso_year", "iso_week"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_recommendation_snapshot_created_at",
        "recommendation_snapshot",
        ["created_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.drop_index(
        "ix_recommendation_snapshot_created_at",
        table_name="recommendation_snapshot",
        schema=SCHEMA,
    )
    op.drop_table("recommendation_snapshot", schema=SCHEMA)
