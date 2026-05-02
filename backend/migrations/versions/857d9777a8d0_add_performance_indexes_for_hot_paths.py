"""add performance indexes for hot paths

Revision ID: 857d9777a8d0
Revises: cf842bbfaeb5
Create Date: 2026-05-01 12:25:21.802193

W4 update: tables now live in the 'creative_radar' schema (F0.2 migration
ran on 2026-05-02). Index DDL targets that schema explicitly. Postgres-only
revision; SQLite test paths bootstrap via SQLModel.metadata.create_all and
do not run alembic.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '857d9777a8d0'
down_revision: Union[str, Sequence[str], None] = 'cf842bbfaeb5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"

# Indexes kept as data so upgrade/downgrade stay symmetrical and the list
# is reviewable at a glance. Order: most-frequent-read first.
_INDEXES = (
    # Time-range queries: services/report_selector.select_assets_for_report
    # filters Post.detected_at between start/end.
    ("ix_post_detected_at", "post", ["detected_at"]),
    # Channel filter on assets list and reports.
    ("ix_post_channel_id", "post", ["channel_id"]),
    # Title-join in services/insights.build_overview and assets API.
    ("ix_asset_title_id", "asset", ["title_id"]),
    # Review-status filter: /api/assets?review_status=...
    ("ix_asset_review_status", "asset", ["review_status"]),
    # Visual-analysis-status counter (services/insights) and the
    # analyze-visual-batch query in api/assets.analyze_visual_batch.
    ("ix_asset_visual_analysis_status", "asset", ["visual_analysis_status"]),
)


def upgrade() -> None:
    for name, table, columns in _INDEXES:
        op.create_index(
            name, table, columns,
            unique=False, if_not_exists=True, schema=SCHEMA,
        )


def downgrade() -> None:
    for name, table, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name=table, if_exists=True, schema=SCHEMA)
