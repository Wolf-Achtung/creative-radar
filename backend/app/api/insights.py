from datetime import date

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.database import get_session
from app.services.insights import build_overview

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("/overview")
def overview(
    week_start: date | None = None,
    week_end: date | None = None,
    session: Session = Depends(get_session),
):
    return build_overview(session, week_start, week_end)
