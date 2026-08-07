"""add video_feature table

Revision ID: a9c3e7f21b58
Revises: b2e8f4a6c1d7
Create Date: 2026-08-07 19:30:00.000000

Trailer-Intelligence Stufe 5 — Speicher fuer Schnitt-Merkmale eines
Videos. Rein additiv: eine neue Tabelle ``creative_radar.video_feature``
mit vier Indizes. Keine Aenderung an bestehenden Tabellen, kein
Backfill, keine Datenwanderung.

Die Tabelle ist beim Deploy leer und bleibt es, bis Material vorliegt.
Sie wird jetzt angelegt, weil die Merkmalsdefinition steht (siehe
``app/services/video_features.py``) und das Schema damit nicht mehr
raten muss.

``post_id`` ist bewusst NULL-bar und nicht Teil eines
Pflicht-Fremdschluessels auf Post: der empfohlene
Machbarkeitsnachweis laeuft auf eigenem Material des Trailerhauses,
das nie gescraped wurde und deshalb keine Post-Zeile hat. Ein
NOT-NULL-Constraint haette genau diesen Weg verbaut.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9c3e7f21b58"
down_revision: Union[str, Sequence[str], None] = "b2e8f4a6c1d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _table_schema() -> Union[str, None]:
    return SCHEMA if _is_postgres() else None


def upgrade() -> None:
    schema = _table_schema()

    # ``if_not_exists=True`` spiegelt das cron_run-/costlog-Muster: der
    # SQLite-Testpfad bootstrappt vorher ueber
    # ``SQLModel.metadata.create_all``, die Tabelle kann also schon da
    # sein, wenn die Alembic-Kette laeuft. Auf Postgres laeuft die
    # Migration gegen ein Schema ohne die Tabelle — beide Wege treffen
    # sich.
    op.create_table(
        "video_feature",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("post_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("external_ref", sa.String(length=512), nullable=True),
        sa.Column("pair_key", sa.String(length=255), nullable=True),
        sa.Column("format_class", sa.String(length=32), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("shot_count", sa.Integer(), nullable=False),
        sa.Column("asl_seconds", sa.Float(), nullable=False),
        sa.Column("median_shot_seconds", sa.Float(), nullable=False),
        sa.Column("shot_length_cv", sa.Float(), nullable=False),
        sa.Column("longest_shot_position", sa.Float(), nullable=False),
        sa.Column("longest_shot_ratio", sa.Float(), nullable=False),
        sa.Column("asl_first_third_ratio", sa.Float(), nullable=True),
        sa.Column("asl_middle_third_ratio", sa.Float(), nullable=True),
        sa.Column("asl_last_third_ratio", sa.Float(), nullable=True),
        sa.Column("rhythm_ratio", sa.Float(), nullable=True),
        sa.Column("loudness_rise_position", sa.Float(), nullable=True),
        sa.Column("loudness_peak_position", sa.Float(), nullable=True),
        sa.Column("tool", sa.String(length=64), nullable=True),
        sa.Column("tool_version", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.JSON(), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["post_id"],
            [f"{SCHEMA}.post.id" if _is_postgres() else "post.id"],
        ),
        schema=schema,
        if_not_exists=True,
    )
    for column in ("post_id", "source", "pair_key", "format_class", "analyzed_at"):
        op.create_index(
            f"ix_video_feature_{column}",
            "video_feature",
            [column],
            unique=False,
            schema=schema,
            if_not_exists=True,
        )


def downgrade() -> None:
    schema = _table_schema()
    for column in ("analyzed_at", "format_class", "pair_key", "source", "post_id"):
        op.drop_index(
            f"ix_video_feature_{column}",
            table_name="video_feature",
            schema=schema,
            if_exists=True,
        )
    op.drop_table("video_feature", schema=schema, if_exists=True)
