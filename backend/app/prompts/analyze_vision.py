"""Vision prompt — Sonnet describes the post asset (thumbnail / cover
image) so the downstream trend-detection (Sprint 5.3.1.5) can match
visual motifs across platforms.

Sprint 5.3.1 Mini-Run 2. The description is a single short paragraph
in English (~2-4 sentences). We deliberately avoid free-form
markdown / lists — clean prose is easier to embed and to skim during
manual review, and shorter outputs keep the per-call cost bounded.

Image is passed via the Anthropic Messages API ``image`` content block
with ``source.type = "url"`` (URL fetching, no need to base64 anything
on our side). The downside is Anthropic must be able to reach the
URL; for posts with expired CDN links this falls through to
``vision_description = None`` (handled by post_analyzer).
"""
from __future__ import annotations


SYSTEM_PROMPT = """You describe still images from film & series social-media
posts for a creative-trend research tool. Your output is a single
short paragraph (2-4 sentences) in English describing what the image
depicts: subjects, composition, mood, on-image text if any, and
visual style. Do not invent context (release dates, character names,
plot points) that is not visually present. Plain prose only — no
markdown, no lists, no preamble.""".strip()


def build_user_message(caption: str, platform: str) -> str:
    """Caption travels along as side-context — useful when on-image
    text is partial or stylised. We mark it explicitly as caption so
    the model doesn't conflate it with on-image text it sees."""
    safe_caption = (caption or "").strip() or "(no caption)"
    return (
        f"Platform: {platform}\n"
        f"Post caption (for context only — do not quote verbatim unless it "
        f'appears on the image): "{safe_caption}"\n\n'
        f"Describe the image."
    )
