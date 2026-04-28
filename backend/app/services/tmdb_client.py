from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from app.config import settings

BASE_URL = "https://api.themoviedb.org/3"


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

    async def discover_movies(self, region: str, language: str, date_from: date, date_to: date) -> list[dict[str, Any]]:
        page = 1
        results: list[dict[str, Any]] = []
        while page <= 3:  # hard cap for weekly sync safety
            data = await self._get(
                "/discover/movie",
                {
                    "region": region,
                    "language": language,
                    "sort_by": "primary_release_date.asc",
                    "include_adult": "false",
                    "include_video": "false",
                    "release_date.gte": date_from.isoformat(),
                    "release_date.lte": date_to.isoformat(),
                    "with_release_type": "2|3",
                    "page": page,
                },
            )
            page_results = data.get("results") or []
            if not page_results:
                break
            results.extend(page_results)
            if page >= int(data.get("total_pages") or 1):
                break
            page += 1
        return results

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
