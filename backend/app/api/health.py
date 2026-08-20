from datetime import datetime, timezone

from fastapi import APIRouter
from app.core.feature_flags import is_trailer_intelligence_enabled
from app.database import database_diagnostics

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health():
    return {
        "status": "ok",
        "service": "creative-radar",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        # Staging-Fundament 20.08.2026: das Frontend liest hier, welche
        # Flag-Features diese Umgebung anbietet, statt es aus der
        # Build-Zeit-Variable zu raten — derselbe main-Build laeuft auf
        # Prod (Flag aus) und Staging (Flag an) und zeigt jeweils das
        # Richtige. Nur An/Aus-Zustaende, keine Geheimnisse.
        "features": {
            "trailer_intelligence": is_trailer_intelligence_enabled(),
        },
    }


@router.get("/health/db")
def health_db():
    return database_diagnostics()
