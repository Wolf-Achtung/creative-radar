"""add app_user.can_view_usage (Monitoring-Freischaltung pro Person)

Revision ID: b2e8f4a6c1d7
Revises: f7a3d2c815b9
Create Date: 2026-07-20 19:30:00.000000

Wolf-Festlegung 2026-07-20: Die Nutzungs-Auswertung soll fuer einzelne
Verantwortliche zugaenglich sein, ohne Voll-Admin-Passwort und ohne
geteiltes Zweit-Passwort. Loesung: ein Flag am Login-User — wer es
traegt, sieht nach dem normalen E-Mail-Code-Login zusaetzlich die
Nutzungs-Seite (/nutzung) samt HTML-/CSV-Export. Default false,
Freischaltung ueber den Admin-Bereich (Tab "Nutzer").
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2e8f4a6c1d7"
down_revision: Union[str, Sequence[str], None] = "f7a3d2c815b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _table_schema() -> Union[str, None]:
    return SCHEMA if _is_postgres() else None


def upgrade() -> None:
    schema = _table_schema()
    # if_not_exists: SQLite-Testpfade bootstrappen via
    # SQLModel.metadata.create_all — die Spalte existiert dort schon.
    op.add_column(
        "app_user",
        sa.Column("can_view_usage", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=schema,
        if_not_exists=True,
    )


def downgrade() -> None:
    schema = _table_schema()
    op.drop_column("app_user", "can_view_usage", schema=schema, if_exists=True)
