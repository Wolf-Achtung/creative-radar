"""add platform_channel_id to channel for youtube resolver

Revision ID: d8a2f5e914bc
Revises: c4f9a82b6d31
Create Date: 2026-05-08 22:30:00.000000

Sprint 4.5 — bug 1 fix. The YouTube channels.list API can resolve a
channel by ``id`` (UCxxx) or by ``forHandle`` (a modern @handle), but
not by the legacy custom-URL slug (``c/<slug>``). Four production
YouTube channels in the whitelist (NetflixDE, SonyPicturesEntertainment,
WaltDisneyStudios, WarnerBrosPictures) carry custom-URL slugs in their
``handle`` column rather than modern handles. The Sprint-4 cron run
hit ``YouTubeNotFoundError`` for all four.

Fix: store the actual UCxxx-ID separately from the human-readable
handle. The Sprint-4.5 resolver prefers ``platform_channel_id`` when
present, falling back to handle-based lookup otherwise. Search-API is
explicitly NOT used as a live fallback (100 quota units per call, would
burn the day's allowance).

Schema-only — no backfill in this migration. The four IDs are filled
in via a separate one-off SQL UPDATE (Wolf, Phase 3) using the
``scripts/resolve_yt_channel_ids.py`` skript output. Idempotent:
``IF NOT EXISTS`` on the column add (Postgres 9.6+).

SQLite path is a no-op-style: SQLModel.metadata.create_all already
includes the column (added via the entity in this sprint), so the
add_column would be redundant. Guard via the dialect check.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8a2f5e914bc"
down_revision: Union[str, Sequence[str], None] = "c4f9a82b6d31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    # IF NOT EXISTS keeps the migration idempotent — SQLite test paths
    # bootstrap via SQLModel.metadata.create_all, where the column is
    # already present from the entity definition.
    op.execute(
        f"ALTER TABLE {SCHEMA}.channel "
        "ADD COLUMN IF NOT EXISTS platform_channel_id VARCHAR(64)"
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute(
        f"ALTER TABLE {SCHEMA}.channel DROP COLUMN IF EXISTS platform_channel_id"
    )
