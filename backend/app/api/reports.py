from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.database import get_session
from app.models.entities import WeeklyReport
from app.schemas.dto import GenerateWeeklyReportRequest, ReportStatusUpdate
from app.services.report_generator import generate_weekly_report_html

router = APIRouter(prefix="/api/reports", tags=["reports"])


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
