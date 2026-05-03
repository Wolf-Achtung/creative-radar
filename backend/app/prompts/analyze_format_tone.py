"""Haiku prompt for the mechanical classification fields (format, tone).

Sprint 5.3.1 Mini-Run 2. The split — Haiku for format/tone, Sonnet for
purpose/lifecycle — is the cost-side reason these prompts live in
separate modules. The schema vocabularies are sourced from the
PostAnalysis Literal types in app/schemas/dto.py; keeping the wording
in sync is enforced at parse time (Pydantic rejects values not in the
Literal union).

Prompt design principles followed here:
- Output shape: bare JSON object, no prose preamble. We instruct the
  model to "respond with valid JSON only" so the retry-on-invalid-JSON
  path in post_analyzer doesn't have to strip markdown fences.
- Few-shots cover trailer / clip / behind-the-scenes for format and
  energetic / humorous / suspenseful / informative for tone — the
  shapes most often misclassified in spot-checks against the Netflix
  / WB / A24 sample.
- Caption-only context: vision is handled by analyze_vision; mixing
  modalities here would inflate Haiku token cost without measurable
  accuracy gain.
"""
from __future__ import annotations


SYSTEM_PROMPT = """You are a precise classifier for film & series social-media posts.
You classify two fields per post: ``format`` and ``tone``, and report
your own ``confidence`` (0.0-1.0). Use 0.9+ when the caption signals
the answer unambiguously, 0.6-0.8 when there's mild ambiguity (e.g.
clip vs short, humorous vs edgy), and below 0.5 only when the caption
is genuinely too thin to choose between the closed-vocabulary options.
Respond with valid JSON only — no prose, no markdown fences, no
commentary.""".strip()


FEW_SHOTS = [
    {
        "caption": "Official trailer is here. STRANGER THINGS Season 5 — only on Netflix.",
        "answer": {"format": "trailer", "tone": "suspenseful", "confidence": 0.95},
    },
    {
        "caption": "30-second sneak peek. The wait is almost over.",
        "answer": {"format": "teaser", "tone": "suspenseful", "confidence": 0.85},
    },
    {
        "caption": "Behind the scenes with the cast on day 42 of shooting.",
        "answer": {"format": "behind_the_scenes", "tone": "informative", "confidence": 0.9},
    },
    {
        "caption": "Wenn dein Lieblingscharakter zum dritten Mal stirbt 💀😂",
        "answer": {"format": "clip", "tone": "humorous", "confidence": 0.7},
    },
    {
        "caption": "OUT NOW — your weekend is sorted. #NewRelease",
        "answer": {"format": "promo", "tone": "energetic", "confidence": 0.85},
    },
    {
        "caption": "vibes",
        "answer": {"format": "other", "tone": "neutral", "confidence": 0.4},
    },
]


# Vocabularies re-stated inside the prompt so the model has an explicit
# closed list to choose from. Keep the order matching dto.py's Literal
# unions to make drift visually obvious during code review.
FORMAT_VOCAB = (
    "teaser, trailer, clip, behind_the_scenes, interview, short, "
    "compilation, promo, other"
)
TONE_VOCAB = (
    "energetic, emotional, humorous, suspenseful, informative, "
    "inspirational, edgy, neutral"
)


def build_user_message(caption: str, platform: str) -> str:
    """Render the user-message body. Keeps the template testable in
    isolation — see test_post_analyzer for the assertion that the
    caption is escaped/included verbatim."""
    safe_caption = (caption or "").strip() or "(no caption)"
    examples_block = "\n".join(
        f'  Caption: "{ex["caption"]}"\n  Answer: {{"format": "{ex["answer"]["format"]}", '
        f'"tone": "{ex["answer"]["tone"]}", "confidence": {ex["answer"]["confidence"]}}}'
        for ex in FEW_SHOTS
    )
    return (
        f"Platform: {platform}\n"
        f"Allowed format values: {FORMAT_VOCAB}\n"
        f"Allowed tone values: {TONE_VOCAB}\n"
        f'Required JSON keys: "format", "tone", "confidence" (float 0.0-1.0).\n\n'
        f"Examples:\n{examples_block}\n\n"
        f'Now classify this post.\n  Caption: "{safe_caption}"\n  Answer:'
    )
