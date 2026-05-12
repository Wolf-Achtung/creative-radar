"""SQLite roundtrip test for the channel(handle, platform) UNIQUE constraint
migration (revision a3e7b5c19d42).

Mirrors test_migration_channel_audit_fields.py: bootstrap the SQLModel
schema, stamp alembic at the prior head (f2c8a4d96e10), then exercise
upgrade / negative-insert / downgrade / upgrade-again. SQLite's
``batch_alter_table`` rebuilds the table with the constraint, which is
the same path real production SQLite tests would take; Postgres
applies a plain ``ADD CONSTRAINT`` from the same migration code.

The migration itself fails at upgrade time if duplicate
``(handle, platform)`` rows already exist (DB rejects the constraint
creation against violating data). The pre-merge audit query in the
PR catches that case on production — these tests cover the post-merge
guarantee: once applied, the constraint enforces uniqueness.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel

import app.database as db_mod
import app.models  # noqa: F401  (side-effect: registers all entities)


PRIOR_REVISION = "f2c8a4d96e10"
NEW_REVISION = "a3e7b5c19d42"
CONSTRAINT_NAME = "channel_handle_platform_unique"


@pytest.fixture
def sqlite_url(tmp_path):
    db_file = tmp_path / "channel_unique_constraint_roundtrip.db"
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


def _channel_unique_constraints(url: str) -> list[dict]:
    engine = create_engine(url)
    try:
        return inspect(engine).get_unique_constraints("channel")
    finally:
        engine.dispose()


def _insert_channel(engine, *, name: str, handle: str, platform: str) -> None:
    """Insert a minimal channel row via raw SQL.

    Defaults match the Channel model (active=true, mvp=false, market=UNKNOWN,
    quality_tier=P1, acquisition_strategy=apify, monitoring_enabled=true).
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO channel (id, name, platform, url, handle, market, "
                "priority, active, mvp, quality_tier, acquisition_strategy, "
                "monitoring_enabled, created_at, updated_at) "
                "VALUES (lower(hex(randomblob(16))), :name, :platform, "
                ":url, :handle, 'UNKNOWN', 'B', 1, 0, 'P1', 'apify', 1, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "name": name,
                "platform": platform,
                "url": f"https://example.com/{handle}",
                "handle": handle,
            },
        )


def test_upgrade_adds_unique_constraint(sqlite_url, alembic_cfg):
    """After upgrade, the channel table carries
    ``channel_handle_platform_unique`` covering exactly the handle and
    platform columns."""
    baseline = _channel_unique_constraints(sqlite_url)
    assert not any(uc.get("name") == CONSTRAINT_NAME for uc in baseline), (
        "baseline must not already carry the new constraint"
    )

    command.upgrade(alembic_cfg, "head")

    after = _channel_unique_constraints(sqlite_url)
    match = next((uc for uc in after if uc.get("name") == CONSTRAINT_NAME), None)
    assert match is not None, (
        f"upgrade did not add {CONSTRAINT_NAME}; constraints seen: {after}"
    )
    # column_names is what SQLAlchemy reports across dialects for unique
    # constraints. Order matches the migration declaration.
    assert match["column_names"] == ["handle", "platform"]


def test_duplicate_handle_platform_insert_fails(sqlite_url, alembic_cfg):
    """Inserting a second row with the same (handle, platform) pair after
    the migration raises IntegrityError. Guards the actual contract the
    constraint exists for."""
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(sqlite_url)
    try:
        _insert_channel(engine, name="Lionsgate Movies", handle="LionsgateMovies", platform="youtube")
        with pytest.raises(IntegrityError):
            _insert_channel(engine, name="Lionsgate Movies (dup)", handle="LionsgateMovies", platform="youtube")
    finally:
        engine.dispose()


def test_same_handle_different_platform_allowed(sqlite_url, alembic_cfg):
    """The constraint is composite: ``disney`` on Instagram and ``disney``
    on TikTok stay legal because the (handle, platform) pair differs.
    Guards against an accidental over-tightening to a single-column
    unique on handle."""
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(sqlite_url)
    try:
        _insert_channel(engine, name="Disney IG", handle="disney", platform="instagram")
        _insert_channel(engine, name="Disney TT", handle="disney", platform="tiktok")
    finally:
        engine.dispose()


def test_roundtrip_up_down_up(sqlite_url, alembic_cfg):
    """Downgrade removes the constraint; subsequent upgrade re-applies
    it. Catches a migration that's only one-way (a common alembic
    foot-gun)."""
    command.upgrade(alembic_cfg, "head")
    assert any(
        uc.get("name") == CONSTRAINT_NAME
        for uc in _channel_unique_constraints(sqlite_url)
    )

    command.downgrade(alembic_cfg, PRIOR_REVISION)
    assert not any(
        uc.get("name") == CONSTRAINT_NAME
        for uc in _channel_unique_constraints(sqlite_url)
    )

    command.upgrade(alembic_cfg, "head")
    assert any(
        uc.get("name") == CONSTRAINT_NAME
        for uc in _channel_unique_constraints(sqlite_url)
    )
