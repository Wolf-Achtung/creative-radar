import os
from datetime import datetime, timezone

from fastapi import APIRouter
from app.core.feature_flags import (
    is_beweis_loop_enabled,
    is_kampagnen_timing_enabled,
    is_katalog_nachladen_enabled,
    is_post_check_enabled,
    is_projekt_export_enabled,
    is_projekt_start_brief_enabled,
    is_referenz_suche_enabled,
    is_release_countdown_enabled,
    is_sound_trends_enabled,
    is_trailer_intelligence_enabled,
    is_wir_projekte_enabled,
)
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
            "wir_projekte": is_wir_projekte_enabled(),
            "projekt_start_brief": is_projekt_start_brief_enabled(),
            "kampagnen_timing": is_kampagnen_timing_enabled(),
            "katalog_nachladen": is_katalog_nachladen_enabled(),
            "sound_trends": is_sound_trends_enabled(),
            "referenz_suche": is_referenz_suche_enabled(),
            "release_countdown": is_release_countdown_enabled(),
            "beweis_loop": is_beweis_loop_enabled(),
            "projekt_export": is_projekt_export_enabled(),
            "post_check": is_post_check_enabled(),
        },
    }


@router.get("/health/db")
def health_db():
    return database_diagnostics()
