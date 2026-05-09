"""Sprint 7-iter-2 — was_diese_woche als Fließtext-Feld in Cutter / Motion-
Designer / Creative-Producer-Sektionen, plus Backwards-Compat zu
persistierten Briefen mit den alten ``must_show`` / ``no_go``-Listen.

Compliance-Listen-Schemafelder erzwingen Bullet-Output, egal was der
Prompt sagt — ein Fließtext-Feld zwingt den LLM zur Erzählung. Die
alten Felder werden via ``extra='ignore'`` aus dem JSON-Parse silently
verworfen; das Frontend rendert sie ebenfalls nicht mehr.
"""
from __future__ import annotations

from app.schemas.insights import (
    FuerCreativeProducer,
    FuerCutter,
    FuerMotionDesigner,
)


# ---------- Forward path: was_diese_woche akzeptiert -----------------------


def test_fuer_cutter_accepts_was_diese_woche():
    cutter = FuerCutter(
        schnitt_pace="kurze Cuts unter 25 Sekunden",
        hook_strategie="Cast-Beat in den ersten zwei Sekunden",
        empfohlene_laengen="22 Sekunden primär",
        was_diese_woche="Was hier auffällt: die Mandalorian-Erinnerungen liegen "
                       "im schwierigen Mittelbereich.",
    )
    assert cutter.was_diese_woche.startswith("Was hier auffällt")
    # must_show / no_go sind keine Felder mehr, also auch nicht abrufbar
    assert not hasattr(cutter, "must_show") or cutter.must_show is None  # type: ignore[attr-defined]


def test_fuer_motion_designer_accepts_was_diese_woche():
    md = FuerMotionDesigner(
        caption_style="DE-Captions kürzer, US erzählt mehr",
        text_overlay="kein Overlay in den ersten Sekunden",
        branding_einsatz="End Card einmalig am Ende",
        was_diese_woche="Was hier auffällt: US-Captions arbeiten erzählerisch.",
    )
    assert "erzählerisch" in md.was_diese_woche


def test_fuer_creative_producer_accepts_was_diese_woche():
    cp = FuerCreativeProducer(
        strategische_pattern="zwei Lager: kurz und vertraut, lang und emotional",
        cross_market_chancen="DE hat das kurze Pattern, fehlt das emotionale Hero-Asset",
        format_empfehlungen="zwei Standard-Pakete pro Verleih-Kunde",
        was_diese_woche="Was hier auffällt: die zwei Lager sind auch Produktions-Modelle.",
    )
    assert "Produktions-Modelle" in cp.was_diese_woche


# ---------- Backwards-Compat: alte Listen-Felder werden ignoriert ----------


def test_fuer_cutter_ignores_legacy_must_show_no_go():
    """Persistierter Sprint-1-7-Brief: ``must_show`` / ``no_go`` als
    Listen plus die neuen Felder. ``extra='ignore'`` lässt das durch
    ohne Crash; die Listen werden silently verworfen, das Frontend
    rendert sowieso nur ``was_diese_woche``."""
    legacy_payload = {
        "schnitt_pace": "kurze Cuts",
        "hook_strategie": "Cast-Beat",
        "empfohlene_laengen": "22 Sekunden",
        "was_diese_woche": "neuer Fließtext",
        "must_show": ["alt 1", "alt 2"],
        "no_go": ["alt 3"],
    }
    cutter = FuerCutter.model_validate(legacy_payload)
    assert cutter.was_diese_woche == "neuer Fließtext"
    # Die Listen wurden silently verworfen — ``model_dump()`` enthält sie
    # nicht mehr, der Round-Trip ist Voice-2.5-clean.
    dumped = cutter.model_dump()
    assert "must_show" not in dumped
    assert "no_go" not in dumped


def test_fuer_cutter_handles_legacy_brief_without_was_diese_woche():
    """Sprint-1-7-Brief ohne ``was_diese_woche``-Feld: das Feld bleibt
    None, kein Fehler; das Frontend rendert dann den Block leer
    (graceful degrade)."""
    legacy_payload = {
        "schnitt_pace": "kurze Cuts",
        "hook_strategie": "Cast-Beat",
        "empfohlene_laengen": "22 Sekunden",
        "must_show": ["alt 1"],
        "no_go": ["alt 2"],
    }
    cutter = FuerCutter.model_validate(legacy_payload)
    assert cutter.was_diese_woche is None
    assert cutter.schnitt_pace == "kurze Cuts"


def test_fuer_motion_designer_extra_ignore():
    """Falls in einem alten Brief unbekannte Zusatzfelder stecken,
    werden sie still verworfen statt ValidationError zu werfen."""
    legacy_payload = {
        "caption_style": "kurz",
        "text_overlay": "minimal",
        "branding_einsatz": "End Card",
        "some_legacy_field": ["foo", "bar"],
    }
    md = FuerMotionDesigner.model_validate(legacy_payload)
    assert md.caption_style == "kurz"
    assert md.was_diese_woche is None


def test_fuer_creative_producer_extra_ignore():
    legacy_payload = {
        "strategische_pattern": "zwei Lager",
        "cross_market_chancen": "DE-Lücke",
        "format_empfehlungen": "zwei Pakete",
        "old_compliance_block": ["item 1", "item 2"],
    }
    cp = FuerCreativeProducer.model_validate(legacy_payload)
    assert cp.strategische_pattern == "zwei Lager"
    assert cp.was_diese_woche is None
