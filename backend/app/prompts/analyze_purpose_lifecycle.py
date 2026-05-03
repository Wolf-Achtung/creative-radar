"""Sonnet prompt for the contextual classification fields (purpose,
lifecycle_stage).

Sprint 5.3.1 Mini-Run 2. These two fields need release-cycle context
that Haiku consistently under-reads in the spot-checks: a "trailer"
posted three days before a release date is ``release_week``, the same
trailer six months out is ``launch_announcement``. Sonnet's reasoning
is worth the price difference here.

Inputs include caption + platform + (optional) published_at to give
the model a temporal anchor. We deliberately do NOT include the title
or franchise — that would leak ground-truth labels in cases where a
human curator has already tagged the post, and bias the lifecycle
estimate via release-date metadata that the analyzer's caller is
supposed to discover, not inject.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional


SYSTEM_PROMPT = """You analyze film & series social-media posts for the
two contextual fields below: ``purpose`` (the marketing intent) and
``lifecycle_stage`` (where in the release cycle the post sits), and
report your own ``confidence`` (0.0-1.0). Use 0.9+ when the caption +
publish-date pair is unambiguous, 0.6-0.8 when the lifecycle is
clear but the marketing intent is mixed (or vice versa), and below
0.5 only when neither signal is conclusive (e.g. evergreen-style
caption with no temporal anchor). Respond with valid JSON only — no
prose, no markdown fences, no commentary.""".strip()


FEW_SHOTS = [
    {
        "context": "Caption: 'COMING SUMMER 2026.' published 8 months before release",
        "answer": {"purpose": "launch_announcement", "lifecycle_stage": "pre_launch", "confidence": 0.9},
    },
    {
        "context": "Caption: 'In cinemas this Friday.' published 4 days before release",
        "answer": {"purpose": "release_week", "lifecycle_stage": "launch", "confidence": 0.95},
    },
    {
        "context": "Caption: 'Watch the cast react to fan theories.' published 3 weeks after release",
        "answer": {"purpose": "audience_engagement", "lifecycle_stage": "post_launch", "confidence": 0.8},
    },
    {
        "context": "Caption: '10 reasons to rewatch this classic.' no clear release window",
        "answer": {"purpose": "evergreen", "lifecycle_stage": "evergreen", "confidence": 0.85},
    },
    {
        "context": "Caption: 'Live from the Berlinale red carpet.' published during a festival",
        "answer": {"purpose": "event_coverage", "lifecycle_stage": "post_launch", "confidence": 0.9},
    },
    {
        "context": "Caption: 'New episode drops soon.' no published_at",
        "answer": {"purpose": "ongoing_promotion", "lifecycle_stage": "unclear", "confidence": 0.45},
    },
]


PURPOSE_VOCAB = (
    "launch_announcement, release_week, ongoing_promotion, evergreen, "
    "audience_engagement, event_coverage, other"
)
LIFECYCLE_VOCAB = "pre_launch, launch, post_launch, evergreen, unclear"


def build_user_message(
    caption: str, platform: str, published_at: Optional[datetime]
) -> str:
    safe_caption = (caption or "").strip() or "(no caption)"
    when = published_at.isoformat() if published_at else "unknown publish date"
    examples_block = "\n".join(
        f'  {ex["context"]}\n  Answer: {{"purpose": "{ex["answer"]["purpose"]}", '
        f'"lifecycle_stage": "{ex["answer"]["lifecycle_stage"]}", '
        f'"confidence": {ex["answer"]["confidence"]}}}'
        for ex in FEW_SHOTS
    )
    return (
        f"Platform: {platform}\n"
        f"Published at: {when}\n"
        f"Allowed purpose values: {PURPOSE_VOCAB}\n"
        f"Allowed lifecycle_stage values: {LIFECYCLE_VOCAB}\n"
        f'Required JSON keys: "purpose", "lifecycle_stage", "confidence" (float 0.0-1.0).\n\n'
        f"Examples:\n{examples_block}\n\n"
        f'Now classify this post.\n  Caption: "{safe_caption}"\n  Answer:'
    )
