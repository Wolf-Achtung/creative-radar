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
    TitlePatch,
    TitleSyncRequest,
    TmdbAnlegen,
)
from app.admin_session import require_admin_session
from app.core.feature_flags import (
    is_katalog_nachladen_enabled,
    is_wir_projekte_enabled,
)
from app.services.candidate_autopilot import (
    _build_exact_title_lookup,
    _normalize,
    run_candidate_autopilot,
)
from app.services.candidate_llm_assist import run_candidate_llm_assist
from app.services.katalog_nachladen import (
    lade_fehlende_titel_nach,
    titel_aus_tmdb_anlegen,
    tmdb_auswahl_fuer_name,
)
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
    """Legt einen Titel an — oder gibt den vorhandenen zurueck.

    Bis zum 25.08.2026 legte jeder Aufruf eine neue Zeile an, ohne zu
    schauen. Der Knopf "Titel anlegen" in der Pruef-Queue wird aber bei
    jedem Kandidaten desselben Werks gedrueckt: drei Posts zu
    "Lanterns" ergaben drei Katalog-Zeilen. Zwei davon blieben ohne ein
    einziges Asset — und der Name war fortan MEHRDEUTIG.

    Das ist teurer als es aussieht. ``_build_exact_title_lookup``
    markiert mehrdeutige Namen als "Menschensache"; Autopilot,
    KI-Assist und Katalog-Nachladen lassen sie danach alle liegen. Ein
    Doppelklick legt also nicht nur eine ueberzaehlige Zeile an, er
    schaltet die Automatik fuer diesen Namen dauerhaft ab.

    Jetzt entscheidet derselbe Lookup, den auch die Automatik liest:

    - genau ein aktiver Titel gleichen Normalnamens -> dieser wird
      zurueckgegeben, keine neue Zeile. Das Frontend nimmt ``title.id``
      und ordnet zu; fuer den Klickenden aendert sich nichts.
    - bereits mehrdeutig -> 409. Eine dritte Zeile machte es schlimmer;
      hier muss jemand aufraeumen.
    - kein Treffer -> anlegen wie bisher.

    Mitgegebene Keywords landen auch am vorhandenen Titel, sofern sie
    dort fehlen — sonst ginge die Eingabe stillschweigend verloren.
    """
    data = payload.model_dump(exclude={"keywords"})
    name = _normalize(data.get("title_original") or "")
    if name:
        lookup = _build_exact_title_lookup(session)
        if name in lookup:
            vorhanden = lookup[name]
            if vorhanden is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"'{data.get('title_original')}' steht mehrfach im "
                        "Katalog. Eine weitere Zeile macht es schlimmer — "
                        "erst zusammenlegen (scripts.titel_doubletten_"
                        "aufraeumen), dann erneut versuchen."
                    ),
                )
            _keywords_ergaenzen(session, vorhanden, payload.keywords)
            # Gleiche Falle wie unten: ein Commit in ``_keywords_
            # ergaenzen`` macht die Instanz stale, und die Antwort waere
            # wieder "{}" — der Fehler, den dieser PR gerade behebt.
            session.refresh(vorhanden)
            return vorhanden

    title = Title(**data)
    session.add(title)
    session.commit()
    session.refresh(title)
    for keyword in payload.keywords:
        session.add(TitleKeyword(title_id=title.id, keyword=keyword))
        session.commit()
    # Ohne dieses Refresh antwortet der Endpoint "{}" — und zwar seit
    # jeher. Der Commit fuer die Keywords laeuft danach, und ein Commit
    # macht die Instanz stale; FastAPI serialisiert dann ein leeres
    # ``__dict__``.
    #
    # Die Folge war kein Schoenheitsfehler. Das Frontend liest
    # ``title.id`` aus der Antwort und schickt es an ``reviewAsset``.
    # ``undefined`` faellt bei JSON.stringify raus, das Asset bekam also
    # KEINEN Titel — waehrend der Kandidat auf "resolved" gesetzt wurde
    # und der Toast "neu angelegt und zugeordnet" meldete. Genau so
    # entstanden die 74 geschlossenen Kandidaten mit titellosem Asset,
    # die die Diagnose vom 25.08.2026 fand, und die Manual-Titel mit
    # assets=0 daneben.
    session.refresh(title)
    return title


def _keywords_ergaenzen(session: Session, titel: Title, keywords: list[str]) -> None:
    """Nur was fehlt — ein zweiter Aufruf mit denselben Keywords darf
    keine Dubletten in der Keyword-Tabelle hinterlassen."""
    if not keywords:
        return
    vorhandene = {
        k.keyword
        for k in session.exec(
            select(TitleKeyword).where(TitleKeyword.title_id == titel.id)
        ).all()
    }
    neu = [k for k in keywords if k not in vorhandene]
    if not neu:
        return
    for keyword in neu:
        session.add(TitleKeyword(title_id=titel.id, keyword=keyword))
    session.commit()


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


