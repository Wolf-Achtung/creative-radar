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
    # Korroborations-Flag: True, wenn ALLE Strong-Hits für diesen Titel
    # ausschliesslich aus placement_title_text (field_key "suggested_title")
    # stammen — d.h. weder caption/ocr_text/ai_summary noch sonst ein Feld
    # stuetzt den Titel. Vision-OCR der placement-Zone faengt Hintergrund-/
    # Lineup-/End-Card-Text ein (z.B. "Andor" auf einem Mandalorian-Clip);
    # ein solcher Allein-Treffer darf KEIN stiller Auto-Match sein. Das Signal
    # bleibt erhalten (Candidate statt Drop), nur is_safe_auto_match sperrt.
    # Additiv mit Default → bestehende MatchResult-Konstruktionen unberuehrt.
    only_from_placement: bool = False


_SAFE_SOURCES = {"exact", "exact_alias", "exact_local", "hashtag", "unique_text"}
# Substring-Magnet-Schutz (Klasse 1): Kandidatenstrings, deren Compact-Form
# (normalisiert, ohne Spaces) NICHT länger als dieser Wert ist, werden aus den
# UNSCHARFEN Substring-Pfaden ausgeschlossen — dem Compact-Hashtag-Fallback und
# dem ``_contains_phrase``-Token-Match. Sie machten kurze Titel/Aliase ("chao",
# "rio2", "mia", "Kara", "Yes") zu Sammel-Mülleimern, weil das Fragment in
# fremden Captions/Hashtags vorkommt. Exakte Gleichheit und exakter Hashtag
# bleiben längenunabhängig (s. find_best_title_match), kurze Titel matchen also
# weiter über echte Volltreffer.
_MIN_SUBSTRING_CANDIDATE_LEN = 4
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
    base = re.sub(r"([a-z])([A-Z])", r"\1 \2", base)       # camelCase boundary
    # Recall-Fix (Post-#277): auch Buchstabe↔Ziffer trennen, sonst bleibt der
    # Titel-Suffix verklebt — #ToyStory5 → "toy story5" ≠ Katalog "toy story 5",
    # #IronMan2 → "iron man2" ≠ "iron man 2". Beide Richtungen, damit auch
    # "2Fast" → "2 fast" zerlegt wird.
    base = re.sub(r"([A-Za-z])([0-9])", r"\1 \2", base)    # letter→digit
    base = re.sub(r"([0-9])([A-Za-z])", r"\1 \2", base)    # digit→letter
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


def build_token_index(
    normalized_to_titles: dict[str, list[tuple[Title, str]]],
) -> dict[str, set[str]]:
    """token -> set of normalized keys that contain it. Built once per batch.

    Performance fix (post-#277, ~29k→14.7k titles): the substring/fuzzy loop used
    to scan EVERY normalized key per text-field per asset and call
    ``SequenceMatcher`` on each (O(assets × keys) — the rematch hang). This index
    lets ``find_best_title_match`` consider only candidates that share a token
    with the haystack. ``_contains_phrase`` needs the candidate as a contiguous
    phrase, so it must share ALL its tokens → token-overlap is a LOSSLESS
    prefilter for substring. For fuzzy it is a deliberate recall scope (a
    zero-shared-token fuzzy hit is a false positive the substring-magnet guard
    rejects anyway). ≤2-char and generic tokens are excluded (they would bucket
    almost everything)."""
    token_index: dict[str, set[str]] = {}
    for normalized in normalized_to_titles:
        for tok in normalized.split():
            if len(tok) <= 2 or tok in _GENERIC_WORDS:
                continue
            token_index.setdefault(tok, set()).add(normalized)
    return token_index


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
                # Substring-Magnet-Schutz (Klasse 1): kurze Compact-Keys ("chao",
                # "rio2") feuerten in JEDEM langen Hashtag. Drei Härtungen:
                #  1) Mindestlänge — Keys ≤ _MIN_SUBSTRING_CANDIDATE_LEN raus
                #     (``not compact_key`` bleibt als redundanter Leer-Guard).
                #  2) nur Vorwärtsrichtung ``compact_key in compact_split`` —
                #     ein KNOWN-TITLE-Compact steckt im längeren Hashtag; die
                #     umgekehrte Richtung machte den Key zum Fragment-Magneten.
                #  3) Mindest-Coverage: der Key muss den Hashtag substantiell
                #     abdecken, sonst ist der Treffer zufällig.
                if not compact_key or len(compact_key) <= _MIN_SUBSTRING_CANDIDATE_LEN:
                    continue
                if compact_key not in compact_split:
                    continue
                if len(compact_key) / len(compact_split) < 0.5:
                    continue
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
    return bool(
        match.title
        and match.source in _SAFE_SOURCES
        and match.confidence >= 0.95
        and not match.only_from_placement
    )


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


