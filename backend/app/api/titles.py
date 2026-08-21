import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from app.database import engine, get_session
from app.models.entities import CandidateStatus, Title, TitleCandidate, TitleKeyword, TitleSyncRun
from app.schemas.dto import (
    KeywordCreate,
    TitleCandidateCreateFromAsset,
    TitleCandidatePatch,
    TitleCreate,
    TitleSyncRequest,
)
from app.admin_session import require_admin_session
from app.services.candidate_autopilot import run_candidate_autopilot
from app.services.seeds import seed_titles
from app.services.title_candidates import create_candidate_from_asset
from app.services.title_rematch import rematch_unassigned_assets
from app.services.title_sync import sync_titles_from_tmdb

logger = logging.getLogger(__name__)

# Sprint 28.05.2026 (Admin-Login): Router-Level-Dependency.
router = APIRouter(
    prefix="/api/titles",
    tags=["titles"],
    dependencies=[Depends(require_admin_session)],
)


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


# Manueller Titel-Sync als Hintergrund-Lauf (21.08.2026). Vorher wartete
# der Handler synchron auf den kompletten Company-Discover (viele Minuten,
# TMDb-Pagination bis Seite 100+ je Studio-Paar) — der Browser-Fetch des
# Admin-Buttons starb dabei mit "Failed to fetch", obwohl der Sync auf dem
# Server weiterlief. Jetzt: sofort 202, Arbeit im BackgroundTask (Muster
# von POST /api/admin/cron/sync-all), Fortschritt steht wie bisher in
# ``TitleSyncRun`` (GET /sync/runs — das Frontend pollt darauf).
#
# Der Rematch haengt hier server-seitig an: vorher kettete das Frontend
# ihn nach der Sync-Antwort an — die es beim Hintergrund-Lauf nicht mehr
# abwartet. Ohne Rematch wuerden frisch gesyncte Titel erst am Montag
# zugeordnet.


def _title_sync_timeout_seconds() -> float:
    """Gleiche Stellschraube wie die Cron-Stage (``TITLE_SYNC_STAGE_
    TIMEOUT_SECONDS``, Default 1800s) — der manuelle Lauf macht exakt
    dieselbe Arbeit und verdient denselben Deckel."""
    try:
        return float(os.getenv("TITLE_SYNC_STAGE_TIMEOUT_SECONDS", "1800"))
    except ValueError:
        return 1800.0


