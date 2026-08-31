"""add asset.last_rematch_at (Rematch-Merker)

Revision ID: c5e9d2a71f38
Revises: f3b8d1c47a92
Create Date: 2026-08-31 19:00:00.000000

Rematch-Merker (31.08.2026): der woechentliche Rematch lud bisher ALLE
titellosen Assets neueste-zuerst und brach nach dem Zeitbudget ab — die
vorderen ~1.200 wurden jede Woche neu geprueft, die hinteren 2.639 nie
erreicht. Der Stempel macht die Auswahl zur Rotation: nie geprueft
zuerst, danach am laengsten nicht geprueft. Der Backlog laeuft damit in
wenigen Wochen einmal komplett durch statt gar nicht.

SQLite-Testpfad bootstrappt via ``SQLModel.metadata.create_all`` und
hat die Spalte bereits aus der Entity; diese Migration ist der
Prod-Eingriff (Muster d4f1a2b6c8e3).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5e9d2a71f38"
down_revision: Union[str, Sequence[str], None] = "f3b8d1c47a92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    with op.batch_alter_table("asset", schema=SCHEMA) as batch_op:
        batch_op.add_column(
            sa.Column("last_rematch_at", sa.DateTime(), nullable=True)
        )
    op.create_index(
        "ix_asset_last_rematch_at",
        "asset",
        ["last_rematch_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.drop_index(
        "ix_asset_last_rematch_at", table_name="asset", schema=SCHEMA
    )
    with op.batch_alter_table("asset", schema=SCHEMA) as batch_op:
        batch_op.drop_column("last_rematch_at")
