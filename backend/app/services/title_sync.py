from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, select

from app.models.entities import Market, Title, TitleKeyword, TitleSyncRun
from app.services.tmdb_client import TMDbClient


def _norm(value: str | None) -> str:
    return " ".join((value or "").lower().split())


async def sync_titles_from_tmdb(
    session: Session,
    markets: list[str] | None = None,
    lookback_weeks: int = 8,
    lookahead_weeks: int = 24,
) -> dict:
    client = TMDbClient()
    markets = markets or ["DE", "US"]
    region_language = {"DE": "de-DE", "US": "en-US"}

    today = datetime.now(timezone.utc).date()
    date_from = today - timedelta(weeks=lookback_weeks)
    date_to = today + timedelta(weeks=lookahead_weeks)

    fetched_count = upserted_count = deduped_count = 0
    seen_tmdb_ids: set[int] = set()

    run = TitleSyncRun(source="tmdb", markets=markets, date_from=date_from, date_to=date_to, status="running")
    session.add(run)
    session.commit()

    try:
        for market in markets:
            language = region_language.get(market, "en-US")
            movies = await client.discover_movies(region=market, language=language, date_from=date_from, date_to=date_to)
            for raw in movies:
                normalized = client.normalize_tmdb_movie(raw)
                tmdb_id = normalized.get("tmdb_id")
                if not tmdb_id:
                    continue
                fetched_count += 1
                if tmdb_id in seen_tmdb_ids:
                    deduped_count += 1
                    continue
                seen_tmdb_ids.add(tmdb_id)

                title = session.exec(select(Title).where(Title.tmdb_id == tmdb_id)).first()
                if not title:
                    release_year = normalized.get("release_year")
                    title_original = normalized.get("title_original")
                    if release_year and title_original:
                        candidates = session.exec(select(Title).where(Title.title_original == title_original)).all()
                        title = next(
                            (
                                item
                                for item in candidates
                                if (item.release_date_de and item.release_date_de.year == release_year)
                                or (item.release_date_us and item.release_date_us.year == release_year)
                            ),
                            None,
                        )

                is_new = title is None
                if is_new:
                    title = Title(
                        tmdb_id=tmdb_id,
                        title_original=normalized.get("title_original") or normalized.get("title_local") or f"TMDb-{tmdb_id}",
                        title_local=normalized.get("title_local"),
                        source="TMDb",
                        market_relevance=Market.MIXED,
                    )

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
                upserted_count += 1

        run.fetched_count = fetched_count
        run.upserted_count = upserted_count
        run.deduped_count = deduped_count
        run.status = "success"
        session.add(run)
        session.commit()

        return {
            "markets": markets,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
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
