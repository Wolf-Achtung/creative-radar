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
    return "sqlite:///./creative_radar.db"


DATABASE_URL = resolve_database_url()
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
try:
    engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args, pool_pre_ping=True)
except ArgumentError:
    DATABASE_URL = "sqlite:///./creative_radar.db"
    engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
