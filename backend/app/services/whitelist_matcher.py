from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from uuid import UUID
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


def load_title_bundle(session: Session) -> list[tuple[Title, dict[str, list[str]]]]:
    """Eager-load all active titles + their keywords in 2 queries total.

    The legacy ``_load_titles`` helper issued one query per title to fetch
    its keywords (N+1). For batch operations like the cron auto-rematch this
    is the dominant cost — see Sprint 10g. Callers can reuse the returned
    bundle across many ``find_best_title_match`` calls.
    """
    titles = list(session.exec(select(Title).where(Title.active == True)).all())  # noqa: E712
    if not titles:
        return []
    title_ids = [t.id for t in titles]
    keywords_by_title: dict[UUID, list[TitleKeyword]] = {}
    for kw in session.exec(
        select(TitleKeyword).where(TitleKeyword.title_id.in_(title_ids))
    ).all():
        keywords_by_title.setdefault(kw.title_id, []).append(kw)
    return [(t, _title_candidates(t, keywords_by_title.get(t.id, []))) for t in titles]


def build_normalized_index(
    bundle: list[tuple[Title, dict[str, list[str]]]],
) -> dict[str, list[tuple[Title, str]]]:
    """Build the normalized-text-to-titles lookup map once per batch."""
    normalized_to_titles: dict[str, list[tuple[Title, str]]] = {}
    for title, candidate_map in bundle:
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
    return normalized_to_titles


def _load_titles(session: Session) -> list[tuple[Title, dict[str, list[str]]]]:
    return load_title_bundle(session)


def _extract_hashtag_matches(text: str, normalized_to_titles: dict[str, list[tuple[Title, str]]]) -> list[tuple[Title, str, str]]:
    hits: list[tuple[Title, str, str]] = []
    # Sprint 10e: build a compact-form index once per call so lowercase
    # hashtags like ``#mortalkombatmovie`` can be resolved against the
    # known title pool. Maps "mortalkombatii" -> "mortal kombat ii".
    compact_to_normalized: dict[str, str] = {
        normalized.replace(" ", ""): normalized
        for normalized in normalized_to_titles
    }
    for raw in re.findall(r"#[A-Za-z][A-Za-z0-9_\-]{2,}", text or ""):
        split = _split_hashtag(raw)
        if not split:
            continue
        if split in normalized_to_titles:
            for title, source in normalized_to_titles[split]:
                hits.append((title, source, split))
            continue

        # Sprint 10e fallback: lowercase hashtags (no CamelCase boundary)
        # stay glued together after _split_hashtag — try a compact-form
        # match against the known title index. Threshold len > 8 keeps
        # noise out (short tags like "#kino", "#film" aren't candidates).
        if " " not in split and len(split) > 8:
            compact_split = split.replace(" ", "")
            for compact_key, normalized_key in compact_to_normalized.items():
                if not compact_key or len(compact_key) < 4:
                    continue
                if compact_key in compact_split or compact_split in compact_key:
                    for title, source in normalized_to_titles[normalized_key]:
                        hits.append((title, source, normalized_key))
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


def _release_anchor(title: Title):
    """Title-Referenz-Releasedatum: DE bevorzugt, sonst US."""
    return title.release_date_de or title.release_date_us


def _resolve_by_release_proximity(
    entries: list[tuple[Title, float, str, str]],
    published_at: datetime | None,
) -> tuple[Title, float, str, str] | None:
    """Variante D Teil 2 — Zeit-Tiebreak für gleich spezifische Strong-Hits.

    Wählt aus gleich langen Treffern den Titel, dessen Release-Datum (DE
    bevorzugt, sonst US) dem ``published_at`` des Posts am nächsten liegt
    (kleinste absolute Tagesdifferenz). Greift NUR wenn: ``published_at``
    bekannt ist, alle Kandidaten dieselbe (nicht-leere) Franchise teilen und
    JEDER ein Release-Datum hat. Sonst ``None`` → Caller fällt auf den
    OPEN-Kandidat-Pfad zurück. Kein DB-Zugriff; nutzt ausschließlich Felder,
    die bereits auf den Title-Rows liegen."""
    if not isinstance(published_at, datetime):
        return None
    franchises = {(entry[0].franchise or "").strip().casefold() for entry in entries}
    if len(franchises) != 1 or "" in franchises:
        return None
    pub = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
    pub_date = pub.date()
    scored: list[tuple[int, tuple[Title, float, str, str]]] = []
    for entry in entries:
        anchor = _release_anchor(entry[0])
        if anchor is None:
            return None  # unvollständige Datenlage -> kein Zeit-Tiebreak
        scored.append((abs((anchor - pub_date).days), entry))
    scored.sort(key=lambda item: item[0])
    if len(scored) >= 2 and scored[0][0] == scored[1][0]:
        return None  # kein eindeutig nächster Titel -> OPEN
    return scored[0][1]


