"""SQLite roundtrip test for the Sprint 5.2.1 channel-registry migration
(revision 7e3b2c4a8f51).

We can't replay the full alembic head against SQLite because the W4
migrations (857d9777a8d0, 4f1c8b2d9e30) are postgres-only. Pattern used
here mirrors the rest of the SQLite test suite: bootstrap the schema via
SQLModel.metadata.create_all, stamp alembic at the cost-log revision,
then exercise the new migration's upgrade/downgrade/upgrade cycle and
assert column presence at each step.

Postgres-specific behavior (CREATE TYPE for the three ENUMs, schema-
qualified type resolution on creative_radar.*) is NOT exercised here.
Verify the postgres path against the live DB after deploy.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

import app.database as db_mod
import app.models  # noqa: F401  (side-effect: registers all entities)


PRIOR_REVISION = "4f1c8b2d9e30"
NEW_REVISION = "7e3b2c4a8f51"
NEW_COLUMNS = {
    "channel_role",
    "quality_tier",
    "acquisition_strategy",
    "monitoring_enabled",
}


@pytest.fixture
def sqlite_url(tmp_path):
    db_file = tmp_path / "channel_registry_roundtrip.db"
    return f"sqlite:///{db_file}"


@pytest.fixture
def alembic_cfg(sqlite_url, monkeypatch):
    # env.py calls resolve_database_url() at every alembic command, which in
    # turn reads settings.database_url. pydantic-settings caches the value at
    # import time, so we patch the live attribute instead of just setting env.
    monkeypatch.setenv("DATABASE_URL", sqlite_url)
    monkeypatch.setattr(db_mod.settings, "database_url", sqlite_url)
    monkeypatch.setattr(db_mod.settings, "database_private_url", "")
    monkeypatch.setattr(db_mod.settings, "database_public_url", "")

    backend_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", sqlite_url)
    cfg.set_main_option("script_location", str(backend_root / "migrations"))

    # Bootstrap the channel table (and the rest of the SQLModel schema) so
    # add_column has something to attach to.
    engine = create_engine(sqlite_url)
    try:
        SQLModel.metadata.create_all(engine)
    finally:
        engine.dispose()

    # Stamp the W4 head so alembic only runs our new revision on upgrade.
    command.stamp(cfg, PRIOR_REVISION)
    return cfg


def _channel_columns(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return {col["name"] for col in inspect(engine).get_columns("channel")}
    finally:
        engine.dispose()


def test_upgrade_adds_all_four_columns(sqlite_url, alembic_cfg):
    baseline = _channel_columns(sqlite_url)
    assert NEW_COLUMNS.isdisjoint(baseline), (
        f"baseline already contains new columns: {NEW_COLUMNS & baseline}"
    )

    command.upgrade(alembic_cfg, "head")

    after_up = _channel_columns(sqlite_url)
    missing = NEW_COLUMNS - after_up
    assert not missing, f"upgrade did not add: {missing}"
    # 'notes' must remain untouched per spec.
    assert "notes" in after_up, "upgrade unexpectedly removed the 'notes' column"


def test_roundtrip_up_down_up(sqlite_url, alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    assert NEW_COLUMNS.issubset(_channel_columns(sqlite_url))

    command.downgrade(alembic_cfg, "-1")
    after_down = _channel_columns(sqlite_url)
    leftover = NEW_COLUMNS & after_down
    assert not leftover, f"downgrade left columns behind: {leftover}"
    assert "notes" in after_down, "downgrade unexpectedly removed 'notes'"

    command.upgrade(alembic_cfg, "head")
    after_reup = _channel_columns(sqlite_url)
    missing = NEW_COLUMNS - after_reup
    assert not missing, f"second upgrade did not re-add: {missing}"
