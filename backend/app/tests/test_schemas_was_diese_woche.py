"""Sprint 9b (Entdopplung, Commit A): ``was_diese_woche`` ist aus den
Rollen-Sektionen ``FuerCutter`` / ``FuerMotionDesigner`` /
``FuerCreativeProducer`` vollständig gestrichen.

Hintergrund: Sprint 7-iter-2 hatte die Compliance-Listen
(``must_show`` / ``no_go``) durch ein Fließtext-Feld ``was_diese_woche``
ersetzt. Der Phase-0-Recon (Sprint 9b) hat gezeigt, dass dasselbe
``was_diese_woche`` in allen drei Rollen-Sektionen denselben
Wochen-Befund wiederholte (Overlap A). Der rollenspezifische
``schnitt_pace`` / ``caption_style`` / ``strategische_pattern`` trägt den
Inhalt jetzt disjunkt.

Dieser Test ist umgedreht: er ist der Regression-Guard, dass
``was_diese_woche`` NICHT mehr im Schema steht — plus der Nachweis, dass
persistierte Alt-Briefe mit dem alten Feld (oder den noch älteren
``must_show`` / ``no_go``-Listen) via ``extra='ignore'`` weiter parsen.
"""
from __future__ import annotations

from app.schemas.insights import (
    FuerCreativeProducer,
    FuerCutter,
    FuerMotionDesigner,
    LLMReport,
)


# ---------- Regression-Guard: was_diese_woche ist gestrichen ---------------


def test_fuer_cutter_has_no_was_diese_woche_field():
    assert "was_diese_woche" not in FuerCutter.model_fields
    # Die rollenspezifischen Felder bleiben und tragen den Inhalt disjunkt.
    assert "schnitt_pace" in FuerCutter.model_fields


def test_fuer_motion_designer_has_no_was_diese_woche_field():
    assert "was_diese_woche" not in FuerMotionDesigner.model_fields
    assert "caption_style" in FuerMotionDesigner.model_fields


def test_fuer_creative_producer_has_no_was_diese_woche_field():
    assert "was_diese_woche" not in FuerCreativeProducer.model_fields
    assert "strategische_pattern" in FuerCreativeProducer.model_fields


def _all_property_names(schema: dict) -> set[str]:
    """Sammelt jeden ``properties``-Key über das Top-Level-Schema und alle
    geschachtelten ``$defs`` ein. Bewusst NUR die Property-Namen, nicht die
    ``description``-Texte — die Docstrings dürfen ``was_diese_woche``
    erklärend erwähnen, ohne dass es ein Feld ist."""
    names: set[str] = set()
    nodes = [schema, *schema.get("$defs", {}).values()]
    for node in nodes:
        props = node.get("properties")
        if isinstance(props, dict):
            names.update(props.keys())
    return names


def test_was_diese_woche_absent_from_llm_report_tool_schema():
    """Der forced-tool-use-Input-Schema baut auf ``LLMReport.model_json_schema()``.
    Wenn ``was_diese_woche`` dort noch als Property steht, kann der LLM es
    opportunistisch weiter füllen — der Guard prüft alle Property-Namen über
    die geschachtelten ``$defs`` (Rollen-Sektionen)."""
    names = _all_property_names(LLMReport.model_json_schema())
    assert "was_diese_woche" not in names
    # Sanity: die disjunkten Rollen-Felder sind weiterhin Properties.
    assert {"schnitt_pace", "caption_style", "strategische_pattern"} <= names


# ---------- Commit B: cross_market_chancen ist gestrichen ------------------


def test_fuer_creative_producer_has_no_cross_market_chancen_field():
    """Sprint 9b (Entdopplung, Commit B): ``cross_market_chancen`` raus —
    ``cross_market_insight`` ist die einzige Markt-Vergleichs-Sektion."""
    assert "cross_market_chancen" not in FuerCreativeProducer.model_fields
    assert "strategische_pattern" in FuerCreativeProducer.model_fields
    assert "format_empfehlungen" in FuerCreativeProducer.model_fields


def test_cross_market_chancen_absent_from_llm_report_tool_schema():
    names = _all_property_names(LLMReport.model_json_schema())
    assert "cross_market_chancen" not in names


