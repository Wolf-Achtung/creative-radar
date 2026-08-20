"""Hook-Intelligence Teil 1 (20.08.2026) — Caption-Mechanik als
Muster-Dimensionen.

Die vier Extraktoren sind deterministisch (kein LLM, kein
Konfidenz-Filter) und wirken deshalb rueckwirkend auf dem gesamten
Post-Bestand — das ist der Grund, warum dieser Teil VOR der
Vision-Erweiterung ausgeliefert wird. Getestet werden die zwei
Hygiene-Regeln (URLs vor der Frage-Messung entfernen, Hashtags vor der
Laengen-Messung), die Bucket-Grenzen exakt an der Kante und die
End-to-End-Sichtbarkeit im Bericht samt Mehrfachvergleichs-Note.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.entities import Channel, Market, Post
from app.services import trailer_patterns as tp

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _post_mit_caption(caption):
    return SimpleNamespace(caption=caption)


# ---------------------------------------------------------------------
# 1 — caption_frage: URLs duerfen nicht als Frage zaehlen
# ---------------------------------------------------------------------


def test_frage_erkennt_fragezeichen():
    assert tp._extract_caption_frage(
        _post_mit_caption("Wer hat das kommen sehen?")
    ) == "mit_frage"
    assert tp._extract_caption_frage(
        _post_mit_caption("Ab Donnerstag im Kino.")
    ) == "ohne_frage"


def test_frage_ignoriert_query_strings_in_urls():
    assert tp._extract_caption_frage(
        _post_mit_caption("Trailer: https://x.test/watch?v=abc&t=1")
    ) == "ohne_frage", (
        "Das '?' steckt im Query-String der URL, nicht im Text — eine "
        "Zelle 'mit_frage' auf dieser Basis waere ein Messfehler."
    )


def test_leere_caption_liefert_kein_merkmal():
    assert tp._extract_caption_frage(_post_mit_caption("")) is None
    assert tp._extract_caption_frage(_post_mit_caption(None)) is None
    assert tp._extract_caption_frage(
        _post_mit_caption("https://x.test/nur-ein-link")
    ) is None


# ---------------------------------------------------------------------
# 2 — caption_cta: konservative Marker, DE und EN
# ---------------------------------------------------------------------


def test_cta_marker_de_und_en():
    assert tp._extract_caption_cta(
        _post_mit_caption("JETZT IM KINO — nur fuer kurze Zeit")
    ) == "mit_cta"
    assert tp._extract_caption_cta(
        _post_mit_caption("Get tickets today!")
    ) == "mit_cta"
    assert tp._extract_caption_cta(
        _post_mit_caption("Ein Blick hinter die Kulissen.")
    ) == "ohne_cta"


# ---------------------------------------------------------------------
# 3 — caption_laenge: Hashtags zaehlen nicht als Text
# ---------------------------------------------------------------------


def test_laenge_grenzen_exakt():
    assert tp._extract_caption_laenge(_post_mit_caption("x" * 80)) == "kurz"
    assert tp._extract_caption_laenge(_post_mit_caption("x" * 81)) == "mittel"
    assert tp._extract_caption_laenge(_post_mit_caption("x" * 199)) == "mittel"
    assert tp._extract_caption_laenge(_post_mit_caption("x" * 200)) == "lang"


def test_laenge_misst_ohne_hashtag_wand():
    kurz_mit_wand = "Kurzer Satz. " + " ".join(f"#tag{i}" for i in range(30))
    assert tp._extract_caption_laenge(_post_mit_caption(kurz_mit_wand)) == "kurz", (
        "Eine Hashtag-Wand macht aus einem kurzen Text keine lange "
        "Caption — gemessen wird der Text ohne Hashtags."
    )


def test_nur_hashtag_caption_hat_keine_laenge():
    assert tp._extract_caption_laenge(_post_mit_caption("#a #b #c")) is None


# ---------------------------------------------------------------------
# 4 — caption_hashtags: Buckets
# ---------------------------------------------------------------------


def test_hashtag_buckets():
    assert tp._extract_caption_hashtags(_post_mit_caption("Ohne alles")) == "keine"
    assert tp._extract_caption_hashtags(_post_mit_caption("x #a #b #c")) == "1-3"
    assert tp._extract_caption_hashtags(_post_mit_caption("x #a #b #c #d")) == "4+"


# ---------------------------------------------------------------------
# 5 — End-to-End: die Dimensionen stehen im Bericht, die
#     Mehrfachvergleichs-Note erscheint ab 20 gepruefte Zellen
# ---------------------------------------------------------------------


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


def _kanal(session: Session) -> Channel:
    ch = Channel(
        name=f"ch-{uuid4().hex[:6]}",
        platform="tiktok",
        url=f"https://x.test/{uuid4()}",
        market=Market.US,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


def _post(session: Session, kanal: Channel, caption: str) -> Post:
    p = Post(
        channel_id=kanal.id,
        platform=kanal.platform,
        post_url=f"https://x.test/p/{uuid4()}",
        caption=caption,
        detected_at=NOW - timedelta(days=1),
        visible_views=1000,
        visible_likes=100,
        visible_comments=0,
        visible_bookmarks=0,
        raw_payload={},
    )
    session.add(p)
    session.commit()
    return p


def test_caption_dimensionen_stehen_im_bericht(session):
    kanal = _kanal(session)
    for _ in range(3):
        _post(session, kanal, "Wer will das sehen? Jetzt im Kino! #film")
    for _ in range(3):
        _post(session, kanal, "Ein ruhiger Blick hinter die Kulissen.")

    report = tp.compute_trailer_patterns(session, window_days=30, now=NOW)

    for name in ("caption_frage", "caption_cta", "caption_laenge", "caption_hashtags"):
        assert name in report.dimensions, f"Dimension {name} fehlt im Bericht"
    frage_werte = {c.value: c.sample_size for c in report.dimensions["caption_frage"]}
    assert frage_werte == {"mit_frage": 3, "ohne_frage": 3}
    cta_werte = {c.value: c.sample_size for c in report.dimensions["caption_cta"]}
    assert cta_werte == {"mit_cta": 3, "ohne_cta": 3}


def test_mehrfachvergleichs_note_logik():
    assert tp._mehrfachvergleichs_note(19) is None
    note_20 = tp._mehrfachvergleichs_note(20)
    assert note_20 is not None and "einem zufaelligen Scheinbefund" in note_20
    note_100 = tp._mehrfachvergleichs_note(100)
    assert "5 zufaelligen Scheinbefunden" in note_100


def _post_voll(session: Session, kanal: Channel, j: int) -> Post:
    """Ein Post mit Merkmalen in ALLEN analysefreien Dimensionen plus
    Analyse-Dict — Baustein fuer das 24-Zellen-Szenario unten."""
    captions = [
        "Wer sieht das? Jetzt im Kino! #a #b #c #d",
        "Ruhiger Einblick.",
        ("Langer Text ueber den Dreh, die Crew und den Schnitt. " * 5) + "#making",
        ("x" * 100) + "?",
    ]
    duration = [10, 20, 45, 100][j % 4]
    p = Post(
        channel_id=kanal.id,
        platform=kanal.platform,
        post_url=f"https://x.test/p/{uuid4()}",
        caption=captions[j % 4],
        detected_at=NOW - timedelta(days=1),
        visible_views=1000 + j,
        # Jeder zehnte Post ist ein Ausreisser (20x Aktivierung) — ohne
        # Breakouts im Korpus ist keine z-Statistik berechenbar und JEDE
        # Zelle bliebe 'insufficient'.
        visible_likes=2000 if j % 10 == 0 else 100,
        visible_comments=0,
        visible_bookmarks=0,
        duration_seconds=duration,
        analysis={
            "format": "trailer" if j % 2 == 0 else "clip",
            "tone": "emotional" if j % 2 == 0 else "humorous",
            "lifecycle_stage": "launch" if j % 2 == 0 else "evergreen",
            "confidence": 0.9,
        },
        raw_payload={
            "_creative_radar_music": {"musicOriginal": j % 2 == 0},
        },
    )
    session.add(p)
    session.commit()
    return p


def test_note_erscheint_im_bericht_ab_20_zellen(session):
    """End-to-End: 3 Kanaele x 20 Posts erzeugen ueber alle Dimensionen
    hinweg >= 20 belastbare Zellen — der Bericht muss dann die
    Mehrfachvergleichs-Note tragen. Faellt, wenn jemand die Note aus
    ``compute_trailer_patterns`` entfernt oder die Zaehlung bricht."""
    kanaele = [_kanal(session) for _ in range(3)]
    for kanal in kanaele:
        for j in range(20):
            _post_voll(session, kanal, j)

    report = tp.compute_trailer_patterns(session, window_days=30, now=NOW)

    tested = sum(
        1
        for cells in report.dimensions.values()
        for c in cells
        if c.breakout_verdict != "insufficient"
    )
    assert tested >= 20, f"Szenario liefert nur {tested} belastbare Zellen"
    assert any("Scheinbefund" in n for n in report.notes)
