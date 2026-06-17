from __future__ import annotations

from datetime import date, datetime, timezone

from sqlmodel import Session, select

from app.models.entities import Market, Title, TitleKeyword, TitleSyncRun
from app.services.tmdb_client import TMDbClient


# --- Studio company-axis sets (Sprint Studio-Title-Sync, 2026-06-16) ----------
# Kuratierte, Wolf-verifizierte TMDb-Company-ID-Sets je Produktionsstudio-Pair.
# Jede ID wurde via scripts/diag_resolve_company_ids gegen einen bekannten Titel
# bestaetigt (hit=True). Pipe-OR-Semantik auf dem TMDb-``with_companies``-Filter.
# Co-Producer sind bewusst NICHT enthalten (sie zoegen Fremdstoff in den Katalog).
#
# Scope: ausschliesslich die 6 PRODUKTIONSSTUDIOS. Die 3 Streamer (netflix,
# primevideo, paramountplus) fehlen absichtlich — sie bekommen kuratierte
# Originals-Sets in ihrem eigenen Sprint (Briefing §7). NICHT hier ergaenzen.
#
# Eine Quelle: dieses Dict ist der einzige Ort, an dem die Pair->Company-IDs
# definiert sind. Kuratierbar ohne weiteren Code-Change.
PAIR_COMPANY_SETS: dict[str, list[int]] = {
    "disney": [2, 3, 420, 1, 127928, 3475, 6125],
    "sonypictures": [34, 5, 559],
    "warnerbros": [174, 12],
    "universalpictures": [33, 6704, 521],
    "paramountpictures": [4],
    "lionsgate": [1632],
}


def _norm(value: str | None) -> str:
    return " ".join((value or "").lower().split())


def _upsert_normalized_title(
    session: Session,
    normalized: dict,
    market: str,
    *,
    is_series: bool,
) -> None:
    """Upsert one normalized TMDb item (movie OR series) into ``title``.

    Variante A (Movie/TV tmdb_id-Namespace-Kollision): TMDb movie- und
    tv-IDs sind getrennte Namespaces — dieselbe Ganzzahl kann ein Film und
    eine Serie sein. Der Existenz-Lookup wird deshalb nach ``content_type``
    gescoped, sodass Film und Serie mit gleicher ``tmdb_id`` als zwei Rows
    koexistieren statt sich gegenseitig zu überschreiben. Keine Migration —
    ``content_type`` ist eine bestehende Spalte.

    Der Movie-Pfad ist im Mapping unverändert; einzige Movie-seitige
    Änderung ist der ``content_type != 'Series'``-Scope am Lookup.
    """
    tmdb_id = normalized.get("tmdb_id")
    type_filter = (
        Title.content_type == "Series" if is_series else Title.content_type != "Series"
    )

    title = session.exec(
        select(Title).where(Title.tmdb_id == tmdb_id, type_filter)
    ).first()
    if not title:
        release_year = normalized.get("release_year")
        title_original = normalized.get("title_original")
        if release_year and title_original:
            candidates = session.exec(
                select(Title).where(Title.title_original == title_original, type_filter)
            ).all()
            title = next(
                (
                    item
                    for item in candidates
                    if (item.release_date_de and item.release_date_de.year == release_year)
                    or (item.release_date_us and item.release_date_us.year == release_year)
                ),
                None,
            )

    if title is None:
        title = Title(
            tmdb_id=tmdb_id,
            title_original=normalized.get("title_original") or normalized.get("title_local") or f"TMDb-{tmdb_id}",
            title_local=normalized.get("title_local"),
            source="TMDb",
            market_relevance=Market.MIXED,
        )
        if is_series:
            title.content_type = "Series"

    title.tmdb_id = tmdb_id
    title.source = title.source or "TMDb"
    title.aliases = sorted(set((title.aliases or []) + (normalized.get("aliases") or [])))
    if normalized.get("title_local"):
        title.title_local = normalized["title_local"]

    rd = normalized.get("release_date")
    if rd:
        parsed = date.fromisoformat(rd)
        if market == "DE":
            title.release_date_de = parsed
        elif market == "US":
            title.release_date_us = parsed

    if market in ["DE", "US"] and title.market_relevance in [Market.UNKNOWN, Market.INT]:
        title.market_relevance = Market.MIXED

    session.add(title)
    session.commit()
    session.refresh(title)

    for alias in normalized.get("aliases") or []:
        if _norm(alias) == _norm(title.title_original):
            continue
        existing_kw = session.exec(
            select(TitleKeyword).where(
                TitleKeyword.title_id == title.id,
                TitleKeyword.keyword == alias,
                TitleKeyword.keyword_type == "alias",
            )
        ).first()
        if not existing_kw:
            session.add(TitleKeyword(title_id=title.id, keyword=alias, keyword_type="alias", active=True))
    session.commit()


