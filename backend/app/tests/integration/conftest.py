"""Postgres-Integration-Suite — Fixtures (Variante Z, Phase 1).

This suite is **strictly additive** to the existing 614-test SQLite suite
that lives at ``app/tests/test_*.py``. The goal is real coverage for the
Postgres-only code paths — advisory locks (``pg_advisory_xact_lock`` in
``_acquire_brief_lock``) and the ``creative_radar``-schema attachment in
``entities._CR_TABLE_ARGS`` — without touching the bulk SQLite suite.

Skip semantics:
  Every fixture below resolves through ``pg_engine``, which calls
  ``pytest.skip`` if ``DATABASE_URL`` is missing or not a Postgres URL.
  Result: running ``pytest`` locally without a Postgres service produces
  cleanly skipped integration tests, **never** an error or import crash.
  In CI the workflow runs the integration suite in its own pytest
  invocation with ``DATABASE_URL=postgresql://...`` so the gate opens.

Module-import-time caveat (PR #143 lesson):
  ``entities.py:_resolve_table_schema`` decides whether the SQLModel
  metadata carries ``schema='creative_radar'`` exactly once when the
  module first imports. ``settings = Settings()`` (config.py:163) is a
  module-level singleton that reads ``DATABASE_URL`` from the env at
  Python-process start. For the integration suite to see the schema-
  bound metadata, ``DATABASE_URL`` MUST be set to the Postgres URL
  BEFORE pytest starts. The workflow guarantees this by running the
  integration step with its own job-level env; the regular SQLite step
  excludes the integration directory via ``--ignore`` so the two import
  contexts never collide in the same Python process.

Per-test isolation:
  ``pg_session`` drops + recreates the ``creative_radar`` schema before
  yielding, then re-runs ``SQLModel.metadata.create_all`` so every test
  starts on a clean slate. Cost is ~100-300ms/test, acceptable for the
  small integration scope (<10 tests). Transaction-savepoint isolation
  would be faster but breaks for tests that need cross-connection
  visibility (the advisory-lock concurrency tests do).
"""
from __future__ import annotations

import os

import pytest
from sqlmodel import Session, SQLModel, create_engine

PG_SKIP_REASON = (
    "Postgres-Integration-Suite requires DATABASE_URL pointing at a "
    "Postgres instance with a user authorised to DROP/CREATE the "
    "'creative_radar' schema. Skip is the intended local behaviour — "
    "the suite is gated to the CI Postgres service container."
)


def _is_postgres_url(url: str | None) -> bool:
    return bool(url and url.lower().startswith(("postgresql://", "postgresql+")))


@pytest.fixture(scope="session")
def pg_engine():
    """Session-scoped SQLAlchemy engine bound to the Postgres test DB.

    Calls ``pytest.skip`` (not ``pytest.fail``) when DATABASE_URL is not
    a Postgres URL — running this suite locally without infra must be a
    no-op, never a hard error. Tests downstream of this fixture inherit
    the skip transparently.
    """
    url = os.environ.get("DATABASE_URL")
    if not _is_postgres_url(url):
        pytest.skip(PG_SKIP_REASON)

    # Audit 2026-08-17: psycopg2 ist entfernt; ein rohes "postgresql://"
    # aus der CI-ENV wuerde SQLAlchemy sonst auf den psycopg2-Dialekt
    # schicken (ModuleNotFoundError). Gleiche Normalisierung wie die App.
    from app.database import _normalize_pg_driver  # noqa: PLC0415

    engine = create_engine(_normalize_pg_driver(url), future=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def pg_session(pg_engine):
    """Per-test Postgres session with a freshly-rebuilt creative_radar schema.

    Sequence on entry:
    1. ``DROP SCHEMA IF EXISTS creative_radar CASCADE`` — clears anything
       previous tests (or stamps) left behind, including the
       ``alembic_version`` table that ``alembic stamp head`` writes
       inside the schema.
    2. ``CREATE SCHEMA creative_radar`` — fresh empty schema.
    3. ``import app.models`` side-effect-imports all entity classes onto
       the SQLModel registry. This is the same hook env.py uses
       (backend/migrations/env.py:8).
    4. ``SQLModel.metadata.create_all`` runs the DDL against Postgres
       with the schema clause attached — exactly the production-path
       bootstrap.

    Yields a fresh ``Session(engine)``. The session is closed on teardown
    but the schema is left in place — the *next* test's DROP/CREATE is
    what cleans up. Avoids double cleanup on the last test.
    """
    # Import order matters: app.models registers entities on the
    # SQLModel metadata via __init_subclass__. Doing it after the
    # DROP/CREATE keeps the side-effect deterministic per test.
    import app.models  # noqa: F401 — side-effect registration

    with pg_engine.begin() as conn:
        conn.exec_driver_sql("DROP SCHEMA IF EXISTS creative_radar CASCADE")
        conn.exec_driver_sql("CREATE SCHEMA creative_radar")

    SQLModel.metadata.create_all(pg_engine)

    session = Session(pg_engine)
    try:
        yield session
    finally:
        session.close()
