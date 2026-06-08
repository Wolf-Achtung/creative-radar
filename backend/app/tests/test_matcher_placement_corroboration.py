"""Matcher-Korroboration: placement-only-Treffer → kein stiller Auto-Match.

Ein Strong-Hit, dessen Quelle AUSSCHLIESSLICH ``placement_title_text`` ist
(field_key ``"suggested_title"``), wird ``only_from_placement=True`` markiert
und damit von ``is_safe_auto_match`` gesperrt — er fliesst als TitleCandidate
statt als stiller ``title_id``-Set ein. Hintergrund: die Vision-OCR der
placement-Zone faengt Hintergrund-/Lineup-/End-Card-Text ein (z.B. "Andor" auf
einem Mandalorian/Grogu-Clip).

KRITISCHE REGEL (Schutz der korrekten placement-Faelle): sobald IRGENDEIN
weiteres Feld (caption/ocr_text/ai_summary) denselben Titel ebenfalls trifft,
ist ``field_origins`` groesser als ``{"suggested_title"}`` →
``only_from_placement=False`` → Auto-Match bleibt erhalten.
"""

from sqlmodel import SQLModel, Session, create_engine

from app.models.entities import Title
from app.services.whitelist_matcher import find_best_title_match, is_safe_auto_match


def _session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _add(session, **kwargs) -> Title:
    title = Title(active=True, **kwargs)
    session.add(title)
    session.commit()
    session.refresh(title)
    return title


# 1. GROGU-FALL: nur placement="Andor" trifft, caption/ai_summary treffen
#    keinen Title-String → only_from_placement=True → KEIN Auto-Match.
def test_placement_only_match_is_not_safe_auto_match():
    with _session() as session:
        andor = _add(session, title_original="Andor")

        match = find_best_title_match(
            session,
            None,
            fields={
                "caption": "Grogu is back — baby yoda returns this week",
                "ai_summary_de": "Kurzer Clip mit dem kleinen gruenen Charakter.",
                "suggested_title": "Andor",  # field_key == placement_title_text
            },
        )

        assert match.title is not None and match.title.id == andor.id
        assert match.source == "exact"
        assert match.confidence == 1.0
        # Allein-Treffer aus placement → Flag gesetzt → Gate sperrt.
        assert match.only_from_placement is True
        assert is_safe_auto_match(match) is False


# 2. KORROBORATION (Schutz der 87): placement UND caption treffen denselben
#    Titel → field_origins enthaelt mehr als nur "suggested_title" →
#    only_from_placement=False → BLEIBT Auto-Match.
def test_placement_corroborated_by_caption_stays_safe_auto_match():
    with _session() as session:
        mk2 = _add(session, title_original="Mortal Kombat II")

        match = find_best_title_match(
            session,
            None,
            fields={
                "caption": "Get ready for Mortal Kombat II in cinemas",
                "suggested_title": "Mortal Kombat II",
            },
        )

        assert match.title is not None and match.title.id == mk2.id
        assert match.confidence == 1.0
        assert match.only_from_placement is False
        assert is_safe_auto_match(match) is True


# 3. REGRESSION: caption-only exact (kein placement) → Auto-Match wie bisher.
def test_caption_only_match_stays_safe_auto_match():
    with _session() as session:
        andor = _add(session, title_original="Andor")

        match = find_best_title_match(session, None, fields={"caption": "Andor"})

        assert match.title is not None and match.title.id == andor.id
        assert match.source == "exact"
        assert match.only_from_placement is False
        assert is_safe_auto_match(match) is True


# 4. Multi-Entry/Längster aus placement-only → Flag greift auch hier.
#    placement="Mortal Kombat II" trifft BEIDE Titel (Substring + Exact);
#    der laengere matched_text ("mortal kombat ii") gewinnt via Variante D,
#    aber die einzige Quelle bleibt placement.
def test_placement_only_longest_branch_is_not_safe_auto_match():
    with _session() as session:
        _add(session, title_original="Mortal Kombat")
        mk2 = _add(session, title_original="Mortal Kombat II")

        match = find_best_title_match(
            session, None, fields={"suggested_title": "Mortal Kombat II"}
        )

        assert match.title is not None and match.title.id == mk2.id
        assert match.only_from_placement is True
        assert is_safe_auto_match(match) is False


# 5. ambiguous → unveraendert: title=None, source bleibt "ambiguous",
#    only_from_placement bleibt Default False (kein placement-Sonderpfad).
def test_ambiguous_unchanged_and_flag_defaults_false():
    with _session() as session:
        _add(session, title_original="Alpha")
        _add(session, title_original="Gamma")

        # Beide gleich lange Substring-Treffer aus derselben caption, keine
        # Franchise/Release → Variante D loest nicht auf → ambiguous.
        match = find_best_title_match(session, None, fields={"caption": "alpha gamma"})

        assert match.title is None
        assert match.source == "ambiguous"
        assert match.only_from_placement is False
        assert is_safe_auto_match(match) is False


# 6. Default-Sicherheit: Nicht-strong-hit-Pfade (empty/none) tragen das Flag
#    als Default False → bestehende MatchResult-Konstruktionen unveraendert.
def test_non_strong_hit_paths_default_flag_false():
    with _session() as session:
        _add(session, title_original="Andor")

        empty = find_best_title_match(session, None, fields={"caption": "   "})
        assert empty.source == "empty"
        assert empty.only_from_placement is False

        none_guess = find_best_title_match(session, "completely unrelated text here")
        assert none_guess.source == "none"
        assert none_guess.only_from_placement is False
