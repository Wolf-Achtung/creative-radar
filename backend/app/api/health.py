from fastapi import APIRouter
from app.database import database_diagnostics

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health():
    return {"status": "ok", "service": "creative-radar", "version": "1.0.0"}


@router.get("/health/db")
def health_db():
    return database_diagnostics()
