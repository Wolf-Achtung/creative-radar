"""add post analysis and asset vision fields

Revision ID: 9a2e7c4f5b18
Revises: 571f54840f19
Create Date: 2026-05-03 14:00:00.000000

Sprint 5.3.1 Mini-Run 1 — additive schema for the cross-platform AI
analysis pipeline. Two tables touched, all new columns nullable so the
existing rows don't need a backfill:

- creative_radar.post: + analysis (JSON), + last_analyzed_at (timestamp,
  indexed for the idempotent skip-pre-check inside the analyze endpoint).
- creative_radar.asset: + asset_url, + vision_description, + vision_model,
  + analyzed_at, plus a partial-unique index on (post_id, asset_url)
  WHERE asset_url IS NOT NULL. Existing Asset rows have asset_url IS
  NULL, so the partial index doesn't conflict with them.

The legacy Asset fields (ai_summary_de/en, visual_evidence_pack,
screenshot_url, thumbnail_url, visual_source_url, ...) are deliberately
left untouched — Sprint 5.3.1 reconciles by adding new fields rather
than remapping old ones (Wolf decision).

Postgres-only DDL details: the partial-unique index uses
``postgresql_where``; SQLite test paths use a plain unique constraint
(safe because no SQLite-test data has asset_url populated yet).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9a2e7c4f5b18"
down_revision: Union[str, Sequence[str], None] = "571f54840f19"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _table_schema() -> Union[str, None]:
    return SCHEMA if _is_postgres() else None


def upgrade() -> None:
    schema = _table_schema()

    # ---- Post: analysis + last_analyzed_at -----------------------------
    with op.batch_alter_table("post", schema=schema) as batch_op:
        batch_op.add_column(sa.Column("analysis", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("last_analyzed_at", sa.DateTime(), nullable=True))

    # Index built non-CONCURRENTLY inside the migration's transaction. This
    # is intentional: the post table is small (low thousands today), the
    # column is brand-new and entirely NULL, so the brief ACCESS EXCLUSIVE
    # lock is harmless. CONCURRENTLY would require breaking out of the
    # transaction which is overkill here.
    op.create_index(
        "ix_post_last_analyzed_at",
        "post",
        ["last_analyzed_at"],
        unique=False,
        schema=schema,
    )

    # ---- Asset: asset_url + vision_* + analyzed_at ---------------------
    with op.batch_alter_table("asset", schema=schema) as batch_op:
        batch_op.add_column(sa.Column("asset_url", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("vision_description", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("vision_model", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("analyzed_at", sa.DateTime(), nullable=True))

    if _is_postgres():
        # Partial unique: idempotency key is (post_id, asset_url) only when
        # asset_url has been populated. Existing rows with asset_url IS NULL
        # are exempt — they belong to the legacy ai_summary pipeline and
        # never see the new analyzer.
        op.create_index(
            "uq_asset_post_id_asset_url",
            "asset",
            ["post_id", "asset_url"],
            unique=True,
            schema=schema,
            postgresql_where=sa.text("asset_url IS NOT NULL"),
        )
    else:
        # SQLite test path: standard unique index. NULLs are distinct in
        # SQLite's UNIQUE semantics (multiple NULL asset_urls allowed),
        # which matches the Postgres partial-index behaviour we want.
        op.create_index(
            "uq_asset_post_id_asset_url",
            "asset",
            ["post_id", "asset_url"],
            unique=True,
        )


def downgrade() -> None:
    schema = _table_schema()

    op.drop_index(
        "uq_asset_post_id_asset_url",
        table_name="asset",
        schema=schema,
    )
    with op.batch_alter_table("asset", schema=schema) as batch_op:
        batch_op.drop_column("analyzed_at")
        batch_op.drop_column("vision_model")
        batch_op.drop_column("vision_description")
        batch_op.drop_column("asset_url")

    op.drop_index(
        "ix_post_last_analyzed_at",
        table_name="post",
        schema=schema,
    )
    with op.batch_alter_table("post", schema=schema) as batch_op:
        batch_op.drop_column("last_analyzed_at")
        batch_op.drop_column("analysis")
