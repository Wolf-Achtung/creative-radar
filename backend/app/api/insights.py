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
    """
    if pair not in PAIRS:
        raise HTTPException(
            status_code=404,
            detail=f"Unbekannter Pair-Key: {pair!r}. Verfügbar: {sorted(PAIRS.keys())}",
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
