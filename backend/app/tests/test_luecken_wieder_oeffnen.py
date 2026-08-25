"""Waechter fuer ``scripts/luecken_wieder_oeffnen`` (25.08.2026).

Das Skript schreibt in den Bestand, aus dem das Katalog-Nachladen
seine Eingabe zieht. Ein Fehler hier wird zu einem falschen Titel im
Katalog, und der wirkt auf jeden kuenftigen Matcher-Lauf.

Der Kern ist die Namens-Reparatur. In Production trugen Kandidaten
``suggested_title='partners'``, waehrend die KI-Notiz
"bewirbt 'Steckerlfisch Fiasko'" sagte. Wuerde das Skript nur den
Status umstellen, suchte das Nachladen nach "partners" — und sein
Text-Beleg-Waechter winkte es durch, denn "partners" steht wirklich in
der Caption. Genau diese Kette nageln die Tests fest.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import (
    Asset,
    CandidateStatus,
    Channel,
    Market,
    Post,
    Title,
    TitleCandidate,
)
from scripts import luecken_wieder_oeffnen as reopen

JETZT = datetime.now(timezone.utc)


@pytest.fixture()
def session():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _fall(
    session, *, vorschlag, notiz, status=CandidateStatus.RESOLVED,
    mit_titel=False, alter_tage=0,
):
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}", platform="instagram",
        url=f"https://x.test/{uuid4()}", market=Market.US,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    post = Post(
        channel_id=ch.id, platform=ch.platform,
        post_url=f"https://x.test/p/{uuid4()}", caption="egal",
        detected_at=JETZT, raw_payload={},
    )
    session.add(post)
    session.commit()
    session.refresh(post)

    title_id = None
    if mit_titel:
        titel = Title(title_original=f"Werk {uuid4().hex[:4]}", active=True)
        session.add(titel)
        session.commit()
        session.refresh(titel)
        title_id = titel.id

    asset = Asset(post_id=post.id, title_id=title_id)
    session.add(asset)
    session.commit()
    session.refresh(asset)

    cand = TitleCandidate(
        asset_id=asset.id, suggested_title=vorschlag, confidence=0.9,
        status=status, llm_note=notiz,
        updated_at=JETZT - timedelta(days=alter_tage),
    )
    session.add(cand)
    session.commit()
    session.refresh(cand)
    return cand


LUECKE = "KI: bewirbt '{}' (nicht im Katalog) — begruendung"


# --- Namens-Reparatur -------------------------------------------------

def test_name_wird_aus_der_notiz_gelesen():
    assert reopen._name_aus_notiz(LUECKE.format("Desperate Housewives")) == (
        "Desperate Housewives"
    )


def test_name_mit_apostroph_wird_nicht_abgeschnitten():
    notiz = "KI: bewirbt 'Ocean's Eleven' (nicht im Katalog) — der Post 'zeigt' es"
    assert reopen._name_aus_notiz(notiz) == "Ocean's Eleven"


def test_wiederholter_marker_in_der_begruendung_verschiebt_den_namen_nicht():
    """Die Begruendung ist freier Modell-Text und wiederholt die Wendung
    gelegentlich. Ein gieriges Muster nimmt dann das LETZTE Vorkommen —
    der "Name" waere der halbe Satz dazwischen, landete als
    ``suggested_title`` und ginge so an TMDb."""
    notiz = (
        "KI: bewirbt 'Lanterns' (nicht im Katalog) — auch 'Green Lantern' "
        "(nicht im Katalog) waere denkbar, ist es aber nicht."
    )
    assert reopen._name_aus_notiz(notiz) == "Lanterns"


def test_abgeschnittene_notiz_gibt_keinen_namen():
    """``llm_note`` ist bei 300 Zeichen gekappt. Fehlt der Marker, ist
    der Name nicht sicher lesbar — dann lieber liegen lassen als raten."""
    assert reopen._name_aus_notiz("KI: bewirbt 'Ein sehr langer Titel") is None
    assert reopen._name_aus_notiz(None) is None


def test_hashtag_gilt_nicht_als_abweichung():
    """"#Lanterns" und "Lanterns" sind derselbe Vorschlag — sonst
    meldete der Bericht Reparaturen, die keine sind."""
    assert reopen._abweichend("#Lanterns", "Lanterns") is False
    assert reopen._abweichend("partners", "Steckerlfisch Fiasko") is True


# --- Auswahl ----------------------------------------------------------

def test_geschlossene_luecke_ohne_titel_ist_ein_fall(session):
    _fall(session, vorschlag="driven", notiz=LUECKE.format("Desperate Housewives"))

    faelle = reopen._faelle(session, tage=7)

    assert len(faelle) == 1
    _cand, alt, neu = faelle[0]
    assert (alt, neu) == ("driven", "Desperate Housewives")


def test_asset_mit_titel_bleibt_unberuehrt(session):
    """Wer den Titel von Hand angelegt hat, hat alles richtig gemacht."""
    _fall(
        session, vorschlag="driven", notiz=LUECKE.format("Desperate Housewives"),
        mit_titel=True,
    )
    assert reopen._faelle(session, tage=7) == []


def test_kandidat_ohne_luecken_marker_ist_kein_fall(session):
    _fall(session, vorschlag="driven", notiz="KI unsicher, ohne Begruendung.")
    assert reopen._faelle(session, tage=7) == []


def test_offener_kandidat_ist_kein_fall(session):
    """Offene sieht das Nachladen selbst — hier waere nichts zu tun."""
    _fall(
        session, vorschlag="driven", notiz=LUECKE.format("Desperate Housewives"),
        status=CandidateStatus.OPEN,
    )
    assert reopen._faelle(session, tage=7) == []


def test_ausserhalb_des_fensters_bleibt_liegen(session):
    _fall(
        session, vorschlag="driven", notiz=LUECKE.format("Desperate Housewives"),
        alter_tage=30,
    )
    assert reopen._faelle(session, tage=7) == []
    assert len(reopen._faelle(session, tage=60)) == 1


def test_unlesbare_notiz_wird_nicht_angefasst(session):
    """Ohne sicheren Namen wuerde das Wiederoeffnen den alten,
    falschen Vorschlag stehen lassen — genau die Gefahr, gegen die
    dieses Skript gebaut ist."""
    _fall(session, vorschlag="partners", notiz="KI: bewirbt 'Steckerlfisch")
    assert reopen._faelle(session, tage=7) == []


# --- Schreiben --------------------------------------------------------

def _lauf(monkeypatch, session, argv):
    """``main()`` gegen die Test-DB. Die Session-Bindung reicht: das
    Skript oeffnet seine eigene Session ueber ``_engine``."""
    monkeypatch.setattr(reopen, "_engine", lambda: session.get_bind())
    monkeypatch.setattr("sys.argv", ["luecken_wieder_oeffnen", *argv])
    return reopen.main()


def test_vorschau_aendert_nichts(session, monkeypatch, capsys):
    cand = _fall(
        session, vorschlag="partners", notiz=LUECKE.format("Steckerlfisch Fiasko"),
    )

    assert _lauf(monkeypatch, session, []) == 0

    session.refresh(cand)
    assert cand.status == CandidateStatus.RESOLVED
    assert cand.suggested_title == "partners"
    ausgabe = capsys.readouterr().out
    assert "VORSCHAU" in ausgabe
    assert "'partners'" in ausgabe and "'Steckerlfisch Fiasko'" in ausgabe


def test_apply_repariert_den_namen_und_oeffnet(session, monkeypatch):
    """Der Kern. Ohne die Namens-Reparatur suchte das Nachladen danach
    bei TMDb nach "partners" — und legte den falschen Titel an."""
    cand = _fall(
        session, vorschlag="partners", notiz=LUECKE.format("Steckerlfisch Fiasko"),
    )

    assert _lauf(monkeypatch, session, ["--apply", "--yes"]) == 0

    session.refresh(cand)
    assert cand.suggested_title == "Steckerlfisch Fiasko"
    assert cand.status == CandidateStatus.OPEN


def test_apply_laesst_die_notiz_stehen(session, monkeypatch):
    """Der Luecken-Marker ist die Eintrittskarte fuers Nachladen —
    wer ihn beim Wiederoeffnen loescht, macht den Fall gleich wieder
    unsichtbar."""
    notiz = LUECKE.format("Lanterns")
    cand = _fall(session, vorschlag="classified", notiz=notiz)

    _lauf(monkeypatch, session, ["--apply", "--yes"])

    session.refresh(cand)
    assert cand.llm_note == notiz


def test_muster_greift_nur_bei_luecken_notizen():
    """Der Namens-Parser ist das EINZIGE Tor: es gibt keinen zweiten
    Marker-Filter daneben (er waere durch keine Mutation toetbar). Also
    muss das Muster hier alle anderen Notiz-Formen abweisen, die der
    KI-Assist schreibt — vor allem den Katalog-Treffer. Griffe es dort,
    wuerde ein korrekt zugeordneter Kandidat wieder aufgerissen."""
    andere = [
        "KI: bewirbt wohl 'Lanterns' — Der Post nennt die Serie.",
        "KI unsicher: kein passender Katalog-Titel.",
        "Katalog ergaenzt: 'Lanterns' aus TMDb angelegt und zugeordnet.",
        "KI unsicher, ohne Begruendung.",
    ]
    for notiz in andere:
        assert reopen._name_aus_notiz(notiz) is None, notiz
