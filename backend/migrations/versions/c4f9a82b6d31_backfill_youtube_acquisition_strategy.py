"""backfill youtube acquisition_strategy

Revision ID: c4f9a82b6d31
Revises: a7f2c4b831e9
Create Date: 2026-05-08 21:00:00.000000

Sprint 4 (Multi-Plattform V2a + YouTube-Aktivierung) — substantielle
Backfill-Migration. Live-Befund Wolf 08.05.2026: alle 13 YouTube-Channels
in ``creative_radar.channel`` haben heute ``acquisition_strategy='apify'``,
weil der Default in Migration ``7e3b2c4a8f51`` für die Spalte ``apify``
gesetzt wurde und nie für YouTube überschrieben wurde.

Folge: ohne Backfill würde die Sprint-4-YT-Cron-Hook nicht greifen — die
Channel-Auswahl in ``cron_channel_selection.py`` filtert nicht nach
acquisition_strategy, aber der spätere Cadence-/Whitelist-Code wird das
tun. Außerdem ist ``apify`` für YT-Channels semantisch falsch und führt
bei Audit-Queries zu Fehlern.

Idempotenz: WHERE-Guard ``acquisition_strategy != 'youtube_api'``, sodass
ein zweiter Lauf ein No-Op ist. SQLite-Path ist no-op (kein Production-
Stand, kein Backfill nötig).

Downgrade: nicht reversibel, weil der ursprüngliche Wert (apify) ohne
weitere Information nicht zwischen "war wirklich Apify" und "war Default
für YT" unterscheidbar ist. Im Sinne der Konvention der bisherigen
Migrationen (``e5d8f1a36b40`` H1+H6 sind ebenfalls nicht reversibel)
lassen wir den Downgrade als Dokumentations-No-Op.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "c4f9a82b6d31"
down_revision: Union[str, Sequence[str], None] = "a7f2c4b831e9"
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
        UPDATE {SCHEMA}.channel
        SET acquisition_strategy = 'youtube_api'
        WHERE platform = 'youtube'
          AND acquisition_strategy != 'youtube_api'
        """
    )


def downgrade() -> None:
    # Not reversible — the original value (``apify``) for YouTube rows was a
    # default-leak from migration 7e3b2c4a8f51, not a deliberate state we
    # want to restore. Documented no-op, matching the precedent in
    # e5d8f1a36b40 (H1, H6).
    pass
