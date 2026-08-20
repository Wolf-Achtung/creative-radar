"""Playbook-Montags-Mail (20.08.2026) — Auswahl, Rendering, Gates.

Die Mail ist das Produkt: Empfehlungen, die ankommen, statt abgeholt
zu werden. Getestet werden die drei Versand-Gates (Flag, Empfaenger,
nichts-zu-berichten), die Auswahl-Logik (Zwilling der Panel-Karten)
und dass der Text die Zahlen traegt, mit denen die Empfehlung steht
und faellt.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.models.entities import PatternBriefing
from app.services import pattern_playbook as pp

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _zelle(value, *, verdict="over", z=2.5, trend=None, vorwoche=None):
    return {
        "value": value,
        "sample_size": 20,
        "channel_count": 4,
        "breakout_rate": 0.2,
        "expected_breakout_rate": 0.13,
        "breakout_z": z,
        "breakout_verdict": verdict,
        "trend": trend,
        "vorwoche": vorwoche,
    }


# ---------------------------------------------------------------------
# 1 — Auswahl (Zwilling der Panel-Karten)
# ---------------------------------------------------------------------


def test_staerkste_befunde_sortiert_nach_signalstaerke_ohne_neutral():
    daten = {
        "dimensions": {
            "format": [
                _zelle("behind_the_scenes", z=2.6),
                _zelle("promo", verdict="neutral", z=0.2),
            ],
            "lifecycle_stage": [_zelle("evergreen", verdict="under", z=-3.4)],
        }
    }
    befunde = pp.staerkste_befunde(daten)
    assert [e["cell"]["value"] for e in befunde] == [
        "evergreen", "behind_the_scenes",
    ]


def test_bewegungen_wechsel_vor_neuzugang_stabil_nie():
    daten = {
        "dimensions": {
            "format": [
                _zelle("clip", verdict="under", z=-9.9, trend="neu"),
                _zelle(
                    "behind_the_scenes", z=2.6, trend="gewechselt",
                    vorwoche={"breakout_rate": 0.14, "breakout_verdict": "neutral"},
                ),
                _zelle("trailer", z=2.2, trend="stabil"),
                _zelle("promo", verdict="neutral", z=0.2, trend="neu"),
            ]
        }
    }
    liste = pp.bewegungen(daten)
    assert [(e["cell"]["value"], e["art"]) for e in liste] == [
        ("behind_the_scenes", "gewechselt"),
        ("clip", "neu"),
    ]


# ---------------------------------------------------------------------
# 2 — Rendering: die Zahlen stehen im Text
# ---------------------------------------------------------------------


def _playbook_minimal(**overrides):
    pb = {
        "iso_year": 2026, "iso_week": 34, "window_days": 90,
        "posts_with_baseline": 5804, "channels_covered": 181,
        "befunde": [{"dim": "format", "cell": _zelle("behind_the_scenes")}],
        "bewegungen": [], "bausteine": {}, "notes": [],
    }
    pb.update(overrides)
    return pb


def test_render_traegt_zahlen_und_richtung():
    subject, text = pp.render_playbook(_playbook_minimal())
    assert "KW 34/2026" in subject
    assert "Behind-the-Scenes" in subject
    assert "Mehr davon testen" in text
    assert "20.0 % statt erwarteter 13.0 %" in text
    assert "5804 Posts" in text
    assert "kein Beweis fuer Ursache und Wirkung" in text.replace("\n", " ")


def test_render_bausteine_mit_hooks_und_belegen():
    pb = _playbook_minimal(bausteine={
        "genre": [{
            "muster": "Romance auf TikTok",
            "hooks_de": ["Hook eins", "Hook zwei"],
            "cited_post_ids": ["https://x.test/p/1"],
        }],
    })
    _, text = pp.render_playbook(pb)
    assert "TEXT-BAUSTEINE (GENRE-MUSTER)" in text
    assert "Romance auf TikTok" in text
    assert "Hook: Hook eins" in text
    assert "Beleg: https://x.test/p/1" in text


# ---------------------------------------------------------------------
# 3 — Versand-Gates
# ---------------------------------------------------------------------


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
def gesendete(monkeypatch):
    """Faengt send_mail ab — kein echter Versand im Test."""
    calls: list[dict] = []

    async def _fake_send(*, to, subject, text, html=None):
        calls.append({"to": to, "subject": subject, "text": text})

    monkeypatch.setattr(pp, "send_mail", _fake_send)
    return calls


@pytest.mark.anyio
async def test_flag_aus_kein_playbook(session, gesendete, monkeypatch):
    monkeypatch.delenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", raising=False)
    monkeypatch.setattr(settings, "playbook_mail_recipients", "a@x.test")
    summary = await pp.send_pattern_playbook(session, now=NOW)
    assert summary == {
        "skipped": True, "sent": 0, "failed": 0, "reason": "feature_flag_off",
    }
    assert gesendete == []


@pytest.mark.anyio
async def test_ohne_empfaenger_kein_versand(session, gesendete, monkeypatch):
    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")
    monkeypatch.setattr(settings, "playbook_mail_recipients", "  ")
    summary = await pp.send_pattern_playbook(session, now=NOW)
    assert summary["reason"] == "no_recipients"
    assert gesendete == []


@pytest.mark.anyio
async def test_nichts_zu_berichten_keine_mail(session, gesendete, monkeypatch):
    """Leere Datenbank: keine Befunde, keine Bewegung, keine Bausteine —
    eine leere Mail trainiert Ignorieren, also gibt es keine."""
    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")
    monkeypatch.setattr(settings, "playbook_mail_recipients", "a@x.test")
    summary = await pp.send_pattern_playbook(session, now=NOW)
    assert summary["reason"] == "nichts_zu_berichten"
    assert gesendete == []


@pytest.mark.anyio
async def test_versand_an_alle_empfaenger_mit_bausteinen(
    session, gesendete, monkeypatch
):
    """Mit persistiertem Briefing dieser Woche geht die Mail raus — an
    jeden Empfaenger der Komma-Liste, mit den Bausteinen im Text."""
    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")
    monkeypatch.setattr(
        settings, "playbook_mail_recipients", "a@x.test, b@x.test"
    )
    iso = NOW.isocalendar()
    session.add(PatternBriefing(
        mode="genre", iso_year=iso.year, iso_week=iso.week, window_days=90,
        evidence={}, llm_output={
            "bausteine": [{
                "muster": "Romance auf TikTok",
                "hooks_de": ["Hook eins"],
                "cited_post_ids": ["https://x.test/p/1"],
            }],
            "data_caveats": [],
        },
        generated_at=NOW, model="test",
    ))
    session.commit()

    summary = await pp.send_pattern_playbook(session, now=NOW)

    assert summary["sent"] == 2 and summary["failed"] == 0
    assert [c["to"] for c in gesendete] == ["a@x.test", "b@x.test"]
    assert "Romance auf TikTok" in gesendete[0]["text"]
    assert "Playbook" in gesendete[0]["subject"]


@pytest.mark.anyio
async def test_briefing_aelterer_woche_zaehlt_nicht(
    session, gesendete, monkeypatch
):
    """Bausteine der VORWOCHE gehoeren nicht in die Montags-Mail — die
    standen schon in der letzten."""
    monkeypatch.setenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "true")
    monkeypatch.setattr(settings, "playbook_mail_recipients", "a@x.test")
    session.add(PatternBriefing(
        mode="genre", iso_year=2026, iso_week=33, window_days=90,
        evidence={}, llm_output={"bausteine": [{"muster": "Alt"}],
                                 "data_caveats": []},
        generated_at=NOW, model="test",
    ))
    session.commit()

    summary = await pp.send_pattern_playbook(session, now=NOW)

    assert summary["reason"] == "nichts_zu_berichten"
    assert gesendete == []
