"""Fuzzy-Compact-Längen-Guard — Initialen-/Kurz-Titel-Fuzzy-Lücke.

Der Fuzzy-Zweig in ``find_best_title_match`` bekommt denselben Compact-Längen-
Guard wie der Substring-Pfad: Kandidaten-Titel mit compact-Länge
<= ``_MIN_SUBSTRING_CANDIDATE_LEN`` (4) erhalten KEINEN Fuzzy-Match mehr.

Hintergrund: Initialen-Titel normalisieren zu reinen "x y z"-Mustern. "M.I.A."
→ "m i a" (compact "mia"=3); ein Post "M:I:6" → "m i 6" liefert dagegen
``SequenceMatcher.ratio`` ~0.8 (> 0.72) → bisher ein falscher Fuzzy-Candidate.
Exact-, Hashtag- und exakter Textpfad bleiben längenunabhängig — kurze Titel
verlieren nur die unscharfe Annäherung, nie den Volltreffer.
"""

from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Title
from app.services.whitelist_matcher import find_best_title_match


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


# 1. KAUSAL (M:I:6 → M.I.A.): Feldtext "M:I:6" (normalisiert "m i 6") gegen
#    Titel "M.I.A." (compact "mia"=3 <= 4) → KEIN Fuzzy-Match mehr.
def test_short_initials_title_no_longer_fuzzy_matches():
    with _session() as session:
        _add(session, title_original="M.I.A.", aliases=["MIA"])

        match = find_best_title_match(session, "M:I:6")

        assert match.title is None
        assert match.source != "fuzzy"


# 2. REGRESSION (kritisch): langer Titel behält Fuzzy. "Drawn to You"
#    (compact "drawntoyou"=10 > 4) vs Tippfehler "Drawn to Yu" → Fuzzy feuert.
def test_long_title_still_fuzzy_matches_on_typo():
    with _session() as session:
        drawn = _add(session, title_original="Drawn to You")

        match = find_best_title_match(session, "Drawn to Yu")

        assert match.title is not None and match.title.id == drawn.id
        assert match.source == "fuzzy"
        assert match.confidence > 0.72


# 3. ≤4-Titel Volltreffer bleibt: "Rio 2" (compact "rio2"=4) per EXACT-Match
#    gegen "rio 2" — Exact ist längenunabhängig, Guard betrifft nur Fuzzy.
def test_short_title_exact_match_preserved():
    with _session() as session:
        rio = _add(session, title_original="Rio 2", aliases=["Rio 2"])

        match = find_best_title_match(session, "rio 2")

        assert match.title is not None and match.title.id == rio.id
        assert match.source == "exact"


# 4. ≤4-Titel Hashtag-Treffer bleibt: "Dune" (compact "dune"=4) per #Dune.
def test_short_title_hashtag_match_preserved():
    with _session() as session:
        dune = _add(session, title_original="Dune", aliases=["Dune"])

        match = find_best_title_match(session, "new clip dropped #Dune")

        assert match.title is not None and match.title.id == dune.id
        assert match.source == "hashtag"
