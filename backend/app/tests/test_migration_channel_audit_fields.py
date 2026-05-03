"""SQLite roundtrip test for the Sprint 5.3.X channel audit-fields migration
(revision b3c7e1d8a204).

Mirrors the pattern in test_migration_extend_channel_registry.py: bootstrap
the SQLModel schema, stamp alembic at the prior head (9a2e7c4f5b18), then
exercise upgrade / downgrade / upgrade and assert column presence at each
step. The migration is additive-only (two nullable VARCHAR columns) so the
test surface is intentionally small.

Postgres-specific behavior is not exercised here — the migration runs
plain VARCHAR DDL on both dialects, so the SQLite path is representative.
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


PRIOR_REVISION = "9a2e7c4f5b18"
NEW_REVISION = "b3c7e1d8a204"
NEW_COLUMNS = {"category", "import_source"}
PRESERVED_COLUMNS = {"notes", "channel_role", "quality_tier"}


@pytest.fixture
def sqlite_url(tmp_path):
    db_file = tmp_path / "channel_audit_fields_roundtrip.db"
    return f"sqlite:///{db_file}"


@pytest.fixture
def alembic_cfg(sqlite_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_url)
    monkeypatch.setattr(db_mod.settings, "database_url", sqlite_url)
    monkeypatch.setattr(db_mod.settings, "database_private_url", "")
    monkeypatch.setattr(db_mod.settings, "database_public_url", "")

    backend_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", sqlite_url)
    cfg.set_main_option("script_location", str(backend_root / "migrations"))

    engine = create_engine(sqlite_url)
    try:
        SQLModel.metadata.create_all(engine)
        # SQLModel.metadata.create_all() spiegelt den *aktuellen* Model-State
        # und legt category + import_source bereits an. Damit die Migration
        # ADD COLUMN echte Arbeit hat (sonst: "duplicate column"), entfernen
        # wir die zwei Spalten hier wieder. Gleicher Workaround wie in
        # test_migration_extend_channel_registry — der permanente Fix
        # (gemeinsame Conftest-Fixture, die das Stripping zentralisiert)
        # steht im Sprint-5.4-Backlog. SQLite >= 3.35 unterstützt
        # DROP COLUMN nativ, was die Test-Umgebung ausliefert.
        with engine.begin() as conn:
            for col in NEW_COLUMNS:
                conn.exec_driver_sql(f"ALTER TABLE channel DROP COLUMN {col}")
    finally:
        engine.dispose()

    command.stamp(cfg, PRIOR_REVISION)
    return cfg


def _channel_columns(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return {col["name"] for col in inspect(engine).get_columns("channel")}
    finally:
        engine.dispose()


def test_upgrade_adds_audit_columns(sqlite_url, alembic_cfg):
    baseline = _channel_columns(sqlite_url)
    assert NEW_COLUMNS.isdisjoint(baseline), (
        f"baseline already contains new columns: {NEW_COLUMNS & baseline}"
    )

    command.upgrade(alembic_cfg, "head")

    after_up = _channel_columns(sqlite_url)
    missing = NEW_COLUMNS - after_up
    assert not missing, f"upgrade did not add: {missing}"
    leftover_prior = PRESERVED_COLUMNS - after_up
    assert not leftover_prior, (
        f"upgrade unexpectedly removed pre-existing columns: {leftover_prior}"
    )


def test_roundtrip_up_down_up(sqlite_url, alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    assert NEW_COLUMNS.issubset(_channel_columns(sqlite_url))

    command.downgrade(alembic_cfg, "-1")
    after_down = _channel_columns(sqlite_url)
    leftover = NEW_COLUMNS & after_down
    assert not leftover, f"downgrade left columns behind: {leftover}"
    assert PRESERVED_COLUMNS.issubset(after_down), (
        "downgrade unexpectedly removed pre-existing columns"
    )

    command.upgrade(alembic_cfg, "head")
    after_reup = _channel_columns(sqlite_url)
    missing = NEW_COLUMNS - after_reup
    assert not missing, f"second upgrade did not re-add: {missing}"
