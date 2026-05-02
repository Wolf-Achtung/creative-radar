"""extend channel registry with role/tier/strategy/monitoring fields

Revision ID: 7e3b2c4a8f51
Revises: 4f1c8b2d9e30
Create Date: 2026-05-02 17:30:00.000000

Sprint 5.2.1 Mini-Run 3a — extends creative_radar.channel with four
operational classification fields used by the channel-registry overhaul.
The existing 'notes' column is left untouched.

Postgres-path: creates three ENUM types in the creative_radar schema and
adds the columns with those enums.
SQLite-path: ENUM types are skipped (no CREATE TYPE), columns fall back
to VARCHAR so the alembic roundtrip test can replay locally.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "7e3b2c4a8f51"
down_revision: Union[str, Sequence[str], None] = "4f1c8b2d9e30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"

CHANNEL_ROLE_VALUES = (
    "studio_distributor",
    "franchise",
    "talent_cast",
    "regional",
    "publisher_platform",
)
QUALITY_TIER_VALUES = ("P0", "P1", "P2")
ACQUISITION_STRATEGY_VALUES = ("apify", "youtube_api", "manual")


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _table_schema() -> Union[str, None]:
    return SCHEMA if _is_postgres() else None


def _enum_column_type(values: Sequence[str], name: str):
    if _is_postgres():
        return postgresql.ENUM(*values, name=name, schema=SCHEMA, create_type=False)
    return sa.String()


def upgrade() -> None:
    schema = _table_schema()

    if _is_postgres():
        for type_name, values in (
            ("channel_role", CHANNEL_ROLE_VALUES),
            ("quality_tier", QUALITY_TIER_VALUES),
            ("acquisition_strategy", ACQUISITION_STRATEGY_VALUES),
        ):
            op.execute(
                f"CREATE TYPE {SCHEMA}.{type_name} AS ENUM ("
                + ", ".join(f"'{v}'" for v in values)
                + ")"
            )

    with op.batch_alter_table("channel", schema=schema) as batch_op:
        batch_op.add_column(
            sa.Column(
                "channel_role",
                _enum_column_type(CHANNEL_ROLE_VALUES, "channel_role"),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "quality_tier",
                _enum_column_type(QUALITY_TIER_VALUES, "quality_tier"),
                nullable=False,
                server_default="P1",
            )
        )
        batch_op.add_column(
            sa.Column(
                "acquisition_strategy",
                _enum_column_type(ACQUISITION_STRATEGY_VALUES, "acquisition_strategy"),
                nullable=False,
                server_default="apify",
            )
        )
        batch_op.add_column(
            sa.Column(
                "monitoring_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    schema = _table_schema()

    with op.batch_alter_table("channel", schema=schema) as batch_op:
        batch_op.drop_column("monitoring_enabled")
        batch_op.drop_column("acquisition_strategy")
        batch_op.drop_column("quality_tier")
        batch_op.drop_column("channel_role")

    if _is_postgres():
        for type_name in ("acquisition_strategy", "quality_tier", "channel_role"):
            op.execute(f"DROP TYPE {SCHEMA}.{type_name}")
