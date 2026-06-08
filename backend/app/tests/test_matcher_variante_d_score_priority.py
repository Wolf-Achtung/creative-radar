"""Variante D Teil 1 — Score-Klasse VOR matched_text-Länge.

Fix B: Bei konkurrierenden Strong-Hits gewinnt zuerst die hoehere Score-Klasse
(hashtag/exact = 1.0 vor unique_text = 0.97), erst danach entscheidet die
matched_text-Länge als Tiebreak INNERHALB derselben Score-Klasse.

Behebt die Floskel-Inversion (Jumanji-Fall): die Alltagsphrase "the boys"
(unique_text, 8 Zeichen) darf den eigentlichen #Jumanji (hashtag, 7 Zeichen)
nicht schlagen, nur weil ihr Substring ein Zeichen länger ist. Die legitime
Längen-Spezifität bei GLEICHEM Score ("Mortal Kombat II" > "Mortal Kombat")
bleibt unberührt — abgesichert in test_matcher_variante_d.py.
"""

from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Title
from app.services.whitelist_matcher import find_best_title_match, is_safe_auto_match


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _add(session: Session, **kwargs) -> Title:
    title = Title(active=True, **kwargs)
    session.add(title)
    session.commit()
    session.refresh(title)
    return title


# 1. JUMANJI-INVERSION: Floskel "the boys" (unique_text/0.97, matched 8) vs
#    expliziter #Jumanji (hashtag/1.0, matched 7) → Jumanji gewinnt, weil
#    die hoehere Score-Klasse vor der Länge rankt.
def test_higher_score_beats_longer_substring():
    with _session() as session:
        _add(session, title_original="The Boys")
        jumanji = _add(session, title_original="Jumanji")

        match = find_best_title_match(session, "The boys are SO back \U0001f40d #Jumanji #Shorts")

        assert match.title is not None and match.title.id == jumanji.id
        assert match.source == "hashtag"
        assert match.confidence == 1.0
        # Korrekter Hashtag-Titel → weiter auto-match-faehig.
        assert is_safe_auto_match(match) is True


# 2. REGRESSION (legitime Längen-Spezifität bei GLEICHEM Score): beide Treffer
#    sind unique_text/0.97 → Score gleich → Länge entscheidet → das Sequel
#    "Mortal Kombat II" (länger) gewinnt weiterhin.
def test_equal_score_longer_match_still_wins():
    with _session() as session:
        _add(session, title_original="Mortal Kombat", franchise="Mortal Kombat")
        mk2 = _add(session, title_original="Mortal Kombat II", franchise="Mortal Kombat")

        match = find_best_title_match(session, "Mortal Kombat II - Baraka vs Johnny Cage")

        assert match.title is not None and match.title.id == mk2.id
        assert is_safe_auto_match(match) is True


# 3. EDGE: zwei gleich hohe Score-Treffer (beide Hashtag/1.0) unterschiedlicher
#    Länge → der Längen-Tiebreak greift INNERHALB der Score-Klasse → der
#    spezifischere (längere) Hashtag-Titel gewinnt.
def test_equal_high_score_length_tiebreak_within_class():
    with _session() as session:
        _add(session, title_original="Jumanji")
        sequel = _add(session, title_original="Jumanji Welcome to the Jungle")

        match = find_best_title_match(session, "#Jumanji #JumanjiWelcomeToTheJungle")

        assert match.title is not None and match.title.id == sequel.id
        assert match.source == "hashtag"
        assert match.confidence == 1.0


# 4. EDGE-Gegenprobe: kürzerer Hashtag (1.0) vs längere Floskel (0.97) — auch
#    wenn die Floskel deutlich länger ist, gewinnt der Score. Sichert ab, dass
#    nicht etwa eine Längen-Schwelle den Score aushebelt.
def test_short_hashtag_beats_much_longer_phrase():
    with _session() as session:
        long_phrase = _add(session, title_original="Once Upon a Time in Hollywood")
        dune = _add(session, title_original="Dune")

        # "once upon a time in hollywood" (29) als Floskel-Substring (unique_text/
        # 0.97) vs #Dune (hashtag/1.0, matched 4). "Dune" hat compact-Länge 4 und
        # matcht damit NUR ueber den Hashtag-Pfad (Substring-Pfad verlangt > 4) —
        # der hochwertige Signal-Pfad. Score schlaegt die viel laengere Floskel.
        match = find_best_title_match(
            session, "once upon a time in hollywood vibes #Dune"
        )

        assert match.title is not None and match.title.id == dune.id
        assert match.source == "hashtag"
        assert long_phrase.id != dune.id