def find_title_matches(session: Session, text: str | None) -> list[Title]:
    result = find_best_title_match(session, text)
    return [result.title] if result.title else []


def find_best_title_match(
    session: Session,
    text: str | None,
    fields: dict[str, str | list[str] | None] | None = None,
    studio: str | None = None,
    *,
    published_at: datetime | None = None,
    cached_bundle: list[tuple[Title, dict[str, list[str]]]] | None = None,
    cached_normalized_index: dict[str, list[tuple[Title, str]]] | None = None,
) -> MatchResult:
    text_fields = _collect_text_fields(fields, text)
    if not text_fields:
        return MatchResult(title=None, confidence=0.0, source="empty")

    titles_with_candidates = (
        cached_bundle if cached_bundle is not None else load_title_bundle(session)
    )
    normalized_to_titles = (
        cached_normalized_index
        if cached_normalized_index is not None
        else build_normalized_index(titles_with_candidates)
    )

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
        # Per Titel den spezifischsten Strong-Hit behalten: längster
        # matched_text gewinnt (Spezifitäts-Signal für die Sequel-
        # Disambiguierung unten), bei Gleichstand der höhere Score.
        by_title: dict[str, tuple[Title, float, str, str]] = {}
        for title, source, matched_text in strong_hits:
            score = 1.0 if source in {"exact", "exact_local", "exact_alias", "hashtag"} else 0.97
            candidate = (title, score, source, matched_text)
            current = by_title.get(str(title.id))
            if current is None or (len(matched_text), score) > (len(current[3]), current[1]):
                by_title[str(title.id)] = candidate

        entries = list(by_title.values())
        if len(entries) == 1:
            title, confidence, source, matched_text = entries[0]
            return MatchResult(title=title, confidence=confidence, source=source, suggested_title=matched_text)

        # Variante D Teil 1 — Spezifität: der eindeutig längste matched_text
        # gewinnt (z.B. "mortal kombat ii" schlägt "mortal kombat").
        max_len = max(len(entry[3]) for entry in entries)
        longest = [entry for entry in entries if len(entry[3]) == max_len]
        if len(longest) == 1:
            title, confidence, source, matched_text = longest[0]
            return MatchResult(title=title, confidence=confidence, source=source, suggested_title=matched_text)

        # Variante D Teil 2 — bei gleich langen Treffern derselben Franchise:
        # Zeit-Tiebreak über Release-Nähe zum Post-Datum.
        resolved = _resolve_by_release_proximity(longest, published_at)
        if resolved is not None:
            title, confidence, source, matched_text = resolved
            return MatchResult(title=title, confidence=confidence, source=source, suggested_title=matched_text)

        # Variante D Teil 3 — weder Spezifität noch Zeit lösen eindeutig auf:
        # NICHT raten. title=None -> der bestehende Pfad legt einen
        # OPEN-TitleCandidate zur manuellen Prüfung an.
        return MatchResult(title=None, confidence=0.0, source="ambiguous", suggested_title=None)

    if weak_best[0]:
        return MatchResult(title=weak_best[0], confidence=weak_best[1], source=weak_best[2], suggested_title=weak_best[3])

    joined = " ".join(value for _, value in text_fields)

    # Sprint 9 (H3): brand-whitelist fallback. Only kicks in when no TMDb-side
    # signal at all — confidence (0.85) stays below the 0.95 auto-tag bar so
    # the result is visible in rankings but not silently auto-applied.
    from app.services.brand_whitelist_loader import find_brand_match  # local import keeps module load cheap
    brand_entry = find_brand_match(joined, studio=studio)
    if brand_entry is not None:
        return MatchResult(
            title=None,
            confidence=0.85,
            source="brand_whitelist",
            suggested_title=brand_entry.title,
        )

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
