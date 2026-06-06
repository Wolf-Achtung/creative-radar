"""Substring-Magnet-Schutz (Klasse 1) — Regression-Pins für den Matcher-Fix.

Hintergrund (Prod-Diagnose 06.06): der Whitelist-Matcher machte kurze
Titel-/Alias-Strings zu Sammel-Mülleimern, weil ihre Compact-Form als
Fragment in fremden Captions/Hashtags vorkam ("chao" in "#chaoticenergy",
Token "Yes"/"Kara"/"mia"). Der Fix schließt Kandidaten mit Compact-Länge
``<= _MIN_SUBSTRING_CANDIDATE_LEN`` (=4) aus den UNSCHARFEN Substring-Pfaden
aus — dem Compact-Hashtag-Fallback und dem ``_contains_phrase``-Token-Match.

Exakte Gleichheit und exakter Hashtag bleiben längenunabhängig: kurze Titel
matchen weiter über echte Volltreffer, nur nicht mehr als Fremd-Substring.
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


# --------------------------------------------------------------------------
# BLOCK: kurze Kandidaten dürfen NICHT mehr als Fremd-Substring/-Token matchen
# --------------------------------------------------------------------------


def test_short_title_not_matched_inside_glued_hashtag(session: Session):
    """"ChaO" (compact "chao", 4) darf nicht in einem fremden glued Hashtag
    (#chaoticenergy) als Compact-Fallback feuern."""
    session.add(Title(tmdb_id=1419053, title_original="ChaO", aliases=["ChaO"], active=True))
    session.commit()

    caption = "Watch the chaos unfold #chaoticenergy"
    match = find_best_title_match(session, caption)

    assert match.title is None, "kurzer 4-Zeichen-Titel darf nicht als Hashtag-Fragment matchen"
    assert match.source != "hashtag"


def test_short_title_not_matched_as_numeric_fragment(session: Session):
    """"Rio 2" (compact "rio2", 4) darf nicht als Fragment in "#rio2024recap"
    matchen."""
    session.add(Title(tmdb_id=121856, title_original="Rio 2", aliases=["Rio 2"], active=True))
    session.commit()

    caption = "Best carnival moment #rio2024recap"
    match = find_best_title_match(session, caption)

    assert match.title is None


def test_three_letter_title_not_matched_as_token(session: Session):
    """"M.I.A." (compact "mia", 3) darf nicht als Token in fremder Caption
    matchen."""
    session.add(Title(title_original="M.I.A.", aliases=["MIA"], active=True))
    session.commit()

    match = find_best_title_match(session, "Mia could not believe what happened next")

    assert match.title is None


def test_short_alias_not_matched_as_token_kara(session: Session):
    """Alias "Kara" (4) darf nicht als Wortgrenzen-Token in fremder Caption
    feuern (Original "கர" ist 2 Zeichen und ohnehin durch das ≤2-Gate raus)."""
    session.add(Title(title_original="கர", aliases=["Kara"], active=True))
    session.commit()

    match = find_best_title_match(session, "Kara walked home after the show")

    assert match.title is None


def test_short_alias_not_matched_as_token_yes(session: Session):
    """Alias "Yes" (3) darf nicht als Token in fremder Caption feuern."""
    session.add(Title(title_original="כן", aliases=["Yes"], active=True))
    session.commit()

    match = find_best_title_match(session, "Yes I absolutely agree with this take")

    assert match.title is None


# --------------------------------------------------------------------------
# PRESERVE: Treffer ab Compact-Länge 5 + exakte Treffer bleiben erhalten
# --------------------------------------------------------------------------


def test_five_letter_alias_still_matches_as_token_tuner(session: Session):
    """Alias "Tuner" (5) > Schwelle → Token-Match bleibt erhalten."""
    session.add(Title(title_original="Tuner", aliases=["Tuner"], active=True))
    session.commit()

    match = find_best_title_match(session, "The Tuner is the standout of the week")

    assert match.title is not None
    assert match.title.title_original == "Tuner"


def test_five_letter_title_still_matches_as_token_hokum(session: Session):
    """"Hokum" (5) > Schwelle → Token-Match bleibt erhalten."""
    session.add(Title(title_original="Hokum", aliases=["Hokum"], active=True))
    session.commit()

    match = find_best_title_match(session, "What a Hokum show that was")

    assert match.title is not None
    assert match.title.title_original == "Hokum"


def test_long_alias_still_matches_as_token(session: Session):
    """Lange Aliase ("The Furious" 10, "Colony" 6) bleiben match-fähig."""
    session.add(Title(title_original="火遮眼", aliases=["The Furious", "Colony"], active=True))
    session.commit()

    m1 = find_best_title_match(session, "Everyone is talking about The Furious tonight")
    assert m1.title is not None

    m2 = find_best_title_match(session, "The Colony returns to theaters")
    assert m2.title is not None


def test_short_title_still_matches_via_exact_hashtag(session: Session):
    """Kurzer Titel "Andor" matcht weiter über EXAKTEN Hashtag #Andor
    (längenunabhängiger Pfad, vom Fix nicht berührt)."""
    session.add(Title(title_original="Andor", aliases=["Andor"], active=True))
    session.commit()

    match = find_best_title_match(session, "New clip dropped #Andor")

    assert match.title is not None
    assert match.title.title_original == "Andor"
    assert match.source == "hashtag"


def test_short_title_still_matches_via_exact_caption(session: Session):
    """Kurzer Titel matcht weiter über exakte Caption-Gleichheit."""
    session.add(Title(title_original="Andor", aliases=["Andor"], active=True))
    session.commit()

    match = find_best_title_match(session, "Andor")

    assert match.title is not None
    assert match.title.title_original == "Andor"


def test_long_compact_hashtag_fallback_preserved(session: Session):
    """Der legitime Compact-Fallback ("mortalkombat" in "mortalkombatmovie")
    bleibt erhalten — Compact-Key 12 > Schwelle, Coverage 12/17 ≥ 0.5."""
    session.add(
        Title(
            tmdb_id=931285,
            title_original="Mortal Kombat II",
            aliases=["Mortal Kombat", "Mortal Kombat II"],
            active=True,
        )
    )
    session.commit()

    match = find_best_title_match(session, "Beste Johnny Cage Impression? #mortalkombatmovie")

    assert match.title is not None
    assert match.title.title_original == "Mortal Kombat II"
    assert match.source == "hashtag"
