"""add app_user, login_code and usage_event tables (User-Login-Sprint)

Revision ID: f7a3d2c815b9
Revises: c1a5f8e3b7d4
Create Date: 2026-07-20 10:00:00.000000

Sprint User-Login 2026-07 — E-Mail+Code-Login fuer ~15 bekannte Nutzer
plus Nutzungs-Event-Log. Flow-Parameter (6-stelliger Code, 10 Minuten
TTL, Einmal-Nutzung) folgen dem Referenzprojekt api-ki-backend-neu;
Persistenz-Entscheidungen weichen bewusst ab:

- ``app_user``: Allowlist als DB-Tabelle statt Code-Frozenset — Wolf
  pflegt User im Admin-Bereich, kein Deploy pro E-Mail-Aenderung.
- ``login_code``: Codes als SHA-256-Hash in der DB statt Klartext in
  Redis/In-Memory — ueberlebt Railway-Restarts, leakt bei DB-Zugriff
  keinen nutzbaren Code, ``attempts`` deckelt Rate-Versuche pro Code.
- ``usage_event``: append-only Log (E-Mail x Aktion x Zeitpunkt +
  JSON-Kontext), bewusst OHNE FK auf app_user, damit Events die
  Loeschung eines Users ueberleben (Audit-Charakter).

Column-type notes: ``context`` nutzt sa.JSON() (nicht JSONB) —
konsistent mit ``cost_log.cost_meta`` und ``post.raw_payload``.
DateTime-Spalten mit timezone=True, Werte schreibt die App als
UTC-aware (``utc_now``).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7a3d2c815b9"
down_revision: Union[str, Sequence[str], None] = "c1a5f8e3b7d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _table_schema() -> Union[str, None]:
    return SCHEMA if _is_postgres() else None


def upgrade() -> None:
    schema = _table_schema()

    # ``if_not_exists=True`` matches the insight_report-migration pattern:
    # SQLite test paths bootstrap via ``SQLModel.metadata.create_all`` so
    # the tables may already exist; Postgres production runs the migration
    # against the live schema.
    op.create_table(
        "app_user",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=schema,
        if_not_exists=True,
    )
    op.create_index(
        "ix_app_user_email",
        "app_user",
        ["email"],
        unique=True,
        schema=schema,
        if_not_exists=True,
    )

    op.create_table(
        "login_code",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=schema,
        if_not_exists=True,
    )
    # Nicht unique — pro request-code entsteht eine frische Row und die
    # aelteren Rows derselben E-Mail werden vom Endpoint geloescht
    # (latest-code-wins); der Index traegt den Lookup beim Einloesen.
    op.create_index(
        "ix_login_code_email",
        "login_code",
        ["email"],
        unique=False,
        schema=schema,
        if_not_exists=True,
    )

    op.create_table(
        "usage_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=schema,
        if_not_exists=True,
    )
    # Drei Einzel-Indizes statt Composite: die Auswertung fragt
    # "pro User zuletzt aktiv" (email), "Events pro Aktion" (action)
    # und "letzte N Tage" (created_at) unabhaengig voneinander ab.
    op.create_index(
        "ix_usage_event_email",
        "usage_event",
        ["email"],
        unique=False,
        schema=schema,
        if_not_exists=True,
    )
    op.create_index(
        "ix_usage_event_action",
        "usage_event",
        ["action"],
        unique=False,
        schema=schema,
        if_not_exists=True,
    )
    op.create_index(
        "ix_usage_event_created_at",
        "usage_event",
        ["created_at"],
        unique=False,
        schema=schema,
        if_not_exists=True,
    )


def downgrade() -> None:
    schema = _table_schema()
    op.drop_index("ix_usage_event_created_at", table_name="usage_event", schema=schema, if_exists=True)
    op.drop_index("ix_usage_event_action", table_name="usage_event", schema=schema, if_exists=True)
    op.drop_index("ix_usage_event_email", table_name="usage_event", schema=schema, if_exists=True)
    op.drop_table("usage_event", schema=schema, if_exists=True)
    op.drop_index("ix_login_code_email", table_name="login_code", schema=schema, if_exists=True)
    op.drop_table("login_code", schema=schema, if_exists=True)
    op.drop_index("ix_app_user_email", table_name="app_user", schema=schema, if_exists=True)
    op.drop_table("app_user", schema=schema, if_exists=True)
