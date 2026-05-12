"""SQLite roundtrip test for the partial UNIQUE index on
channel(handle, platform) WHERE active (revision a3e7b5c19d42).

Mirrors test_migration_channel_audit_fields.py: bootstrap the SQLModel
schema, stamp alembic at the prior head (f2c8a4d96e10), then exercise
upgrade / negative-insert / down-up roundtrip. SQLite supports partial
indexes natively since 3.8.0; the migration writes
``sqlite_where=active = 1`` for SQLite and ``postgresql_where=active =
true`` for Postgres so both dialects encode the same business rule.

The index — not a constraint — covers (handle, platform) only when
``active = true``. Deactivated audit-trail rows (active=false) are
deliberately allowed to share (handle, platform) with the active row;
this mirrors the manual cleanup pattern used on 2026-05-12 for
starwars/IG (older INT row deactivated, newer US row kept active, FK
posts preserved on both).
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
INDEX_NAME = "channel_handle_platform_unique"


@pytest.fixture
def sqlite_url(tmp_path):
    db_file = tmp_path / "channel_unique_partial_roundtrip.db"
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


def _channel_indexes(url: str) -> list[dict]:
    engine = create_engine(url)
    try:
        return inspect(engine).get_indexes("channel")
    finally:
        engine.dispose()


def _find_partial_index(indexes: list[dict]) -> dict | None:
    return next((idx for idx in indexes if idx.get("name") == INDEX_NAME), None)


def _insert_channel(
    engine,
    *,
    name: str,
    handle: str,
    platform: str,
    active: bool = True,
) -> None:
    """Insert a minimal channel row via raw SQL.

    ``active`` is the only flag the partial index keys on, so it's
    parametrised; the rest stay at the model defaults.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO channel (id, name, platform, url, handle, market, "
                "priority, active, mvp, quality_tier, acquisition_strategy, "
                "monitoring_enabled, created_at, updated_at) "
                "VALUES (lower(hex(randomblob(16))), :name, :platform, "
                ":url, :handle, 'UNKNOWN', 'B', :active, 0, 'P1', 'apify', 1, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "name": name,
                "platform": platform,
                "url": f"https://example.com/{handle}",
                "handle": handle,
                "active": 1 if active else 0,
            },
        )


# ---------- Index presence ----------------------------------------------------


def test_upgrade_adds_partial_unique_index(sqlite_url, alembic_cfg):
    """After upgrade, the channel table carries the partial unique index
    over (handle, platform). SQLite reports the partial predicate via
    ``dialect_options['sqlite_where']`` (per SQLAlchemy reflection)."""
    baseline = _channel_indexes(sqlite_url)
    assert _find_partial_index(baseline) is None, (
        "baseline must not already carry the new index"
    )

    command.upgrade(alembic_cfg, "head")

    after = _channel_indexes(sqlite_url)
    idx = _find_partial_index(after)
    assert idx is not None, (
        f"upgrade did not add {INDEX_NAME}; indexes seen: {after}"
    )
    assert bool(idx["unique"]) is True
    assert idx["column_names"] == ["handle", "platform"]


def test_partial_predicate_visible_in_index_definition(sqlite_url, alembic_cfg):
    """The WHERE clause must be part of the index, otherwise the
    "deactivated duplicates are legal" rule isn't enforced and the test
    suite below would be passing on a normal full UNIQUE index. Read the
    raw SQL from sqlite_master to assert the predicate text is there."""
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(sqlite_url)
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'index' AND name = :name"
                ),
                {"name": INDEX_NAME},
            ).first()
        assert row is not None, "index not registered in sqlite_master"
        index_sql = (row[0] or "").lower()
        assert "where" in index_sql, (
            f"partial predicate missing from index definition: {row[0]!r}"
        )
        assert "active" in index_sql, (
            f"partial predicate doesn't reference 'active': {row[0]!r}"
        )
    finally:
        engine.dispose()


# ---------- Negative: two active rows for same (handle, platform) -------------


def test_active_duplicate_handle_platform_fails(sqlite_url, alembic_cfg):
    """Inserting a second active row with the same (handle, platform)
    pair raises IntegrityError. The actual contract the partial index
    exists for."""
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(sqlite_url)
    try:
        _insert_channel(engine, name="Lionsgate Movies", handle="LionsgateMovies", platform="youtube", active=True)
        with pytest.raises(IntegrityError):
            _insert_channel(
                engine,
                name="Lionsgate Movies (dup)",
                handle="LionsgateMovies",
                platform="youtube",
                active=True,
            )
    finally:
        engine.dispose()


# ---------- Allowed combinations the partial predicate must preserve ---------


def test_inactive_duplicate_allowed(sqlite_url, alembic_cfg):
    """One active + one deactivated row with the same (handle, platform)
    pair must remain legal — this is the post-2026-05-12 cleanup state
    for starwars/IG (older INT row deactivated, newer US row active).
    A full UNIQUE constraint would have rejected this; the partial
    index permits it."""
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(sqlite_url)
    try:
        _insert_channel(engine, name="starwars INT (legacy)", handle="starwars", platform="instagram", active=False)
        _insert_channel(engine, name="starwars US", handle="starwars", platform="instagram", active=True)
    finally:
        engine.dispose()


def test_both_inactive_allowed(sqlite_url, alembic_cfg):
    """Two deactivated rows with the same (handle, platform) are
    legal — edge case, documents that the partial predicate restricts
    the uniqueness to active=true only. Two inactive audit-trail rows
    might arise if a channel ping-pongs through multiple inventory
    sweeps; that's a separate data-quality issue, not a constraint
    violation."""
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(sqlite_url)
    try:
        _insert_channel(engine, name="dup A", handle="zombie", platform="instagram", active=False)
        _insert_channel(engine, name="dup B", handle="zombie", platform="instagram", active=False)
    finally:
        engine.dispose()


def test_same_handle_different_platform_allowed(sqlite_url, alembic_cfg):
    """Composite uniqueness: ``disney`` active on Instagram and ``disney``
    active on TikTok stay legal. Guards against accidental
    over-tightening to a single-column unique on handle."""
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(sqlite_url)
    try:
        _insert_channel(engine, name="Disney IG", handle="disney", platform="instagram", active=True)
        _insert_channel(engine, name="Disney TT", handle="disney", platform="tiktok", active=True)
    finally:
        engine.dispose()


# ---------- Roundtrip ---------------------------------------------------------


def test_roundtrip_up_down_up(sqlite_url, alembic_cfg):
    """Downgrade removes the index; subsequent upgrade re-applies it.
    Catches a one-way migration."""
    command.upgrade(alembic_cfg, "head")
    assert _find_partial_index(_channel_indexes(sqlite_url)) is not None

    command.downgrade(alembic_cfg, PRIOR_REVISION)
    assert _find_partial_index(_channel_indexes(sqlite_url)) is None

    command.upgrade(alembic_cfg, "head")
    assert _find_partial_index(_channel_indexes(sqlite_url)) is not None
