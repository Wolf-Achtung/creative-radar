"""add designer_weekly_briefing table (Designer-Wochenbriefing Trockenlauf)

Revision ID: c1a5f8e3b7d4
Revises: b7e4d2c9a1f6
Create Date: 2026-07-06

Sprint 2026-07-06: Designer-Wochenbriefing — mirror von
``cutter_weekly_briefing`` (Migration b7e4d2c9a1f6) field-fuer-field.
Gleiche Trockenlauf-Logik: die Tabelle wird vom Cron beschrieben und nur
ueber Admin-/DB-Lesezugriff gelesen, kein Frontend-Pfad bis zur
Kalibrierung der Evidenzschwelle. Feature-Flag ``FEATURE_DESIGNER_WEEKLY_ENABLED``
default off.

``evidence`` (NOT NULL) ist das Kalibrierungs-Produkt: p75-Schwellen,
Kandidaten-Zahlen, freigegebene UND verworfene Muster mit Grund.
``llm_output`` ist NULLABLE — eine Woche mit verworfener LLM-Synthese
(Citation strict) wird trotzdem persistiert, weil die Evidenz zaehlt;
``raw_llm_text`` traegt dann die letzte verworfene Antwort.

Composite-PK ``(iso_year, iso_week)`` — ein Briefing pro Woche, kein FK
(gleiche Konvention wie insight_report/segment_roundup/cutter_weekly_briefing).

SQLite-Pfad: ``_table_schema()`` -> None. Die CI bootstrappt via
``SQLModel.metadata.create_all``; diese Migration ist der Prod-Eingriff.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c1a5f8e3b7d4"
down_revision: Union[str, Sequence[str], None] = "b7e4d2c9a1f6"
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
        "designer_weekly_briefing",
        sa.Column("iso_year", sa.Integer(), nullable=False),
        sa.Column("iso_week", sa.Integer(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("llm_output", sa.JSON(), nullable=True),
        sa.Column("raw_llm_text", sa.String(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("cost_usd_cents", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("iso_year", "iso_week"),
        schema=schema,
    )
    op.create_index(
        "ix_designer_weekly_briefing_generated_at",
        "designer_weekly_briefing",
        ["generated_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _table_schema()
    op.drop_index(
        "ix_designer_weekly_briefing_generated_at",
        table_name="designer_weekly_briefing",
        schema=schema,
    )
    op.drop_table("designer_weekly_briefing", schema=schema)
