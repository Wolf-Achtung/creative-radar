"""Vision prompt — OpenAI Vision describes a creative-marketing
image and returns structured JSON for downstream analysis (Sprint
Alpha — Vision-Prompt v2).

The prompt was previously inline in ``services/visual_analysis.py``.
Externalising mirrors the P1 layout (see ``analyze_vision.py``) and
makes iteration / version-pinning sane.

Schema is flat-only. The parser in ``visual_analysis.py`` keeps its
nested-fallback (``title_placement.text`` / ``kinetics.has_kinetic``)
so already-stored responses don't have to be rewritten — but new
responses go flat.

Enum constants stay in lockstep with ``models.entities.AssetType``
(``ASSET_TYPES``) and the ad-hoc string sets the parser already
accepts (``PLACEMENT_POSITIONS`` / ``PLACEMENT_STRENGTHS`` /
``KINETIC_TYPES``). When you change one of these, audit the
corresponding Python enum and the documentation here together.
"""
from __future__ import annotations


PLACEMENT_POSITIONS: tuple[str, ...] = (
    "top", "center", "bottom", "full_frame", "caption_only", "unknown",
)
PLACEMENT_STRENGTHS: tuple[str, ...] = (
    "strong", "medium", "weak", "none",
)
KINETIC_TYPES: tuple[str, ...] = (
    "text_overlay", "title_card", "animated_text", "motion_graphic",
    "none", "unknown",
)
# Mirrors models.entities.AssetType values (string side of the enum).
ASSET_TYPES: tuple[str, ...] = (
    "Trailer", "Trailer Drop", "Teaser", "Poster", "Poster / Key Art",
    "Story", "Kinetic", "Character Card", "Character / Cast Post",
    "Quote / Review", "CTA Post", "Ticket CTA", "Release Reminder",
    "Behind the Scenes", "Event / Festival", "Series Episode Push",
    "Franchise / Brand Post", "Discovery", "Unknown",
)


SYSTEM_PROMPT = (
    "Du bist ein praeziser Visual-Analyst fuer Entertainment-Marketing. "
    "Analysiere Standbilder von Film-/Serien-/Game-Marketing-Posts und "
    "liefere ausschliesslich valides JSON nach dem unten definierten flachen "
    "Schema. Erfinde keine Inhalte, die im Bild nicht sichtbar sind. Bei "
    "Unsicherheit nutze \"unknown\" statt zu raten."
)


_EXAMPLE_JSON = """{
  "ocr_text": "TICKETS NOW · IN THEATERS DECEMBER 20",
  "visual_summary_de": "Filmtitel-Kachel mit grossem Logo, daneben Datums-Hinweis und Kinosaal-Gaenge im Hintergrund.",
  "placement_title_text": "MOTHER MARY",
  "placement_position": "center",
  "placement_strength": "strong",
  "has_title_placement": true,
  "has_kinetic": true,
  "kinetic_type": "title_card",
  "kinetic_text": "MOTHER MARY",
  "kinetic_confidence": 0.85,
  "asset_type": "Ticket CTA",
  "de_us_match_key": "mother-mary",
  "visual_confidence_score": 0.78
}"""


