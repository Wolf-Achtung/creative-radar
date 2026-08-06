import os
from urllib.parse import quote_plus

from sqlalchemy import inspect, text
from sqlalchemy.exc import ArgumentError
from sqlmodel import SQLModel, Session, create_engine

from .config import settings


def _looks_like_unresolved_reference(value: str) -> bool:
    return "${{" in value or "}}" in value


def _is_valid_database_url(value: str) -> bool:
    clean = (value or "").strip().strip('"').strip("'")
    if not clean or _looks_like_unresolved_reference(clean):
        return False
    return clean.startswith(("sqlite://", "postgresql://", "postgresql+psycopg2://", "postgresql+psycopg://"))


def _clean_url(value: str) -> str:
    return (value or "").strip().strip('"').strip("'")


def _pg_url_from_parts() -> str | None:
    if not (settings.pghost and settings.pguser and settings.pgpassword and settings.pgdatabase):
        return None
    port = settings.pgport or "5432"
    user = quote_plus(settings.pguser)
    password = quote_plus(settings.pgpassword)
    host = settings.pghost
    database = settings.pgdatabase
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def resolve_database_url() -> str:
    for candidate in (settings.database_url, settings.database_private_url, settings.database_public_url):
        if _is_valid_database_url(candidate):
            return _clean_url(candidate)
    pg_url = _pg_url_from_parts()
    if pg_url:
        return pg_url
    if settings.allow_sqlite_fallback:
        return "sqlite:///./creative_radar.db"
    raise RuntimeError(
        "Keine gültige Datenbank-Konfiguration gefunden. Bitte DATABASE_URL, DATABASE_PRIVATE_URL, DATABASE_PUBLIC_URL oder PGHOST/PGUSER/PGPASSWORD/PGDATABASE in Railway setzen."
    )


def _guard_staging_database(url: str) -> None:
    """Boot-Check (Staging-Briefing 2026-08-06): ein Backend mit
    ``APP_ENV=staging`` darf nur gegen die dafuer vorgesehene DB booten.

    Whitelist-Prinzip: ``STAGING_EXPECTED_DB_HOST`` muss gesetzt sein und
    im aufgeloesten DATABASE_URL vorkommen. Faellt ABSICHTLICH in beiden
    Fehlerfaellen um — fehlende Variable UND falscher Host — damit weder
    eine kopierte Prod-URL noch eine vergessene Staging-Konfiguration
    still durchbootet. Prod/Dev/Tests (APP_ENV != staging) sind No-Ops.
    """
    if settings.app_env != "staging":
        return
    expected = (settings.staging_expected_db_host or "").strip()
    if not expected:
        raise RuntimeError(
            "APP_ENV=staging, aber STAGING_EXPECTED_DB_HOST ist nicht gesetzt. "
            "Boot verweigert — die Variable muss den Host der Staging-Postgres "
            "enthalten (Schutz gegen versehentliche Prod-DB-Verbindung)."
        )
    if expected not in url:
        raise RuntimeError(
            "APP_ENV=staging, aber DATABASE_URL zeigt nicht auf "
            f"STAGING_EXPECTED_DB_HOST={expected!r}. Boot verweigert — das "
            "ist mit hoher Wahrscheinlichkeit eine kopierte Prod-DB-URL."
        )


DATABASE_URL = resolve_database_url()
_guard_staging_database(DATABASE_URL)
_is_sqlite = DATABASE_URL.startswith("sqlite")


def _pg_statement_timeout_ms() -> int:
    """Server-side ``statement_timeout`` in milliseconds for the Postgres
    engine. ENV ``PG_STATEMENT_TIMEOUT_MS`` (Default 60000 = 60s); ``0``
    disables it. Bad values fall back to the default."""
    raw = os.environ.get("PG_STATEMENT_TIMEOUT_MS", "60000")
    try:
        return max(0, int(raw))
    except ValueError:
        return 60000


