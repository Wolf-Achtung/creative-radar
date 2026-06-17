"""Sprint 9 H1 tests — TMDb release-type filter expansion.

Asserts that ``TMDbClient.discover_movies`` requests the right
``with_release_type`` mask and that the response is passed through
verbatim regardless of TMDb-side release-type semantics. The mask
itself (``2|3|4|6``) is what actually drives which titles TMDb returns,
so we pin the request shape — not invent fake type-tagging.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest

from app.services import tmdb_client as tc
from app.services.tmdb_client import TMDbAuthError, TMDbClient


class _RecordingClient:
    """Captures ``_get`` calls and returns canned payloads in order."""

    def __init__(self, payloads: list[dict[str, Any]]):
        self._payloads = list(payloads)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((path, dict(params or {})))
        if not self._payloads:
            return {"results": [], "total_pages": 1}
        return self._payloads.pop(0)


def _make_client(payloads: list[dict[str, Any]]) -> tuple[TMDbClient, _RecordingClient]:
    client = TMDbClient(api_key="dummy")
    recorder = _RecordingClient(payloads)
    # Bypass HTTP layer — exercise discover_movies' filter/pagination logic only.
    client._get = recorder._get  # type: ignore[method-assign]
    return client, recorder


@pytest.mark.asyncio
async def test_discover_movies_uses_expanded_release_type_mask():
    """Sprint 9: mask must be ``2|3|4|6`` so Digital + TV releases are pulled."""
    client, recorder = _make_client(
        [{"results": [{"id": 1, "title": "X"}], "total_pages": 1}]
    )

    await client.discover_movies(
        region="US", language="en-US", date_from=date(2026, 1, 1), date_to=date(2026, 6, 1)
    )

    assert recorder.calls, "discover_movies must hit the API at least once"
    _, params = recorder.calls[0]
    assert params["with_release_type"] == "2|3|4|6"


@pytest.mark.asyncio
async def test_discover_movies_includes_digital_release_payload():
    """A movie returned by TMDb (regardless of source type) is propagated."""
    digital_movie = {"id": 4242, "title": "Drawn to You", "release_date": "2026-04-01"}
    client, _ = _make_client(
        [{"results": [digital_movie], "total_pages": 1}]
    )

    results = await client.discover_movies(
        region="US", language="en-US", date_from=date(2026, 1, 1), date_to=date(2026, 6, 1)
    )

    assert any(movie["id"] == 4242 for movie in results)


@pytest.mark.asyncio
async def test_discover_movies_includes_tv_release_payload():
    """TV-typed releases (type 6) reach the caller — the expanded mask is the gate."""
    tv_movie = {"id": 9001, "title": "TV Special", "release_date": "2026-05-15"}
    client, _ = _make_client(
        [{"results": [tv_movie], "total_pages": 1}]
    )

    results = await client.discover_movies(
        region="DE", language="de-DE", date_from=date(2026, 1, 1), date_to=date(2026, 12, 31)
    )

    assert any(movie["id"] == 9001 for movie in results)


@pytest.mark.asyncio
async def test_discover_movies_does_not_include_premiere_or_physical_in_mask():
    """Type 1 (Premiere) and 5 (Physical) must stay out of the request mask."""
    client, recorder = _make_client(
        [{"results": [], "total_pages": 1}]
    )

    await client.discover_movies(
        region="US", language="en-US", date_from=date(2026, 1, 1), date_to=date(2026, 6, 1)
    )

    mask = recorder.calls[0][1]["with_release_type"]
    parts = set(mask.split("|"))
    assert "1" not in parts
    assert "5" not in parts


@pytest.mark.asyncio
async def test_discover_movies_still_includes_theatrical_types():
    """Pre-Sprint-9 acceptance: theatrical types (2 and 3) remain in the mask."""
    client, recorder = _make_client(
        [{"results": [], "total_pages": 1}]
    )

    await client.discover_movies(
        region="US", language="en-US", date_from=date(2026, 1, 1), date_to=date(2026, 6, 1)
    )

    mask = recorder.calls[0][1]["with_release_type"]
    parts = set(mask.split("|"))
    assert "2" in parts
    assert "3" in parts


# ---------------------------------------------------------------- TV discover ---


@pytest.mark.asyncio
async def test_discover_series_hits_tv_endpoint_with_first_air_date_window():
    """discover_series queries /discover/tv and filters on first_air_date."""
    client, recorder = _make_client(
        [{"results": [{"id": 10, "name": "Murderbot"}], "total_pages": 1}]
    )

    results = await client.discover_series(
        region="US", language="en-US", date_from=date(2026, 1, 1), date_to=date(2026, 6, 1)
    )

    path, params = recorder.calls[0]
    assert path == "/discover/tv"
    assert params["first_air_date.gte"] == "2026-01-01"
    assert params["first_air_date.lte"] == "2026-06-01"
    assert params["sort_by"] == "popularity.desc"
    assert "with_release_type" not in params  # movie-only filter, not TV
    assert any(s["id"] == 10 for s in results)


@pytest.mark.asyncio
async def test_discover_series_paginates_beyond_old_three_page_cap():
    """Sprint Studio-Title-Sync: the former 3-page hard cap is gone. The
    paginator now follows ``total_pages`` (here 5), so all 5 pages are pulled."""
    pages = [{"results": [{"id": i}], "total_pages": 5} for i in range(5)]
    client, recorder = _make_client(pages)

    results = await client.discover_series(
        region="DE", language="de-DE", date_from=date(2026, 1, 1), date_to=date(2026, 12, 31)
    )

    assert len(recorder.calls) == 5  # not capped at 3 anymore
    assert len(results) == 5
    # page param advanced 1..5
    assert [c[1]["page"] for c in recorder.calls] == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_discover_stops_on_empty_results_page():
    """Pagination stops as soon as a page returns no results, even if
    total_pages claims more."""
    pages = [
        {"results": [{"id": 1}], "total_pages": 99},
        {"results": [{"id": 2}], "total_pages": 99},
        {"results": [], "total_pages": 99},
    ]
    client, recorder = _make_client(pages)

    results = await client.discover_movies(
        region="US", language="en-US", date_from=date(2026, 1, 1), date_to=date(2026, 6, 1)
    )

    assert len(recorder.calls) == 3  # third page empty -> stop
    assert len(results) == 2


def test_tmdb_region_maps_uk_to_gb():
    """App market 'UK' must translate to TMDb region 'GB'; others pass through."""
    assert TMDbClient.tmdb_region("UK") == "GB"
    assert TMDbClient.tmdb_region("uk") == "GB"
    assert TMDbClient.tmdb_region("DE") == "DE"
    assert TMDbClient.tmdb_region("US") == "US"
    assert TMDbClient.tmdb_region(None) is None


def test_normalize_tmdb_series_maps_tv_fields():
    """name -> title_local, original_name -> title_original, first_air_date -> release_date."""
    client = TMDbClient(api_key="dummy")
    normalized = client.normalize_tmdb_series({
        "id": 555,
        "name": "Murderbot",
        "original_name": "Murderbot",
        "first_air_date": "2026-05-16",
        "overview": "A SecUnit.",
        "popularity": 42.0,
    })

    assert normalized["tmdb_id"] == 555
    assert normalized["title_original"] == "Murderbot"
    assert normalized["title_local"] == "Murderbot"
    assert normalized["release_date"] == "2026-05-16"
    assert normalized["release_year"] == 2026
    assert "Murderbot" in normalized["aliases"]


# ---------------------------------------------- transient-retry resilience ---
# These exercise the REAL TMDbClient._get (not the _RecordingClient stub) by
# faking httpx.AsyncClient.get with a scripted sequence of responses/exceptions.


def _resp(status: int, payload: dict | None = None, headers: dict | None = None) -> httpx.Response:
    req = httpx.Request("GET", "https://api.themoviedb.org/3/x")
    return httpx.Response(status, json=(payload if payload is not None else {}),
                          request=req, headers=headers)


def _patch_get(monkeypatch, actions: list) -> dict:
    """Fake httpx.AsyncClient.get popping ``actions`` in order. An item that is
    an Exception is raised; otherwise it is returned as the response."""
    state = {"actions": list(actions), "calls": 0}

    async def fake_get(self, url, params=None, headers=None):
        state["calls"] += 1
        action = state["actions"].pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    return state


@pytest.fixture
def fast_retry(monkeypatch):
    """Zero out the backoffs so retry tests run instantly (asyncio.sleep(0))."""
    monkeypatch.setattr(tc, "TMDB_RETRY_BACKOFFS", (0.0, 0.0, 0.0))


@pytest.mark.asyncio
async def test_get_retries_transient_500_then_succeeds(monkeypatch, fast_retry):
    state = _patch_get(monkeypatch, [_resp(500), _resp(200, {"ok": True})])
    client = TMDbClient(api_key="dummy")
    assert await client._get("/discover/movie", {"page": 12}) == {"ok": True}
    assert state["calls"] == 2  # one retry, then success


@pytest.mark.asyncio
async def test_get_gives_up_after_exhausting_retries_on_persistent_500(monkeypatch, fast_retry):
    state = _patch_get(monkeypatch, [_resp(500)] * 4)
    client = TMDbClient(api_key="dummy")
    with pytest.raises(httpx.HTTPStatusError):
        await client._get("/discover/movie")
    assert state["calls"] == 4  # 1 initial + 3 retries, then re-raise


@pytest.mark.asyncio
async def test_get_does_not_retry_4xx(monkeypatch, fast_retry):
    state = _patch_get(monkeypatch, [_resp(404), _resp(200, {"ok": True})])
    client = TMDbClient(api_key="dummy")
    with pytest.raises(httpx.HTTPStatusError):
        await client._get("/movie/999")
    assert state["calls"] == 1  # 4xx is a real error -> no retry


@pytest.mark.asyncio
async def test_get_retries_network_timeout_then_succeeds(monkeypatch, fast_retry):
    state = _patch_get(monkeypatch, [httpx.ReadTimeout("transient"), _resp(200, {"ok": True})])
    client = TMDbClient(api_key="dummy")
    assert await client._get("/discover/tv") == {"ok": True}
    assert state["calls"] == 2


@pytest.mark.asyncio
async def test_get_401_raises_auth_error_without_retry(monkeypatch, fast_retry):
    state = _patch_get(monkeypatch, [_resp(401), _resp(200, {"ok": True})])
    client = TMDbClient(api_key="dummy")
    with pytest.raises(TMDbAuthError):
        await client._get("/discover/movie")
    assert state["calls"] == 1  # auth is permanent -> no retry


def test_retry_after_seconds_honours_429_header():
    req = httpx.Request("GET", "https://api.themoviedb.org/3/x")
    resp = httpx.Response(429, headers={"Retry-After": "7"}, request=req)
    exc = httpx.HTTPStatusError("429", request=req, response=resp)
    assert TMDbClient._retry_after_seconds(exc, fallback=2.0) == 7.0
    # No header -> fall back to the backoff value.
    resp2 = httpx.Response(429, request=req)
    exc2 = httpx.HTTPStatusError("429", request=req, response=resp2)
    assert TMDbClient._retry_after_seconds(exc2, fallback=2.0) == 2.0


@pytest.mark.asyncio
async def test_discover_paginates_through_transient_500(monkeypatch, fast_retry):
    """A transient 500 mid-pagination must NOT abort the run — _get retries it
    and the paginator continues to the empty page. This is the #277 live-sync
    failure mode (500 on /discover/movie page 12)."""
    actions = [
        _resp(200, {"results": [{"id": 1}], "total_pages": 3}),   # page 1
        _resp(500),                                                # page 2 (transient)
        _resp(200, {"results": [{"id": 2}], "total_pages": 3}),   # page 2 retry
        _resp(200, {"results": [], "total_pages": 3}),            # page 3 empty -> stop
    ]
    state = _patch_get(monkeypatch, actions)
    client = TMDbClient(api_key="dummy")
    results = await client.discover_movies_by_company("2|3", language="en-US", region="US")
    assert [r["id"] for r in results] == [1, 2]
    assert state["calls"] == 4
