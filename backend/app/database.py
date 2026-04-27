from urllib.parse import quote_plus

from sqlalchemy.exc import ArgumentError
from sqlmodel import SQLModel, Session, create_engine

from .config import settings


def _looks_like_unresolved_reference(value: str) -> bool:
    return "${{" in value or "}}" in value


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
    raw = (settings.database_url or "").strip()
    if raw and not _looks_like_unresolved_reference(raw):
        if raw.startswith(("sqlite://", "postgresql://", "postgresql+psycopg2://", "postgresql+psycopg://")):
            return raw
    pg_url = _pg_url_from_parts()
    if pg_url:
        return pg_url
    if settings.allow_sqlite_fallback:
        return "sqlite:///./creative_radar.db"
    raise RuntimeError(
        "Keine gültige Datenbank-Konfiguration gefunden. Bitte DATABASE_URL oder PGHOST/PGUSER/PGPASSWORD/PGDATABASE in Railway setzen."
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
        raise RuntimeError(f"Ungültige DATABASE_URL: {settings.database_url!r}") from exc


def database_diagnostics() -> dict:
    return {
        "database_kind": "sqlite" if DATABASE_URL.startswith("sqlite") else "postgres",
        "database_url_prefix": DATABASE_URL.split(":", 1)[0] if DATABASE_URL else "missing",
        "sqlite_fallback_allowed": settings.allow_sqlite_fallback,
        "has_database_url": bool(settings.database_url),
        "has_pg_parts": bool(settings.pghost and settings.pguser and settings.pgpassword and settings.pgdatabase),
    }


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
