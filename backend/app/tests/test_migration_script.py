"""Tests for the F0.2 schema migration scripts.

We mock the SQLAlchemy Connection so we can assert which DDL is attempted
without needing a Postgres server. The tests are deliberately blunt:
- correct list of tables enumerated
- idempotency (already-in-target -> skipped, absent-from-source -> skipped)
- ALTER TABLE only fires for tables that need to move
- error from a single table doesn't abort the whole run
- rollback is the symmetric inverse
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scripts import (
    migrate_to_creative_radar_schema as forward,
    rollback_creative_radar_schema as rollback,
)


# ---------- helpers ----------


def _make_connection(table_locations: dict[str, str], schemas_present: set[str]):
    """Build a mock SQLAlchemy Connection that pretends each table lives in
    `table_locations[name]` (or is missing if the name is absent from the
    dict). schemas_present controls the existence check for CREATE SCHEMA.

    The mock tracks ALTER TABLE calls and CREATE SCHEMA calls so tests can
    assert against them.
    """
    conn = MagicMock(name="Connection")
    executed_ddl: list[str] = []
    error_for_table: dict[str, Exception] = {}

    def execute(stmt, params=None):
        sql = str(stmt).strip()
        # --- existence check for tables ---
        if sql.startswith("SELECT 1 FROM information_schema.tables"):
            schema = params["schema"]
            table = params["table"]
            result = MagicMock()
            if table_locations.get(table) == schema:
                result.first.return_value = (1,)
            else:
                result.first.return_value = None
            return result
        # --- existence check for schemas ---
        if sql.startswith("SELECT 1 FROM information_schema.schemata"):
            schema = params["schema"]
            result = MagicMock()
            result.first.return_value = (1,) if schema in schemas_present else None
            return result
        # --- CREATE SCHEMA IF NOT EXISTS ---
        if sql.startswith("CREATE SCHEMA"):
            executed_ddl.append(sql)
            return MagicMock()
        # --- ALTER TABLE ... SET SCHEMA ... ---
        if sql.startswith("ALTER TABLE"):
            executed_ddl.append(sql)
            # Update the location bookkeeping so later existence checks reflect
            # the move (matches real Postgres semantics).
            for table, prior_schema in list(table_locations.items()):
                marker = f'"{prior_schema}"."{table}"'
                if marker in sql and "SET SCHEMA" in sql:
                    new_schema = sql.split('SET SCHEMA "')[-1].rstrip('"')
                    if table in error_for_table:
                        raise error_for_table[table]
                    table_locations[table] = new_schema
                    break
            return MagicMock()
        return MagicMock()

    conn.execute.side_effect = execute
    conn._executed_ddl = executed_ddl
    conn._error_for_table = error_for_table
    return conn


# ---------- forward migration ----------


def test_forward_creates_schema_and_moves_all_cr_tables() -> None:
    """All 8 CR tables in public, schema doesn't exist yet -> all 8 moved."""
    table_locations = {t: "public" for t in forward.CR_TABLES}
    conn = _make_connection(table_locations, schemas_present={"public"})

    stats = forward.run(connection=conn)

    assert stats["schema_created"] is True
    assert set(stats["tables_moved"]) == set(forward.CR_TABLES)
    # alembic_version is absent here, so it must be reported as skipped
    assert any("alembic_version" in s for s in stats["tables_skipped"])
    assert stats["errors"] == {}
    assert "8 tables moved" in stats["summary"]

    # Every CR table got an ALTER TABLE
    alter_count = sum(1 for ddl in conn._executed_ddl if "ALTER TABLE" in ddl)
    assert alter_count == len(forward.CR_TABLES)


def test_forward_is_idempotent_when_tables_already_in_target() -> None:
    """All 8 CR tables already in creative_radar -> 0 moves, 8 skips, no errors."""
    table_locations = {t: "creative_radar" for t in forward.CR_TABLES}
    conn = _make_connection(
        table_locations, schemas_present={"public", "creative_radar"}
    )

    stats = forward.run(connection=conn)

    assert stats["tables_moved"] == []
    assert len(stats["tables_skipped"]) == 9  # 8 CR + alembic_version
    assert stats["errors"] == {}
    assert "All 9 tables already in creative_radar" in stats["summary"]
    # No ALTER TABLE for already-migrated tables
    assert not any("ALTER TABLE" in ddl for ddl in conn._executed_ddl)


def test_forward_handles_partial_state() -> None:
    """Half in public, half already in creative_radar — moves only the public half."""
    half = len(forward.CR_TABLES) // 2
    in_public = forward.CR_TABLES[:half]
    in_target = forward.CR_TABLES[half:]
    table_locations = {**{t: "public" for t in in_public},
                       **{t: "creative_radar" for t in in_target}}
    conn = _make_connection(
        table_locations, schemas_present={"public", "creative_radar"}
    )

    stats = forward.run(connection=conn)

    assert set(stats["tables_moved"]) == set(in_public)
    skipped_names = {entry.split(" ")[0] for entry in stats["tables_skipped"]}
    assert set(in_target).issubset(skipped_names)
    assert stats["errors"] == {}


def test_forward_includes_alembic_version_when_present_in_public() -> None:
    """alembic_version sits in public (existing setup) -> moved with the rest."""
    table_locations = {t: "public" for t in forward.CR_TABLES}
    table_locations["alembic_version"] = "public"
    conn = _make_connection(table_locations, schemas_present={"public"})

    stats = forward.run(connection=conn)

    assert "alembic_version" in stats["tables_moved"]


def test_forward_isolates_per_table_errors() -> None:
    """An error on one table does not abort the rest of the run."""
    table_locations = {t: "public" for t in forward.CR_TABLES}
    conn = _make_connection(table_locations, schemas_present={"public"})
    # Inject a failure for the 'asset' table
    conn._error_for_table["asset"] = RuntimeError("permission denied")

    stats = forward.run(connection=conn)

    assert "asset" in stats["errors"]
    assert "permission denied" in stats["errors"]["asset"]
    # Remaining 7 CR tables still moved
    moved = set(stats["tables_moved"])
    assert "asset" not in moved
    assert moved == set(forward.CR_TABLES) - {"asset"}
    assert "ERRORS" in stats["summary"]


# ---------- rollback ----------


def test_rollback_moves_all_cr_tables_back_to_public() -> None:
    """Symmetric inverse: tables in creative_radar -> all moved back."""
    table_locations = {t: "creative_radar" for t in rollback.CR_TABLES}
    conn = _make_connection(
        table_locations, schemas_present={"public", "creative_radar"}
    )

    stats = rollback.run(connection=conn)

    assert set(stats["tables_moved"]) == set(rollback.CR_TABLES)
    assert stats["errors"] == {}
    assert "rolled back to public" in stats["summary"]


def test_rollback_idempotent_when_tables_already_in_public() -> None:
    table_locations = {t: "public" for t in rollback.CR_TABLES}
    conn = _make_connection(
        table_locations, schemas_present={"public", "creative_radar"}
    )

    stats = rollback.run(connection=conn)

    assert stats["tables_moved"] == []
    assert "All 9 tables already in public" in stats["summary"]
