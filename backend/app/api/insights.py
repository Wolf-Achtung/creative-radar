from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.database import get_session
from app.schemas.insights import InsightReport
from app.services.anthropic_client import (
    AnthropicAPIError,
    AnthropicAuthError,
    AnthropicRateLimitError,
)
from app.services.insight_engine import PAIRS, generate_weekly_report
from app.services.insights import build_overview

router = APIRouter(prefix="/api/insights", tags=["insights"])


def _enabled_pair_keys() -> list[str]:
    return sorted(k for k, v in PAIRS.items() if v.get("enabled", True))


@router.get("/overview")
def overview(
    week_start: date | None = None,
    week_end: date | None = None,
    session: Session = Depends(get_session),
):
    return build_overview(session, week_start, week_end)


@router.get("/weekly", response_model=InsightReport)
def weekly(
    pair: str = Query(..., description="Pair-Key, z.B. 'warnerbros'"),
    window_days: int = Query(30, ge=7, le=90, description="Datenfenster in Tagen"),
    dry_run: bool = Query(
        False,
        description="True = nur Aggregation, kein LLM-Call (für Quality-Gate ohne Cost).",
    ),
    session: Session = Depends(get_session),
) -> InsightReport:
    """Generiere den Trailerhaus-Wochenreport für einen Pair.

    Beim Dry-Run wird ausschließlich die deterministische Aggregation
    zurückgegeben — nützlich, um vor dem ersten echten LLM-Call zu prüfen,
    welche Daten an Opus 4.7 gehen.

    Sprint-2: Pairs können in ``services/insight_engine.PAIRS`` mit
    ``enabled=False`` registriert sein, um sie als „coming soon" anzukündigen
    ohne Code-Push. Solche Pairs antworten mit 503 und einem strukturierten
    Body, den das Frontend zu einer Aktivierungs-Notiz rendert.
    """
    if pair not in PAIRS:
        raise HTTPException(
            status_code=404,
            detail=f"Unbekannter Pair-Key: {pair!r}. Verfügbar: {_enabled_pair_keys()}",
        )
    pair_def = PAIRS[pair]
    if not pair_def.get("enabled", True):
        # 503 statt 404, weil der Pair existiert (Frontend kann ein Label
        # rendern), nur nicht ausgeliefert wird. Strukturierter Body, damit
        # der Frontend-Fehlerpfad den ``reason`` direkt zeigen kann ohne den
        # Endpoint nochmal zu re-fetchen.
        raise HTTPException(
            status_code=503,
            detail={
                "error": "pair_not_activated",
                "pair": pair,
                "reason": pair_def.get("reason") or "Pair ist aktuell deaktiviert.",
            },
        )
    try:
        return generate_weekly_report(
            session,
            pair,
            window_days=window_days,
            dry_run=dry_run,
        )
    except AnthropicAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except AnthropicRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except AnthropicAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
