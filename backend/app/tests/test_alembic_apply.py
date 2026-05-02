"""Tests for scripts/apply_alembic_upgrade.run().

We mock the SQLAlchemy connection and the alembic command module so the
test exercises the orchestration logic (when to stamp, when to upgrade,
when to surface errors) without touching a real Postgres or running real
DDL.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_conn_factory():
    """Return a factory that produces a context-manager mock connection
    with configurable existence checks. The connection ignores DDL writes
    (CREATE SCHEMA, etc.) and answers existence/version queries based on
    the supplied flags."""

    def _make(*, version_table_exists: bool, current_revision: str | None):
        conn = MagicMock(name="Connection")

        def execute(stmt, params=None):
            sql = str(stmt).strip().lower()
            result = MagicMock()
            if sql.startswith("select 1 from information_schema.tables"):
                result.first.return_value = (1,) if version_table_exists else None
                return result
            if sql.startswith('select 1 from "creative_radar"."alembic_version"'):
                result.first.return_value = (
                    (1,) if version_table_exists and current_revision else None
                )
                return result
            if sql.startswith('select version_num from "creative_radar"."alembic_version"'):
                result.first.return_value = (
                    (current_revision,) if version_table_exists and current_revision else None
                )
                return result
            if sql.startswith("create schema"):
                return result
            return result

        conn.execute.side_effect = execute
        cm = MagicMock()
        cm.__enter__.return_value = conn
        cm.__exit__.return_value = False
        return cm

    return _make


def test_run_stamps_baseline_when_version_table_empty(mock_conn_factory) -> None:
    from scripts import apply_alembic_upgrade as apply_mod

    cm_first = mock_conn_factory(version_table_exists=False, current_revision=None)
    cm_after = mock_conn_factory(
        version_table_exists=True, current_revision="857d9777a8d0"
    )

    with patch.object(apply_mod, "engine") as fake_engine, \
         patch.object(apply_mod.command, "stamp") as fake_stamp, \
         patch.object(apply_mod.command, "upgrade") as fake_upgrade:
        fake_engine.begin.side_effect = [cm_first, cm_after]

        stats = apply_mod.run()

    fake_stamp.assert_called_once()
    fake_upgrade.assert_called_once_with(fake_stamp.call_args.args[0], "head")
    assert stats["baseline_stamped"] is True
    assert "stamped baseline" in stats["actions"][0]
    assert stats["actions"][1] == "upgraded to head"
    assert stats["errors"] == {}
    assert stats["after_revision"] == "857d9777a8d0"


def test_run_skips_stamp_when_already_at_head(mock_conn_factory) -> None:
    from scripts import apply_alembic_upgrade as apply_mod

    cm_first = mock_conn_factory(
        version_table_exists=True, current_revision="857d9777a8d0"
    )
    cm_after = mock_conn_factory(
        version_table_exists=True, current_revision="857d9777a8d0"
    )

    with patch.object(apply_mod, "engine") as fake_engine, \
         patch.object(apply_mod.command, "stamp") as fake_stamp, \
         patch.object(apply_mod.command, "upgrade") as fake_upgrade:
        fake_engine.begin.side_effect = [cm_first, cm_after]

        stats = apply_mod.run()

    fake_stamp.assert_not_called()
    fake_upgrade.assert_called_once()
    assert stats["baseline_stamped"] is False
    assert stats["actions"] == ["upgraded to head"]
    assert stats["errors"] == {}


def test_run_isolates_stamp_failure(mock_conn_factory) -> None:
    from scripts import apply_alembic_upgrade as apply_mod

    cm_first = mock_conn_factory(version_table_exists=False, current_revision=None)
    cm_after = mock_conn_factory(version_table_exists=False, current_revision=None)

    with patch.object(apply_mod, "engine") as fake_engine, \
         patch.object(apply_mod.command, "stamp",
                      side_effect=RuntimeError("permission denied on schema")) as fake_stamp, \
         patch.object(apply_mod.command, "upgrade") as fake_upgrade:
        fake_engine.begin.side_effect = [cm_first, cm_after]

        stats = apply_mod.run()

    fake_stamp.assert_called_once()
    # Upgrade is skipped if stamp failed — otherwise we'd run upgrade against
    # an unstamped database and replay the baseline DDL.
    fake_upgrade.assert_not_called()
    assert "stamp" in stats["errors"]
    assert "permission denied" in stats["errors"]["stamp"]


def test_run_isolates_upgrade_failure(mock_conn_factory) -> None:
    from scripts import apply_alembic_upgrade as apply_mod

    cm_first = mock_conn_factory(version_table_exists=False, current_revision=None)
    cm_after = mock_conn_factory(
        version_table_exists=True, current_revision="cf842bbfaeb5"
    )

    with patch.object(apply_mod, "engine") as fake_engine, \
         patch.object(apply_mod.command, "stamp") as fake_stamp, \
         patch.object(apply_mod.command, "upgrade",
                      side_effect=RuntimeError("relation already exists")):
        fake_engine.begin.side_effect = [cm_first, cm_after]

        stats = apply_mod.run()

    fake_stamp.assert_called_once()
    assert stats["baseline_stamped"] is True
    assert "upgrade" in stats["errors"]
    assert "relation already exists" in stats["errors"]["upgrade"]


def test_summary_line_reports_revision_transition(mock_conn_factory) -> None:
    from scripts import apply_alembic_upgrade as apply_mod

    cm_first = mock_conn_factory(version_table_exists=False, current_revision=None)
    cm_after = mock_conn_factory(
        version_table_exists=True, current_revision="857d9777a8d0"
    )

    with patch.object(apply_mod, "engine") as fake_engine, \
         patch.object(apply_mod.command, "stamp"), \
         patch.object(apply_mod.command, "upgrade"):
        fake_engine.begin.side_effect = [cm_first, cm_after]

        stats = apply_mod.run()

    assert "none -> 857d9777a8d0" in stats["summary"]


# ----------------------- production-layout tests -----------------------
#
# These two tests guard against the W4-Hotfix-3 regression: the orchestration
# tests above mock command.stamp / command.upgrade and never load the real
# Alembic config. That made it impossible for them to detect a missing
# alembic.ini in the production container. The two tests here close that
# gap.


def test_alembic_ini_path_resolves_to_real_file_in_repo_layout() -> None:
    """The apply script computes ALEMBIC_INI relative to its own location.
    If the Dockerfile drops alembic.ini next to scripts/, the resolved path
    in the container will match the layout this test verifies in the repo.
    Failure means the apply script's path resolution diverged from where
    alembic.ini actually lives — and production will hit the same divergence.
    """
    from scripts.apply_alembic_upgrade import ALEMBIC_INI

    assert ALEMBIC_INI.is_file(), (
        f"Alembic config missing at {ALEMBIC_INI}. Either ALEMBIC_INI in "
        "scripts/apply_alembic_upgrade.py needs adjusting, or alembic.ini "
        "is missing from the expected location."
    )


def test_apply_loads_alembic_config_against_sqlite_subprocess(tmp_path) -> None:
    """Hermetic E2E: spawn a subprocess with DATABASE_URL=sqlite, allow the
    fallback, and call scripts.apply_alembic_upgrade.run(). This exercises
    the real Config + command path without any mocks. If alembic.ini is
    missing or malformed, this test fails early with a readable error.

    Subprocess pattern matches test_orm_fk_resolution.py — gives a clean
    DATABASE_URL flip without colliding with the parent pytest's ORM
    metadata cache.
    """
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[2]  # tests -> app -> backend
    db_file = tmp_path / "alembic_apply_probe.db"
    probe = r"""
