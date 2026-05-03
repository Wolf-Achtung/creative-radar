"""SQLite roundtrip test for the Sprint 5.2.2-Hygiene release_command
smoketest migration (revision 571f54840f19).

The migration itself is a Postgres-only COMMENT ON TABLE — on SQLite it is
a deliberate no-op. What the SQLite path *can* still verify is that the
revision is wired correctly into the chain: alembic locates it, runs it
without error, advances alembic_version to the new head, and the
downgrade path returns alembic_version to the prior revision. That is
exactly what we need to know before the first release_command run in
production.

Pattern mirrors test_migration_extend_channel_registry.py (5.2.1
roundtrip). The W4 revisions (857d9777a8d0, 4f1c8b2d9e30) include
postgres-only DDL, so we bootstrap via SQLModel.metadata.create_all and
stamp at the prior head 7e3b2c4a8f51 before exercising the new
revision.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

import app.database as db_mod
import app.models  # noqa: F401  (side-effect: registers all entities)


PRIOR_REVISION = "7e3b2c4a8f51"
NEW_REVISION = "571f54840f19"


@pytest.fixture
def sqlite_url(tmp_path):
    db_file = tmp_path / "release_command_smoketest_roundtrip.db"
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
    finally:
        engine.dispose()

    command.stamp(cfg, PRIOR_REVISION)
    return cfg


def _current_revision(url: str) -> str | None:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
        return row[0] if row else None
    finally:
        engine.dispose()


def _channel_columns(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return {col["name"] for col in inspect(engine).get_columns("channel")}
    finally:
        engine.dispose()


def test_upgrade_advances_alembic_version_and_is_schema_noop(sqlite_url, alembic_cfg):
    assert _current_revision(sqlite_url) == PRIOR_REVISION
    columns_before = _channel_columns(sqlite_url)

    # Pin to NEW_REVISION rather than "head" so this test stays valid
    # when later migrations land — otherwise it replays them too and
    # may collide with SQLModel.metadata.create_all bootstrap state.
    command.upgrade(alembic_cfg, NEW_REVISION)

    assert _current_revision(sqlite_url) == NEW_REVISION, (
        "release_command smoketest migration did not advance alembic_version"
    )
    columns_after = _channel_columns(sqlite_url)
    assert columns_before == columns_after, (
        "SQLite schema must be unchanged — COMMENT path is postgres-only "
        f"(diff: {columns_before ^ columns_after})"
    )


def test_roundtrip_up_down_up(sqlite_url, alembic_cfg):
    command.upgrade(alembic_cfg, NEW_REVISION)
    assert _current_revision(sqlite_url) == NEW_REVISION

    command.downgrade(alembic_cfg, "-1")
    assert _current_revision(sqlite_url) == PRIOR_REVISION, (
        "downgrade did not return alembic_version to the prior head"
    )

    command.upgrade(alembic_cfg, NEW_REVISION)
    assert _current_revision(sqlite_url) == NEW_REVISION, (
        "second upgrade did not re-advance alembic_version"
    )