def _build_connect_args(is_sqlite: bool) -> dict:
    """Per-driver ``connect_args`` for ``create_engine``.

    Postgres path bounds the libpq connection so a dead/half-open socket — e.g.
    the public proxy (tramway.proxy.rlwy.net) on local runs — fails fast and the
    pool reconnects, instead of blocking the ``pool_pre_ping`` reconnect with no
    client-side deadline (the observed minutes-long ``_do_ping`` -> ``pool.connect()``
    hang). ``connect_timeout`` bounds the establishment phase; the TCP keepalives
    detect an already-dead established socket so ``SELECT 1`` / queries fail fast
    too. All are libpq parameters honoured by psycopg2 and psycopg3.

    Unschädlich für den internen Cron-Pfad (``postgres.railway.internal``): ein
    gesunder interner Connect ist im einstelligen Millisekunden-Bereich und nähert
    sich der 10s-Deadline nie; die Keepalives greifen nur auf einem bereits toten
    Socket. Es ist reine Defense-in-depth-Härtung — der Cron geht ohnehin nicht
    über den flakigen Proxy.
    """
    if is_sqlite:
        return {"check_same_thread": False}
    args = {
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    }
    # Server-side statement_timeout (Sprint 2026-06-29). Die Title-Sync-Stage
    # hing am 29.06. 5,4h in einem ``_upsert_normalized_title``-``session.commit``
    # (run 11415f23); der 16.06.-Run 5,8 Tage. ``connect_timeout``/Keepalives
    # oben fangen nur einen TOTEN Socket — ein vom Server angenommener, aber
    # serverseitig blockierender Commit/Lock-Wait laeuft unbegrenzt. libpq
    # ``options="-c statement_timeout=<ms>"`` laesst Postgres jedes Statement
    # (inkl. COMMIT/Lock-Wait) nach der Frist abbrechen → der synchron
    # blockierende Commit wirft, ``sync_titles_from_tmdb`` faengt es und
    # verbucht den Run als error statt den Worker stundenlang zu pinnen.
    #
    # Default 60000ms (60s): weit ueber jeder legitimen Einzel-Query der App
    # (per-Row-Upserts, Brief-/Roundup-Aggregations-SELECTs sind sub-Sekunde
    # bis niedrige Sekunden), deckelt aber den Stunden-/Tage-Hang entschieden.
    # ENV ``PG_STATEMENT_TIMEOUT_MS`` zum Nachjustieren ohne Deploy; ``0`` =
    # aus (Notausstieg). Postgres-only: der sqlite-Testpfad nimmt den Branch
    # oben und sieht ``options`` nie; Alembic baut eine eigene Engine ohne
    # ``_build_connect_args``, ein langer Migrations-/Index-Build wird also
    # nicht gekillt.
    timeout_ms = _pg_statement_timeout_ms()
    if timeout_ms > 0:
        args["options"] = f"-c statement_timeout={timeout_ms}"
    return args


connect_args = _build_connect_args(_is_sqlite)

# Block 2.5 — Connection-Pool tuning.
#
# PR #79 (Block 2 async refactor) deployed at 2026-05-06 09:20 UTC and
# triggered a Postgres connection-pool storm during the first cron run:
# 261 IG items × per-task short Sessions in 4 phases = up to ~1044
# connection open/close cycles in a few minutes against the default
# SQLAlchemy pool (pool_size=5, max_overflow=10). Postgres logged
# "Connection reset by peer" and "unexpected EOF on client connection";
# the run hung in status=running for 2h+. The PR was reverted at 11:38 UTC.
#
# This config is the corrective stance for Block 2.5:
# - pool_size=10:        baseline; covers the synchronous request handlers
#                        and cron's foreground work.
# - max_overflow=10:     burst headroom for the async Phase A/C bursts
#                        under concurrency=3 (OpenAI) + 5 (httpx). Even
#                        worst-case the pool ceiling stays at 20 — well
#                        below Railway's per-DB connection cap.
# - pool_pre_ping=True:  validate connections before checkout. Cheap
#                        (one ``SELECT 1``) and prevents the "connection
#                        was invalidated" stalls observed in the
#                        post-revert window when Railway briefly reset
#                        the upstream Postgres.
# - pool_recycle=300:    recycle connections older than 5 min. Railway's
#                        Postgres has a server-side idle timeout that
#                        previously surfaced as random InvalidatePoolError
#                        on the next checkout; recycle keeps us under that.
# - pool_use_lifo=True:  return the most-recently-used connection first.
#                        Keeps the working set small (idle connections
#                        get to time out and recycle) instead of round-
#                        robining all pool slots equally.
#
# SQLite test/CI runs ignore these knobs because SQLAlchemy uses a
# different pool class for sqlite:/// URLs; the ``_pg_pool_kwargs`` dict
# stays empty for sqlite so ``create_engine`` doesn't reject them.
_pg_pool_kwargs: dict = {} if _is_sqlite else {
    "pool_size": 10,
    "max_overflow": 10,
    "pool_recycle": 300,
    "pool_use_lifo": True,
}

# Block-2.5 budget contract: the asset-creation pipeline's per-task
# semaphores (api/monitor.ASSET_CREATION_*_CONCURRENCY) must stay below
# the pool ceiling so a saturated cron run can't outrun the pool.
# A test enforces ``concurrency_total <= DB_POOL_TOTAL_BUDGET``.
DB_POOL_SIZE = _pg_pool_kwargs.get("pool_size", 0)
DB_POOL_MAX_OVERFLOW = _pg_pool_kwargs.get("max_overflow", 0)
DB_POOL_TOTAL_BUDGET = DB_POOL_SIZE + DB_POOL_MAX_OVERFLOW

try:
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        connect_args=connect_args,
        pool_pre_ping=True,
        **_pg_pool_kwargs,
    )
except ArgumentError as exc:
    if settings.allow_sqlite_fallback:
        DATABASE_URL = "sqlite:///./creative_radar.db"
        engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})
    else:
        raise RuntimeError("Ungültige Datenbank-URL-Konfiguration in Railway.") from exc


