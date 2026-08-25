"""``POST /api/titles`` legt nicht mehr blind an (25.08.2026).

Wolfs Katalog nach einem Vormittag Handarbeit:

    'lanterns':
      'Lanterns'  tmdb_id=—      quelle=Manual  assets=0
      'Lanterns'  tmdb_id=—      quelle=Manual  assets=0
      'Lanterns'  tmdb_id=95350  quelle=TMDb    assets=1

Der Knopf "Titel anlegen" in der Pruef-Queue wird bei jedem Kandidaten
desselben Werks gedrueckt. Drei Posts zu "Lanterns" ergaben drei
Zeilen, zwei davon ohne ein einziges Asset.

Der Schaden ist groesser als die zwei toten Zeilen: ein mehrdeutiger
Name gilt im Katalog-Lookup als "Menschensache". Autopilot, KI-Assist
und Katalog-Nachladen lassen ihn danach ALLE liegen. Ein Doppelklick
schaltet also die Automatik fuer diesen Namen dauerhaft ab.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.main import app
from app.models.entities import Title, TitleKeyword


@pytest.fixture()
def client_und_session():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    with Session(eng) as session:
        app.dependency_overrides[get_session] = lambda: session
        try:
            yield TestClient(app), session
        finally:
            app.dependency_overrides.clear()


def test_neuer_name_wird_angelegt(client_und_session):
    client, session = client_und_session

    antwort = client.post("/api/titles", json={"title_original": "Lanterns"})

    assert antwort.status_code == 200
    assert len(session.exec(select(Title)).all()) == 1


def test_zweiter_klick_gibt_denselben_titel_zurueck(client_und_session):
    """Der Kern. Fuer den Klickenden aendert sich nichts — das Frontend
    nimmt ``title.id`` und ordnet zu — aber es entsteht keine zweite
    Zeile, die den Namen mehrdeutig machen wuerde."""
    client, session = client_und_session

    erst = client.post("/api/titles", json={"title_original": "Lanterns"})
    zweit = client.post("/api/titles", json={"title_original": "Lanterns"})

    assert zweit.status_code == 200
    assert zweit.json()["id"] == erst.json()["id"]
    assert len(session.exec(select(Title)).all()) == 1


def test_gross_und_kleinschreibung_zaehlen_als_derselbe_name(client_und_session):
    """Der Lookup normalisiert Gross-/Kleinschreibung und Leerraum —
    wer hier strenger vergleicht, laesst genau die Doubletten durch,
    die den Lookup blockieren."""
    client, session = client_und_session

    client.post("/api/titles", json={"title_original": "Lanterns"})
    client.post("/api/titles", json={"title_original": "  lanterns  "})

    assert len(session.exec(select(Title)).all()) == 1


def test_inaktiver_titel_blockiert_die_neuanlage_nicht(client_und_session):
    """Stillgelegte Titel stehen nicht im Lookup — sie kollidieren mit
    nichts und duerfen eine Neuanlage nicht verhindern."""
    client, session = client_und_session
    session.add(Title(title_original="Lanterns", active=False))
    session.commit()

    antwort = client.post("/api/titles", json={"title_original": "Lanterns"})

    assert antwort.status_code == 200
    assert len(session.exec(select(Title)).all()) == 2


def test_bereits_mehrdeutiger_name_wird_abgelehnt(client_und_session):
    """Eine dritte Zeile macht es schlimmer. 409 statt stiller Anlage —
    und der Text sagt, was zu tun ist."""
    client, session = client_und_session
    for _ in range(2):
        session.add(Title(title_original="Lanterns", active=True))
    session.commit()

    antwort = client.post("/api/titles", json={"title_original": "Lanterns"})

    assert antwort.status_code == 409
    assert "zusammenlegen" in antwort.json()["detail"]
    assert len(session.exec(select(Title)).all()) == 2, "Keine dritte Zeile."


def test_keywords_gehen_am_vorhandenen_titel_nicht_verloren(client_und_session):
    """Wer Keywords mitschickt und den vorhandenen Titel zurueckbekommt,
    darf sie nicht stillschweigend einbuessen."""
    client, session = client_und_session
    erst = client.post(
        "/api/titles", json={"title_original": "Lanterns", "keywords": ["hbo"]}
    )
    titel_id = UUID(erst.json()["id"])

    client.post(
        "/api/titles",
        json={"title_original": "Lanterns", "keywords": ["hbo", "green lantern"]},
    )

    keywords = session.exec(
        select(TitleKeyword).where(TitleKeyword.title_id == titel_id)
    ).all()
    assert {k.keyword for k in keywords} == {"hbo", "green lantern"}
    assert len(keywords) == 2, "Kein doppeltes 'hbo'."


# --- Die leere Antwort (25.08.2026) -----------------------------------
#
# Der Endpoint lieferte seit jeher "{}". Ursache: der Commit fuer die
# Keywords lief NACH dem Refresh und machte die Instanz stale; FastAPI
# serialisierte ein leeres ``__dict__``.
#
# Die Folge war kein Schoenheitsfehler. Das Frontend liest ``title.id``
# und schickt es an ``reviewAsset``; ``undefined`` faellt bei
# JSON.stringify heraus, das Asset bekam also KEINEN Titel — waehrend
# der Kandidat auf "resolved" ging und der Toast "neu angelegt und
# zugeordnet" meldete. So entstanden die 74 geschlossenen Kandidaten
# mit titellosem Asset und die Manual-Titel mit assets=0 daneben.


def test_antwort_traegt_eine_id(client_und_session):
    """Der Vorfall selbst. Ohne ``id`` in der Antwort greift jeder
    Aufrufer ins Leere — und merkt es nicht."""
    client, _session = client_und_session

    daten = client.post("/api/titles", json={"title_original": "Lanterns"}).json()

    assert daten.get("id"), "Ohne ID kann das Frontend nichts zuordnen."
    assert daten["title_original"] == "Lanterns"


def test_antwort_traegt_eine_id_auch_mit_keywords(client_und_session):
    """Der Keyword-Commit war die Ursache — genau dieser Pfad muss
    dieselbe vollstaendige Antwort liefern."""
    client, _session = client_und_session

    daten = client.post(
        "/api/titles", json={"title_original": "Lanterns", "keywords": ["hbo"]}
    ).json()

    assert daten.get("id")
    assert daten["title_original"] == "Lanterns"


def test_antwort_traegt_eine_id_beim_vorhandenen_titel(client_und_session):
    """Der neue Rueckgabe-Pfad darf die Falle nicht wiederholen: auch
    ``_keywords_ergaenzen`` committet."""
    client, _session = client_und_session
    client.post("/api/titles", json={"title_original": "Lanterns"})

    daten = client.post(
        "/api/titles", json={"title_original": "Lanterns", "keywords": ["hbo"]}
    ).json()

    assert daten.get("id")
    assert daten["title_original"] == "Lanterns"
