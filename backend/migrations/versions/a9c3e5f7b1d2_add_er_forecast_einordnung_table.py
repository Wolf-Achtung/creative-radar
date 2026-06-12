"""add er_forecast_einordnung table (#252 split-cache)

Revision ID: a9c3e5f7b1d2
Revises: d4f1a2b6c8e3
Create Date: 2026-06-11

#252 ER-Prognose-Freischaltung: Die Regression laeuft live und gratis;
nur die LLM-Einordnung kostet einen Opus-Call. Diese Tabelle cached den
Einordnungs-Text pro (pair_key, Ziel-ISO-Woche) — max. 9 Opus-Calls pro
Woche statt einem Call pro Seitenansicht. Der Text ist die gegatete
(public-safe) Fassung und wird von Admin- und Public-Endpoint geteilt.

Composite-PK ``(pair_key, iso_year, iso_week)`` analog ``insight_report``;
kein FK (gleiche Konvention wie insight_report/segment_roundup).

SQLite-Pfad: ``_table_schema()`` -> None. Die CI bootstrappt via
``SQLModel.metadata.create_all``; diese Migration ist der Prod-Eingriff.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a9c3e5f7b1d2"
down_revision: Union[str, Sequence[str], None] = "d4f1a2b6c8e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _table_schema() -> Union[str, None]:
    return SCHEMA if _is_postgres() else None


def upgrade() -> None:
    schema = _table_schema()

    op.create_table(
        "er_forecast_einordnung",
        sa.Column("pair_key", sa.String(length=64), nullable=False),
        sa.Column("iso_year", sa.Integer(), nullable=False),
        sa.Column("iso_week", sa.Integer(), nullable=False),
        sa.Column("einordnung", sa.String(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("pair_key", "iso_year", "iso_week"),
        schema=schema,
    )
    op.create_index(
        "ix_er_forecast_einordnung_generated_at",
        "er_forecast_einordnung",
        ["generated_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _table_schema()
    op.drop_index(
        "ix_er_forecast_einordnung_generated_at",
        table_name="er_forecast_einordnung",
        schema=schema,
    )
    op.drop_table("er_forecast_einordnung", schema=schema)
