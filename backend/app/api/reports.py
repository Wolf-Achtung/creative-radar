from datetime import date, timedelta
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.database import get_session
from app.models.entities import WeeklyReport, Asset
from app.schemas.dto import GenerateWeeklyReportRequest, ReportStatusUpdate, ReportSuggestRequest, ReportGenerateRequest
from app.services.report_generator import generate_weekly_report_html
from app.services.report_selector import select_assets_for_report

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _range_to_dates(date_range: str) -> tuple[date, date]:
    end = date.today()
    mapping = {"7d": 7, "14d": 14, "30d": 30}
    days = mapping.get(date_range, 7)
    return end - timedelta(days=days), end


@router.get("")
def list_reports(session: Session = Depends(get_session)):
    return session.exec(select(WeeklyReport).order_by(WeeklyReport.generated_at.desc())).all()


@router.get("/latest")
def latest_report(session: Session = Depends(get_session)):
    report = session.exec(select(WeeklyReport).order_by(WeeklyReport.generated_at.desc())).first()
    if not report:
        raise HTTPException(status_code=404, detail="No report found")
    return report


@router.get("/{report_id}")
def get_report(report_id: UUID, session: Session = Depends(get_session)):
    report = session.get(WeeklyReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.post("/generate-weekly")
def generate_weekly(payload: GenerateWeeklyReportRequest, session: Session = Depends(get_session)):
    html, meta = generate_weekly_report_html(session, payload.week_start, payload.week_end, payload.include_only_reviewed)
    report = WeeklyReport(
        week_start=payload.week_start,
        week_end=payload.week_end,
        html_content=html,
        executive_summary_de=meta["executive_summary_de"],
        executive_summary_en=meta["executive_summary_en"],
        trend_summary_de=meta["trend_summary_de"],
    )
    session.add(report)
    session.commit()
    session.refresh(report)
    return report


@router.post('/suggest')
def suggest_report(payload: ReportSuggestRequest, session: Session = Depends(get_session)):
    date_from, date_to = _range_to_dates(payload.date_range)
    return select_assets_for_report(
        session=session,
        report_type=payload.report_type,
        date_from=date_from,
        date_to=date_to,
        channels=payload.channels or None,
        markets=payload.markets or None,
        limit=payload.limit,
    )


@router.post('/generate')
def generate_from_suggestion(payload: ReportGenerateRequest, session: Session = Depends(get_session)):
    date_from, date_to = _range_to_dates(payload.date_range)
    assets = [session.get(Asset, asset_id) for asset_id in payload.asset_ids]
    valid_assets = [a for a in assets if a is not None]
    if not valid_assets:
        raise HTTPException(status_code=400, detail='No valid assets selected')

    for asset in valid_assets:
        asset.include_in_report = True
    session.add_all(valid_assets)
    session.commit()

    html, meta = generate_weekly_report_html(session, date_from, date_to, include_only_reviewed=False)
    report = WeeklyReport(
        week_start=date_from,
        week_end=date_to,
        html_content=html,
        executive_summary_de=meta['executive_summary_de'],
        executive_summary_en=meta['executive_summary_en'],
        trend_summary_de=meta['trend_summary_de'],
    )
    session.add(report)
    session.commit()
    session.refresh(report)
    return report


@router.patch("/{report_id}/status")
def update_report_status(report_id: UUID, payload: ReportStatusUpdate, session: Session = Depends(get_session)):
    report = session.get(WeeklyReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    report.status = payload.status
    session.add(report)
    session.commit()
    session.refresh(report)
    return report