# Wir-Projekte (22.08.2026): bewusst NACH den statischen Pfaden
# (/candidates, /sync, /stats) registriert — die Route-Reihenfolge haelt
# den {title_id}-Catch-all hinter den spezifischen Endpunkten.
# Feature-Flag-Gate (Arbeitsregel 23.08.2026): das einzige Patch-Feld
# ist die Wir-Projekt-Markierung, deshalb gate't ihr Flag den ganzen
# Endpoint.
@router.patch("/{title_id}")
def patch_title(title_id: UUID, payload: TitlePatch, session: Session = Depends(get_session)):
    if not is_wir_projekte_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Die Wir-Projekt-Markierung ist deaktiviert. "
                "FEATURE_WIR_PROJEKTE_ENABLED muss in Railway-ENV auf 'true' gesetzt sein."
            ),
        )
    title = session.get(Title, title_id)
    if not title:
        raise HTTPException(status_code=404, detail="Title not found")
    data = payload.model_dump(exclude_none=True)
    for key, value in data.items():
        setattr(title, key, value)
    session.add(title)
    session.commit()
    session.refresh(title)
    return title


@router.get("/stats/whitelist")
def whitelist_stats(session: Session = Depends(get_session)):
    active_titles = len(session.exec(select(Title).where(Title.active == True)).all())  # noqa: E712
    latest_run = session.exec(select(TitleSyncRun).order_by(TitleSyncRun.created_at.desc())).first()
    open_candidates = len(session.exec(select(TitleCandidate).where(TitleCandidate.status == CandidateStatus.OPEN)).all())
    # Wolfs Befund 31.08.2026: die Kachel meldete "Neue Titel diese
    # Woche: 37.978" bei 19.464 aktiven Titeln — mehr "neue" als
    # ueberhaupt vorhanden. Sie las ``latest_run.upserted_count``, und
    # der zaehlt jeden Upsert des letzten Sync-Laufs, Insert UND Update.
    # Der Sync zieht woechentlich die vollen Slates aller Studios und
    # Streamer; fast alle Zeilen existieren bereits, und ein Titel wird
    # je Markt-Achse erneut angefasst. Beide Woerter im Label waren
    # falsch: nicht "neu" (ueberwiegend Aktualisierungen) und nicht
    # "diese Woche" (der letzte Lauf, wann immer der war). Sichtbar
    # wurde es daran, dass die Zahl ueber einen ganzen Tag unveraendert
    # stand, waehrend 29 echte Titel dazukamen.
    #
    # Jetzt die Frage, die das Label stellt: Titel-Zeilen, die in den
    # letzten sieben Tagen ANGELEGT wurden. Zaehlt beide Quellen —
    # Sync und Katalog-Nachladen bzw. Hand-Anlage aus der Queue.
    woche_zurueck = datetime.now(timezone.utc) - timedelta(days=7)
    new_titles_this_week = len(
        session.exec(
            select(Title).where(Title.created_at >= woche_zurueck)
        ).all()
    )
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


@router.post("/candidates/llm-assist")
def run_candidates_llm_assist(session: Session = Depends(get_session)):
    """Kandidaten-LLM-Assist (21.08.2026): loest die Rest-Kandidaten
    OHNE Exakt-Treffer per Haiku auf — den Teil, den der mechanische
    Autopilot bewusst ueberspringt ("beware" statt "Beware Boiúna").
    Batch je Aufruf (Default 12, ~30 s synchron); die Antwort nennt
    ``offen_danach`` — bei Bedarf einfach erneut klicken. Zuordnung nur
    bei ``sicher: true``, identisch zum manuellen Bestaetigen-Klick."""
    summary = run_candidate_llm_assist(session)
    return summary.to_dict()


@router.post("/katalog-nachladen")
async def run_katalog_nachladen(
    anwenden: bool = False,
    session: Session = Depends(get_session),
):
    """Legt Titel an, die ein beobachteter Post nachweislich bewirbt.

    Wolfs Befund vom 24.08.2026: Von 58 KI-gepruefter Vorschlaege liess
    sich genau EINER automatisch zuordnen — nicht weil die Pruefung
    schwach waere, sondern weil der Katalog die beworbenen Werke nicht
    kennt ("Desperate Housewives", "Lanterns", "Cadet Kelly"). Er deckt
    sechs Studios und drei Streamer ab, beobachtet werden ueber 200
    Kanaele.

    Dieser Pfad schliesst die Luecke bedarfsgetrieben: Was ein Post
    nachweislich bewirbt und TMDb eindeutig kennt, kommt in den Katalog.
    Drei Waechter im Code (Text-Beleg, eindeutiger TMDb-Treffer, nicht
    schon vorhanden) — Details in ``katalog_nachladen``.

    ``anwenden`` steuert Vorschau (Vorgabe) gegen Ernstfall. Die
    Vorschau laesst denselben Code vollstaendig durchlaufen und rollt am
    Ende zurueck — sie zeigt also, was passieren WUERDE, nicht was
    passieren sollte.

    Der Grund (25.08.2026): Dieses Feature ist in Staging nicht
    erprobbar. Seine Eingabe ist der Marker ``(nicht im Katalog)``, den
    nur die KI-Pruefung setzt — und die braucht den Anthropic-Key, den
    Staging bewusst nicht hat. Wolfs Freigabe-Modell "Staging zuerst"
    greift hier ins Leere; die Vorschau tritt an seine Stelle.

    Hinter ``FEATURE_KATALOG_NACHLADEN_ENABLED``: der Pfad legt Titel an
    und ordnet Assets zu (Arbeitsregel 23.08.2026).
    """
    if not is_katalog_nachladen_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Katalog-Nachladen ist nicht aktiv. Env-Var: "
                "FEATURE_KATALOG_NACHLADEN_ENABLED"
            ),
        )
    summary = await lade_fehlende_titel_nach(session, anwenden=anwenden)
    return summary.to_dict()


