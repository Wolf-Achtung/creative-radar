"""add llm check marker to title_candidate (KI-Assist-Fortschritt)

Revision ID: e7f3a9c258d1
Revises: d2e5c7a91f04
Create Date: 2026-08-21 20:30:00.000000

Wolfs Befund vom 21.08.2026: "Rest-Vorschlaege mit KI pruefen" zeigte
nach jedem Klick dieselbe Meldung — der Assist nahm immer die ERSTEN 12
offenen Kandidaten, und als unsicher eingestufte blieben offen. Jeder
Klick prüfte dieselben Faelle erneut, kostete erneut Geld und kam nie
bei den uebrigen an.

``llm_checked_at`` markiert einen KI-gepruefte Kandidaten (wird beim
naechsten Lauf uebersprungen), ``llm_note`` traegt die Begruendung des
Modells — sichtbar als Hinweis in der "Treffer pruefen"-Queue, damit
die Hand-Pruefung schneller geht.

SQLite-Testpfad bootstrappt via ``SQLModel.metadata.create_all`` und
hat die Spalten bereits aus der Entity — dort No-op (Muster
``title.genres``, a4b7c2e9d1f3).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "e7f3a9c258d1"
down_revision: Union[str, Sequence[str], None] = "d2e5c7a91f04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.execute(
        f"ALTER TABLE {SCHEMA}.title_candidate "
        "ADD COLUMN IF NOT EXISTS llm_checked_at TIMESTAMP NULL"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.title_candidate "
        "ADD COLUMN IF NOT EXISTS llm_note VARCHAR(300) NULL"
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute(f"ALTER TABLE {SCHEMA}.title_candidate DROP COLUMN IF EXISTS llm_checked_at")
    op.execute(f"ALTER TABLE {SCHEMA}.title_candidate DROP COLUMN IF EXISTS llm_note")
