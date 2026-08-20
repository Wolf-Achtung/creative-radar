"""add pattern_briefing table (Trailer-Intelligence Stufe 1, Schritt 3)

Revision ID: b6d3f8a1c5e7
Revises: a4b7c2e9d1f3
Create Date: 2026-08-20

Persistenz fuer die Text-Bausteine aus dem Muster-Bericht: pro Woche und
Modus eine Row mit deterministischer Evidenz (belastbare Muster-Zellen +
Beispiel-Posts) und der LLM-Synthese (Hooks, Captions, Hashtags DE/EN,
zitatbelegt).

Composite-PK ``(mode, iso_year, iso_week)``: ``mode`` ist heute nur
``"genre"``; der Titel-Modus (zweiter PR, Wolf-Entscheidung "Beides,
Genre zuerst") kommt damit ohne weitere Migration aus.

Konventionen wie ``cutter_weekly_briefing`` (b7e4d2c9a1f6):
- ``evidence`` NOT NULL — der deterministische Teil ist das Audit-Produkt
  und wird auch bei LLM-Fehlschlag persistiert.
- ``llm_output`` NULLABLE, ``raw_llm_text`` traegt bei Parse-/Schema-Fail
  die letzte verworfene Antwort.
- ``model='none'`` markiert Leerlauf-Wochen ohne LLM-Call.

Zusaetzlich ``citation_dropped`` (INT NOT NULL DEFAULT 0): Anzahl der
Bausteine, die die Citation-Pruefung verworfen hat — als eigene Spalte,
damit die Quote ueber Wochen ohne JSON-Parsing abfragbar bleibt.

SQLite-Pfad: ``_table_schema()`` -> None. Die CI bootstrappt via
``SQLModel.metadata.create_all``; diese Migration ist der Prod-Eingriff.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b6d3f8a1c5e7"
down_revision: Union[str, Sequence[str], None] = "a4b7c2e9d1f3"
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
        "pattern_briefing",
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("iso_year", sa.Integer(), nullable=False),
        sa.Column("iso_week", sa.Integer(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("llm_output", sa.JSON(), nullable=True),
        sa.Column("raw_llm_text", sa.String(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("cost_usd_cents", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "citation_dropped",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.PrimaryKeyConstraint("mode", "iso_year", "iso_week"),
        schema=schema,
    )
    op.create_index(
        "ix_pattern_briefing_generated_at",
        "pattern_briefing",
        ["generated_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _table_schema()
    op.drop_index(
        "ix_pattern_briefing_generated_at",
        table_name="pattern_briefing",
        schema=schema,
    )
    op.drop_table("pattern_briefing", schema=schema)
