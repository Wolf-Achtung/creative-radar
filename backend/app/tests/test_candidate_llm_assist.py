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
    # confidence=1.0: ein echter Exakt-/Hashtag-Treffer. Der Default 0.9
    # steht seit dem 24.08.2026 fuer den schwachen Einzelwort-Substring —
    # und "Wicked" ist ein Ein-Wort-Titel, faellt also in die neue
    # Zweifelsfall-Ausnahme. Der Testzweck ist der sichere Treffer.
    _fall(session, kanal, caption="WICKED now.", vorschlag="Wicked",
          confidence=1.0)
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
    assert summary.offen_danach == 2, (
        "Die 2 nicht angefassten sind noch ungeprueft; die 2 unsicheren "
        "sind seit dem Fortschritts-Fix ERLEDIGT (Marker + Notiz) und "
        "gehoeren der Hand-Pruefung — sie duerfen nicht mehr als offen "
        "zaehlen, sonst verspricht die Meldung endlose Runden."
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


def test_gepruefte_kandidaten_werden_markiert_und_uebersprungen(
    session, monkeypatch
):
    """Wolfs Befund vom 21.08.: jeder Klick prüfte dieselben 12
    Kandidaten erneut. Ein unsicherer Kandidat muss Marker + Notiz
    tragen und beim naechsten Lauf ohne LLM-Call uebersprungen werden."""
    kanal = _kanal(session)
    _titel(session, "Beware Boiúna")
    _, cand = _fall(session, kanal, caption="beware…", vorschlag="beware")
    prompts = _fake_llm(monkeypatch, [{
        "auswahl": None, "sicher": False,
        "begruendung": "Caption nennt keinen Titel.",
    }])

    erster = cla.run_candidate_llm_assist(session)
    zweiter = cla.run_candidate_llm_assist(session)

    session.refresh(cand)
    assert erster.unsicher == 1
    assert cand.llm_checked_at is not None
    assert "Caption nennt keinen Titel" in (cand.llm_note or "")
    assert len(prompts) == 1, (
        "Der zweite Lauf darf den gepruefte Kandidaten NICHT erneut ans "
        "LLM geben — genau das war die Endlos-Schleife."
    )
    assert zweiter.bereits_geprueft == 1 and zweiter.geprueft == 0
    assert zweiter.offen_danach == 0


def test_string_zahlen_und_string_true_werden_akzeptiert(session, monkeypatch):
    """Haiku antwortet Auswahl/sicher gelegentlich als Strings — die
    strikte Typ-Pruefung liess am 21.08. JEDEN Treffer durchfallen."""
    kanal = _kanal(session)
    titel = _titel(session, "Beware Boiúna")
    asset, _ = _fall(
        session, kanal, caption="BEWARE BOIÚNA — trailer.", vorschlag="beware",
    )
    _fake_llm(monkeypatch, [{
        "auswahl": "1", "sicher": "true", "begruendung": "Caption nennt den Titel.",
    }])

    summary = cla.run_candidate_llm_assist(session)

    session.refresh(asset)
    assert summary.zugeordnet == 1
    assert asset.title_id == titel.id


def test_parse_fehler_setzt_keinen_marker(session, monkeypatch):
    """Ein API-/Parse-Fehler ist kein Urteil: der Kandidat bleibt
    unmarkiert und kommt beim naechsten Lauf wieder dran."""
    kanal = _kanal(session)
    _titel(session, "Beware Boiúna")
    _, cand = _fall(session, kanal, caption="beware", vorschlag="beware")
    _fake_llm(monkeypatch, [None])

    summary = cla.run_candidate_llm_assist(session)

    session.refresh(cand)
    assert summary.fehler == 1
    assert cand.llm_checked_at is None
    assert summary.offen_danach == 1, "Fehler-Faelle zaehlen als weiterhin ungeprueft."


# --- Ein-Wort-Zweifelsfaelle (Vorfall 24.08.2026) ------------------------
#
# Der Autopilot verlangt fuer Ein-Wort-Titel seit dem Vorfall Confidence
# >= 0.95 und laesst schwache Treffer liegen. Dieser Assist uebersprang
# sie bis dahin als "Exakt-Fall — nicht unser Revier". Damit fielen sie
# zwischen beide Stuehle und blieben komplett Handarbeit: 83 Stueck aus
# einem einzigen Cron-Lauf.


def test_ein_wort_zweifelsfall_geht_ans_llm_trotz_katalog_treffer(
    session, monkeypatch
):
    kanal = _kanal(session)
    _titel(session, "Driven")
    _fall(session, kanal, caption="so driven by the story", vorschlag="Driven",
          confidence=0.9)
    prompts = _fake_llm(monkeypatch, [{"auswahl": None, "sicher": False,
                                       "begruendung": "nur ein Wort"}])

    summary = cla.run_candidate_llm_assist(session)

    assert summary.geprueft == 1, (
        "Ein-Wort-Titel mit schwachem Treffer lehnt der Autopilot ab — "
        "wenn der Assist sie auch ueberspringt, bleiben sie liegen."
    )
    assert summary.ein_wort_zweifel == 1
    assert len(prompts) == 1


def test_ein_wort_zweifelsfall_warnt_das_modell_vor_dem_zufallstreffer(
    session, monkeypatch
):
    kanal = _kanal(session)
    _titel(session, "Driven")
    _fall(session, kanal, caption="so driven by the story", vorschlag="Driven",
          confidence=0.9)
    prompts = _fake_llm(monkeypatch, [{"auswahl": None, "sicher": False,
                                       "begruendung": "x"}])

    cla.run_candidate_llm_assist(session)

    assert "ACHTUNG" in prompts[0], (
        "Ohne Warnung liest das Modell Regel 2 ('Caption benennt den "
        "Titel') auf ein zufaelliges Wortvorkommen an — genau der Fehler "
        "des mechanischen Matchers."
    )
    assert "auswahl=null" in prompts[0]


def test_sicherer_ein_wort_treffer_bleibt_beim_autopiloten(session, monkeypatch):
    kanal = _kanal(session)
    _titel(session, "Barbie")
    _fall(session, kanal, caption="#Barbie", vorschlag="Barbie", confidence=1.0)
    prompts = _fake_llm(monkeypatch, [{"auswahl": 1, "sicher": True,
                                       "begruendung": "x"}])

    summary = cla.run_candidate_llm_assist(session)

    assert prompts == [], (
        "Ein Hashtag-/Exakt-Treffer ist sicher — den ordnet der "
        "kostenlose Autopilot zu, jeder LLM-Call waere verschwendet."
    )
    assert summary.ein_wort_zweifel == 0


def test_mehrwort_kandidat_mit_katalog_treffer_bleibt_ausgeschlossen(
    session, monkeypatch
):
    kanal = _kanal(session)
    _titel(session, "Beware Boiúna")
    _fall(session, kanal, caption="Beware Boiúna kommt", vorschlag="Beware Boiúna",
          confidence=0.9)
    prompts = _fake_llm(monkeypatch, [{"auswahl": 1, "sicher": True,
                                       "begruendung": "x"}])

    summary = cla.run_candidate_llm_assist(session)

    assert prompts == [], (
        "Die Ausnahme gilt nur fuer EIN-Wort-Titel. Ein Mehrwort-Treffer "
        "ist auch bei 0.9 verlaesslich (unique_text) — der bleibt beim "
        "Autopiloten."
    )
    assert summary.geprueft == 0
