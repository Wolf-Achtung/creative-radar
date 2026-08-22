import os
from datetime import datetime, timezone

from fastapi import APIRouter
from app.core.feature_flags import is_trailer_intelligence_enabled
from app.database import database_diagnostics

router = APIRouter(prefix="/api", tags=["health"])


def _commit_sha() -> str | None:
    """Kurzer Commit-Stand des laufenden Deploys (22.08.2026).

    Railway setzt ``RAILWAY_GIT_COMMIT_SHA`` in jeden Container. Beim
    Vorfall am 21.08. (Deploy schlug fehl, alter Container lief weiter)
    war von aussen nicht erkennbar, WELCHER Stand antwortet — dieser
    Wert macht es mit einem Blick auf /api/health pruefbar. Lokal/CI
    fehlt die Variable → ``null``, kein Geheimnis, nur die ersten
    7 Zeichen.
    """
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "").strip()
    return sha[:7] or None


@router.get("/health")
def health():
    return {
        "status": "ok",
        "service": "creative-radar",
        "version": "1.0.0",
        "commit": _commit_sha(),
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
