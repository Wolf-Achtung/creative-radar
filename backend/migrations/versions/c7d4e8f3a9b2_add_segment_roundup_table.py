"""add segment_roundup table for non-pair-channel weekly roundups

Revision ID: c7d4e8f3a9b2
Revises: b8f2a7c40e91
Create Date: 2026-05-25

Master-Plan-Schritt-3, Commit 3/N. Eigene Persistenz-Tabelle fuer den
Non-Pair-Roundup-Generator. Disjunkt zur ``insight_report``-Tabelle
(Pair-Pfad) — Wolf-Ping-1-(a) 25.05.: eigene Tabelle statt Erweiterung,
weil ``insight_report.pair_key NOT NULL`` im PK und der ``_acquire_brief_lock``-
Pfad pair_key-keyed ist; ein Typ-Diskriminator-Ansatz waere strukturell
zu invasiv gewesen.

Composite-PK ``(segment, iso_year, iso_week)`` spiegelt die natuerliche
Cache-Lookup-Semantik (ein Roundup pro Segment pro Woche). Last-Write-
Wins-Semantik beim Regenerate analog ``insight_report``.

Schema-Felder:
- ``segment`` (ENUM creative_radar.channel_segment, PK) — Type wurde in
  e1c93a4d7f08 angelegt; hier ``create_type=False`` damit nicht doppelt.
- ``iso_year``, ``iso_week`` (PK) — gleiche Konvention wie insight_report.
- ``window_days`` — Audit, welches Zeitfenster den Brief erzeugt hat.
  Default-Fenster fuer Roundup ist 14d (Wolf 25.05., bewusste Abweichung
  von Pair-30d), parametrisiert; Spalte traegt den Wert pro Row.
- ``channels_aggregation``, ``llm_output`` — JSON-Blobs (siehe entities.py
  SegmentRoundup-docstring).
- ``generated_at``, ``model``, ``cost_usd_cents``, ``input_tokens``,
  ``output_tokens`` — Audit + Kosten-Trail analog insight_report.

SQLite-Pfad: ENUM faellt auf VARCHAR(64) zurueck (same Pattern wie
7e3b2c4a8f51 und e1c93a4d7f08); ``_table_schema()`` liefert ``None``,
batch_alter_table-friendly.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c7d4e8f3a9b2"
down_revision: Union[str, Sequence[str], None] = "b8f2a7c40e91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _table_schema() -> Union[str, None]:
    return SCHEMA if _is_postgres() else None


def _segment_type():
    if _is_postgres():
        # Type wurde in e1c93a4d7f08 angelegt; nur referenzieren, nicht
        # erneut erzeugen.
        return postgresql.ENUM(
            "us_major",
            "us_independent",
            "uk_major",
            "uk_independent",
            "de_verleih",
            "de_independent",
            name="channel_segment",
            schema=SCHEMA,
            create_type=False,
        )
    return sa.String(length=64)


def upgrade() -> None:
    schema = _table_schema()

    op.create_table(
        "segment_roundup",
        sa.Column("segment", _segment_type(), nullable=False),
        sa.Column("iso_year", sa.Integer(), nullable=False),
        sa.Column("iso_week", sa.Integer(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("channels_aggregation", sa.JSON(), nullable=False),
        sa.Column("llm_output", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("cost_usd_cents", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("segment", "iso_year", "iso_week"),
        schema=schema,
    )
    # Index auf generated_at — analog insight_report fuer Audit-Queries
    # "letzte erzeugte Roundups ueber alle Segmente".
    op.create_index(
        "ix_segment_roundup_generated_at",
        "segment_roundup",
        ["generated_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _table_schema()
    op.drop_index("ix_segment_roundup_generated_at", table_name="segment_roundup", schema=schema)
    op.drop_table("segment_roundup", schema=schema)
