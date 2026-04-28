from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata

from sqlmodel import Session, select

from app.models.entities import Title, TitleKeyword


@dataclass
class MatchResult:
    title: Title | None
    confidence: float
    source: str
    suggested_title: str | None = None


_SAFE_SOURCES = {"exact", "exact_alias", "exact_local", "hashtag", "unique_text"}
_GENERIC_WORDS = {
    "movie",
    "film",
    "official",
    "trailer",
    "teaser",
    "cinema",
    "video",
    "clip",
}


def _normalize_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold().strip()
    text = text.replace("_", " ").replace("-", " ")
    text = text.translate(
        str.maketrans(
            {
                "„": '"',
                "“": '"',
                "”": '"',
                "‚": "'",
                "‘": "'",
                "’": "'",
                "`": "'",
            }
        )
    )
    text = re.sub(r"[^\w\s#]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _split_hashtag(tag: str) -> str:
    base = tag.lstrip("#")
    base = re.sub(r"([a-z])([A-Z])", r"\1 \2", base)
    base = re.sub(r"[_-]+", " ", base)
    return _normalize_text(base)


def _contains_phrase(haystack: str, needle: str) -> bool:
    if not haystack or not needle:
        return False
    padded_haystack = f" {haystack} "
    padded_needle = f" {needle} "
    return padded_needle in padded_haystack


def _title_candidates(title: Title, keywords: list[TitleKeyword]) -> dict[str, list[str]]:
    exact = [title.title_original]
    local = [title.title_local] if title.title_local else []
    aliases = list(title.aliases or [])
    weak = [title.franchise, *[kw.keyword for kw in keywords if kw.active]]
    return {
        "exact": [value.strip() for value in exact if value and value.strip()],
        "local": [value.strip() for value in local if value and value.strip()],
        "alias": [value.strip() for value in aliases if value and value.strip()],
        "weak": [value.strip() for value in weak if value and value.strip()],
    }


def _load_titles(session: Session) -> list[tuple[Title, dict[str, list[str]]]]:
    titles = session.exec(select(Title).where(Title.active == True)).all()  # noqa: E712
    bundle: list[tuple[Title, dict[str, list[str]]]] = []
    for title in titles:
        keywords = session.exec(select(TitleKeyword).where(TitleKeyword.title_id == title.id)).all()
        bundle.append((title, _title_candidates(title, keywords)))
    return bundle


def _extract_hashtag_matches(text: str, normalized_to_titles: dict[str, list[tuple[Title, str]]]) -> list[tuple[Title, str, str]]:
    hits: list[tuple[Title, str, str]] = []
    for raw in re.findall(r"#[A-Za-z][A-Za-z0-9_\-]{2,}", text or ""):
        split = _split_hashtag(raw)
        if split and split in normalized_to_titles:
            for title, source in normalized_to_titles[split]:
                hits.append((title, source, split))
    return hits


def _collect_text_fields(fields: dict[str, str | list[str] | None] | None, fallback: str | None) -> list[tuple[str, str]]:
    collected: list[tuple[str, str]] = []
    if fields:
        for key, value in fields.items():
            if isinstance(value, list):
                joined = " ".join(str(item) for item in value if item)
                if joined.strip():
                    collected.append((key, joined))
            elif value and str(value).strip():
                collected.append((key, str(value)))
    if fallback and fallback.strip():
        collected.append(("text", fallback))
    return collected


def is_safe_auto_match(match: MatchResult) -> bool:
    return bool(match.title and match.source in _SAFE_SOURCES and match.confidence >= 0.95)


def find_title_matches(session: Session, text: str | None) -> list[Title]:
    result = find_best_title_match(session, text)
    return [result.title] if result.title else []


def find_best_title_match(
    session: Session,
    text: str | None,
    fields: dict[str, str | list[str] | None] | None = None,
) -> MatchResult:
    text_fields = _collect_text_fields(fields, text)
    if not text_fields:
        return MatchResult(title=None, confidence=0.0, source="empty")

    titles_with_candidates = _load_titles(session)
    normalized_to_titles: dict[str, list[tuple[Title, str]]] = {}
    for title, candidate_map in titles_with_candidates:
        for source_key, values in candidate_map.items():
            if source_key == "weak":
                continue
            for candidate in values:
                normalized = _normalize_text(candidate)
                if not normalized:
                    continue
                if len(normalized) <= 2 or normalized in _GENERIC_WORDS:
                    continue
                normalized_to_titles.setdefault(normalized, []).append((title, source_key))

    strong_hits: list[tuple[Title, str, str]] = []
    weak_best: tuple[Title | None, float, str, str | None] = (None, 0.0, "none", None)

    for _, raw in text_fields:
        normalized_haystack = _normalize_text(raw)
        if not normalized_haystack:
            continue

        hashtag_hits = _extract_hashtag_matches(raw, normalized_to_titles)
        for title, source_key, matched_text in hashtag_hits:
            strong_hits.append((title, "hashtag" if source_key != "alias" else "hashtag", matched_text))

        for normalized, title_refs in normalized_to_titles.items():
            if normalized == normalized_haystack:
                for title, source_key in title_refs:
                    mapped_source = "exact" if source_key == "exact" else ("exact_local" if source_key == "local" else "exact_alias")
                    strong_hits.append((title, mapped_source, normalized))
                continue
            if _contains_phrase(normalized_haystack, normalized):
                for title, source_key in title_refs:
                    mapped_source = "unique_text" if source_key == "exact" else ("exact_local" if source_key == "local" else "exact_alias")
                    strong_hits.append((title, mapped_source, normalized))

            ratio = SequenceMatcher(None, normalized_haystack, normalized).ratio()
            if ratio > 0.72 and ratio > weak_best[1]:
                weak_best = (title_refs[0][0], ratio, "fuzzy", normalized)

    if strong_hits:
        by_title: dict[str, tuple[Title, float, str, str]] = {}
        for title, source, matched_text in strong_hits:
            current = by_title.get(str(title.id))
            score = 1.0 if source in {"exact", "exact_local", "exact_alias", "hashtag"} else 0.97
            if current is None or score > current[1]:
                by_title[str(title.id)] = (title, score, source, matched_text)

        if len(by_title) == 1:
            title, confidence, source, matched_text = next(iter(by_title.values()))
            return MatchResult(title=title, confidence=confidence, source=source, suggested_title=matched_text)
        return MatchResult(title=None, confidence=0.0, source="ambiguous", suggested_title=None)

    if weak_best[0]:
        return MatchResult(title=weak_best[0], confidence=weak_best[1], source=weak_best[2], suggested_title=weak_best[3])

    joined = " ".join(value for _, value in text_fields)
    guess = _extract_title_guess(joined)
    return MatchResult(title=None, confidence=0.0, source="none", suggested_title=guess)


def _extract_title_guess(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    tokens = [token for token in raw.replace("#", " ").split() if token]
    filtered = [token for token in tokens if token.lower() not in _GENERIC_WORDS]
    guess = " ".join(filtered[:6]).strip("-:| ")
    return guess[:80] if len(guess) >= 3 else None