async def sync_titles_from_tmdb(
    session: Session,
    markets: list[str] | None = None,
    pairs: list[str] | None = None,
) -> dict:
    """Company-axis title sync (Sprint Studio-Title-Sync, 2026-06-16).

    Replaces the former popularity-window discover (``[-8w, +24w]`` +
    ``with_release_type`` + 3-page cap) with ``with_companies``-discover per
    production-studio pair. The company set is the selector, so the FULL studio
    slate — incl. back-catalogue and returning series seasons — enters the
    catalogue regardless of release date. Diagnose: the window+cap was the root
    of the 96 % no_title_match (catalog gap).

    Scope: the 6 production-studio pairs in ``PAIR_COMPANY_SETS``. The 3
    streamers are NOT synced here (own sprint, §7) — their existing rows persist.

    Localization preserved: each pair is discovered once per market/language
    (DE→de-DE, US→en-US). The per-(media, tmdb_id, MARKET) dedup key lets BOTH
    passes upsert, so ``release_date_de`` + ``release_date_us`` populate and the
    DE-Verleihtitel lands in aliases (matcher reads aliases). The former
    cross-market dedup skipped the second market's pass entirely.
    """
    client = TMDbClient()
    markets = markets or ["DE", "US"]
    region_language = {"DE": "de-DE", "US": "en-US"}

    pair_sets = (
        PAIR_COMPANY_SETS if pairs is None
        else {k: v for k, v in PAIR_COMPANY_SETS.items() if k in pairs}
    )

    today = datetime.now(timezone.utc).date()

    fetched_count = upserted_count = deduped_count = 0
    # Dedup key (media, tmdb_id, market): media because TMDb movie/tv ID
    # namespaces overlap; market so each language/region pass still upserts
    # (both release dates + both localized titles land).
    seen_keys: set[tuple[str, int, str]] = set()

    # Company axis has no release-date window; date_from/date_to are recorded as
    # the run date so the NOT-NULL audit columns stay populated (a schema change
    # to make them nullable is out of scope for this sprint).
    run = TitleSyncRun(
        source="tmdb", markets=markets, date_from=today, date_to=today, status="running"
    )
    session.add(run)
    session.commit()

    try:
        for pair_key, company_ids in pair_sets.items():
            companies = "|".join(str(c) for c in company_ids)
            for market in markets:
                language = region_language.get(market, "en-US")

                movies = await client.discover_movies_by_company(
                    companies, language=language, region=market
                )
                for raw in movies:
                    normalized = client.normalize_tmdb_movie(raw)
                    tmdb_id = normalized.get("tmdb_id")
                    if not tmdb_id:
                        continue
                    fetched_count += 1
                    key = ("movie", tmdb_id, market)
                    if key in seen_keys:
                        deduped_count += 1
                        continue
                    seen_keys.add(key)
                    _upsert_normalized_title(session, normalized, market, is_series=False)
                    upserted_count += 1

                series = await client.discover_series_by_company(
                    companies, language=language, region=market
                )
                for raw in series:
                    normalized = client.normalize_tmdb_series(raw)
                    tmdb_id = normalized.get("tmdb_id")
                    if not tmdb_id:
                        continue
                    fetched_count += 1
                    key = ("tv", tmdb_id, market)
                    if key in seen_keys:
                        deduped_count += 1
                        continue
                    seen_keys.add(key)
                    _upsert_normalized_title(session, normalized, market, is_series=True)
                    upserted_count += 1

        run.fetched_count = fetched_count
        run.upserted_count = upserted_count
        run.deduped_count = deduped_count
        run.status = "success"
        session.add(run)
        session.commit()

        return {
            "markets": markets,
            "pairs": list(pair_sets.keys()),
            "axis": "company",
            "fetched_count": fetched_count,
            "upserted_count": upserted_count,
            "deduped_count": deduped_count,
            "run_id": str(run.id),
        }
    except Exception as exc:
        run.status = "error"
        run.error_message = str(exc)
        session.add(run)
        session.commit()
        raise
