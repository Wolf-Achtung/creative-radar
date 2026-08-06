"""Idempotenter DB-Bootstrap — der ``preDeployCommand`` fuer jede Umgebung.

    python -m scripts.db_bootstrap

Ersetzt das nackte ``alembic upgrade head`` aus railway.json. Grund
(Staging-Setup 2026-08-06): auf einer **frischen, leeren** Postgres
scheitert ``alembic upgrade head`` in diesem Repo zuverlaessig — zweimal:

1. ``migrations/env.py`` legt die ``alembic_version``-Tabelle im Schema
   ``creative_radar`` an, aber kein Migrations-Skript erzeugt das Schema.
2. Selbst mit Schema kollidiert die Kette: die autogenerierte Baseline
   ``cf842bbfaeb5`` traegt keine ``schema='creative_radar'``-Argumente,
   und die Folge-Migrationen legen ENUM-Typen an, die SQLModel aus den
   Enum-Spalten ohnehin implizit erzeugt (Duplicate-Type-Fehler).

Production ist davon nie betroffen gewesen, weil dort Schema und Tabellen
historisch per ``scripts/migrate_to_creative_radar_schema.py`` entstanden
sind und ``alembic_version`` seither existiert. CI umgeht dasselbe Problem
mit ``metadata.create_all()`` + ``alembic stamp head`` (siehe
.github/workflows/backend-tests.yml). Dieses Skript macht genau das —
aber automatisch und nur dann, wenn die DB wirklich leer ist:

    Schema sicherstellen
    ├─ alembic_version existiert  → alembic upgrade head   (Normalfall,
    │                                Prod-Verhalten unveraendert)
    └─ alembic_version fehlt      → metadata.create_all()
                                    + alembic stamp head   (frische DB)

Beide Pfade sind wiederholbar: ``upgrade head`` auf aktuellem Stand ist
ein No-Op, und der Bootstrap-Pfad greift nur einmal — ab dem zweiten
Deploy existiert ``alembic_version``.
"""
from __future__ import annotations

import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

import app.models  # noqa: F401  — Side-Effect: registriert alle Entities
from app.database import DATABASE_URL, _ensure_cr_schema, engine
from app.models.entities import _resolve_table_schema

ALEMBIC_VERSION_TABLE = "alembic_version"


def _alembic_config() -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    return config


def _alembic_version_exists() -> bool:
    """True, wenn Alembic diese DB schon verwaltet."""
    inspector = inspect(engine)
    return inspector.has_table(ALEMBIC_VERSION_TABLE, schema=_resolve_table_schema())


def main() -> None:
    target = "sqlite" if DATABASE_URL.startswith("sqlite") else "postgres"
    print(f"db_bootstrap: target={target} schema={_resolve_table_schema() or '-'}")

    _ensure_cr_schema()

    config = _alembic_config()
    if _alembic_version_exists():
        print("db_bootstrap: alembic_version vorhanden -> upgrade head")
        command.upgrade(config, "head")
        print("db_bootstrap: upgrade head fertig")
        return

    # Frische DB: die Migrationskette ist auf leerem Postgres nicht
    # lauffaehig (siehe Modul-Docstring) — Tabellen aus der ORM-Metadata
    # erzeugen und Alembic auf HEAD stempeln.
    print("db_bootstrap: leere DB erkannt -> create_all + stamp head")
    from sqlmodel import SQLModel  # noqa: PLC0415

    SQLModel.metadata.create_all(engine)
    command.stamp(config, "head")
    print("db_bootstrap: bootstrap fertig")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover — Deploy-Sichtbarkeit
        print(f"db_bootstrap: FEHLGESCHLAGEN — {exc}", file=sys.stderr)
        raise