def build_user_message(
    *,
    channel_name: str,
    market: str,
    title_guess: str,
    caption: str,
) -> str:
    """Render the user message for the OpenAI Vision call.

    All four context strings are inlined verbatim — the caller is
    responsible for substituting safe defaults (``"Unbekannt"``,
    ``"UNKNOWN"``, ``"kein Match"``, ``"nicht verfuegbar"``) for
    absent values. Same convention as the previous inline prompt.
    """
    enum_block = (
        f"- placement_position: {' | '.join(PLACEMENT_POSITIONS)}\n"
        f"- placement_strength: {' | '.join(PLACEMENT_STRENGTHS)}\n"
        f"- kinetic_type: {' | '.join(KINETIC_TYPES)}\n"
        f"- asset_type (1:1 wie geschrieben): "
        f"{', '.join(ASSET_TYPES)}"
    )
    return (
        "Analysiere das Creative-Visual fuer ein Film-/Serien-/Game-"
        "Marketing-Monitoring.\n\n"
        "Kontext:\n"
        f"- Kanal: {channel_name}\n"
        f"- Markt: {market}\n"
        f"- Titel/Franchise-Vermutung: {title_guess}\n"
        f"- Caption: {caption}\n\n"
        "Aufgaben:\n"
        "1. OCR / sichtbaren Text im Bild erfassen (\"ocr_text\"). Wenn "
        "kein Text sichtbar: leerer String.\n"
        "2. Filmtitel-/Serien-/Game-Titel-Placement IM BILD erkennen "
        "(\"placement_title_text\"). Caption-Text zaehlt nicht — nur was "
        "im Bild selbst steht.\n"
        "3. Position des Title/Claim-Placements grob bestimmen "
        "(\"placement_position\"). Wenn kein Placement: \"unknown\".\n"
        "4. Staerke des Placements (\"placement_strength\"). Wenn kein "
        "Placement: \"none\".\n"
        "5. \"has_title_placement\" (Boolean): true wenn ein Titel/Claim "
        "im Bild steht, sonst false.\n"
        "6. Kinetics-Erkennung (\"has_kinetic\"):\n"
        "   - true, wenn Title-Cards, Text-Overlays, animierte Schrift, "
        "Motion-Graphic-Elemente oder typografische Bewegungselemente "
        "sichtbar sind. Auch in einem Standbild ist Kinetik durch "
        "Position, Layering, Motion-Blur oder klare Title-Card-Komposition "
        "erschliessbar.\n"
        "   - false nur, wenn das Bild eindeutig statisch ist und keinen "
        "sichtbaren Bildtext aufweist.\n"
        "   - Bei Unsicherheit: true mit niedrigem "
        "\"kinetic_confidence\" — NICHT false.\n"
        "7. Wenn has_kinetic=true: \"kinetic_type\" aus der Enum-Liste, "
        "sichtbarer Bildtext in \"kinetic_text\".\n"
        "8. \"kinetic_confidence\" (0.0-1.0): wie sicher die Kinetics-"
        "Einschaetzung ist — separat vom allgemeinen "
        "visual_confidence_score.\n"
        "9. \"de_us_match_key\": stabiler kebab-case-Slug aus Franchise/"
        "Titel (z.B. \"mother-mary\", \"avatar-fire-and-ash\").\n"
        "10. \"asset_type\" aus der vorgegebenen Liste — wenn unklar: "
        "\"Unknown\". CTA-Posts mit Ticket-Hinweis: \"Ticket CTA\". "
        "Andere CTA-Mechaniken: \"CTA Post\".\n"
        "11. \"visual_summary_de\": ein bis zwei deutsche Saetze, die "
        "das Bild beschreiben (Subjekte, Komposition, Stimmung).\n"
        "12. \"visual_confidence_score\" (0.0-1.0): Gesamtsicherheit "
        "der Analyse.\n\n"
        "Erlaubte Enum-Werte:\n"
        f"{enum_block}\n\n"
        "Antworte AUSSCHLIESSLICH als flaches JSON mit folgenden Keys "
        "(Strings, Booleans oder Zahlen, keine verschachtelten Objekte, "
        "kein Markdown, kein Code-Fence, kein Praeambel):\n"
        "ocr_text, visual_summary_de, placement_title_text, "
        "placement_position, placement_strength, has_title_placement, "
        "has_kinetic, kinetic_type, kinetic_text, kinetic_confidence, "
        "asset_type, de_us_match_key, visual_confidence_score\n\n"
        "Beispiel-Antwort (Format-Referenz, nicht inhaltlich uebernehmen):\n"
        f"{_EXAMPLE_JSON}"
    )
