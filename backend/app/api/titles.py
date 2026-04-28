from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.models.entities import CandidateStatus, Title, TitleCandidate, TitleKeyword, TitleSyncRun
from app.schemas.dto import (
    KeywordCreate,
    TitleCandidateCreateFromAsset,
    TitleCandidatePatch,
    TitleCreate,
    TitleSyncRequest,
)
from app.services.seeds import seed_titles
from app.services.title_candidates import create_candidate_from_asset
from app.services.title_rematch import rematch_unassigned_assets
from app.services.title_sync import sync_titles_from_tmdb

router = APIRouter(prefix="/api/titles", tags=["titles"])


@router.get("")
def list_titles(active: bool | None = None, session: Session = Depends(get_session)):
    statement = select(Title)
    if active is not None:
        statement = statement.where(Title.active == active)
    titles = session.exec(statement).all()
    deduped: dict[str, Title] = {}
    for title in titles:
        key = (title.title_original or "").strip().lower()
        current = deduped.get(key)
        if not current:
            deduped[key] = title
            continue
        if current.tmdb_id is None and title.tmdb_id is not None:
            deduped[key] = title
    return list(deduped.values())


@router.post("")
def create_title(payload: TitleCreate, session: Session = Depends(get_session)):
    data = payload.model_dump(exclude={"keywords"})
    title = Title(**data)
    session.add(title)
    session.commit()
    session.refresh(title)
    for keyword in payload.keywords:
        session.add(TitleKeyword(title_id=title.id, keyword=keyword))
    session.commit()
    return title


@router.get("/{title_id}/keywords")
def list_keywords(title_id: UUID, session: Session = Depends(get_session)):
    return session.exec(select(TitleKeyword).where(TitleKeyword.title_id == title_id)).all()


@router.post("/{title_id}/keywords")
def add_keyword(title_id: UUID, payload: KeywordCreate, session: Session = Depends(get_session)):
    title = session.get(Title, title_id)
    if not title:
        raise HTTPException(status_code=404, detail="Title not found")
    keyword = TitleKeyword(title_id=title_id, **payload.model_dump())
    session.add(keyword)
    session.commit()
    session.refresh(keyword)
    return keyword


@router.delete("/keywords/{keyword_id}")
def delete_keyword(keyword_id: UUID, session: Session = Depends(get_session)):
    keyword = session.get(TitleKeyword, keyword_id)
    if not keyword:
        raise HTTPException(status_code=404, detail="Keyword not found")
    session.delete(keyword)
    session.commit()
    return {"deleted": True}


@router.post("/seed-mvp")
def seed_mvp_titles(session: Session = Depends(get_session)):
    created = seed_titles(session)
    return {"created": created}


@router.post("/sync/tmdb")
async def sync_tmdb(payload: TitleSyncRequest, session: Session = Depends(get_session)):
    return await sync_titles_from_tmdb(
        session,
        markets=payload.markets,
        lookback_weeks=payload.lookback_weeks,
        lookahead_weeks=payload.lookahead_weeks,
    )


@router.get("/sync/runs")
def list_sync_runs(session: Session = Depends(get_session)):
    return session.exec(select(TitleSyncRun).order_by(TitleSyncRun.created_at.desc())).all()


@router.post("/candidates/from-asset/{asset_id}")
def create_candidate(asset_id: UUID, payload: TitleCandidateCreateFromAsset | None = None, session: Session = Depends(get_session)):
    try:
        candidate = create_candidate_from_asset(session, asset_id)
        if payload and payload.suggested_title:
            candidate.suggested_title = payload.suggested_title
            session.add(candidate)
            session.commit()
            session.refresh(candidate)
        return candidate
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/candidates")
def list_candidates(status: CandidateStatus | None = None, session: Session = Depends(get_session)):
    statement = select(TitleCandidate).order_by(TitleCandidate.created_at.desc())
    if status is not None:
        statement = statement.where(TitleCandidate.status == status)
    return session.exec(statement).all()


@router.patch("/candidates/{candidate_id}")
def patch_candidate(candidate_id: UUID, payload: TitleCandidatePatch, session: Session = Depends(get_session)):
    candidate = session.get(TitleCandidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Title candidate not found")
    data = payload.model_dump(exclude_none=True)
    for key, value in data.items():
        setattr(candidate, key, value)
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return candidate


@router.get("/stats/whitelist")
def whitelist_stats(session: Session = Depends(get_session)):
    active_titles = len(session.exec(select(Title).where(Title.active == True)).all())  # noqa: E712
    latest_run = session.exec(select(TitleSyncRun).order_by(TitleSyncRun.created_at.desc())).first()
    open_candidates = len(session.exec(select(TitleCandidate).where(TitleCandidate.status == CandidateStatus.OPEN)).all())
    new_titles_this_week = 0
    if latest_run:
        new_titles_this_week = latest_run.upserted_count
    return {
        "active_titles": active_titles,
        "last_sync": latest_run.created_at if latest_run else None,
        "new_titles_this_week": new_titles_this_week,
        "open_title_candidates": open_candidates,
    }


@router.post("/rematch-assets")
def rematch_assets(session: Session = Depends(get_session)):
    summary = rematch_unassigned_assets(session)
    return summary.to_dict()