def _katalog_nachladen_oder_503() -> None:
    """Beide Auswahl-Endpoints gehoeren fachlich zum Katalog-Nachladen
    und teilen dessen Flag — Muster Trailer Intelligence: 503 nennt die
    Env-Var, damit die Meldung zugleich die Anleitung ist."""
    if not is_katalog_nachladen_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Katalog-Nachladen ist nicht aktiv. Env-Var: "
                "FEATURE_KATALOG_NACHLADEN_ENABLED"
            ),
        )


@router.get("/tmdb-auswahl")
async def get_tmdb_auswahl(name: str):
    """Exakte, aktuelle TMDb-Treffer zu einem Namen — auch mehrdeutige.

    Die menschliche Haelfte von Waechter 2 des Nachladens: der
    automatische Pfad verlangt GENAU EINEN Treffer und laesst
    mehrdeutige Namen liegen (31.08.2026: zehn von dreizehn
    Restfaellen). Dieser Endpoint legt die Kandidaten der Pruef-Queue
    zur Auswahl vor; ``POST /titles/tmdb-anlegen`` setzt die Wahl um.
    """
    _katalog_nachladen_oder_503()
    name = (name or "").strip()
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="Name zu kurz.")
    return {"treffer": await tmdb_auswahl_fuer_name(name)}


@router.post("/tmdb-anlegen")
async def post_tmdb_anlegen(
    payload: TmdbAnlegen, session: Session = Depends(get_session)
):
    """Setzt eine TMDb-Auswahl um: Titel anlegen (oder per ``tmdb_id``
    wiederverwenden), Asset zuordnen, Kandidat schliessen — ein Klick.
    Details und Schutzregeln in ``titel_aus_tmdb_anlegen``."""
    _katalog_nachladen_oder_503()
    try:
        return await titel_aus_tmdb_anlegen(
            session,
            asset_id=payload.asset_id,
            candidate_id=payload.candidate_id,
            tmdb_id=payload.tmdb_id,
            medium=payload.medium,
            name=payload.name,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/candidates/aufraeumen")
async def run_candidates_aufraeumen(session: Session = Depends(get_session)):
    """Die Kandidaten-Pipeline in EINEM Klick (Wolfs Befund 31.08.2026:
    zu viele Buttons, deren Reihenfolge man kennen muss — der Queue-Tag
    brauchte neun Klicks fuer das, was fachlich EIN Ablauf ist).

    Feste Reihenfolge, identisch zum Montags-Cron: Autopilot (exakte
    Treffer bestaetigen) → KI-Pruefung (Rest-Vorschlaege lesen) →
    Katalog-Nachladen scharf (frisch markierte Luecken aus TMDb
    schliessen). Jeder Schritt ist derselbe Code wie sein Einzel-Button
    und wie seine Cron-Stage; ein Fehler in einem Schritt faellt als
    ``error`` in die Antwort statt die uebrigen zu verhindern.

    Das Nachladen respektiert sein Feature-Flag: ohne Flag wird es als
    ``skipped`` gemeldet — die beiden anderen Schritte laufen trotzdem,
    denn sie sind lange freigegeben. Kein eigenes Flag: der Endpoint
    verkettet nur Bestehendes.
    """
    ergebnis: dict = {}
    try:
        ergebnis["autopilot"] = run_candidate_autopilot(session).to_dict()
    except Exception as exc:  # noqa: BLE001 — ein Schritt reisst nicht den Rest
        logger.exception("aufraeumen: autopilot fehlgeschlagen")
        ergebnis["autopilot"] = {"error": str(exc)[:300]}
    try:
        ergebnis["ki"] = run_candidate_llm_assist(session).to_dict()
    except Exception as exc:  # noqa: BLE001
        logger.exception("aufraeumen: ki-pruefung fehlgeschlagen")
        ergebnis["ki"] = {"error": str(exc)[:300]}
    if is_katalog_nachladen_enabled():
        try:
            nachladen = await lade_fehlende_titel_nach(session, anwenden=True)
            ergebnis["nachladen"] = nachladen.to_dict()
        except Exception as exc:  # noqa: BLE001
            logger.exception("aufraeumen: katalog-nachladen fehlgeschlagen")
            ergebnis["nachladen"] = {"error": str(exc)[:300]}
    else:
        ergebnis["nachladen"] = {"skipped": True, "reason": "feature_flag_disabled"}
    return ergebnis