def _finalize_strong_hit(
    entry: tuple[Title, float, str, str],
    field_origins: set[str],
) -> MatchResult:
    """Mappt einen gewonnenen by_title-Eintrag auf ein MatchResult und setzt
    das Korroborations-Flag an EINER Stelle. ``field_origins`` ist die Menge
    aller field_keys, die fuer DIESEN Titel einen Strong-Hit geliefert haben.
    ``only_from_placement`` ist nur True, wenn das die exakte Einermenge
    ``{"suggested_title"}`` ist — sobald caption/ocr_text/ai_summary denselben
    Titel ebenfalls treffen, bleibt es ein regulaerer (korroborierter) Treffer
    und damit Auto-Match-faehig (Schutz der korrekten placement-Faelle)."""
    title, confidence, source, matched_text = entry
    # Präzisions-Fix (Post-#277): ein Ein-Token-Substring (``substring_weak``) ist
    # für sich allein KEIN Safe-Auto-Match. Er wird NUR sicher, wenn ein ZWEITES
    # Feld denselben Titel stützt (Korroboration). ``"text"`` ist das synthetische
    # Caption-Duplikat von ``_collect_text_fields`` und zählt NICHT als eigenes Feld
    # — sonst gälte ein reiner Caption-Treffer fälschlich als 2-Feld-korroboriert.
    if source == "substring_weak":
        distinct = set(field_origins)
        if "caption" in distinct:
            distinct.discard("text")
        if len(distinct) >= 2:
            source, confidence = "unique_text", 0.97  # 2-Feld-korroboriert → safe
    return MatchResult(
        title=title,
        confidence=confidence,
        source=source,
        suggested_title=matched_text,
        only_from_placement=(field_origins == {"suggested_title"}),
    )


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
    cached_token_index: dict[str, set[str]] | None = None,
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
    token_index = (
        cached_token_index
        if cached_token_index is not None
        else build_token_index(normalized_to_titles)
    )

    # 4-Tupel: (title, source, matched_text, field_key). field_key traegt die
    # Herkunfts-Information bis in den by_title-Aufbau, damit dort pro Titel
    # die Menge der stuetzenden Felder (field_origins) gebildet werden kann.
    strong_hits: list[tuple[Title, str, str, str]] = []
    weak_best: tuple[Title | None, float, str, str | None] = (None, 0.0, "none", None)

    # Phase 1 — Strong-Hits: exact (O(1)-Lookup), Hashtag, Substring NUR auf
    # token-overlappenden Kandidaten (Token-Inverted-Index-Vorfilter, Perf-Fix).
    # Pro Feld merken wir (haystack, candidate_keys) fuer einen evtl. Fuzzy-
    # Fallback, damit wir ihn nicht neu ableiten muessen.
    per_field_candidates: list[tuple[str, set[str]]] = []
    for field_key, raw in text_fields:
        normalized_haystack = _normalize_text(raw)
        if not normalized_haystack:
            continue

        hashtag_hits = _extract_hashtag_matches(raw, normalized_to_titles)
        for title, source_key, matched_text in hashtag_hits:
            strong_hits.append((title, "hashtag", matched_text, field_key))

        # Exakter Volltreffer: O(1)-Dict-Lookup (war ein O(N)-Scan ueber alle Keys).
        exact_refs = normalized_to_titles.get(normalized_haystack)
        if exact_refs:
            for title, source_key in exact_refs:
                mapped_source = "exact" if source_key == "exact" else ("exact_local" if source_key == "local" else "exact_alias")
                strong_hits.append((title, mapped_source, normalized_haystack, field_key))

        # Substring-/Fuzzy-Kandidaten: nur Keys, die >=1 Token mit dem Haystack
        # teilen. ``_contains_phrase`` braucht den Kandidaten als zusammenhaengende
        # Phrase → er muss ALLE seine Tokens teilen, Token-Overlap ist also ein
        # verlustfreier Vorfilter fuer Substring. Kurze Kandidaten (Compact-Laenge
        # <= _MIN_SUBSTRING_CANDIDATE_LEN, "mia"/"Yes"/"Kara") bleiben aus den
        # unscharfen Pfaden ausgeschlossen (Substring-Magnet-Schutz).
        haystack_tokens = {
            t for t in normalized_haystack.split()
            if len(t) > 2 and t not in _GENERIC_WORDS
        }
        candidate_keys: set[str] = set()
        for tok in haystack_tokens:
            candidate_keys.update(token_index.get(tok, ()))
        candidate_keys.discard(normalized_haystack)  # exact schon behandelt

        for normalized in candidate_keys:
            if len(normalized.replace(" ", "")) <= _MIN_SUBSTRING_CANDIDATE_LEN:
                continue
            if _contains_phrase(normalized_haystack, normalized):
                # Praezisions-Fix (Post-#277): Substring ist KEIN exakter Volltreffer.
                # Multi-Token-Phrase bleibt verlaesslich → ``unique_text`` (0.97, safe);
                # Ein-Token-Substring → ``substring_weak`` (nicht in _SAFE_SOURCES):
                # Titel taucht als Treffer auf (Recall → TitleCandidate), wird aber nur
                # safe, wenn ein zweites Feld ihn stuetzt (_finalize_strong_hit) oder ein
                # echter exact-/hashtag-Treffer im by_title-Scoring gewinnt.
                mapped_source = "unique_text" if " " in normalized else "substring_weak"
                for title, source_key in normalized_to_titles[normalized]:
                    strong_hits.append((title, mapped_source, normalized, field_key))

        per_field_candidates.append((normalized_haystack, candidate_keys))

    # Phase 2 — Fuzzy-Fallback NUR, wenn nichts Starkes matchte (Strong-Hit-
    # Short-Circuit): laeuft ueber dieselbe kleine token-overlap-Kandidatenmenge.
    if not strong_hits:
        for normalized_haystack, candidate_keys in per_field_candidates:
            for normalized in candidate_keys:
                if len(normalized.replace(" ", "")) <= _MIN_SUBSTRING_CANDIDATE_LEN:
                    continue
                ratio = SequenceMatcher(None, normalized_haystack, normalized).ratio()
                if ratio > 0.72 and ratio > weak_best[1]:
                    weak_best = (normalized_to_titles[normalized][0][0], ratio, "fuzzy", normalized)

    if strong_hits:
        # Per Titel den spezifischsten Strong-Hit behalten: längster
        # matched_text gewinnt (Spezifitäts-Signal für die Sequel-
        # Disambiguierung unten), bei Gleichstand der höhere Score.
        by_title: dict[str, tuple[Title, float, str, str]] = {}
        # Pro Titel ALLE beitragenden field_keys sammeln — unabhaengig davon,
        # welcher Hit als spezifischster gewinnt. Daraus leitet _finalize_strong_hit
        # only_from_placement ab (== {"suggested_title"} -> nur placement stuetzt).
        field_origins_by_title: dict[str, set[str]] = {}
        for title, source, matched_text, field_key in strong_hits:
            tid = str(title.id)
            field_origins_by_title.setdefault(tid, set()).add(field_key)
            if source in {"exact", "exact_local", "exact_alias", "hashtag"}:
                score = 1.0
            elif source == "substring_weak":
                score = 0.90  # single-token substring -> non-safe, needs corroboration
            else:  # unique_text (multi-token substring)
                score = 0.97
            candidate = (title, score, source, matched_text)
            current = by_title.get(tid)
            if current is None or (len(matched_text), score) > (len(current[3]), current[1]):
                by_title[tid] = candidate

        entries = list(by_title.values())
        if len(entries) == 1:
            return _finalize_strong_hit(
                entries[0], field_origins_by_title[str(entries[0][0].id)]
            )

        # Variante D Teil 1 — Spezifität, aber Score-Klasse VOR Länge: ein
        # expliziter Hashtag/Exact-Treffer (score 1.0) schlägt einen längeren,
        # schwächeren Floskel-Substring (unique_text 0.97). Sonst gewänne z.B.
        # die Alltagsphrase "the boys" (8) gegen den eigentlichen #Jumanji (7),
        # nur weil ihr matched_text ein Zeichen länger ist. Bei GLEICHEM Score
        # bleibt die Längen-Spezifität der Tiebreak (z.B. "mortal kombat ii"
        # schlägt "mortal kombat").
        max_score = max(entry[1] for entry in entries)
        top_score = [entry for entry in entries if entry[1] == max_score]
        max_len = max(len(entry[3]) for entry in top_score)
        longest = [entry for entry in top_score if len(entry[3]) == max_len]
        if len(longest) == 1:
            return _finalize_strong_hit(
                longest[0], field_origins_by_title[str(longest[0][0].id)]
            )

        # Variante D Teil 2 — bei gleich langen Treffern derselben Franchise:
        # Zeit-Tiebreak über Release-Nähe zum Post-Datum.
        resolved = _resolve_by_release_proximity(longest, published_at)
        if resolved is not None:
            return _finalize_strong_hit(
                resolved, field_origins_by_title[str(resolved[0].id)]
            )

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
