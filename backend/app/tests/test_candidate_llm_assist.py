"""Kandidaten-LLM-Assist (21.08.2026) — Haiku loest die Rest-Kandidaten
ohne Exakt-Treffer auf.

Sicherheits-Vertrag, den diese Tests festnageln:
- Zugeordnet wird NUR bei ``sicher: true`` UND gueltiger Auswahl aus
  der Code-Shortlist — das LLM kann keinen Titel nennen, den der Code
  nicht vorgeschlagen hat.
- Exakt-Treffer-Kandidaten gehen NICHT ans LLM (Revier des kostenlosen
  Autopiloten).
- Die Zuordnung ist identisch zum Autopiloten/manuellen Klick
  (title_id + de_us_match_key + resolve der offenen Kandidaten).
- Batch-Deckel je Lauf, Rest ehrlich als ``offen_danach``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import (
    Asset,
    CandidateStatus,
    Channel,
    Market,
    Post,
    Title,
    TitleCandidate,
)
from app.services import candidate_llm_assist as cla


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


@pytest.fixture(autouse=True)
def _anthropic_konfiguriert(monkeypatch):
    monkeypatch.setattr(cla, "is_anthropic_configured", lambda: True)
    monkeypatch.setattr(cla, "record_anthropic_call", lambda *a, **k: None)


class _Antwort:
    """Nachbau von JsonRetryResult mit leeren call_attempts."""

    def __init__(self, parsed):
        self.parsed = parsed
        self.call_attempts = []


def _fake_llm(monkeypatch, antworten):
    """call_with_json_retry-Stub: liefert Antworten in Reihenfolge und
    zeichnet die User-Prompts auf."""
    prompts: list[str] = []

    def _stub(*, model, system, user_message, **kwargs):
        prompts.append(user_message)
        return _Antwort(antworten[min(len(prompts) - 1, len(antworten) - 1)])

    monkeypatch.setattr(cla, "call_with_json_retry", _stub)
    return prompts


def _kanal(session):
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}", platform="instagram",
        url=f"https://x.test/{uuid4()}", market=Market.US,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


def _fall(session, kanal, *, caption, vorschlag, confidence=0.9):
    post = Post(
        channel_id=kanal.id, platform=kanal.platform,
        post_url=f"https://x.test/p/{uuid4()}", caption=caption,
        detected_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        raw_payload={},
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    asset = Asset(post_id=post.id)
    session.add(asset)
    session.commit()
    session.refresh(asset)
    cand = TitleCandidate(
        asset_id=asset.id, suggested_title=vorschlag, confidence=confidence,
    )
    session.add(cand)
    session.commit()
    session.refresh(cand)
    return asset, cand


def _titel(session, name, **kwargs):
    t = Title(title_original=name, active=True, **kwargs)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def test_sichere_auswahl_ordnet_wie_der_manuelle_klick_zu(session, monkeypatch):
    kanal = _kanal(session)
    titel = _titel(session, "Beware Boiúna")
    asset, cand = _fall(
        session, kanal,
        caption="BEWARE BOIÚNA — only in cinemas.", vorschlag="beware",
    )
    _fake_llm(monkeypatch, [{"auswahl": 1, "sicher": True, "begruendung": "Caption nennt den Titel."}])

    summary = cla.run_candidate_llm_assist(session)

    session.refresh(asset)
    session.refresh(cand)
    assert summary.zugeordnet == 1
    assert asset.title_id == titel.id
    assert asset.de_us_match_key, "Match-Key muss wie beim manuellen Klick gesetzt sein."
    assert cand.status != CandidateStatus.OPEN, "Offene Kandidaten des Assets muessen resolved sein."


def test_unsicher_bleibt_offen_und_schreibt_nichts(session, monkeypatch):
    kanal = _kanal(session)
    _titel(session, "Beware Boiúna")
    asset, cand = _fall(
        session, kanal, caption="beware of spoilers…", vorschlag="beware",
    )
    _fake_llm(monkeypatch, [{"auswahl": 1, "sicher": False, "begruendung": "Koennte auch was anderes sein."}])

    summary = cla.run_candidate_llm_assist(session)

    session.refresh(asset)
    session.refresh(cand)
    assert summary.zugeordnet == 0 and summary.unsicher == 1
    assert asset.title_id is None
    assert cand.status == CandidateStatus.OPEN


def test_auswahl_ausserhalb_der_shortlist_wird_verworfen(session, monkeypatch):
    kanal = _kanal(session)
    _titel(session, "Beware Boiúna")
    asset, _ = _fall(session, kanal, caption="beware", vorschlag="beware")
    _fake_llm(monkeypatch, [{"auswahl": 99, "sicher": True, "begruendung": "erfunden"}])

    summary = cla.run_candidate_llm_assist(session)

    session.refresh(asset)
    assert summary.zugeordnet == 0 and summary.unsicher == 1
    assert asset.title_id is None, (
        "Das LLM darf keinen Titel zuordnen, den die Code-Shortlist "
        "nicht vorgeschlagen hat."
    )


def test_exakt_treffer_gehen_nicht_ans_llm(session, monkeypatch):
    kanal = _kanal(session)
    _titel(session, "Wicked")
    _fall(session, kanal, caption="WICKED now.", vorschlag="Wicked")
    prompts = _fake_llm(monkeypatch, [{"auswahl": 1, "sicher": True, "begruendung": "x"}])

    summary = cla.run_candidate_llm_assist(session)

    assert prompts == [], (
        "Exakt-Treffer sind das Revier des kostenlosen Autopiloten — "
        "jeder LLM-Call dafuer waere verschwendetes Geld."
    )
    assert summary.geprueft == 0


def test_batch_deckel_und_offen_danach(session, monkeypatch):
    kanal = _kanal(session)
    _titel(session, "Beware Boiúna")
    for i in range(4):
        _fall(session, kanal, caption=f"beware {i}", vorschlag="beware")
    _fake_llm(monkeypatch, [{"auswahl": None, "sicher": False, "begruendung": "unklar"}])

    summary = cla.run_candidate_llm_assist(session, max_candidates=2)

    assert summary.geprueft == 2
    assert summary.offen_danach == 4, (
        "2 nicht angefasste + 2 unsichere muessen als offen ausgewiesen "
        "sein — stilles Liegenlassen taeuscht Vollstaendigkeit vor."
    )


def test_shortlist_findet_teil_treffer(session):
    titel = _titel(session, "Beware Boiúna")
    _titel(session, "Wicked")
    katalog = cla._katalog_eintraege(session)

    shortlist = cla._shortlist(katalog, cla._tokens("beware — trailer drop"))

    assert [t.title_original for t, _ in shortlist] == ["Beware Boiúna"]
    assert titel.id == shortlist[0][0].id


def test_ohne_anthropic_key_wird_uebersprungen(session, monkeypatch):
    monkeypatch.setattr(cla, "is_anthropic_configured", lambda: False)

    summary = cla.run_candidate_llm_assist(session)

    assert summary.skipped == "anthropic_not_configured"
    assert summary.geprueft == 0