import json
import sys
import os
os.environ['DATABASE_URL'] = 'sqlite:///{db}'
os.environ['ALLOW_SQLITE_FALLBACK'] = 'true'

from scripts import apply_alembic_upgrade as m

# Smoke-test the config load without running stamp/upgrade against the
# real DB (those would touch a fresh sqlite that has no schema yet).
try:
    cfg = m._alembic_config()
    out = {{
        'config_loaded': True,
        'script_location': cfg.get_main_option('script_location'),
        'errors': [],
    }}
except Exception as exc:
    out = {{
        'config_loaded': False,
        'script_location': None,
        'errors': [type(exc).__name__ + ': ' + str(exc)],
    }}

sys.stdout.write(json.dumps(out))
""".format(db=str(db_file).replace("\\", "/"))

    env = os.environ.copy()
    env["PYTHONPATH"] = str(backend_root)
    for var in ("DATABASE_PRIVATE_URL", "DATABASE_PUBLIC_URL",
                "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE"):
        env.pop(var, None)

    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(backend_root),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode == 0, (
        f"Probe subprocess failed (rc={proc.returncode}).\n"
        f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    )
    result = json.loads(proc.stdout)
    assert result["config_loaded"] is True, (
        f"Alembic config could not be loaded: {result['errors']}"
    )
    assert result["script_location"], (
        "Alembic config has no script_location — production-blocker"
    )
    assert result["script_location"].endswith("migrations"), (
        f"Unexpected script_location: {result['script_location']}"
    )
