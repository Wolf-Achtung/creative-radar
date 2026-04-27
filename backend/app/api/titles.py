from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.database import get_session
from app.models.entities import Title, TitleKeyword
from app.schemas.dto import KeywordCreate, TitleCreate
from app.services.seeds import seed_titles

router = APIRouter(prefix="/api/titles", tags=["titles"])


@router.get("")
def list_titles(active: bool | None = None, session: Session = Depends(get_session)):
    statement = select(Title)
    if active is not None:
        statement = statement.where(Title.active == active)
    titles = session.exec(statement).all()
    return titles


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
