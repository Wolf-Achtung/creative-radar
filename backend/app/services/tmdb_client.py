from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from app.config import settings

BASE_URL = "https://api.themoviedb.org/3"

# TMDb caps the ``page`` query param at 500 on the /discover endpoints.
# We honour that as the upper bound for full pagination.
TMDB_MAX_DISCOVER_PAGES = 500


class TMDbAuthError(RuntimeError):
    pass


class TMDbClient:
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

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._ensure_auth()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{BASE_URL}{path}", params=self._params(params), headers=self._headers())
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if response.status_code == 401:
                    raise TMDbAuthError(
                        "TMDb-Zugangsdaten ungültig. Bitte TMDB_READ_ACCESS_TOKEN und TMDB_API_KEY in Railway prüfen."
                    ) from exc
                raise
            return response.json()

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
        }

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
        }
