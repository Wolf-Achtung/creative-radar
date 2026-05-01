"""Idempotent forward migration for the F0.2 schema separation.

Moves the eight Creative-Radar tables from the public schema into a dedicated
'creative_radar' schema. Foreign-key references between CR tables follow the
move automatically (Postgres rewrites them). Tables already in
creative_radar are reported as skipped, so re-running the script is safe.

Not run by pytest — touches the real DB. Wolf executes via the throwaway
endpoint introduced in Task 4.1d:

    POST /api/admin/run-schema-migration
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.database import engine

# Whitelist over blacklist: enumerate exactly which tables move. Any unknown
# table in production stays untouched. Order is irrelevant — the SET SCHEMA
# DDL is per-table and rewires FKs automatically.
CR_TABLES: tuple[str, ...] = (
    "channel",
    "title",
    "titlekeyword",
    "post",
    "asset",
    "titlesyncrun",
    "titlecandidate",
    "weeklyreport",
)

TARGET_SCHEMA = "creative_radar"
SOURCE_SCHEMA = "public"


def _table_in_schema(connection: Connection, schema: str, table: str) -> bool:
    """True if `<schema>.<table>` exists right now."""
    result = connection.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = :schema AND table_name = :table LIMIT 1"
        ),
        {"schema": schema, "table": table},
    ).first()
    return result is not None


def _ensure_schema(connection: Connection, schema: str) -> bool:
    """Idempotent CREATE SCHEMA. Returns True if it had to create, False if it
    was already there (best effort — the existence check happens before the
    DDL so the result reflects the real state, not just the IF NOT EXISTS)."""
    already_there = connection.execute(
        text(
            "SELECT 1 FROM information_schema.schemata "
            "WHERE schema_name = :schema LIMIT 1"
        ),
        {"schema": schema},
    ).first()
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    return already_there is None


def _move_table(
    connection: Connection, table: str, source: str, target: str
) -> tuple[str, str]:
    """Move `<source>.<table>` -> `<target>.<table>` if it lives in source.
    Returns (status, detail) where status is one of:
        'moved'   — table was in source, ALTER ran
        'skipped' — table is already in target, or absent from source
        'error'   — DDL raised; detail carries the message
    """
    if _table_in_schema(connection, target, table):
        return "skipped", f"already in {target}"
    if not _table_in_schema(connection, source, table):
        return "skipped", f"absent from {source}"
    try:
        connection.execute(
            text(f'ALTER TABLE "{source}"."{table}" SET SCHEMA "{target}"')
        )
    except Exception as exc:  # noqa: BLE001
        return "error", f"{type(exc).__name__}: {exc}"
    return "moved", f"{source}.{table} -> {target}.{table}"


def _maybe_move_alembic_version(
    connection: Connection,
) -> tuple[str, str]:
    """Postgres standard table that tracks Alembic head. If it already lives
    in public, move it with the rest of CR (so the creative_radar schema is
    self-contained); if it's not in public yet, leave it alone — Task 4.2
    will configure migrations/env.py with version_table_schema=creative_radar
    so the table gets created in the right place on the first upgrade."""
    if not _table_in_schema(connection, SOURCE_SCHEMA, "alembic_version"):
        return "skipped", "absent from public (will be created in target by alembic)"
    return _move_table(connection, "alembic_version", SOURCE_SCHEMA, TARGET_SCHEMA)


def _summary_line(stats: dict) -> str:
    moved = len(stats["tables_moved"])
    skipped = len(stats["tables_skipped"])
    errors = len(stats["errors"])
    if errors:
        return (
            f"{moved} tables moved to {TARGET_SCHEMA}, {skipped} skipped, "
            f"{errors} ERRORS — see error map."
        )
    if moved == 0 and skipped > 0:
        return f"All {skipped} tables already in {TARGET_SCHEMA} (idempotent re-run)."
    return f"{moved} tables moved to {TARGET_SCHEMA}, {skipped} skipped."


def run(connection: Connection | None = None) -> dict:
    """Idempotent forward migration. Caller may pass an existing Connection
    (for tests or for endpoint reuse) or omit it for a one-shot script run
    that opens its own engine connection inside a transaction."""

    def _do(conn: Connection) -> dict:
        schema_created = _ensure_schema(conn, TARGET_SCHEMA)

        tables_moved: list[str] = []
        tables_skipped: list[str] = []
        errors: dict[str, str] = {}

        for table in CR_TABLES:
            status, detail = _move_table(conn, table, SOURCE_SCHEMA, TARGET_SCHEMA)
            if status == "moved":
                tables_moved.append(table)
            elif status == "skipped":
                tables_skipped.append(f"{table} ({detail})")
            else:
                errors[table] = detail

        # alembic_version handled separately because its lifecycle differs
        status, detail = _maybe_move_alembic_version(conn)
        if status == "moved":
            tables_moved.append("alembic_version")
        elif status == "skipped":
            tables_skipped.append(f"alembic_version ({detail})")
        else:
            errors["alembic_version"] = detail

        stats = {
            "schema_created": schema_created,
            "target_schema": TARGET_SCHEMA,
            "tables_moved": tables_moved,
            "tables_skipped": tables_skipped,
            "errors": errors,
        }
        stats["summary"] = _summary_line(stats)
        return stats

    if connection is not None:
        return _do(connection)
    with engine.begin() as conn:
        return _do(conn)


def main() -> int:
    stats = run()
    print(stats["summary"])
    if stats["errors"]:
        for table, msg in stats["errors"].items():
            print(f"  ERROR {table}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
