"""Substring-Magnet-Schutz Klasse 2 — generische Ein-Wort-Titel (24.08.2026).

Der Streamer-Katalog (8.940 Serien ueber die TMDb-Network-Achse) brachte
hunderte Allerweltswoerter als Titel in die Whitelist. Danach fand der
Matcher "Driven", "Personality" oder "Classified" als Substring in ganz
normalen Marketing-Captions — 83 Fehlzuordnungen in einem Cron-Lauf.

Der Fix schliesst solche Woerter aus dem UNSCHARFEN Substring-Pfad aus.
Die Trennlinie, die diese Tests bewachen: Ein Post, der das Wort nur
zufaellig ENTHAELT, matcht nicht mehr; ein Post, der den Titel wirklich
NENNT (Volltreffer, Hashtag), matcht weiterhin — sonst waeren echte
Filme wie "Focus" oder "Prime" unauffindbar geworden.
"""
from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Title
from app.services.whitelist_matcher import find_best_title_match


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_generisches_wort_matcht_nicht_als_fremd_substring(session: Session):
    """Der Vorfall: eine Caption enthaelt beilaeufig "driven" — das darf
    die Streamer-Serie "Driven" nicht als Treffer erzeugen."""
    session.add(Title(tmdb_id=999001, title_original="Driven", active=True))
    session.commit()

    caption = "Our cast is driven by one thing: telling this story right."
    match = find_best_title_match(session, caption)

    assert match.title is None, (
        "Ein Allerweltswort in einer Caption ist kein Titel-Treffer — "
        "genau daraus entstanden die 83 Fehlzuordnungen."
    )


def test_generischer_titel_matcht_weiter_als_volltreffer(session: Session):
    """Gegenprobe, damit der Schutz keine echten Filme verschluckt:
    nennt der Post den Titel als Ganzes, greift der exakte Pfad."""
    session.add(Title(tmdb_id=999002, title_original="Focus", active=True))
    session.commit()

    match = find_best_title_match(session, "Focus")

    assert match.title is not None
    assert match.title.title_original == "Focus"
    assert match.confidence >= 0.95, "Volltreffer bleibt eine sichere Quelle."


def test_generischer_titel_matcht_weiter_ueber_hashtag(session: Session):
    """Zweiter erlaubter Pfad: der Hashtag nennt den Titel ausdruecklich."""
    session.add(
        Title(tmdb_id=999003, title_original="Prime", aliases=["Prime"], active=True)
    )
    session.commit()

    match = find_best_title_match(session, "Tickets are live #Prime")

    assert match.title is not None, (
        "Ein Hashtag ist eine bewusste Nennung, kein Zufallsfund."
    )


def test_nicht_generischer_ein_wort_titel_matcht_weiter_als_substring(session: Session):
    """Die Liste ist eng: ein spezifischer Ein-Wort-Titel wie
    "Boiúna" bleibt im Substring-Pfad, sonst haetten wir den Recall
    zerstoert, den die Queue braucht."""
    session.add(Title(tmdb_id=999004, title_original="Boiúna", active=True))
    session.commit()

    match = find_best_title_match(session, "Beware Boiúna — only in cinemas")

    assert match.title is not None
    assert match.title.title_original == "Boiúna"
