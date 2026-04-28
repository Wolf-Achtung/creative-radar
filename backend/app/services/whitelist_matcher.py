from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlmodel import Session, select

from app.models.entities import Title, TitleKeyword


@dataclass
class MatchResult:
    title: Title | None
    confidence: float
    source: str
    suggested_title: str | None = None


def _norm(value: str | None) -> str:
    return " ".join((value or "").lower().split())


def _title_candidates(title: Title, keywords: list[TitleKeyword]) -> list[str]:
    values = [title.title_original, title.title_local, title.franchise, *(title.aliases or [])]
    values.extend([kw.keyword for kw in keywords if kw.active])
    return [value.strip() for value in values if value and value.strip()]


def find_title_matches(session: Session, text: str | None) -> list[Title]:
    result = find_best_title_match(session, text)
    return [result.title] if result.title else []


def find_best_title_match(session: Session, text: str | None) -> MatchResult:
    haystack = _norm(text)
    if not haystack:
        return MatchResult(title=None, confidence=0.0, source="empty")

    titles = session.exec(select(Title).where(Title.active == True)).all()  # noqa: E712
    best: tuple[Title | None, float, str, str | None] = (None, 0.0, "none", None)

    for title in titles:
        keywords = session.exec(select(TitleKeyword).where(TitleKeyword.title_id == title.id)).all()
        for candidate in _title_candidates(title, keywords):
            normalized = _norm(candidate)
            if not normalized:
                continue
            if normalized in haystack:
                confidence = 0.99 if normalized == haystack else 0.95
                if confidence > best[1]:
                    best = (title, confidence, "exact", candidate)
            else:
                ratio = SequenceMatcher(None, haystack, normalized).ratio()
                if ratio > 0.72 and ratio > best[1]:
                    best = (title, ratio, "fuzzy", candidate)

    if best[0]:
        return MatchResult(title=best[0], confidence=best[1], source=best[2], suggested_title=best[3])

    guess = _extract_title_guess(text or "")
    return MatchResult(title=None, confidence=0.0, source="none", suggested_title=guess)


def _extract_title_guess(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    tokens = [token for token in raw.replace("#", " ").split() if token]
    filtered = [token for token in tokens if token.lower() not in {"official", "trailer", "movie", "film", "teaser"}]
    guess = " ".join(filtered[:6]).strip("-:| ")
    return guess[:80] if len(guess) >= 3 else None
