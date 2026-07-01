from uuid import UUID
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select
from app.database import get_session
from app.models.entities import Channel, Market
from app.schemas.dto import ChannelCreate, ChannelUpdate
from app.admin_session import require_admin_session
from app.services.channel_importer import import_channels_from_excel
from app.services.seeds import seed_channels

# Sprint 28.05.2026 (Admin-Login): Router-Level-Dependency.
router = APIRouter(
    prefix="/api/channels",
    tags=["channels"],
    dependencies=[Depends(require_admin_session)],
)

# Sicherheits-Audit 2026-07-01: der Excel-Import las die komplette Datei
# ohne Limit in den Speicher (im Unterschied zum Image-Proxy, der
# image_proxy_max_bytes durchsetzt) — ein einfacher Memory-DoS-Vektor ueber
# eine sehr grosse Datei. 10 MiB sind grosszuegig fuer eine Channel-
# Whitelist-Tabelle.
_MAX_IMPORT_EXCEL_BYTES = 10 * 1024 * 1024


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

    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > _MAX_IMPORT_EXCEL_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Datei zu groß (Limit {_MAX_IMPORT_EXCEL_BYTES // (1024 * 1024)} MiB).",
            )
        chunks.append(chunk)

    result = import_channels_from_excel(session, b"".join(chunks))
    return result
