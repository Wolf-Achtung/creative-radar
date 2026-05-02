"""ORM Foreign-Key Resolution Tests for the creative_radar schema (F0.2).

Why this test file exists
-------------------------
SQLite-based tests cannot detect Postgres-specific schema problems. When a
SQLModel class declares ``__table_args__ = {"schema": "creative_radar"}``,
its metadata key becomes ``"creative_radar.<table>"`` instead of
``"<table>"``. Foreign-key strings then have to match that exact key —
otherwise SQLAlchemy raises ``NoReferencedTableError`` at first use.

Pytest's normal SQLite-in-memory fixture strips the schema clause via
``_resolve_table_schema()`` (returns ``None`` for SQLite URLs), so the
unqualified FK strings register fine and never throw. Production hits the
real bug at app boot, hours after CI was green.

This file forces the Postgres-mode codepath in a clean subprocess (so the
SQLAlchemy declarative-class registry can register tables under the
schema-prefixed key without colliding with the SQLite-mode classes that
the regular pytest run already loaded). The subprocess imports
``app.models.entities`` with ``DATABASE_URL=postgresql://...``, then asks
SQLAlchemy to resolve every FK in the metadata. Any mismatch surfaces as a
non-zero exit + serialised error map.

Coverage
--------
* every FK declared on a CR ORM class resolves under Postgres-mode mapping
* the resolved target carries the expected ``creative_radar`` schema prefix
* the SQLite path (used by the rest of the suite) keeps producing a fully
  resolved FK graph
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


_BACKEND_ROOT = Path(__file__).resolve().parents[2]  # tests -> app -> backend


def _run_in_postgres_mode_subprocess() -> dict:
    """Spawn a fresh Python interpreter with DATABASE_URL pointing at Postgres,
    import app.models.entities, and force-resolve every FK. Returns a parsed
    JSON dict with the schema, the table list, the FK pairs, and any errors.

    A subprocess is the only clean way to flip ``_resolve_table_schema()``
    after the parent pytest process has already imported the SQLite-mode
    metadata. SQLAlchemy's class registry is process-global; clearing it
    in-process leaves dangling references that re-collide on reload.
    """
    probe = r"""
import json
import os
import sys

# Settings reads DATABASE_URL via env. Force PG so _resolve_table_schema flips.
os.environ['DATABASE_URL'] = 'postgresql://probe:probe@localhost/probe'
os.environ['ALLOW_SQLITE_FALLBACK'] = 'false'

from app.models import entities

result = {
    'schema': entities._resolve_table_schema(),
    'tables': sorted(entities.SQLModel.metadata.tables.keys()),
    'fks': [],
    'errors': [],
}

for table_name, table in entities.SQLModel.metadata.tables.items():
    for fk in table.foreign_keys:
        try:
            target = fk.column
            result['fks'].append({
                'parent_table': table_name,
                'parent_column': fk.parent.name,
                'target': f'{target.table.fullname}.{target.name}',
            })
        except Exception as exc:
            result['errors'].append({
                'parent_table': table_name,
                'parent_column': fk.parent.name,
                'colspec': str(fk._colspec),
                'error_class': type(exc).__name__,
                'error': str(exc),
            })

