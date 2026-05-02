"""Apply pending Alembic migrations to the creative_radar schema.

Idempotent. Designed to be called from the W4 throwaway endpoint
``POST /api/admin/run-alembic-upgrade``. Performs three steps in order:

1. Make sure ``creative_radar.alembic_version`` exists (so the version
   table travels with CR data, not orphaned in public).
2. If the version table is empty, stamp it at the baseline revision
   ``cf842bbfaeb5``. The baseline reflects the schema we already have
   (W2 created it from the live SQLModel state) — Alembic should not
   replay it.
3. Run ``alembic upgrade head`` to apply every still-pending revision.

After step 2+3, ``alembic_version`` carries the head revision and the
five performance indexes from revision ``857d9777a8d0`` are present.

The script is safe to re-run. Steps 1 and 2 are no-ops on the second
call; step 3 reports "already at head" without DDL.

Not run by pytest — touches the real DB.
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.database import engine

BASELINE_REVISION = "cf842bbfaeb5"
TARGET_SCHEMA = "creative_radar"
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _alembic_config() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    return cfg


def _alembic_version_exists(connection: Connection) -> bool:
    return (
        connection.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name = 'alembic_version' LIMIT 1"
            ),
            {"schema": TARGET_SCHEMA},
        ).first()
        is not None
    )


def _alembic_version_is_empty(connection: Connection) -> bool:
    if not _alembic_version_exists(connection):
        return True
    return (
        connection.execute(
            text(f'SELECT 1 FROM "{TARGET_SCHEMA}"."alembic_version" LIMIT 1')
        ).first()
        is None
    )


def _current_revision(connection: Connection) -> str | None:
    if not _alembic_version_exists(connection):
        return None
    row = connection.execute(
        text(f'SELECT version_num FROM "{TARGET_SCHEMA}"."alembic_version" LIMIT 1')
    ).first()
    return row[0] if row else None


def run() -> dict:
    cfg = _alembic_config()
    actions: list[str] = []
    errors: dict[str, str] = {}

    with engine.begin() as conn:
        before = _current_revision(conn)

        # Step 1: ensure schema exists (defensive — should already be there
        # because the F0.2 migration ran first).
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{TARGET_SCHEMA}"'))

        empty = _alembic_version_is_empty(conn)

    # Step 2: stamp baseline if version table is empty. Alembic's own
    # `stamp` command needs its own connection, so we drop our
    # `engine.begin()` block and call it directly.
    if empty:
        try:
            command.stamp(cfg, BASELINE_REVISION)
            actions.append(f"stamped baseline {BASELINE_REVISION}")
        except Exception as exc:  # noqa: BLE001
            errors["stamp"] = f"{type(exc).__name__}: {exc}"

    # Step 3: upgrade to head.
    if "stamp" not in errors:
        try:
            command.upgrade(cfg, "head")
            actions.append("upgraded to head")
        except Exception as exc:  # noqa: BLE001
            errors["upgrade"] = f"{type(exc).__name__}: {exc}"

    with engine.begin() as conn:
        after = _current_revision(conn)

    summary = (
        f"Alembic: {before or 'none'} -> {after or 'none'}. Actions: "
        + (", ".join(actions) if actions else "none")
    )
    if errors:
        summary += f" — ERRORS: {list(errors.keys())}"

    return {
        "before_revision": before,
        "after_revision": after,
        "baseline_stamped": empty and "stamp" not in errors,
        "actions": actions,
        "errors": errors,
        "summary": summary,
    }


def main() -> int:
    import sys

    stats = run()
    print(stats["summary"])
    if stats["errors"]:
        for step, msg in stats["errors"].items():
            print(f"  ERROR {step}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
