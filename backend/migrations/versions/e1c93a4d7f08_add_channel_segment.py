"""add channel.segment ENUM field (nullable, no default)

Revision ID: e1c93a4d7f08
Revises: a3e7b5c19d42
Create Date: 2026-05-25

Master-Plan-Schritt-2 — non-pair coverage / roundup-pipeline foundation.
Adds a ``segment`` column to ``creative_radar.channel`` that classifies
non-pair channels into one of six fixed buckets used downstream by the
roundup generator (Schritt 3, not in this migration):

    us_major, us_independent, uk_major, uk_independent,
    de_verleih, de_independent

Disjoint to the pair-pool registry: channels that belong to a pair
stay ``segment = NULL`` and are never targeted by a roundup. The
classification backfill runs in a separate commit after Wolf-Ping 2.

Field type rationale (Ping-1 decision, 2026-05-25): ENUM ``creative_radar.channel_segment``.
Five of the seven categorical channel fields (``market``, ``priority``,
``channel_role``, ``quality_tier``, ``acquisition_strategy``) are already
Postgres ENUMs in the ``creative_radar`` schema — consistency outweighs
the extensibility advantage of TEXT+CHECK. Future segments (e.g.
``fr_major``) follow the established ``ALTER TYPE ... ADD VALUE``
pattern from ``e5d8f1a36b40_whitelist_expansion``.

Backward-compat: ``segment`` is nullable without server_default, so:
- all existing channel rows survive the migration with ``segment = NULL``;
- every Channel-constructor call in the test suite (all keyword-arg,
  none pass ``segment``) continues to work unchanged;
- the SQLite-bootstrap path used by per-test fixtures (which goes via
  ``SQLModel.metadata.create_all`` rather than alembic) is unaffected
  until ``entities.py`` later opts to mirror the column. This is the
  same lazy-mirror pattern that ``segment``'s sibling fields (e.g.
  ``category``, ``import_source``) used.

Postgres-/SQLite-dialect handling mirrors the ``7e3b2c4a8f51_extend_channel_registry_fields``
template exactly: PG gets a real ENUM via ``CREATE TYPE`` + ``schema=creative_radar``,
SQLite falls back to ``sa.String()`` so the alembic-roundtrip smoketests
keep replaying locally.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e1c93a4d7f08"
down_revision: Union[str, Sequence[str], None] = "a3e7b5c19d42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"

CHANNEL_SEGMENT_VALUES = (
    "us_major",
    "us_independent",
    "uk_major",
    "uk_independent",
    "de_verleih",
    "de_independent",
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _table_schema() -> Union[str, None]:
    return SCHEMA if _is_postgres() else None


def _enum_column_type():
    if _is_postgres():
        # ``create_type=False`` because we ``CREATE TYPE`` explicitly in
        # ``upgrade()`` — same pattern as 7e3b2c4a8f51 to keep the type
        # creation visible in the migration body rather than implicit in
        # the column add.
        return postgresql.ENUM(
            *CHANNEL_SEGMENT_VALUES,
            name="channel_segment",
            schema=SCHEMA,
            create_type=False,
        )
    return sa.String()


def upgrade() -> None:
    schema = _table_schema()

    if _is_postgres():
        op.execute(
            f"CREATE TYPE {SCHEMA}.channel_segment AS ENUM ("
            + ", ".join(f"'{v}'" for v in CHANNEL_SEGMENT_VALUES)
            + ")"
        )

    with op.batch_alter_table("channel", schema=schema) as batch_op:
        batch_op.add_column(
            sa.Column(
                "segment",
                _enum_column_type(),
                nullable=True,
            )
        )


def downgrade() -> None:
    schema = _table_schema()

    with op.batch_alter_table("channel", schema=schema) as batch_op:
        batch_op.drop_column("segment")

    if _is_postgres():
        op.execute(f"DROP TYPE {SCHEMA}.channel_segment")
