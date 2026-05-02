"""Temporary admin endpoints for the F0.2 schema migration (Phase 4 W4).

WIRD WIEDER ENTFERNT in Task 4.5 nach erfolgreicher Migration und UI-Smoke.
Both endpoints use raw SQL via the migration scripts; they do not touch the
ORM and therefore stay reachable even if the ORM mapping currently points at
a schema whose tables haven't been moved yet (the very window this endpoint
is meant to close).
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from app.config import settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _verify_token(authorization: str | None) -> None:
    expected = settings.admin_migration_token
    if not expected:
        raise HTTPException(status_code=503, detail="Migration endpoints disabled (ADMIN_MIGRATION_TOKEN not set)")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    if authorization.removeprefix("Bearer ") != expected:
        raise HTTPException(status_code=403, detail="Invalid token")


@router.post("/run-schema-migration")
def run_schema_migration(authorization: str | None = Header(None)) -> dict:
    """Forward migration: move the eight CR tables from public to
    creative_radar. Idempotent (re-run is safe — already-moved tables are
    reported as skipped). The migration script handles its own transaction."""
    _verify_token(authorization)
    # In-function import per the W3 hotfix lesson: keep app boot decoupled
    # from scripts/ being importable.
    from scripts import migrate_to_creative_radar_schema as forward  # noqa: PLC0415

    stats = forward.run()
    return stats


@router.post("/run-schema-rollback")
def run_schema_rollback(authorization: str | None = Header(None)) -> dict:
    """Symmetric rollback: move the eight CR tables back from
    creative_radar to public. Used only if the forward migration leaves
    production in a state Wolf cannot recover otherwise. Same token gate."""
    _verify_token(authorization)
    from scripts import rollback_creative_radar_schema as backward  # noqa: PLC0415

    stats = backward.run()
    return stats
