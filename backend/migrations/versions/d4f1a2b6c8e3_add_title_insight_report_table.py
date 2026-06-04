"""add title_insight_report table for title-centric briefs (C4)

Revision ID: d4f1a2b6c8e3
Revises: c7d4e8f3a9b2
Create Date: 2026-06-04

Title-Brief-Persistenz (Variante 1, C4). Eigene Tabelle, disjunkt zur
``insight_report`` (Pair-Pfad) — gleiche Begruendung wie bei ``segment_roundup``:
``insight_report.pair_key NOT NULL`` im PK und die aggregation-Spalte traegt die
PairAggregation-Form; ein Titel-keyed Row passt dort nicht. Composite-PK
``(title_id, iso_year, iso_week)`` spiegelt die Cache-Lookup-Semantik (ein
Titel-Brief pro Titel pro ISO-Woche), Last-Write-Wins beim Regenerate.

Schema-Felder:
- ``title_id`` (UUID, PK) — Postgres ``uuid``, SQLite-Fallback ``VARCHAR(36)``.
  Kein FK auf ``title.id`` (analog ``insight_report``/``segment_roundup``, die
  ihren Key auch ohne FK fuehren) — haelt die Tabelle entkoppelt.
- ``iso_year``, ``iso_week`` (PK) — gleiche Konvention wie insight_report.
- ``window_days`` — Audit, welches Fenster den Brief erzeugte (Default 30).
- ``aggregation``, ``llm_output`` — JSON-Blobs (TitleAggregation /
  TitleLLMReport).
- ``generated_at`` (+Index), ``model``, ``cost_usd_cents``, ``input_tokens``,
  ``output_tokens`` — Audit + Kosten-Trail analog insight_report.

SQLite-Pfad: ``_table_schema()`` -> None, UUID -> VARCHAR(36). Die CI bootstrappt
ohnehin via ``SQLModel.metadata.create_all`` (Tabelle erscheint dort automatisch);
diese Migration ist der Prod-Eingriff.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d4f1a2b6c8e3"
down_revision: Union[str, Sequence[str], None] = "c7d4e8f3a9b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _table_schema() -> Union[str, None]:
    return SCHEMA if _is_postgres() else None


def _uuid_type():
    if _is_postgres():
        return postgresql.UUID(as_uuid=True)
    return sa.String(length=36)


def upgrade() -> None:
    schema = _table_schema()

    op.create_table(
        "title_insight_report",
        sa.Column("title_id", _uuid_type(), nullable=False),
        sa.Column("iso_year", sa.Integer(), nullable=False),
        sa.Column("iso_week", sa.Integer(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("aggregation", sa.JSON(), nullable=False),
        sa.Column("llm_output", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("cost_usd_cents", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("title_id", "iso_year", "iso_week"),
        schema=schema,
    )
    op.create_index(
        "ix_title_insight_report_generated_at",
        "title_insight_report",
        ["generated_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _table_schema()
    op.drop_index(
        "ix_title_insight_report_generated_at",
        table_name="title_insight_report",
        schema=schema,
    )
    op.drop_table("title_insight_report", schema=schema)
