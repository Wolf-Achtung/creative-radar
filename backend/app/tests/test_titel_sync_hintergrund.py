"""Titel-Sync als Hintergrund-Lauf (21.08.2026).

Ausloeser: Wolfs Klick auf "Titelquellen aktualisieren" starb im Browser
mit "Failed to fetch", waehrend der Sync auf dem Server munter
weiterlief (TMDb-Pagination Seite 115+ im Log). Der Handler wartete
synchron auf einen Lauf, der viele Minuten dauert.

Der Umbau folgt dem Muster von ``POST /api/admin/cron/sync-all``:
sofort 202, Arbeit im BackgroundTask, Fortschritt in ``TitleSyncRun``.
Getestet werden die vier Vertragspunkte:

- 202 sofort, Hintergrund-Task geplant (nicht synchron gelaufen).
- Doppelstart -> 409, solange eine ``running``-Row existiert.
- Haengengebliebene ``running``-Rows (Deploy-Neustart/Absturz) werden
  beim naechsten Start aufgeraeumt statt fuer immer zu blockieren.
- Der Hintergrund-Koerper kettet den Rematch NACH dem Sync an — das
  machte vorher das Frontend, das die Antwort jetzt nicht mehr abwartet.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import TitleSyncRun


@pytest.fixture
def session():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        with Session(eng) as s:
            yield s
    finally:
        eng.dispose()


@pytest.fixture
def hintergrund_stub(monkeypatch):
    """Ersetzt den Hintergrund-Task durch einen Rekorder — die Tests
    pruefen die Planung, nicht den (anderswo getesteten) Sync selbst."""
    from app.api import titles as titles_module

    aufrufe: list[tuple] = []

    async def _stub(markets, pairs, **kwargs):
        aufrufe.append((markets, pairs))

    monkeypatch.setattr(titles_module, "_run_title_sync_background", _stub)
    return aufrufe


async def _post_sync(session, body=None):
    from app.admin_session import require_admin_session
    from app.database import get_session
    from app.main import app

    def _session_override():
        yield session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[require_admin_session] = lambda: None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/titles/sync/tmdb", json=body or {})
    finally:
        app.dependency_overrides.clear()


async def test_endpoint_antwortet_sofort_und_plant_den_hintergrund_lauf(
    session, hintergrund_stub
):
    antwort = await _post_sync(session, {"markets": ["DE", "US"]})

    assert antwort.status_code == 202
    daten = antwort.json()
    assert daten["started"] is True and daten["status"] == "running"
    # ASGITransport fuehrt BackgroundTasks nach der Antwort aus — der
    # Stub muss genau einmal mit den Request-Parametern gelaufen sein.
    assert hintergrund_stub == [(["DE", "US"], None)]


async def test_laufender_sync_wird_mit_409_abgewiesen(session, hintergrund_stub):
    session.add(TitleSyncRun(
        source="tmdb", markets=["DE"], status="running",
        date_from=datetime.now(timezone.utc).date(),
        date_to=datetime.now(timezone.utc).date(),
    ))
    session.commit()

    antwort = await _post_sync(session)

    assert antwort.status_code == 409
    assert hintergrund_stub == [], "Bei 409 darf kein zweiter Lauf starten."


async def test_haengengebliebene_running_row_blockiert_nicht_fuer_immer(
    session, hintergrund_stub
):
    """Deploy-Neustart mitten im Lauf hinterlaesst eine ewige
    ``running``-Row — genau das ist nach Wolfs abgebrochenem Lauf vom
    21.08. der Zustand in Production. Der naechste Start muss sie als
    ``error`` aufraeumen und selbst durchkommen."""
    alt = TitleSyncRun(
        source="tmdb", markets=["DE"], status="running",
        date_from=datetime.now(timezone.utc).date(),
        date_to=datetime.now(timezone.utc).date(),
        created_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )
    session.add(alt)
    session.commit()

    antwort = await _post_sync(session)

    assert antwort.status_code == 202
    assert len(hintergrund_stub) == 1
    session.refresh(alt)
    assert alt.status == "error"
    assert "aufgeraeumt" in (alt.error_message or "")


async def test_hintergrund_lauf_kettet_rematch_nach_dem_sync(session, monkeypatch):
    from app.api import titles as titles_module

    reihenfolge: list[str] = []

    async def _sync_stub(session_, markets=None, pairs=None):
        reihenfolge.append("sync")
        return {"fetched_count": 1, "upserted_count": 1, "deduped_count": 0}

    def _rematch_stub():
        reihenfolge.append("rematch")
        return {"auto_matched": 2}

    monkeypatch.setattr(titles_module, "sync_titles_from_tmdb", _sync_stub)

    await titles_module._run_title_sync_background(
        ["DE"], None,
        session_factory=lambda: nullcontext(session),
        rematch=_rematch_stub,
    )

    assert reihenfolge == ["sync", "rematch"], (
        "Ohne den angehaengten Rematch wuerden frisch gesyncte Titel "
        "erst am Montag zugeordnet."
    )


async def test_timeout_raeumt_die_eigene_running_row_auf(session, monkeypatch):
    from app.api import titles as titles_module

    monkeypatch.setenv("TITLE_SYNC_STAGE_TIMEOUT_SECONDS", "0.05")

    async def _haengt(session_, markets=None, pairs=None):
        # Haengt laenger als der Deckel — wait_for muss abbrechen.
        import asyncio
        session_.add(TitleSyncRun(
            source="tmdb", markets=["DE"], status="running",
            date_from=datetime.now(timezone.utc).date(),
            date_to=datetime.now(timezone.utc).date(),
            created_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
        session_.commit()
        await asyncio.sleep(1)

    monkeypatch.setattr(titles_module, "sync_titles_from_tmdb", _haengt)

    await titles_module._run_title_sync_background(
        ["DE"], None,
        session_factory=lambda: nullcontext(session),
        rematch=lambda: {},
    )

    laeufe = session.exec(select(TitleSyncRun)).all()
    assert laeufe and all(lauf.status == "error" for lauf in laeufe), (
        "Nach dem Timeout darf keine running-Row zurueckbleiben — sie "
        "wuerde jeden weiteren manuellen Sync mit 409 blockieren."
    )
