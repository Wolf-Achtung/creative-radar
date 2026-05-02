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


DATABASE_URL = resolve_database_url()
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
try:
    engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args, pool_pre_ping=True)
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
    """If the ORM is configured to put CR tables in the 'creative_radar'
    schema (Postgres production only), make sure the schema exists before
    metadata.create_all walks the table list. Idempotent CREATE SCHEMA;
    no-op for SQLite because SQLite ignores schema clauses."""
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
    _ensure_cr_schema()
    SQLModel.metadata.create_all(engine)
    _ensure_pg_enum_values("assettype", ASSETTYPE_ENUM_VALUES)
    _ensure_columns("asset", ASSET_COLUMNS)
    _ensure_columns("post", POST_COLUMNS)
    _ensure_columns("title", TITLE_COLUMNS)


def get_session():
    with Session(engine) as session:
        yield session
