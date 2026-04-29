from datetime import date, timedelta
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session, select
from app.database import get_session
from app.models.entities import WeeklyReport, Asset
from app.schemas.dto import GenerateWeeklyReportRequest, ReportStatusUpdate, ReportSuggestRequest, ReportGenerateRequest
from app.services.report_generator import generate_weekly_report_html
from app.services.report_renderer_v2 import generate_report_html
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


@router.get("/latest/download.html")
def latest_report_download_html(session: Session = Depends(get_session)):
    report = session.exec(select(WeeklyReport).order_by(WeeklyReport.generated_at.desc())).first()
    if not report:
        raise HTTPException(status_code=404, detail="No report found")
    return Response(
        content=report.html_content or "",
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="creative-radar-report.html"'},
    )


@router.get("/latest/download.md")
def latest_report_download_markdown(session: Session = Depends(get_session)):
    report = session.exec(select(WeeklyReport).order_by(WeeklyReport.generated_at.desc())).first()
    if not report:
        raise HTTPException(status_code=404, detail="No report found")
    assets = list(session.exec(select(Asset).where(Asset.include_in_report == True)).all())
    report_type = "weekly_overview"
    if report.trend_summary_de and report.trend_summary_de.startswith("["):
        report_type = report.trend_summary_de.split("]", 1)[0].strip("[]")
    lines = [
        "# Creative Radar Report",
        "",
        f"- Report-Typ: {report_type}",
        f"- Zeitraum: {report.week_start} bis {report.week_end}",
        f"- Erstellt am: {report.generated_at}",
        "",
        "## Executive Summary (DE)",
        "",
        report.executive_summary_de or "Keine Zusammenfassung verfügbar.",
        "",
        "## Trend Summary",
        "",
        report.trend_summary_de or "Keine Trend-Zusammenfassung verfügbar.",
        "",
        "## Top Findings",
    ]
    for a in assets[:5]:
        lines += [f"- {a.ai_summary_de or 'Creative-Fund'} ({a.asset_type.value})"]
    lines += ["", "## Asset-Details"]
    for a in assets:
        lines += [
            f"### Asset {a.id}",
            f"- Evidence URL: {a.visual_evidence_url or a.screenshot_url or a.thumbnail_url or 'keine'}",
            f"- OCR: {a.ocr_text or 'nicht erkannt'}",
            f"- Titel/Claim: {a.placement_title_text or 'nicht erkannt'}",
            f"- Kinetic: {a.kinetic_text or a.kinetic_type or 'nicht erkannt'}",
            f"- CTA: {'erkannt' if a.asset_type.value in {'CTA Post', 'Ticket CTA'} else 'nicht erkannt'}",
            f"- KI-Zusammenfassung: {a.ai_summary_de or 'keine'}",
            "",
        ]
    lines += ["## Datenlücken", "", "- Assets ohne gesichertes Evidence-Bild wurden als Datenlücke behandelt."]
    markdown = "\n".join(lines)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="creative-radar-report.md"'},
    )


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

    html, meta = generate_report_html(session=session, report_type=payload.report_type, asset_ids=payload.asset_ids, date_from=date_from, date_to=date_to)
    report = WeeklyReport(
        week_start=date_from,
        week_end=date_to,
        html_content=html,
        executive_summary_de=meta['executive_summary_de'],
        executive_summary_en=meta['executive_summary_en'],
        trend_summary_de=f"[{meta.get('report_type','weekly_overview')}] {meta['trend_summary_de']}",
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
