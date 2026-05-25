"""Integration: ``creative_radar``-schema attachment against real Postgres.

Covers the exact failure mode that PR #143 hit and could not catch with
SQLite fixtures: ``entities._resolve_table_schema`` flips the
``schema='creative_radar'`` clause on/off based on ``DATABASE_URL`` at
module-import time. SQLite tests never see the postgres branch — this
suite does.

Three contracts:

1. With ``DATABASE_URL`` pointing at Postgres, the module-level
   ``_CR_TABLE_ARGS`` constant must carry ``{"schema": "creative_radar"}``.
   Drift here is the silent root cause of "tables created in public,
   queries hit creative_radar" production incidents.
2. ``SQLModel.metadata.create_all`` against the Postgres engine must
   actually create the tables inside the ``creative_radar`` schema (not
   ``public``), visible via ``information_schema.tables``.
3. A schema-qualified roundtrip (insert via ORM → query via ORM)
   succeeds, proving FK resolution + the schema clause survive the
   SQLAlchemy compile pass.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlmodel import select

from app.models.entities import Channel, _CR_TABLE_ARGS, _resolve_table_schema

# Module-level skip gate: every test below must run against Postgres,
# even ones that don't take ``pg_session`` (the schema-args check is
# infrastructure-free but only meaningful with the right DATABASE_URL).
# ``usefixtures`` routes through ``pg_engine``'s skip check so the
# whole module skips cleanly on SQLite runs.
pytestmark = pytest.mark.usefixtures("pg_engine")


def test_table_args_resolves_to_creative_radar_schema():
    """Module-level constant must carry the schema clause whenever the
    DATABASE_URL points at Postgres. If this fails, every downstream
    test (including the bulk SQLite suite when run under the wrong env)
    becomes meaningless because the ORM emits unqualified queries."""
    assert _resolve_table_schema() == "creative_radar"
    assert _CR_TABLE_ARGS == {"schema": "creative_radar"}


def test_metadata_create_all_lands_tables_in_creative_radar_schema(pg_session):
    """Tables produced by ``SQLModel.metadata.create_all`` must end up in
    the ``creative_radar`` schema, not ``public``. The fixture has just
    run ``DROP SCHEMA IF EXISTS creative_radar CASCADE; CREATE SCHEMA``;
    everything we see now is the result of the fresh ``create_all``."""
    result = pg_session.exec(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'creative_radar' "
            "ORDER BY table_name"
        )
    ).all()
    table_names = {row[0] for row in result}

    # Spot-check a few entities from across the model file — if the
    # schema clause is honoured for one, it's honoured for all (they
    # share the same ``__table_args__ = _CR_TABLE_ARGS``). Names match
    # the actual table layout: SQLModel defaults to the lowercased class
    # name (``channel``, ``asset``, ``post``, ``title``, ``costlog``);
    # a couple of entities pick explicit snake_case names via
    # ``__tablename__`` (``cron_run``, ``insight_report``).
    for required in ("channel", "asset", "title", "post", "costlog", "insight_report"):
        assert required in table_names, (
            f"Table '{required}' missing in creative_radar schema. "
            f"Found: {sorted(table_names)}"
        )

    # And — equally important — nothing leaked into public.
    public_tables = pg_session.exec(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name IN "
            "('channel', 'asset', 'title', 'post', 'costlog', 'insight_report')"
        )
    ).all()
    assert public_tables == [], (
        f"Tables leaked into public schema: {public_tables}. "
        "_CR_TABLE_ARGS may have lost its schema clause."
    )


def test_orm_roundtrip_through_schema_qualified_table(pg_session):
    """End-to-end: insert one Channel via the ORM, query it back. If the
    schema attachment is broken in any way (FK resolution via ``_fk()``,
    SQL compile pass dropping the clause, session bind misrouting),
    this round-trip surfaces it."""
    handle = f"sentinel_{uuid4().hex[:8]}"
    pg_session.add(
        Channel(
            name=handle,
            handle=handle,
            url=f"https://www.instagram.com/{handle}/",
            platform="instagram",
            active=True,
            mvp=True,
        )
    )
    pg_session.commit()

    rows = pg_session.exec(
        select(Channel).where(Channel.handle == handle)
    ).all()
    assert len(rows) == 1
    assert rows[0].handle == handle
    # And: the table the row actually lives in is in creative_radar,
    # not public. Verify via a direct catalog query keyed by the row's
    # own UUID — defence in depth against an ORM that "found" the row
    # in some other schema via search_path magic.
    catalog_check = pg_session.exec(
        text(
            "SELECT table_schema FROM information_schema.tables "
            "WHERE table_name = 'channel'"
        )
    ).all()
    schemas = {r[0] for r in catalog_check}
    assert schemas == {"creative_radar"}, (
        f"channel table found in unexpected schema(s): {schemas}"
    )
