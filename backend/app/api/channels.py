from uuid import UUID
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select
from app.database import get_session
from app.models.entities import Channel, Market
from app.schemas.dto import ChannelCreate, ChannelUpdate
from app.services.channel_importer import import_channels_from_excel
from app.services.seeds import seed_channels

router = APIRouter(prefix="/api/channels", tags=["channels"])


@router.get("")
def list_channels(market: Market | None = None, active: bool | None = None, mvp: bool | None = None, session: Session = Depends(get_session)):
    statement = select(Channel)
    if market is not None:
        statement = statement.where(Channel.market == market)
    if active is not None:
        statement = statement.where(Channel.active == active)
    if mvp is not None:
        statement = statement.where(Channel.mvp == mvp)
    return session.exec(statement).all()


@router.post("")
def create_channel(payload: ChannelCreate, session: Session = Depends(get_session)):
    channel = Channel(**payload.model_dump())
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


@router.patch("/{channel_id}")
def update_channel(channel_id: UUID, payload: ChannelUpdate, session: Session = Depends(get_session)):
    channel = session.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(channel, key, value)
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


@router.delete("/{channel_id}")
def delete_channel(channel_id: UUID, session: Session = Depends(get_session)):
    channel = session.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    session.delete(channel)
    session.commit()
    return {"deleted": True}


@router.post("/seed-mvp")
def seed_mvp_channels(session: Session = Depends(get_session)):
    created = seed_channels(session)
    return {"created": created}


@router.post("/import-excel")
async def import_excel(file: UploadFile = File(...), session: Session = Depends(get_session)):
    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Bitte eine Excel-Datei .xlsx hochladen.")
    data = await file.read()
    result = import_channels_from_excel(session, data)
    return result