sys.stdout.write(json.dumps(result))
"""
    import os
    env = os.environ.copy()
    # pytest's pythonpath = . resolves 'app' for the parent run; the
    # subprocess needs the same root explicitly on PYTHONPATH.
    env["PYTHONPATH"] = str(_BACKEND_ROOT)
    # Strip any inherited Postgres-specific env that would shadow our probe.
    for var in ("DATABASE_PRIVATE_URL", "DATABASE_PUBLIC_URL",
                "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE"):
        env.pop(var, None)

    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(_BACKEND_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"Postgres-mode probe subprocess failed (rc={proc.returncode}).\n"
            f"stderr:\n{proc.stderr}\n"
            f"stdout:\n{proc.stdout}"
        )
    return json.loads(proc.stdout)


# ----------------------- core regression tests -----------------------


def test_postgres_mode_registers_tables_with_creative_radar_schema() -> None:
    result = _run_in_postgres_mode_subprocess()

    assert result["schema"] == "creative_radar"

    expected = {
        "creative_radar.channel",
        "creative_radar.title",
        "creative_radar.titlekeyword",
        "creative_radar.post",
        "creative_radar.asset",
        "creative_radar.titlesyncrun",
        "creative_radar.titlecandidate",
        "creative_radar.weeklyreport",
    }
    actual = set(result["tables"])
    missing = expected - actual
    assert not missing, f"Missing tables in Postgres mode: {missing}"


def test_postgres_mode_all_foreign_keys_resolve() -> None:
    """The regression we hit on 2026-05-02: every FK must be resolvable in
    Postgres-mode. The probe forces resolution by accessing ``fk.column``,
    which raises ``NoReferencedTableError`` if the bare-name vs.
    schema-qualified mismatch re-appears."""
    result = _run_in_postgres_mode_subprocess()

    assert len(result["fks"]) >= 5, (
        f"Expected at least 5 FKs across CR tables, got {len(result['fks'])} — "
        f"resolved set: {result['fks']}, errors: {result['errors']}"
    )
    assert result["errors"] == [], (
        "FK resolution failures in Postgres mode:\n"
        + "\n".join(
            f"  {e['parent_table']}.{e['parent_column']} -> {e['colspec']}: "
            f"{e['error_class']}: {e['error']}"
            for e in result["errors"]
        )
    )


def test_postgres_mode_specific_fks_target_creative_radar_schema() -> None:
    """Lock in the specific (parent_table, parent_column) -> target triples so
    a future refactor that drops one of them is loud about it."""
    result = _run_in_postgres_mode_subprocess()

    expected_pairs = {
        ("creative_radar.titlekeyword", "title_id"): "creative_radar.title.id",
        ("creative_radar.post", "channel_id"): "creative_radar.channel.id",
        ("creative_radar.asset", "post_id"): "creative_radar.post.id",
        ("creative_radar.asset", "title_id"): "creative_radar.title.id",
        ("creative_radar.titlecandidate", "asset_id"): "creative_radar.asset.id",
    }
    seen = {
        (fk["parent_table"], fk["parent_column"]): fk["target"]
        for fk in result["fks"]
    }
    for key, expected in expected_pairs.items():
        assert key in seen, f"Expected FK on {key} no longer present"
        assert seen[key] == expected, (
            f"{key} resolves to {seen[key]} (expected {expected})"
        )


# ----------------------- sqlite path stays clean -----------------------


def test_sqlite_mode_tables_keep_bare_names() -> None:
    """Use the in-process metadata that pytest's normal SQLite fixture set up.
    No subprocess needed because the parent process is already in SQLite
    mode (DATABASE_URL=sqlite:///:memory: from the pytest invocation)."""
    from app.models import entities  # noqa: PLC0415

    assert entities._resolve_table_schema() is None
    bare_names = {
        "channel", "title", "titlekeyword", "post", "asset",
        "titlesyncrun", "titlecandidate", "weeklyreport",
    }
    actual = set(entities.SQLModel.metadata.tables.keys())
    missing = bare_names - actual
    assert not missing, f"Missing bare-name tables in SQLite mode: {missing}"
    for name in bare_names:
        assert entities.SQLModel.metadata.tables[name].schema is None


def test_sqlite_mode_all_foreign_keys_resolve() -> None:
    from app.models import entities  # noqa: PLC0415

    failures: list[str] = []
    for table_name, table in entities.SQLModel.metadata.tables.items():
        for fk in table.foreign_keys:
            try:
                fk.column
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    f"{table_name}.{fk.parent.name}: "
                    f"{type(exc).__name__}: {exc}"
                )
    assert failures == [], "SQLite FK failures:\n" + "\n".join(failures)
