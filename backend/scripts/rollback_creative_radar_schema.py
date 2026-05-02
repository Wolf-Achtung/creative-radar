"""Symmetric rollback for the F0.2 schema separation. Moves the eight CR
tables back from creative_radar into public. Idempotent. Tables already in
public are reported as skipped.

Not run by pytest — touches the real DB. Wolf executes only if Task 4.1d
left production in a broken state and the throwaway endpoint at
POST /api/admin/run-schema-rollback is still wired up.

Note: rollback does NOT restore foreign-key references that previously
crossed schemas (there were none in CR's own ORM, and Wolf decided in 4.1a
that auth_audit stays in public). It is therefore a clean inverse of the
forward migration in migrate_to_creative_radar_schema.py.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.database import engine
from scripts.migrate_to_creative_radar_schema import (
    CR_TABLES,
    SOURCE_SCHEMA,
    TARGET_SCHEMA,
    _move_table,
    _table_in_schema,
)


def _maybe_rollback_alembic_version(connection: Connection) -> tuple[str, str]:
    """Inverse of _maybe_move_alembic_version: move alembic_version back to
    public if it currently lives in creative_radar. If it's not in
    creative_radar (because Task 4.2 was never run, or alembic was reset),
    leave it alone."""
    if not _table_in_schema(connection, TARGET_SCHEMA, "alembic_version"):
        return "skipped", f"absent from {TARGET_SCHEMA}"
    return _move_table(connection, "alembic_version", TARGET_SCHEMA, SOURCE_SCHEMA)


def _summary_line(stats: dict) -> str:
    moved = len(stats["tables_moved"])
    skipped = len(stats["tables_skipped"])
    errors = len(stats["errors"])
    if errors:
        return (
            f"{moved} tables rolled back to {SOURCE_SCHEMA}, {skipped} skipped, "
            f"{errors} ERRORS — see error map."
        )
    if moved == 0 and skipped > 0:
        return f"All {skipped} tables already in {SOURCE_SCHEMA} (idempotent re-run)."
    return f"{moved} tables rolled back to {SOURCE_SCHEMA}, {skipped} skipped."


def run(connection: Connection | None = None) -> dict:
    """Symmetric inverse of migrate_to_creative_radar_schema.run."""

    def _do(conn: Connection) -> dict:
        tables_moved: list[str] = []
        tables_skipped: list[str] = []
        errors: dict[str, str] = {}

        for table in CR_TABLES:
            status, detail = _move_table(conn, table, TARGET_SCHEMA, SOURCE_SCHEMA)
            if status == "moved":
                tables_moved.append(table)
            elif status == "skipped":
                tables_skipped.append(f"{table} ({detail})")
            else:
                errors[table] = detail

        status, detail = _maybe_rollback_alembic_version(conn)
        if status == "moved":
            tables_moved.append("alembic_version")
        elif status == "skipped":
            tables_skipped.append(f"alembic_version ({detail})")
        else:
            errors["alembic_version"] = detail

        stats = {
            "source_schema": TARGET_SCHEMA,
            "target_schema": SOURCE_SCHEMA,
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