def database_diagnostics() -> dict:
    return {
        "database_kind": "sqlite" if DATABASE_URL.startswith("sqlite") else "postgres",
        "database_url_prefix": DATABASE_URL.split(":", 1)[0] if DATABASE_URL else "missing",
        "sqlite_fallback_allowed": settings.allow_sqlite_fallback,
        "has_database_url": bool(settings.database_url),
        "has_database_private_url": bool(settings.database_private_url),
        "has_database_public_url": bool(settings.database_public_url),
        "has_pg_parts": bool(settings.pghost and settings.pguser and settings.pgpassword and settings.pgdatabase),
    }


ASSET_COLUMNS = {
    "visual_analysis_status": "VARCHAR DEFAULT 'pending'",
    "visual_source_url": "VARCHAR",
    "visual_notes": "VARCHAR",
    "placement_title_text": "VARCHAR",
    "placement_position": "VARCHAR",
    "placement_strength": "VARCHAR",
    "has_title_placement": "BOOLEAN DEFAULT FALSE",
    "has_kinetic": "BOOLEAN DEFAULT FALSE",
    "kinetic_type": "VARCHAR",
    "kinetic_text": "VARCHAR",
    "de_us_match_key": "VARCHAR",
    "visual_confidence_score": "FLOAT",
    "visual_evidence_url": "VARCHAR",
    "visual_crop_title_url": "VARCHAR",
    "visual_crop_cta_url": "VARCHAR",
    "visual_crop_kinetic_url": "VARCHAR",
    "visual_evidence_status": "VARCHAR",
    "visual_evidence_pack": "JSON",
}

POST_COLUMNS = {
    "external_id": "VARCHAR",
    "visible_shares": "INTEGER",
    "visible_bookmarks": "INTEGER",
    "duration_seconds": "INTEGER",
}


TITLE_COLUMNS = {
    "tmdb_id": "INTEGER",
    "source": "VARCHAR DEFAULT 'Manual'",
    "aliases": "JSON",
}
ASSETTYPE_ENUM_VALUES = [
    "TRAILER",
    "TRAILER_DROP",
    "TEASER",
    "POSTER",
    "KEY_ART",
    "STORY",
    "KINETIC",
    "CHARACTER_CARD",
    "CAST_POST",
    "REVIEW_QUOTE",
    "CTA_POST",
    "TICKET_CTA",
    "RELEASE_REMINDER",
    "BEHIND_THE_SCENES",
    "EVENT_FESTIVAL",
    "SERIES_EPISODE_PUSH",
    "FRANCHISE_BRAND_POST",
    "DISCOVERY",
    "UNKNOWN",
]


def _ensure_columns(table_name: str, columns: dict[str, str]) -> None:
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    if table_name not in table_names:
        return
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    with engine.begin() as connection:
        for name, ddl in columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl}"))


def _ensure_pg_enum_values(enum_name: str, values: list[str]) -> None:
    if DATABASE_URL.startswith("sqlite"):
        return
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        enum_exists = connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = :enum_name)"),
            {"enum_name": enum_name},
        ).scalar()
        if not enum_exists:
            return
        existing = set(
            connection.execute(
                text(
                    """
                    SELECT enumlabel
                    FROM pg_enum
                    JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
                    WHERE pg_type.typname = :enum_name
                    """
                ),
                {"enum_name": enum_name},
            ).scalars().all()
        )
        for value in values:
            if value not in existing:
                safe_value = value.replace("'", "''")
                connection.execute(text(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{safe_value}'"))


def _ensure_cr_schema() -> None:
    """Idempotent CREATE SCHEMA IF NOT EXISTS for the creative_radar schema.

    Runs only when the ORM is configured for the schema (Postgres production).
    Re-Boot-safe because of IF NOT EXISTS — re-running adds no DDL, no error,
    no race. SQLite paths are no-ops because SQLite ignores schema clauses.
    """
    if DATABASE_URL.startswith("sqlite"):
        return
    # Late import to avoid a circular dep at module load time.
    from app.models.entities import _resolve_table_schema  # noqa: PLC0415
    schema = _resolve_table_schema()
    if not schema:
        return
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))


def create_db_and_tables() -> None:
    """App startup hook. Deliberately does NOT run metadata.create_all on
    Postgres — DDL is owned exclusively by Alembic + the W4 migration scripts
    once F0.2 is in flight. Running create_all on Postgres would either fail
    (schema absent) or, worse, silently shadow the migration by creating
    empty tables in the target schema and blocking ALTER TABLE SET SCHEMA on
    the real data.

    SQLite tests keep create_all so the in-memory test DB still bootstraps
    every fixture from the metadata. The _ensure_columns / _ensure_pg_enum
    helpers are retained as additive Alembic-gap patches; they only execute
    when the inspected default-search-path table is present, so they remain
    safe on Postgres regardless of which schema currently holds the data.
    """
    _ensure_cr_schema()
    if DATABASE_URL.startswith("sqlite"):
        SQLModel.metadata.create_all(engine)
    _ensure_pg_enum_values("assettype", ASSETTYPE_ENUM_VALUES)
    _ensure_columns("asset", ASSET_COLUMNS)
    _ensure_columns("post", POST_COLUMNS)
    _ensure_columns("title", TITLE_COLUMNS)


def get_session():
    with Session(engine) as session:
        yield session
