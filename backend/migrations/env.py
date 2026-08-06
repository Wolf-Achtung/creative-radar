from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# Import the project's models so SQLModel.metadata is populated for autogenerate.
from app import models  # noqa: F401  (side-effect: registers all entities)
from app.database import resolve_database_url

config = context.config

# Resolve DB URL from app config so alembic compares against the real schema.
# The alembic.ini ships with a placeholder URL; resolve_database_url() honours
# DATABASE_URL / DATABASE_PRIVATE_URL / PG* / sqlite fallback like the app does.
config.set_main_option("sqlalchemy.url", resolve_database_url())

if config.config_file_name is not None:
    # disable_existing_loggers=False (Kosten-Audit/Staging-Nachtrag
    # 2026-08-06): Python's fileConfig() default disables jeden Logger,
    # der zum Aufrufzeitpunkt schon existiert und NICHT in alembic.ini
    # gelistet ist (nur root/sqlalchemy/alembic sind dort gelistet) —
    # dauerhaft fuer den Rest des Prozesses (Logger.disabled=True kann
    # caplog nicht rueckgaengig machen, da es nur .level anfasst).
    # Betrifft nur In-Process-Aufrufe (alembic.command.upgrade(...) aus
    # Python heraus, wie es die test_migration_*.py-Tests tun) — die
    # Produktion ruft Alembic immer als eigenen CLI-Prozess auf
    # (railway.json preDeployCommand), frischer Interpreter, nicht
    # betroffen. Ohne den Fix schweigt z. B. app.services.mailer in
    # jedem Test, der alphabetisch nach einer test_migration_*.py-Datei
    # laeuft, sobald der Test caplog benutzt — reines Bestell-Gluecksspiel.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _migration_schema() -> str | None:
    """Postgres deployments place the alembic_version table in the
    creative_radar schema so it travels with the rest of CR's data. SQLite
    paths leave it in the default schema (no concept of schemas here)."""
    url = config.get_main_option("sqlalchemy.url") or ""
    return "creative_radar" if "postgres" in url.lower() else None


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=_migration_schema(),
            include_schemas=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
