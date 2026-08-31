"""Wächter für ``katalog_nachladen`` (24.08.2026).

Wolfs Befund: Von 58 KI-geprüften Vorschlägen ließ sich genau EINER
automatisch zuordnen — der Katalog kennt die beworbenen Werke nicht.
Er deckt sechs Studios und drei Streamer ab, beobachtet werden über
200 Kanäle.

Der Pfad legt Titel an und ordnet Assets zu — der wirksamste Schreib-
Pfad im System. Entsprechend eng sind die Tests: drei Wächter, jeder
einzeln geprüft, und keiner darf sich mit einem Ja begnügen, das nur
vom Modell behauptet wurde.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
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
from app.services import katalog_nachladen as kn

HEUTE = datetime.now(timezone.utc).date()
AKTUELL = (HEUTE - timedelta(days=30)).isoformat()
VERALTET = (HEUTE - timedelta(days=3000)).isoformat()


@pytest.fixture()
def session():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


class _FakeTMDb:
    """Nachbau von TMDbClient mit steuerbaren Suchergebnissen."""

    def __init__(self, *, filme=None, serien=None, raises=False):
        self._filme = filme or []
        self._serien = serien or []
        self._raises = raises
        self.gesucht: list[str] = []

    async def search_movies(self, query, **kwargs):
        if self._raises:
            raise RuntimeError("TMDb down")
        self.gesucht.append(query)
        return self._filme

    async def search_series(self, query, **kwargs):
        if self._raises:
            raise RuntimeError("TMDb down")
        return self._serien

    def normalize_tmdb_movie(self, raw):
        return {
            "tmdb_id": raw.get("id"),
            "title_original": raw.get("title"),
            "title_local": raw.get("title"),
            "genres": ["Drama"],
            "aliases": [],
        }

    def normalize_tmdb_series(self, raw):
        return {
            "tmdb_id": raw.get("id"),
            "title_original": raw.get("name"),
            "title_local": raw.get("name"),
            "genres": ["Drama"],
            "aliases": [],
        }


def _luecke(session, *, name, caption, notiz=None):
    """Ein offener Kandidat, dem die KI ein nicht katalogisiertes Werk
    zugeschrieben hat — der Zustand nach #426/#428."""
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}", platform="instagram",
        url=f"https://x.test/{uuid4()}", market=Market.US,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    post = Post(
        channel_id=ch.id, platform=ch.platform,
        post_url=f"https://x.test/p/{uuid4()}", caption=caption,
        detected_at=datetime(2026, 8, 24, tzinfo=timezone.utc), raw_payload={},
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    asset = Asset(post_id=post.id)
    session.add(asset)
    session.commit()
    session.refresh(asset)
    cand = TitleCandidate(
        asset_id=asset.id, suggested_title=name, confidence=0.9,
        status=CandidateStatus.OPEN,
        llm_checked_at=datetime(2026, 8, 24, 20, tzinfo=timezone.utc),
        llm_note=notiz or f"KI: bewirbt '{name}' (nicht im Katalog) — begruendung",
    )
    session.add(cand)
    session.commit()
    session.refresh(cand)
    return asset, cand


def _lauf(session, client, *, anwenden=True):
    """Vorgabe hier ist der Ernstfall — die Waechter-Tests pruefen, was
    wirklich geschrieben wird. Die Vorschau hat eigene Tests unten."""
    return asyncio.run(
        kn.lade_fehlende_titel_nach(session, client=client, anwenden=anwenden)
    )


def test_belegter_titel_wird_angelegt_und_zugeordnet(session):
    asset, cand = _luecke(
        session, name="Desperate Housewives",
        caption="Her love language is acts of service. Now streaming: #DesperateHousewives",
    )
    client = _FakeTMDb(serien=[
        {"id": 1668, "name": "Desperate Housewives",
         "original_name": "Desperate Housewives", "first_air_date": AKTUELL},
    ])

    summary = _lauf(session, client)

    session.refresh(asset)
    session.refresh(cand)
    neu = session.exec(
        select(Title).where(Title.title_original == "Desperate Housewives")
    ).first()
    assert neu is not None, "Der belegte, eindeutige Titel gehoert in den Katalog."
    assert neu.tmdb_id == 1668
    assert neu.content_type == "Series"
    assert asset.title_id == neu.id
    assert cand.status == CandidateStatus.RESOLVED
    assert summary.angelegt == 1
    assert summary.zugeordnet == 1


def test_ohne_text_beleg_wird_nichts_angelegt(session):
    """Waechter 1. Eine blosse Behauptung der KI darf keinen Titel in den
    Katalog schreiben — dort wirkt er dauerhaft auf jeden Matcher-Lauf."""
    asset, _cand = _luecke(
        session, name="Erfundene Serie", caption="#THEWAYOUT drops next week.",
    )
    client = _FakeTMDb(serien=[
        {"id": 999, "name": "Erfundene Serie", "first_air_date": AKTUELL},
    ])

    summary = _lauf(session, client)

    session.refresh(asset)
    assert session.exec(select(Title)).all() == []
    assert asset.title_id is None
    assert summary.nicht_belegt == 1
    assert client.gesucht == [], (
        "Ohne Beleg darf gar nicht erst bei TMDb gefragt werden."
    )


def test_mehrdeutiger_tmdb_treffer_legt_nichts_an(session):
    """Waechter 2. Zwei gleichnamige Werke: welches gemeint ist, weiss
    der Code nicht — also entscheidet der Mensch."""
    asset, _cand = _luecke(
        session, name="The Fox", caption="Official teaser poster for The Fox",
    )
    client = _FakeTMDb(filme=[
        {"id": 1, "title": "The Fox", "release_date": AKTUELL},
        {"id": 2, "title": "The Fox", "release_date": AKTUELL},
    ])

    summary = _lauf(session, client)

    session.refresh(asset)
    assert session.exec(select(Title)).all() == []
    assert asset.title_id is None
    # Seit 31.08.2026 getrennt gezaehlt: mehrdeutig heisst "ein Mensch
    # muss nur auswaehlen" — dafuer gibt es die TMDb-Auswahl in der
    # Queue. Die Meldung nennt den Namen, sonst weiss niemand, WELCHER
    # Fall zur Auswahl ansteht.
    assert summary.tmdb_mehrdeutig == 1
    assert summary.tmdb_mehrdeutig_namen == ["The Fox"]
    assert summary.tmdb_ohne_treffer == 0


def test_veralteter_tmdb_treffer_zaehlt_nicht(session):
    """Waechter 2, zweite Haelfte: Namensgleichheit allein reicht nicht.
    Ein 1974er Film gleichen Namens ist nicht das beworbene Werk."""
    asset, _cand = _luecke(
        session, name="The Fox", caption="Official teaser poster for The Fox",
    )
    client = _FakeTMDb(filme=[
        {"id": 1, "title": "The Fox", "release_date": VERALTET},
    ])

    summary = _lauf(session, client)

    session.refresh(asset)
    assert session.exec(select(Title)).all() == []
    # Ein 1974er Namensvetter faellt durchs Aktualitaets-Fenster — fuer
    # den Zaehler ist das "TMDb kennt das beworbene Werk nicht", NICHT
    # "mehrdeutig": hier gibt es nichts auszuwaehlen, das ist Handarbeit.
    assert summary.tmdb_ohne_treffer == 1
    assert summary.tmdb_ohne_treffer_namen == ["The Fox"]
    assert summary.tmdb_mehrdeutig == 0


def test_bereits_vorhandener_titel_wird_nur_zugeordnet(session):
    """Waechter 3. Zwischen KI-Urteil und Nachladen kann jemand den Titel
    angelegt haben — dann gibt es nichts anzulegen, nur zuzuordnen."""
    titel = Title(title_original="Lanterns", active=True)
    session.add(titel)
    session.commit()
    session.refresh(titel)
    asset, _cand = _luecke(
        session, name="Lanterns",
        caption="A new episode of #Lanterns is now streaming.",
    )
    client = _FakeTMDb()

    summary = _lauf(session, client)

    session.refresh(asset)
    assert asset.title_id == titel.id
    assert summary.angelegt == 0
    assert summary.schon_vorhanden == 1
    assert client.gesucht == [], "Kein TMDb-Aufruf fuer einen bekannten Titel."


def test_zweiter_post_desselben_werks_legt_keine_zweite_zeile_an(session):
    """Zwei Posts derselben Serie in EINEM Lauf: der frisch angelegte
    Titel muss sofort im Lookup stehen, sonst entstehen Doubletten —
    und Doubletten machen den Namen im Exakt-Lookup mehrdeutig, womit
    der Autopilot ihn dauerhaft ueberspringt."""
    for i in range(2):
        _luecke(
            session, name="Lanterns",
            caption=f"Episode {i}: #Lanterns now streaming.",
        )
    client = _FakeTMDb(serien=[
        {"id": 42, "name": "Lanterns", "first_air_date": AKTUELL},
    ])

    summary = _lauf(session, client)

    titel = session.exec(select(Title).where(Title.title_original == "Lanterns")).all()
    assert len(titel) == 1, "Ein Werk, eine Katalog-Zeile."
    assert summary.angelegt == 1
    assert summary.zugeordnet == 2


def test_tmdb_fehler_stoppt_den_lauf_nicht(session):
    asset, _cand = _luecke(
        session, name="Desperate Housewives",
        caption="Now streaming: #DesperateHousewives",
    )
    client = _FakeTMDb(raises=True)

    summary = _lauf(session, client)

    session.refresh(asset)
    assert asset.title_id is None
    assert summary.fehler == 1


def test_kandidaten_ohne_luecken_marker_bleiben_unberuehrt(session):
    """Nur wer den Marker traegt, ist gemeint. Ein normaler offener
    Vorschlag darf nicht ploetzlich einen Titel anlegen."""
    _luecke(
        session, name="Driven", caption="so driven by the story",
        notiz="KI unsicher: kein Titelbezug.",
    )
    client = _FakeTMDb(filme=[
        {"id": 7, "title": "Driven", "release_date": AKTUELL},
    ])

    summary = _lauf(session, client)

    assert session.exec(select(Title)).all() == []
    assert summary.geprueft == 0


def test_batch_deckel_meldet_den_rest(session):
    for i in range(3):
        _luecke(session, name=f"Werk {i}", caption=f"Werk {i} kommt bald")
    client = _FakeTMDb()

    summary = asyncio.run(
        kn.lade_fehlende_titel_nach(
            session, client=client, max_kandidaten=2, anwenden=True
        )
    )

    assert summary.geprueft == 2
    assert summary.offen_danach == 1


async def _post_nachladen(session):
    from httpx import ASGITransport, AsyncClient

    from app.database import get_session
    from app.main import app

    app.dependency_overrides[get_session] = lambda: session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/titles/katalog-nachladen")
    finally:
        app.dependency_overrides.clear()


def test_endpoint_503_bei_abgeschaltetem_flag(session, monkeypatch):
    """Feature-Flag-Gate (Arbeitsregel 23.08.2026). Hier wiegt es
    schwerer als anderswo: ohne Sperre legt ein einziger unbedachter
    Klick Titel an und ordnet Assets zu. Der 503 nennt die Env-Var,
    damit klar ist, welcher Schalter fehlt — nicht 404, denn der Pfad
    existiert."""
    from app.api import titles as titles_module

    monkeypatch.delenv("FEATURE_KATALOG_NACHLADEN_ENABLED", raising=False)
    gerufen: list[int] = []

    async def _spion(*a, **k):
        gerufen.append(1)
        return kn.NachladeSummary()

    # Am Endpoint-Modul patchen, nicht am Service: ``titles.py`` bindet
    # den Namen beim Import, ein Patch auf ``kn`` erreicht ihn nicht —
    # die Zusicherung unten waere dann leer und ein entfernter Gate
    # bliebe gruen.
    monkeypatch.setattr(titles_module, "lade_fehlende_titel_nach", _spion)

    antwort = asyncio.run(_post_nachladen(session))

    assert antwort.status_code == 503
    assert "FEATURE_KATALOG_NACHLADEN_ENABLED" in antwort.json()["detail"]
    assert gerufen == [], "Ohne Flag darf der Schreib-Pfad nicht einmal anlaufen."


# --- Vorschau (25.08.2026) --------------------------------------------
#
# Anlass: Dieses Feature ist in Staging nicht erprobbar. Sein Ausloeser
# ist der Marker "(nicht im Katalog)", den nur die KI-Pruefung setzt —
# und die braucht den Anthropic-Key, den Staging bewusst nicht hat. Der
# erste Klick in Staging meldete darum korrekt "0 geprueft", und der
# zweite "KI-Pruefung uebersprungen: kein Anthropic-API-Key". Wolfs
# Freigabe-Modell "Staging zuerst" greift hier ins Leere; die Vorschau
# tritt an seine Stelle.


def test_cron_stage_laeuft_scharf_und_ist_doppelt_gegated(session, monkeypatch):
    """Cron-Stage (31.08.2026, Wolfs Auftrag: nicht jede Woche 40-50
    Titel von Hand): direkt nach der KI-Pruefung laeuft das Nachladen
    mit ``anwenden=True`` — kein Vorschau-Rollback im Cron. Zwei Gates:
    Feature-Flag UND Settings-Not-Aus; ein Fehler kippt den Lauf nicht."""
    import inspect

    from app.api import cron as cron_module
    from app.config import settings

    aufrufe: list[dict] = []

    async def _stub(session_, **kwargs):
        aufrufe.append(kwargs)
        return kn.NachladeSummary(vorschau=not kwargs.get("anwenden", False))

    monkeypatch.setattr(cron_module, "lade_fehlende_titel_nach", _stub)

    # Gate 1: Feature-Flag aus → Stage skippt ohne Aufruf.
    monkeypatch.delenv("FEATURE_KATALOG_NACHLADEN_ENABLED", raising=False)
    ergebnis = asyncio.run(cron_module._run_katalog_nachladen_stage(session))
    assert ergebnis == {"skipped": True, "reason": "feature_flag_disabled"}
    assert aufrufe == []

    # Gate 2: Settings-Not-Aus → Stage skippt ohne Aufruf.
    monkeypatch.setenv("FEATURE_KATALOG_NACHLADEN_ENABLED", "true")
    monkeypatch.setattr(settings, "katalog_nachladen_in_cron", False, raising=False)
    ergebnis = asyncio.run(cron_module._run_katalog_nachladen_stage(session))
    assert ergebnis == {"skipped": True, "reason": "disabled"}
    assert aufrufe == []

    # Beide Gates offen: echter Lauf mit anwenden=True und Cron-Deckel.
    monkeypatch.setattr(settings, "katalog_nachladen_in_cron", True, raising=False)
    monkeypatch.setattr(settings, "katalog_nachladen_cron_max", 7, raising=False)
    ergebnis = asyncio.run(cron_module._run_katalog_nachladen_stage(session))
    assert ergebnis["vorschau"] is False, (
        "Im Cron darf KEINE Vorschau laufen — sie wuerde jede Woche "
        "dieselben Titel finden und nichts festschreiben."
    )
    assert aufrufe == [{"anwenden": True, "max_kandidaten": 7}]

    # Fehler-Guard: eine Exception wird gemeldet, kippt aber nichts.
    async def _explodiert(session_, **kwargs):
        raise RuntimeError("tmdb weg")

    monkeypatch.setattr(cron_module, "lade_fehlende_titel_nach", _explodiert)
    ergebnis = asyncio.run(cron_module._run_katalog_nachladen_stage(session))
    assert "tmdb weg" in ergebnis["error"]

    # Verdrahtungs-Waechter (Muster Empfehlungs-Snapshot): die Stage
    # steht im Hintergrund-Lauf, und zwar NACH der KI-Pruefung — deren
    # frische Marker sollen im selben Lauf aufgeloest werden.
    quelle = inspect.getsource(cron_module._run_cron_sync_background_impl)
    anker = 'summary["katalog_nachladen"] = await _run_katalog_nachladen_stage(session)'
    assert anker in quelle, "Die Nachladen-Stage ist nicht im Hintergrund-Lauf verdrahtet."
    assert quelle.index("candidate_llm_assist_stage") < quelle.index(anker)
    assert quelle.index(anker) < quelle.index("recommendation_snapshot_stage")


def test_vorschau_schreibt_nichts_meldet_aber_dasselbe(session):
    """Der Kern: gleiche Zahlen, kein Schreiben. Waeren es zwei
    getrennte Codepfade, wuerde die Vorschau irgendwann luegen — sie
    laeuft deshalb komplett durch und rollt am Ende zurueck."""
    asset, cand = _luecke(
        session, name="Desperate Housewives",
        caption="Now streaming: #DesperateHousewives",
    )
    client = _FakeTMDb(serien=[
        {"id": 1668, "name": "Desperate Housewives", "first_air_date": AKTUELL},
    ])

    vorschau = _lauf(session, client, anwenden=False)

    assert vorschau.vorschau is True
    assert vorschau.angelegt == 1
    assert vorschau.angelegte_titel == ["Desperate Housewives"]
    # ... und nichts davon steht in der Datenbank.
    assert session.exec(select(Title)).all() == []
    session.refresh(asset)
    session.refresh(cand)
    assert asset.title_id is None
    assert cand.status == CandidateStatus.OPEN


def test_vorschau_dann_ernstfall_ergibt_dasselbe(session):
    """Die Reihenfolge, die Wolf am Montag klickt: erst sehen, dann
    anwenden. Der zweite Lauf muss genau das tun, was der erste
    angekuendigt hat — sonst ist die Vorschau eine Behauptung."""
    _luecke(
        session, name="Lanterns",
        caption="A new episode of #Lanterns is now streaming.",
    )
    client = _FakeTMDb(serien=[
        {"id": 4242, "name": "Lanterns", "first_air_date": AKTUELL},
    ])

    vorschau = _lauf(session, client, anwenden=False)
    echt = _lauf(session, client, anwenden=True)

    assert vorschau.angelegte_titel == echt.angelegte_titel
    assert (vorschau.angelegt, vorschau.zugeordnet) == (echt.angelegt, echt.zugeordnet)
    assert echt.vorschau is False
    assert len(session.exec(select(Title)).all()) == 1


def test_endpoint_ist_ohne_parameter_eine_vorschau(session, monkeypatch):
    """Der gefaehrlichere Zustand gehoert hinter den ausdruecklichen
    Klick: ein POST ohne ``anwenden`` schreibt nicht."""
    from app.api import titles as titles_module

    monkeypatch.setenv("FEATURE_KATALOG_NACHLADEN_ENABLED", "true")
    gesehen: list[bool] = []

    async def _spion(session_, *, anwenden=False, **k):
        gesehen.append(anwenden)
        return kn.NachladeSummary(vorschau=not anwenden)

    monkeypatch.setattr(titles_module, "lade_fehlende_titel_nach", _spion)

    antwort = asyncio.run(_post_nachladen(session))
    assert antwort.status_code == 200
    assert antwort.json()["vorschau"] is True
    assert gesehen == [False], "Ohne Parameter darf nichts festgeschrieben werden."


# --- Mehrdeutiger Katalog-Name (25.08.2026) ---------------------------
#
# Der Bug, der in Production auffiel: nach dem Anlegen von "Lanterns"
# stand derselbe Titel in der naechsten Vorschau WIEDER als Neuanlage.
# Ursache war ein ``lookup.get(name)``, das den mehrdeutigen Zustand
# (Schluessel da, Wert None) nicht vom fehlenden Schluessel trennt —
# der mehrdeutige Fall landete im TMDb-Pfad und legte eine weitere
# Zeile gleichen Namens an. Jede Runde eine mehr.


def test_mehrdeutiger_katalog_name_legt_nichts_an(session):
    """Zwei aktive Titel gleichen Namens: welcher gemeint ist, weiss der
    Code nicht. Anlegen waere die schlechteste aller Antworten — es
    macht den Namen noch mehrdeutiger."""
    for _ in range(2):
        session.add(Title(title_original="Lanterns", active=True))
    session.commit()
    asset, cand = _luecke(
        session, name="Lanterns",
        caption="A new episode of #Lanterns is now streaming.",
    )
    client = _FakeTMDb(serien=[
        {"id": 4242, "name": "Lanterns", "first_air_date": AKTUELL},
    ])

    summary = _lauf(session, client)

    session.refresh(asset)
    session.refresh(cand)
    assert summary.katalog_mehrdeutig == 1
    assert summary.angelegt == 0
    assert summary.zugeordnet == 0
    assert len(session.exec(select(Title)).all()) == 2, "Keine dritte Zeile."
    assert asset.title_id is None
    assert cand.status == CandidateStatus.OPEN, "Bleibt Menschensache."
    assert client.gesucht == [], "Mehrdeutig heisst: gar nicht erst bei TMDb fragen."


def test_zweiter_lauf_legt_den_frisch_angelegten_titel_nicht_erneut_an(session):
    """Die Schleife von Ende zu Ende: anlegen, dann ein zweiter Lauf
    ueber einen zweiten Post desselben Werks. Der zweite Lauf baut
    seinen Lookup neu aus der DB — der frische Titel muss darin
    eindeutig stehen, sonst beginnt die Doubletten-Kette."""
    _luecke(session, name="Lanterns", caption="#Lanterns is streaming.")
    client = _FakeTMDb(serien=[
        {"id": 4242, "name": "Lanterns", "first_air_date": AKTUELL},
    ])
    erst = _lauf(session, client)
    assert erst.angelegt == 1

    _luecke(session, name="Lanterns", caption="New episode of #Lanterns tonight.")
    zweit = _lauf(session, client)

    assert zweit.angelegt == 0, "Der Titel steht bereits im Katalog."
    assert zweit.schon_vorhanden == 1
    assert len(session.exec(select(Title)).all()) == 1


# --- TMDb-Auswahl (31.08.2026) ----------------------------------------
#
# Wolfs Lauf an diesem Tag: 10 von 13 Restfaellen "bei TMDb nicht
# eindeutig". Der automatische Pfad laesst sie zu Recht liegen — aber
# ein Mensch muesste nur AUSWAEHLEN. Die Auswahl-Funktionen sind die
# menschliche Haelfte von Waechter 2: gleiche Filter (exakter Name,
# aktuelles Datum), nur ohne die Eindeutigkeits-Sperre.


def test_tmdb_auswahl_liefert_alle_exakten_aktuellen_treffer():
    client = _FakeTMDb(
        filme=[
            {"id": 1, "title": "The Fox", "release_date": AKTUELL},
            {"id": 2, "title": "The Fox", "release_date": AKTUELL},
            {"id": 3, "title": "The Fox", "release_date": VERALTET},
            {"id": 4, "title": "Fox and Friends", "release_date": AKTUELL},
        ],
        serien=[{"id": 9, "name": "The Fox", "first_air_date": AKTUELL}],
    )

    auswahl = asyncio.run(kn.tmdb_auswahl_fuer_name("The Fox", client=client))

    assert [w["tmdb_id"] for w in auswahl] == [1, 2, 9], (
        "Beide aktuellen Filme UND die Serie — der Namensvetter von "
        "damals und der unpassende Name bleiben draussen."
    )
    assert auswahl[0]["medium"] == "film"
    assert auswahl[2]["medium"] == "serie"
    assert auswahl[0]["jahr"] == AKTUELL[:4]


def test_tmdb_anlegen_legt_an_ordnet_zu_und_schliesst(session):
    asset, cand = _luecke(
        session, name="The Fox", caption="Official teaser poster for The Fox",
    )
    client = _FakeTMDb(filme=[
        {"id": 1, "title": "The Fox", "release_date": AKTUELL},
        {"id": 2, "title": "The Fox", "release_date": AKTUELL},
    ])

    ergebnis = asyncio.run(kn.titel_aus_tmdb_anlegen(
        session, asset_id=asset.id, candidate_id=cand.id,
        tmdb_id=2, medium="film", name="The Fox", client=client,
    ))

    session.refresh(asset)
    session.refresh(cand)
    titel = session.exec(select(Title)).all()
    assert len(titel) == 1
    assert titel[0].tmdb_id == 2, "Es zaehlt die AUSWAHL, nicht der erste Treffer."
    assert titel[0].source == "TMDb"
    assert asset.title_id == titel[0].id
    assert cand.status == CandidateStatus.RESOLVED
    assert ergebnis["angelegt"] is True


def test_tmdb_anlegen_verwendet_vorhandene_tmdb_zeile_wieder(session):
    """Dieselbe tmdb_id darf nie zweimal im Katalog stehen — sonst
    entsteht genau die Doubletten-Lage, die #435 aufgeraeumt hat."""
    vorhanden = Title(title_original="The Fox", tmdb_id=2, active=True)
    session.add(vorhanden)
    session.commit()
    session.refresh(vorhanden)
    asset, cand = _luecke(
        session, name="The Fox", caption="Official teaser poster for The Fox",
    )
    client = _FakeTMDb()

    ergebnis = asyncio.run(kn.titel_aus_tmdb_anlegen(
        session, asset_id=asset.id, candidate_id=cand.id,
        tmdb_id=2, medium="film", name="The Fox", client=client,
    ))

    session.refresh(asset)
    assert ergebnis["angelegt"] is False
    assert asset.title_id == vorhanden.id
    assert len(session.exec(select(Title)).all()) == 1
    assert client.gesucht == [], "Bekannte tmdb_id -> kein TMDb-Aufruf."


def test_tmdb_anlegen_unbekannte_id_wirft(session):
    """Zwischen Auswahl-Anzeige und Klick kann sich TMDb geaendert
    haben — dann lieber ein sichtbarer Fehler als ein falscher Titel."""
    asset, cand = _luecke(
        session, name="The Fox", caption="Official teaser poster for The Fox",
    )
    client = _FakeTMDb(filme=[
        {"id": 1, "title": "The Fox", "release_date": AKTUELL},
    ])

    with pytest.raises(LookupError):
        asyncio.run(kn.titel_aus_tmdb_anlegen(
            session, asset_id=asset.id, candidate_id=cand.id,
            tmdb_id=999, medium="film", name="The Fox", client=client,
        ))
    assert session.exec(select(Title)).all() == []


def test_tmdb_auswahl_endpoint_503_bei_abgeschaltetem_flag(session, monkeypatch):
    """Beide Auswahl-Endpoints teilen das Nachladen-Flag — derselbe
    Schreib-Pfad, dieselbe Sperre."""
    from httpx import ASGITransport, AsyncClient

    from app.database import get_session
    from app.main import app

    monkeypatch.delenv("FEATURE_KATALOG_NACHLADEN_ENABLED", raising=False)
    app.dependency_overrides[get_session] = lambda: session

    async def _abfragen():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            auswahl = await client.get("/api/titles/tmdb-auswahl", params={"name": "The Fox"})
            anlegen = await client.post("/api/titles/tmdb-anlegen", json={
                "asset_id": str(uuid4()), "candidate_id": str(uuid4()),
                "tmdb_id": 1, "medium": "film", "name": "The Fox",
            })
            return auswahl, anlegen

    try:
        auswahl, anlegen = asyncio.run(_abfragen())
    finally:
        app.dependency_overrides.clear()

    assert auswahl.status_code == 503
    assert anlegen.status_code == 503
    assert "FEATURE_KATALOG_NACHLADEN_ENABLED" in auswahl.json()["detail"]
