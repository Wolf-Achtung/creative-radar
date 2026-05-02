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
