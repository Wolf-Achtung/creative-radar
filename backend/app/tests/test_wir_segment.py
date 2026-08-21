"""Wir-Segment Schritt 1 (21.08.2026) — empfohlen → gemacht → gewirkt.

Getestet wird die Verknuepfungs-Logik, nicht die Muster-Statistik (die
hat ihre eigenen Tests in test_trailer_patterns): Bericht und Kontext
werden gestubbt, gemessen wird, dass

- nur ``breakout_verdict == "over"``-Zellen als "empfohlen" zaehlen
  (dieselbe MACHEN-Auswahl wie das Playbook),
- "gemacht" ausschliesslich Posts der ``is_own``-Kanaele zaehlt,
- "gewirkt" der Median-Lift genau dieser Posts ist,
- ohne markierte Kanaele ein Klartext-Hinweis kommt statt Zahlen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Channel, Market
from app.services import wir_segment as ws

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


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


def _kanal(session, *, is_own=False):
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}", platform="tiktok",
        url=f"https://x.test/{uuid4()}", market=Market.US, is_own=is_own,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


@dataclass
class _FakePost:
    id: object
    channel_id: object


@dataclass
class _FakeCtx:
    usable: list = field(default_factory=list)
    lift_by_post: dict = field(default_factory=dict)


def _zelle(value, verdict, *, z=3.0, lift=1.2):
    return SimpleNamespace(
        value=value, breakout_verdict=verdict, breakout_z=z, median_lift=lift,
    )


def _stub_statistik(monkeypatch, ctx, dimensions, members_by_cell):
    monkeypatch.setattr(ws, "build_lift_context", lambda *a, **k: ctx)
    monkeypatch.setattr(
        ws, "compute_trailer_patterns",
        lambda *a, **k: SimpleNamespace(dimensions=dimensions),
    )
    monkeypatch.setattr(
        ws, "posts_for_cell",
        lambda session_, ctx_, dim, value: members_by_cell.get((dim, value), []),
    )


def test_ohne_markierte_kanaele_kommt_klartext_statt_zahlen(session):
    _kanal(session, is_own=False)

    ergebnis = ws.compute_wir_segment(session, now=NOW)

    assert ergebnis["own_channels"] == 0
    assert ergebnis["zeilen"] == []
    assert "Wir-Kanäle" in ergebnis["note"]


def test_gemacht_zaehlt_nur_eigene_posts_und_gewirkt_ist_ihr_median(
    session, monkeypatch
):
    wir = _kanal(session, is_own=True)
    fremd = _kanal(session, is_own=False)
    p_wir_1 = _FakePost(id="w1", channel_id=wir.id)
    p_wir_2 = _FakePost(id="w2", channel_id=wir.id)
    p_fremd = _FakePost(id="f1", channel_id=fremd.id)
    ctx = _FakeCtx(
        usable=[p_wir_1, p_wir_2, p_fremd],
        lift_by_post={"w1": 2.0, "w2": 1.0, "f1": 9.0},
    )
    _stub_statistik(
        monkeypatch, ctx,
        {"genre": [_zelle("SciFi", "over", lift=1.18)]},
        {("genre", "SciFi"): [p_wir_1, p_wir_2, p_fremd]},
    )

    ergebnis = ws.compute_wir_segment(session, now=NOW)

    assert ergebnis["own_channels"] == 1
    assert ergebnis["eigene_posts_im_fenster"] == 2
    [zeile] = ergebnis["zeilen"]
    assert zeile["gemacht"] == 2, "Fremde Posts duerfen nicht als 'gemacht' zaehlen."
    assert zeile["gewirkt_median_lift"] == 1.5, (
        "Median der EIGENEN Lifts (2.0, 1.0) — der fremde 9.0-Post "
        "darf das Ergebnis nicht schoenen."
    )
    assert zeile["empfohlen_median_lift"] == 1.18


def test_nur_over_zellen_zaehlen_als_empfohlen(session, monkeypatch):
    wir = _kanal(session, is_own=True)
    post = _FakePost(id="w1", channel_id=wir.id)
    ctx = _FakeCtx(usable=[post], lift_by_post={"w1": 1.0})
    _stub_statistik(
        monkeypatch, ctx,
        {"genre": [
            _zelle("SciFi", "over"),
            _zelle("Crime", "under"),
            _zelle("Fantasy", "neutral"),
            _zelle("Doku", "insufficient"),
        ]},
        {("genre", v): [post] for v in ["SciFi", "Crime", "Fantasy", "Doku"]},
    )

    ergebnis = ws.compute_wir_segment(session, now=NOW)

    assert [z["value"] for z in ergebnis["zeilen"]] == ["SciFi"], (
        "Empfohlen sind NUR die over-Zellen — dieselbe MACHEN-Auswahl "
        "wie im Playbook; under/neutral/insufficient sind keine "
        "Empfehlung zum Nachmachen."
    )


def test_empfehlung_ohne_eigene_posts_erscheint_mit_gemacht_null(
    session, monkeypatch
):
    _kanal(session, is_own=True)
    fremd = _kanal(session, is_own=False)
    post = _FakePost(id="f1", channel_id=fremd.id)
    ctx = _FakeCtx(usable=[post], lift_by_post={"f1": 2.0})
    _stub_statistik(
        monkeypatch, ctx,
        {"genre": [_zelle("SciFi", "over")]},
        {("genre", "SciFi"): [post]},
    )

    ergebnis = ws.compute_wir_segment(session, now=NOW)

    [zeile] = ergebnis["zeilen"]
    assert zeile["gemacht"] == 0
    assert zeile["gewirkt_median_lift"] is None, (
        "Nicht gemacht heisst: keine Wirkungs-Zahl — die Luecke IST die "
        "Aussage (Empfehlung, die wir noch nicht spielen)."
    )
