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
    fileConfig(config.config_file_name)

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


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