def _reap_stale_title_sync_runs(session: Session, *, max_age_seconds: float) -> int:
    """``running``-Rows aelter als ``max_age_seconds`` auf ``error`` setzen.

    Eine Row bleibt haengen, wenn ein Deploy-Neustart oder Absturz den
    Lauf mitten im Discover killt (die Batch-Commits davor bleiben
    erhalten — der Sync ist idempotent und einfach wiederholbar). Ohne
    Aufraeumen wuerde der Doppelstart-Schutz unten jeden weiteren Sync
    fuer immer mit 409 abweisen."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    reaped = 0
    for run in session.exec(
        select(TitleSyncRun).where(TitleSyncRun.status == "running")
    ).all():
        created = run.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created < cutoff:
            run.status = "error"
            run.error_message = (
                f"running-Row aelter als {max_age_seconds:.0f}s aufgeraeumt "
                f"(Deploy-Neustart oder Absturz waehrend des Laufs)"
            )
            session.add(run)
            reaped += 1
    if reaped:
        session.commit()
    return reaped


def _rematch_with_own_session() -> dict:
    """Rematch im Thread mit eigener Session — die Request-Session ist
    beim Hintergrund-Lauf laengst geschlossen."""
    with Session(engine) as session:
        return rematch_unassigned_assets(session).to_dict()


async def _run_title_sync_background(
    markets: list[str] | None,
    pairs: list[str] | None,
    *,
    session_factory=None,
    rematch=_rematch_with_own_session,
) -> None:
    """Hintergrund-Koerper: Sync (mit Zeitdeckel) → Rematch. Fehler landen
    im Log und in der ``TitleSyncRun``-Row (setzt der Service selbst);
    ein Timeout raeumt die eigene running-Row auf."""
    factory = session_factory or (lambda: Session(engine))
    timeout_s = _title_sync_timeout_seconds()
    try:
        with factory() as session:
            result = await asyncio.wait_for(
                sync_titles_from_tmdb(session, markets=markets, pairs=pairs),
                timeout=timeout_s,
            )
            logger.info(
                "title_sync.manual.complete fetched=%s upserted=%s deduped=%s",
                result.get("fetched_count"),
                result.get("upserted_count"),
                result.get("deduped_count"),
            )
        rematch_result = await asyncio.to_thread(rematch)
        logger.info("title_sync.manual.rematch %s", rematch_result)
    except asyncio.TimeoutError:
        logger.error("manueller Titel-Sync nach %ss abgebrochen", timeout_s)
        # Die eigene Row ist jetzt ~timeout_s alt; der Puffer von 120s
        # verschont einen gerade frisch gestarteten parallelen Lauf.
        with factory() as session:
            _reap_stale_title_sync_runs(
                session, max_age_seconds=max(timeout_s - 120, 0)
            )
    except Exception:  # noqa: BLE001 — Task-Grenze, es gibt keinen Aufrufer mehr
        logger.exception("manueller Titel-Sync fehlgeschlagen")


@router.post("/sync/tmdb", status_code=202)
async def sync_tmdb(
    payload: TitleSyncRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    stale_after = _title_sync_timeout_seconds() + 300
    _reap_stale_title_sync_runs(session, max_age_seconds=stale_after)

    running = session.exec(
        select(TitleSyncRun).where(TitleSyncRun.status == "running")
    ).first()
    if running:
        raise HTTPException(
            status_code=409,
            detail=(
                "Ein Titel-Sync läuft bereits (gestartet "
                f"{running.created_at:%H:%M} UTC). Fortschritt unter "
                "GET /api/titles/sync/runs."
            ),
        )

    background_tasks.add_task(_run_title_sync_background, payload.markets, payload.pairs)
    return {
        "started": True,
        "status": "running",
        "message": (
            "Titel-Sync läuft im Hintergrund (einige Minuten); der Rematch "
            "folgt direkt danach. Fortschritt: GET /api/titles/sync/runs."
        ),
    }


@router.get("/sync/runs")
def list_sync_runs(session: Session = Depends(get_session)):
    return session.exec(select(TitleSyncRun).order_by(TitleSyncRun.created_at.desc())).all()


@router.post("/candidates/from-asset/{asset_id}")
def create_candidate(asset_id: UUID, payload: TitleCandidateCreateFromAsset | None = None, session: Session = Depends(get_session)):
    try:
        # User-Request-Pfad: der Admin will explizit einen Candidate
        # anlegen (ggf. mit eigenem suggested_title aus payload). Hier
        # KEIN Guess-Skip — der User-Intent ueberstimmt die
        # Whitelist-Match-Bedingung.
        candidate = create_candidate_from_asset(
            session, asset_id, skip_if_guess_only=False,
        )
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


@router.post("/candidates/autopilot")
def run_candidates_autopilot(session: Session = Depends(get_session)):
    """Kandidaten-Autopilot on-demand (Sprint Review-Automatisierung
    2026-07-20): bestaetigt offene Titel-Vorschlaege mit eindeutigem
    Exakt-Treffer in der Whitelist und schliesst Karteileichen — dieselbe
    Logik, die im woechentlichen Cron nach dem Rematch laeuft. Der
    Admin-Button dafuer lebt in "Quellen" neben dem Rematch; gedacht
    v. a. fuer den initialen Abbau des Alt-Backlogs."""
    summary = run_candidate_autopilot(session)
    return summary.to_dict()