def test_fuer_creative_producer_ignores_legacy_cross_market_chancen():
    """Persistierter Alt-Brief mit ``cross_market_chancen`` parst weiter
    via ``extra='ignore'``; das Feld wird beim Re-Hydrate verworfen."""
    legacy_payload = {
        "strategische_pattern": "zwei Lager",
        "cross_market_chancen": "DE adaptiert US",
        "format_empfehlungen": "zwei Pakete",
    }
    cp = FuerCreativeProducer.model_validate(legacy_payload)
    assert cp.strategische_pattern == "zwei Lager"
    assert "cross_market_chancen" not in cp.model_dump()


# ---------- Forward path: Rollen-Sektionen ohne das Feld -------------------


def test_fuer_cutter_constructs_without_was_diese_woche():
    cutter = FuerCutter(
        schnitt_pace="kurze Cuts unter 25 Sekunden",
        hook_strategie="Cast-Beat in den ersten zwei Sekunden",
        empfohlene_laengen="22 Sekunden primär",
    )
    assert cutter.schnitt_pace.startswith("kurze Cuts")
    assert not hasattr(cutter, "was_diese_woche")


def test_fuer_motion_designer_constructs_without_was_diese_woche():
    md = FuerMotionDesigner(
        caption_style="DE-Captions kürzer, US erzählt mehr",
        text_overlay="kein Overlay in den ersten Sekunden",
        branding_einsatz="End Card einmalig am Ende",
    )
    assert "US" in md.caption_style
    assert not hasattr(md, "was_diese_woche")


def test_fuer_creative_producer_constructs_without_was_diese_woche():
    cp = FuerCreativeProducer(
        strategische_pattern="zwei Lager: kurz und vertraut, lang und emotional",
        cross_market_chancen="DE hat das kurze Pattern, fehlt das emotionale Hero-Asset",
        format_empfehlungen="zwei Standard-Pakete pro Verleih-Kunde",
    )
    assert "Lager" in cp.strategische_pattern
    assert not hasattr(cp, "was_diese_woche")


# ---------- Backwards-Compat: Alt-Felder werden ignoriert ------------------


def test_fuer_cutter_ignores_legacy_was_diese_woche_and_lists():
    """Persistierter Sprint-7-Brief: ``was_diese_woche`` plus die noch
    älteren ``must_show`` / ``no_go``-Listen. ``extra='ignore'`` lässt das
    ohne Crash durch; alle drei Alt-Felder werden silently verworfen."""
    legacy_payload = {
        "schnitt_pace": "kurze Cuts",
        "hook_strategie": "Cast-Beat",
        "empfohlene_laengen": "22 Sekunden",
        "was_diese_woche": "alter Fließtext",
        "must_show": ["alt 1", "alt 2"],
        "no_go": ["alt 3"],
    }
    cutter = FuerCutter.model_validate(legacy_payload)
    assert cutter.schnitt_pace == "kurze Cuts"
    dumped = cutter.model_dump()
    assert "was_diese_woche" not in dumped
    assert "must_show" not in dumped
    assert "no_go" not in dumped


def test_fuer_motion_designer_ignores_legacy_was_diese_woche():
    legacy_payload = {
        "caption_style": "kurz",
        "text_overlay": "minimal",
        "branding_einsatz": "End Card",
        "was_diese_woche": "alter Fließtext",
        "some_legacy_field": ["foo", "bar"],
    }
    md = FuerMotionDesigner.model_validate(legacy_payload)
    assert md.caption_style == "kurz"
    assert "was_diese_woche" not in md.model_dump()


def test_fuer_creative_producer_ignores_legacy_was_diese_woche():
    legacy_payload = {
        "strategische_pattern": "zwei Lager",
        "cross_market_chancen": "DE-Lücke",
        "format_empfehlungen": "zwei Pakete",
        "was_diese_woche": "alter Fließtext",
    }
    cp = FuerCreativeProducer.model_validate(legacy_payload)
    assert cp.strategische_pattern == "zwei Lager"
    assert "was_diese_woche" not in cp.model_dump()
