"""SQLite roundtrip test for the Sprint 5.3.1 cross-platform analysis
migration (revision 9a2e7c4f5b18).

Bootstrap pattern note: ``SQLModel.metadata.create_all`` reflects the
*current* SQLModel definitions and therefore already creates the new
columns we're trying to test the migration for. We strip them after
bootstrap so the migration's add_column ops have something to do —
otherwise SQLite raises ``duplicate column``. (The same blind spot
exists in test_migration_extend_channel_registry.py from 5.2.1, which
fails on the disjoint assertion against the current SQLModel state;
that pre-existing failure is out of scope here and noted in the
Mini-Run 1 status report.)

Postgres-only DDL — the partial-unique index using
``postgresql_where=asset_url IS NOT NULL`` — is exercised against the
live DB after deploy. SQLite gets a regular UNIQUE index instead;
NULL semantics in SQLite (multiple NULLs allowed in a UNIQUE column)
match the behaviour we want from the partial index, so the test
remains meaningful for idempotency reasoning.
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


PRIOR_REVISION = "571f54840f19"
NEW_REVISION = "9a2e7c4f5b18"

NEW_POST_COLUMNS = {"analysis", "last_analyzed_at"}
NEW_ASSET_COLUMNS = {"asset_url", "vision_description", "vision_model", "analyzed_at"}


@pytest.fixture
def sqlite_url(tmp_path):
    db_file = tmp_path / "analysis_fields_roundtrip.db"
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
        # Strip the columns we're about to test so the migration has work
        # to do. SQLite >= 3.35 supports ALTER TABLE DROP COLUMN, but the
        # auto-generated index from SQLModel's ``index=True`` on
        # last_analyzed_at must be dropped first — otherwise the column
        # drop fails with "error in index after drop column".
        with engine.begin() as conn:
            conn.execute(text("DROP INDEX IF EXISTS ix_post_last_analyzed_at"))
            for col in NEW_POST_COLUMNS:
                conn.execute(text(f"ALTER TABLE post DROP COLUMN {col}"))
            for col in NEW_ASSET_COLUMNS:
                conn.execute(text(f"ALTER TABLE asset DROP COLUMN {col}"))
    finally:
        engine.dispose()

    command.stamp(cfg, PRIOR_REVISION)
    return cfg


def _columns(url: str, table: str) -> set[str]:
    engine = create_engine(url)
    try:
        return {col["name"] for col in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def _indexes(url: str, table: str) -> dict[str, dict]:
    engine = create_engine(url)
    try:
        return {idx["name"]: idx for idx in inspect(engine).get_indexes(table)}
    finally:
        engine.dispose()


def _current_revision(url: str) -> str | None:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
        return row[0] if row else None
    finally:
        engine.dispose()


def test_baseline_lacks_new_columns(sqlite_url, alembic_cfg):
    """Sanity: the fixture's column-strip step must produce a baseline
    that genuinely lacks the new columns, otherwise the upgrade test
    would silently no-op on add_column."""
    post_cols = _columns(sqlite_url, "post")
    asset_cols = _columns(sqlite_url, "asset")
    assert NEW_POST_COLUMNS.isdisjoint(post_cols), post_cols & NEW_POST_COLUMNS
    assert NEW_ASSET_COLUMNS.isdisjoint(asset_cols), asset_cols & NEW_ASSET_COLUMNS


def test_upgrade_adds_columns_and_indexes(sqlite_url, alembic_cfg):
    command.upgrade(alembic_cfg, NEW_REVISION)

    assert _current_revision(sqlite_url) == NEW_REVISION

    post_cols = _columns(sqlite_url, "post")
    asset_cols = _columns(sqlite_url, "asset")
    assert NEW_POST_COLUMNS.issubset(post_cols), NEW_POST_COLUMNS - post_cols
    assert NEW_ASSET_COLUMNS.issubset(asset_cols), NEW_ASSET_COLUMNS - asset_cols

    post_idxs = _indexes(sqlite_url, "post")
    asset_idxs = _indexes(sqlite_url, "asset")
    assert "ix_post_last_analyzed_at" in post_idxs

    uq = asset_idxs.get("uq_asset_post_id_asset_url")
    assert uq is not None, asset_idxs.keys()
    # SQLAlchemy's SQLite reflector reports ``unique`` as 0/1, Postgres
    # as bool; ``bool(...)`` normalises both.
    assert bool(uq["unique"]) is True
    assert set(uq["column_names"]) == {"post_id", "asset_url"}


def test_unique_index_allows_multiple_null_asset_urls(sqlite_url, alembic_cfg):
    """The Postgres partial-index ``WHERE asset_url IS NOT NULL`` and
    SQLite's NULL-distinct UNIQUE semantics both permit multiple
    asset rows for the same post_id when asset_url is NULL — that's
    the legacy-row exemption the design relies on. Verify the SQLite
    behaviour directly so a future refactor doesn't silently flip it.
    """
    import uuid
    from datetime import datetime, timezone

    command.upgrade(alembic_cfg, NEW_REVISION)

    engine = create_engine(sqlite_url)
    try:
        with engine.begin() as conn:
            # Need a post first (FK target). Inline minimal rows; the
            # full ORM-roundtrip path is covered elsewhere.
            channel_id = str(uuid.uuid4())
            post_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(text(
                "INSERT INTO channel (id, name, platform, url, market, priority, "
                "active, mvp, quality_tier, acquisition_strategy, monitoring_enabled, "
                "created_at, updated_at) VALUES "
                "(:id, 'TestCh', 'youtube', 'https://x', 'INT', 'B', 1, 0, "
                "'P1', 'youtube_api', 1, :now, :now)"
            ), {"id": channel_id, "now": now})
            conn.execute(text(
                "INSERT INTO post (id, channel_id, platform, post_url, "
                "raw_payload, status, detected_at, created_at, updated_at) VALUES "
                "(:id, :ch, 'youtube', 'https://yt/x', '{}', 'new', :now, :now, :now)"
            ), {"id": post_id, "ch": channel_id, "now": now})

            for _ in range(2):
                asset_id = str(uuid.uuid4())
                conn.execute(text(
                    "INSERT INTO asset (id, post_id, asset_type, language, "
                    "review_status, include_in_report, is_highlight, "
                    "visual_analysis_status, has_title_placement, has_kinetic, "
                    "created_at, updated_at) VALUES "
                    "(:id, :post, 'Unknown', 'Unknown', 'new', 0, 0, "
                    "'pending', 0, 0, :now, :now)"
                ), {"id": asset_id, "post": post_id, "now": now})
    finally:
        engine.dispose()


def test_unique_index_blocks_duplicate_asset_url_per_post(sqlite_url, alembic_cfg):
    """Idempotency-guard: same (post_id, asset_url) pair must not
    create a second Asset row when asset_url is set."""
    import uuid
    from datetime import datetime, timezone

    command.upgrade(alembic_cfg, NEW_REVISION)

    engine = create_engine(sqlite_url)
    try:
        channel_id = str(uuid.uuid4())
        post_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        url = "https://i.ytimg.com/maxres.jpg"

        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO channel (id, name, platform, url, market, priority, "
                "active, mvp, quality_tier, acquisition_strategy, monitoring_enabled, "
                "created_at, updated_at) VALUES "
                "(:id, 'TestCh', 'youtube', 'https://x', 'INT', 'B', 1, 0, "
                "'P1', 'youtube_api', 1, :now, :now)"
            ), {"id": channel_id, "now": now})
            conn.execute(text(
                "INSERT INTO post (id, channel_id, platform, post_url, "
                "raw_payload, status, detected_at, created_at, updated_at) VALUES "
                "(:id, :ch, 'youtube', 'https://yt/y', '{}', 'new', :now, :now, :now)"
            ), {"id": post_id, "ch": channel_id, "now": now})
            conn.execute(text(
                "INSERT INTO asset (id, post_id, asset_type, language, asset_url, "
                "review_status, include_in_report, is_highlight, "
                "visual_analysis_status, has_title_placement, has_kinetic, "
                "created_at, updated_at) VALUES "
                "(:id, :post, 'Unknown', 'Unknown', :url, 'new', 0, 0, "
                "'pending', 0, 0, :now, :now)"
            ), {"id": str(uuid.uuid4()), "post": post_id, "url": url, "now": now})

        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO asset (id, post_id, asset_type, language, asset_url, "
                    "review_status, include_in_report, is_highlight, "
                    "visual_analysis_status, has_title_placement, has_kinetic, "
                    "created_at, updated_at) VALUES "
                    "(:id, :post, 'Unknown', 'Unknown', :url, 'new', 0, 0, "
                    "'pending', 0, 0, :now, :now)"
                ), {"id": str(uuid.uuid4()), "post": post_id, "url": url, "now": now})
    finally:
        engine.dispose()


def test_roundtrip_up_down_up(sqlite_url, alembic_cfg):
    command.upgrade(alembic_cfg, NEW_REVISION)
    assert NEW_POST_COLUMNS.issubset(_columns(sqlite_url, "post"))
    assert NEW_ASSET_COLUMNS.issubset(_columns(sqlite_url, "asset"))

    command.downgrade(alembic_cfg, PRIOR_REVISION)
    assert _current_revision(sqlite_url) == PRIOR_REVISION
    post_after_down = _columns(sqlite_url, "post")
    asset_after_down = _columns(sqlite_url, "asset")
    assert NEW_POST_COLUMNS.isdisjoint(post_after_down), (
        f"downgrade left columns behind on post: {NEW_POST_COLUMNS & post_after_down}"
    )
    assert NEW_ASSET_COLUMNS.isdisjoint(asset_after_down), (
        f"downgrade left columns behind on asset: {NEW_ASSET_COLUMNS & asset_after_down}"
    )

    command.upgrade(alembic_cfg, NEW_REVISION)
    assert NEW_POST_COLUMNS.issubset(_columns(sqlite_url, "post"))
    assert NEW_ASSET_COLUMNS.issubset(_columns(sqlite_url, "asset"))
