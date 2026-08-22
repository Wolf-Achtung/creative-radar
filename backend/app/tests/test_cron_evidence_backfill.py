"""Evidence-Backfill als Cron-Stage (22.08.2026).

Der Scrape captured jedes neue Asset sofort (Sprint 5.3.6) — transiente
Fehler liessen Assets aber dauerhaft ohne gespeichertes Bild zurueck,
und Instagram-CDN-Links verfallen nach 24-48 h. Der Backfill holt
Captures fuer JUNGE Assets nach.

Vertragspunkte:
- Nur Assets OHNE Evidence und JUENGER als max_age_days.
- Zeit-Budget VOR jedem Asset geprueft (Vision-Lektion 10./20.08.) —
  was nicht passt, steht ehrlich als skipped_budget im Ergebnis.
- Stueckzahl-Deckel + uebrig.
- Kill-Switch skippt ohne Capture-Versuch; Stage-Fehler kippt den
  Lauf nicht; im Hintergrund-Lauf verdrahtet.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import cron as cron_module
from app.config import settings
from app.models.entities import Asset, Channel, Market, Post
from app.services import asset_screenshot_persistence as persistence


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


def _asset(session, *, alter_tage=0, evidence=None):
    kanal = session.get(Channel, getattr(_asset, "_kanal_id", None))
    if kanal is None:
        kanal = Channel(
            name=f"ch-{uuid4().hex[:6]}", platform="instagram",
            url=f"https://x.test/{uuid4()}", market=Market.US,
        )
        session.add(kanal)
        session.commit()
        session.refresh(kanal)
        _asset._kanal_id = kanal.id
    post = Post(
        channel_id=kanal.id, platform="instagram",
        post_url=f"https://x.test/p/{uuid4()}", caption="x",
        detected_at=datetime.now(timezone.utc), raw_payload={},
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    asset = Asset(
        post_id=post.id,
        visual_evidence_url=evidence,
        thumbnail_url="https://scontent.cdninstagram.com/x.jpg",
        created_at=datetime.now(timezone.utc) - timedelta(days=alter_tage),
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def _capture_stub(erfolge: list):
    async def _stub(asset):
        erfolge.append(asset.id)
        asset.visual_evidence_url = f"evidence/{asset.id}.jpg"
    return _stub


async def test_nur_junge_assets_ohne_evidence_werden_gecaptured(session, monkeypatch):
    offen = _asset(session, alter_tage=2)
    zu_alt = _asset(session, alter_tage=30)
    fertig = _asset(session, alter_tage=1, evidence="evidence/da.jpg")
    versuche = []
    monkeypatch.setattr(persistence, "persist_asset_screenshot_async", _capture_stub(versuche))

    ergebnis = await persistence.backfill_missing_evidence(session)

    session.refresh(offen)
    assert versuche == [offen.id], (
        "Alt-Assets (CDN laengst verfallen) und bereits gecapturte "
        "duerfen keinen Fetch mehr ausloesen."
    )
    assert offen.visual_evidence_url
    assert zu_alt.visual_evidence_url is None and fertig.visual_evidence_url == "evidence/da.jpg"
    assert ergebnis["captured"] == 1 and ergebnis["kandidaten"] == 1


async def test_zeit_budget_wird_vor_jedem_asset_geprueft(session, monkeypatch):
    for _ in range(3):
        _asset(session, alter_tage=1)
    versuche = []
    monkeypatch.setattr(persistence, "persist_asset_screenshot_async", _capture_stub(versuche))

    ergebnis = await persistence.backfill_missing_evidence(session, budget_seconds=-1)

    assert versuche == [], "Budget 0 heisst: kein einziger Fetch — Pruefung VOR dem Asset."
    assert ergebnis["skipped_budget"] == 3, (
        "Was nicht mehr ins Zeitfenster passt, muss ehrlich als "
        "skipped_budget erscheinen — stilles Liegenlassen war der "
        "Vision-Fehler vom 10.08."
    )


async def test_stueckzahl_deckel_und_uebrig(session, monkeypatch):
    for _ in range(4):
        _asset(session, alter_tage=1)
    versuche = []
    monkeypatch.setattr(persistence, "persist_asset_screenshot_async", _capture_stub(versuche))

    ergebnis = await persistence.backfill_missing_evidence(session, max_assets=2)

    assert len(versuche) == 2
    assert ergebnis["uebrig"] == 2


def test_kill_switch_skippt_ohne_capture(session, monkeypatch):
    monkeypatch.setattr(settings, "evidence_backfill_in_cron", False, raising=False)

    ergebnis = asyncio.run(cron_module._run_evidence_backfill_stage(session))

    assert ergebnis == {"skipped": True, "reason": "disabled"}


def test_stage_fehler_kippt_den_lauf_nicht(session, monkeypatch):
    monkeypatch.setattr(settings, "evidence_backfill_in_cron", True, raising=False)

    async def _kaputt(session_, **kwargs):
        raise RuntimeError("storage down")

    monkeypatch.setattr(cron_module, "backfill_missing_evidence", _kaputt)

    ergebnis = asyncio.run(cron_module._run_evidence_backfill_stage(session))

    assert "storage down" in ergebnis["error"]


def test_hintergrund_lauf_ruft_die_stage():
    quelle = inspect.getsource(cron_module._run_cron_sync_background_impl)
    assert 'summary["evidence_backfill"] = await _run_evidence_backfill_stage(session)' in quelle, (
        "Ohne Verdrahtung bleiben transiente Scrape-Fehler dauerhaft "
        "bilderlos — genau die kaputten Vorschaubilder vom 21.08."
    )
