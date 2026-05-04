"""normalize de_us_match_key to slug form

Revision ID: 6f8e3c1a47d2
Revises: c84a1f3b9e72
Create Date: 2026-05-04 16:00:00.000000

Sprint Match-Key-Konsistenz. Two pre-existing write paths
(``services.title_rematch`` and the ``/api/assets/{id}/review`` handler)
wrote the raw franchise / title-original string to ``de_us_match_key``
while the vision pipeline wrote the slug form. Cross-market pairing per
match-key equality fell through whenever the same title travelled
through both paths.

This migration backfills the existing Raw-Form rows to match the slug
algorithm in ``services.match_key.slugify_match_key``: lower-case,
replace ``[^a-z0-9äöüß]+`` with '-', strip trailing dashes, collapse
all-separator results to NULL. The character class is identical to the
Python helper so the in-process result and the in-DB result agree.

Idempotent: WHERE clause excludes rows already in slug form, so re-runs
are no-ops. SQLite branch is a no-op too — the test DB starts fresh per
session and never has Raw-Form data to convert.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "6f8e3c1a47d2"
down_revision: Union[str, Sequence[str], None] = "c84a1f3b9e72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.execute(
        f"""
        UPDATE {SCHEMA}.asset
        SET de_us_match_key = NULLIF(
            trim(both '-' from
                regexp_replace(lower(de_us_match_key), '[^a-z0-9äöüß]+', '-', 'g')
            ),
            ''
        )
        WHERE de_us_match_key IS NOT NULL
          AND de_us_match_key !~ '^[a-z0-9äöüß-]+$';
        """
    )


def downgrade() -> None:
    # No-op: slug conversion is one-way (lossy lowercasing + character-
    # class collapse). Reverting would require the original franchise /
    # title strings, which aren't preserved separately on the asset row.
    pass
