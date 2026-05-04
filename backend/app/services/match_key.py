"""Slug-form normalisation for ``Asset.de_us_match_key``.

Single source of truth so every write path produces the same kebab-case
form. Pre-Sprint-A there were two writers (the heuristic and vision-LLM
paths in ``visual_analysis``) that slugged via this algorithm, plus two
writers (``title_rematch`` and the ``/api/assets/{id}/review`` handler)
that wrote the raw franchise / title-original string. Cross-market
pairing per match-key equality fell through whenever a single asset's
key drifted between forms.

Algorithm: lower-case, replace any run of non-[a-z0-9äöüß] with '-',
strip leading/trailing '-'. German umlauts and ß are preserved
deliberately so 'Über uns' -> 'über-uns' (not 'ber-uns'); the Alembic
migration that backfills historical Raw-Form rows uses the same
character class so historical and new writes converge on the same key.
"""
from __future__ import annotations

import re


_SLUG_PATTERN = re.compile(r"[^a-z0-9äöüß]+")


def slugify_match_key(value: str | None) -> str | None:
    """Return the canonical slug form of ``value`` or None.

    Empty / None / all-separators inputs collapse to None — that's the
    same behaviour the legacy private ``_slug`` had in
    ``visual_analysis``, kept so callers can continue to use the result
    as ``... or None`` fallback chains without surprise.
    """
    if not value:
        return None
    clean = _SLUG_PATTERN.sub("-", value.lower().strip())
    return clean.strip("-") or None
