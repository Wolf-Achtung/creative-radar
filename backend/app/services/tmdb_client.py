from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.themoviedb.org/3"

# TMDb caps the ``page`` query param at 500 on the /discover endpoints.
# We honour that as the upper bound for full pagination.
TMDB_MAX_DISCOVER_PAGES = 500

# Retry policy for transient TMDb failures (Sprint TMDb-retry-resilience).
# TMDb sporadically returns 5xx on deep /discover pages and occasionally 429
# rate-limits; with several hundred calls per company-axis sync a single blip
# would otherwise abort the whole run. We retry ONLY transient classes
# (5xx, 429, network timeouts) — never 4xx (real errors, incl. 401 auth).
# One backoff value per retry; exhausting them re-raises so a genuine permanent
# failure still stops the run.
TMDB_RETRY_BACKOFFS = (1.0, 2.0, 4.0)


class TMDbAuthError(RuntimeError):
    pass


class TMDbClient:
    # Market-code -> TMDb region (ISO 3166-1 alpha-2). The app uses "UK"
    # internally, but TMDb's ``region``/``watch_region`` params expect "GB" —
    # passing "UK" silently yields null. Latent today (sync markets are DE/US),
    # wired now so any future UK pass resolves correctly.
    _REGION_OVERRIDES = {"UK": "GB"}

    @staticmethod
    def tmdb_region(market: str | None) -> str | None:
        """Translate an app market code to the TMDb region code (UK->GB)."""
        if not market:
            return None
        m = market.upper()
        return TMDbClient._REGION_OVERRIDES.get(m, m)

    def __init__(self, api_key: str | None = None, read_access_token: str | None = None):
        self.api_key = self._clean_secret(api_key or settings.tmdb_api_key)
        self.read_access_token = self._clean_secret(read_access_token or settings.tmdb_read_access_token)

    @staticmethod
    def _clean_secret(value: str | None) -> str | None:
        if not value:
            return None
        clean = value.strip().strip('"').strip("'")
        if clean.lower().startswith("bearer "):
            clean = clean.split(" ", 1)[1].strip()
        return clean or None

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.read_access_token:
            headers["Authorization"] = f"Bearer {self.read_access_token}"
        return headers

    def _params(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        final_params = dict(params or {})
        if not self.read_access_token and self.api_key:
            final_params["api_key"] = self.api_key
        return final_params

    def _ensure_auth(self) -> None:
        if not self.read_access_token and not self.api_key:
            raise TMDbAuthError("TMDb-Zugangsdaten fehlen. Bitte TMDB_READ_ACCESS_TOKEN oder TMDB_API_KEY setzen.")

    @staticmethod
    def _retry_after_seconds(exc: httpx.HTTPStatusError, fallback: float) -> float:
        """Honour a 429 ``Retry-After`` header (integer seconds) when present;
        otherwise use the exponential-backoff fallback. HTTP-date forms are
        rare on TMDb and fall back to the backoff value."""
        raw = exc.response.headers.get("retry-after")
        if raw and raw.strip().isdigit():
            return float(raw.strip())
        return fallback

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._ensure_auth()
        url = f"{BASE_URL}{path}"
        final_params = self._params(params)
        headers = self._headers()

        # attempt 0 = initial try; attempts 1..N = retries with TMDB_RETRY_BACKOFFS.
        for attempt in range(len(TMDB_RETRY_BACKOFFS) + 1):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.get(url, params=final_params, headers=headers)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 401:
                    # Auth is permanent — never retry, surface the clear message.
                    raise TMDbAuthError(
                        "TMDb-Zugangsdaten ungültig. Bitte TMDB_READ_ACCESS_TOKEN und TMDB_API_KEY in Railway prüfen."
                    ) from exc
                transient = status == 429 or 500 <= status < 600
                if not transient or attempt >= len(TMDB_RETRY_BACKOFFS):
                    # Non-retryable 4xx, or retries exhausted → propagate so a
                    # genuine permanent failure still stops the run.
                    raise
                delay = self._retry_after_seconds(exc, TMDB_RETRY_BACKOFFS[attempt])
                logger.warning(
                    "tmdb transient %s on %s (attempt %d/%d), retrying in %.1fs",
                    status, path, attempt + 1, len(TMDB_RETRY_BACKOFFS) + 1, delay,
                )
                await asyncio.sleep(delay)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # Network-level transient (connect/read timeout, conn reset).
                if attempt >= len(TMDB_RETRY_BACKOFFS):
                    raise
                delay = TMDB_RETRY_BACKOFFS[attempt]
                logger.warning(
                    "tmdb network error on %s (%s, attempt %d/%d), retrying in %.1fs",
                    path, type(exc).__name__, attempt + 1, len(TMDB_RETRY_BACKOFFS) + 1, delay,
                )
                await asyncio.sleep(delay)
        # Unreachable: the loop either returns or raises. Defensive guard for
        # static analysers / future edits to the loop bounds.
        raise RuntimeError("tmdb _get retry loop exhausted without return")

    async def _discover_paginated(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Fully paginate a /discover endpoint up to ``total_pages``.

        Replaces the former hard 3-page cap (Sprint Studio-Title-Sync): the cap
        only ever captured the popularity-window spike. The company axis needs
        the complete studio slate. ``TMDB_MAX_DISCOVER_PAGES`` (TMDb's own hard
        limit on the ``page`` param) keeps a pathological response bounded; the
        empty-results break stops earlier in practice.
        """
        results: list[dict[str, Any]] = []
        page = 1
        while page <= TMDB_MAX_DISCOVER_PAGES:
            data = await self._get(path, {**params, "page": page})
            page_results = data.get("results") or []
            if not page_results:
                break
            results.extend(page_results)
            total_pages = int(data.get("total_pages") or 1)
            if page >= min(total_pages, TMDB_MAX_DISCOVER_PAGES):
                break
            page += 1
        return results

    async def discover_movies(self, region: str, language: str, date_from: date, date_to: date) -> list[dict[str, Any]]:
        return await self._discover_paginated(
            "/discover/movie",
            {
                "region": region,
                "language": language,
                "sort_by": "popularity.desc",
                "include_adult": "false",
                "include_video": "false",
                "release_date.gte": date_from.isoformat(),
                "release_date.lte": date_to.isoformat(),
                # Sprint 9 (H1): include Digital (4) and TV (6) alongside theatrical
                # types so streaming originals and TV-movies enter the title pool.
                # Premiere (1) and Physical (5) stay out — low marketing relevance.
                "with_release_type": "2|3|4|6",
            },
        )

    async def discover_series(self, region: str, language: str, date_from: date, date_to: date) -> list[dict[str, Any]]:
        """TV sibling of ``discover_movies`` (series blind-spot fix).

        Hits ``/discover/tv`` with the same region/language/popularity shape,
        but filters on ``first_air_date`` (TV has no ``release_date``).
        """
        return await self._discover_paginated(
            "/discover/tv",
            {
                "region": region,
                "language": language,
                "sort_by": "popularity.desc",
                "include_adult": "false",
                "first_air_date.gte": date_from.isoformat(),
                "first_air_date.lte": date_to.isoformat(),
            },
        )

    async def discover_movies_by_company(
        self, company_ids: str, language: str, region: str | None = None
    ) -> list[dict[str, Any]]:
        """Company-axis movie discover (Sprint Studio-Title-Sync).

        ``company_ids`` is a TMDb pipe-OR set (``"2|3|420"``). Unlike the
        window discover, this drops ``release_date.*`` and ``with_release_type``
        — the company set IS the selector, so the full studio slate (incl.
        back-catalogue) is returned regardless of release date. ``language``
        drives the localized title (DE-Verleihtitel preservation); ``region``,
        when set, selects the region-specific release date in the payload.
        """
        params: dict[str, Any] = {
            "language": language,
            "sort_by": "popularity.desc",
            "include_adult": "false",
            "include_video": "false",
            "with_companies": company_ids,
        }
        if region:
            params["region"] = region
        return await self._discover_paginated("/discover/movie", params)

    async def discover_series_by_company(
        self, company_ids: str, language: str, region: str | None = None
    ) -> list[dict[str, Any]]:
        """TV sibling of ``discover_movies_by_company``. ``with_companies`` on
        ``/discover/tv`` ignores ``first_air_date``, so returning seasons of
        catalogue series are captured too (the series blind-spot fix)."""
        params: dict[str, Any] = {
            "language": language,
            "sort_by": "popularity.desc",
            "include_adult": "false",
            "with_companies": company_ids,
        }
        if region:
            params["region"] = region
        return await self._discover_paginated("/discover/tv", params)

    # Trailer-Intelligence Stufe 1 (20.08.2026): TMDb liefert in jeder
    # discover-Antwort ``genre_ids`` mit — bis heute wurden sie beim
    # Normalisieren verworfen, und ``title`` hatte keine Genre-Spalte.
    # Ohne Genre gibt es keine Muster-Aussage der Form "Romance-Titel:
    # 15-Sekuender in 9:16 schlagen alles andere".
    #
    # Die ID→Name-Tabellen sind hier STATISCH eingebettet statt per
    # ``/genre/movie/list`` abgerufen: die Listen sind seit Jahren stabil
    # und offiziell dokumentiert, der Verzicht auf den Extra-Call spart
    # Quota und funktioniert auch im Mock-Modus. Kommt eine unbekannte ID,
    # wird sie still uebersprungen (lieber ein fehlendes Genre als ein
    # geratenes); der naechste Wartungsdurchgang sieht das an der
    # Coverage-Zahl des Patterns-Endpoints.
    _MOVIE_GENRES: dict[int, str] = {
        28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy",
        80: "Crime", 99: "Documentary", 18: "Drama", 10751: "Family",
        14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music",
        9648: "Mystery", 10749: "Romance", 878: "Science Fiction",
        10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
    }
    _TV_GENRES: dict[int, str] = {
        10759: "Action & Adventure", 16: "Animation", 35: "Comedy",
        80: "Crime", 99: "Documentary", 18: "Drama", 10751: "Family",
        10762: "Kids", 9648: "Mystery", 10763: "News", 10764: "Reality",
        10765: "Sci-Fi & Fantasy", 10766: "Soap", 10767: "Talk",
        10768: "War & Politics", 37: "Western",
    }

    @classmethod
    def _genre_names(cls, genre_ids: Any, *, is_series: bool) -> list[str]:
        """ID-Liste → Namensliste, TMDb-Reihenfolge bleibt erhalten
        (das erste Genre ist TMDbs primaeres — die Muster-Aggregation
        gruppiert danach)."""
        mapping = cls._TV_GENRES if is_series else cls._MOVIE_GENRES
        if not isinstance(genre_ids, list):
            return []
        return [mapping[g] for g in genre_ids if isinstance(g, int) and g in mapping]

    def normalize_tmdb_series(self, series: dict[str, Any]) -> dict[str, Any]:
        """TV sibling of ``normalize_tmdb_movie``. Maps the TV field names
        (``name`` / ``original_name`` / ``first_air_date``) onto the same
        normalized shape the sync consumes; ``release_date`` is the series'
        ``first_air_date``."""
        air_date_raw = series.get("first_air_date")
        release_year = None
        if air_date_raw and isinstance(air_date_raw, str) and len(air_date_raw) >= 4:
            try:
                release_year = int(air_date_raw[:4])
            except Exception:
                release_year = None

        aliases = [series.get("name"), series.get("original_name")]
        aliases = sorted({alias.strip() for alias in aliases if isinstance(alias, str) and alias.strip()})

        return {
            "tmdb_id": series.get("id"),
            "title_original": (series.get("original_name") or series.get("name") or "").strip(),
            "title_local": (series.get("name") or "").strip() or None,
            "release_date": air_date_raw,
            "release_year": release_year,
            "aliases": aliases,
            "overview": series.get("overview"),
            "popularity": series.get("popularity"),
            "genres": self._genre_names(series.get("genre_ids"), is_series=True),
        }

    async def discover_series_by_network(
        self, network_ids: str, language: str, region: str | None = None
    ) -> list[dict[str, Any]]:
        """Network-Achse fuer Streamer-Originals (Sprint §7, 22.08.2026).

        TMDb kennt fuer Serien den ``with_networks``-Filter — das ist die
        verlaessliche Originals-Achse (Netflix/Prime/Paramount+ als
        ausstrahlendes Netzwerk). Fuer FILME gibt es keinen Network-
        Filter; der ``with_watch_providers``-Ersatz zoege den kompletten
        LIZENZ-Katalog (zigtausende Fremdtitel) in die Whitelist —
        deshalb laufen Streamer-Filme weiterhin ueber kuratierbare
        Company-Sets (siehe STREAMER_COMPANY_SETS in title_sync)."""
        params: dict[str, Any] = {
            "language": language,
            "sort_by": "popularity.desc",
            "include_adult": "false",
            "with_networks": network_ids,
        }
        if region:
            params["region"] = region
        return await self._discover_paginated("/discover/tv", params)

    async def search_movies(self, query: str, *, language: str = "de-DE") -> list[dict[str, Any]]:
        """Namens-Suche ``/search/movie`` — Grundlage der Anreicherung
        manuell angelegter Titel (22.08.2026). TMDb sortiert nach
        Popularitaet; ``language`` steuert den lokalisierten ``title``
        im Ergebnis (de-DE liefert den DE-Verleihtitel)."""
        data = await self._get("/search/movie", {"query": query, "language": language, "include_adult": "false"})
        return data.get("results") or []

    async def search_series(self, query: str, *, language: str = "de-DE") -> list[dict[str, Any]]:
        """TV-Geschwister von ``search_movies`` (``/search/tv``)."""
        data = await self._get("/search/tv", {"query": query, "language": language, "include_adult": "false"})
        return data.get("results") or []

    async def get_title_details(self, tmdb_id: int, *, is_series: bool) -> dict[str, Any]:
        """Details-Endpoint (``/movie/{id}`` bzw. ``/tv/{id}``) — im
        Gegensatz zu discover liefert er volle Genre-Objekte mit Namen
        in TMDb-Reihenfolge (erstes = primaeres Genre). Grundlage des
        Genre-Backfills (21.08.2026) fuer Titel, die nie durch einen
        Company-Discover laufen (Streamer-Originals, Kandidaten-Titel)."""
        kind = "tv" if is_series else "movie"
        return await self._get(f"/{kind}/{tmdb_id}")

    async def get_movie_release_dates(self, tmdb_id: int) -> dict[str, Any]:
        return await self._get(f"/movie/{tmdb_id}/release_dates")

    async def get_movie_external_ids(self, tmdb_id: int) -> dict[str, Any]:
        return await self._get(f"/movie/{tmdb_id}/external_ids")

    def normalize_tmdb_movie(self, movie: dict[str, Any]) -> dict[str, Any]:
        release_date_raw = movie.get("release_date")
        release_year = None
        if release_date_raw and isinstance(release_date_raw, str) and len(release_date_raw) >= 4:
            try:
                release_year = int(release_date_raw[:4])
            except Exception:
                release_year = None

        aliases = [movie.get("title"), movie.get("original_title")]
        aliases = sorted({alias.strip() for alias in aliases if isinstance(alias, str) and alias.strip()})

        return {
            "tmdb_id": movie.get("id"),
            "title_original": (movie.get("original_title") or movie.get("title") or "").strip(),
            "title_local": (movie.get("title") or "").strip() or None,
            "release_date": release_date_raw,
            "release_year": release_year,
            "aliases": aliases,
            "overview": movie.get("overview"),
            "popularity": movie.get("popularity"),
            "genres": self._genre_names(movie.get("genre_ids"), is_series=False),
        }
