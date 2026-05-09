"""Sprint 9 (H3) — brand-whitelist loader & match helper.

Acts as a curated fallback pool for the whitelist matcher: brand spots,
industry events, streaming-platform mentions etc. that will never get a
TMDb id but are still inhaltlich klar erkennbar.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel


class BrandEntry(BaseModel):
    title: str
    aliases: List[str]
    franchise: str
    type: str
    studio: Optional[str] = None


_WHITELIST_PATH = Path(__file__).resolve().parent.parent / "data" / "brand_whitelist.yaml"
_cache: Optional[List[BrandEntry]] = None

_HASHTAG_RE = re.compile(r"#([A-Za-z][A-Za-z0-9_\-]{2,})")


def _normalize(value: str) -> str:
    return value.lower().replace("-", "").replace("_", "").replace(" ", "")


def load_brand_whitelist(force_reload: bool = False) -> List[BrandEntry]:
    """Lazy-load the YAML with an in-memory cache."""
    global _cache
    if _cache is None or force_reload:
        with _WHITELIST_PATH.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        _cache = [BrandEntry(**entry) for entry in data.get("brand_entries", [])]
    return _cache


def find_brand_match(text: str | None, studio: str | None = None) -> Optional[BrandEntry]:
    """Return the first brand-whitelist entry that fires for ``text``.

    Studio filter — entries with a non-null ``studio`` only match when
    the caller's studio matches; cross-studio entries (``studio=null``)
    match everywhere. When the caller passes ``studio=None``, no studio
    filter is applied.
    """
    if not text:
        return None

    whitelist = load_brand_whitelist()
    text_lower = text.lower()
    hashtags = _HASHTAG_RE.findall(text)
    normalized_hashtags = {_normalize(tag) for tag in hashtags if tag}

    for entry in whitelist:
        if entry.studio is not None and studio is not None and entry.studio != studio:
            continue

        for alias in entry.aliases:
            alias_norm = _normalize(alias)
            if not alias_norm:
                continue
            if any(alias_norm in h for h in normalized_hashtags):
                return entry
            if len(alias) >= 5 and alias.lower() in text_lower:
                return entry

    return None
