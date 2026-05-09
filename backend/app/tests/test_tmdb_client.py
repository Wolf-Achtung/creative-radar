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

import pytest

from app.services.tmdb_client import TMDbClient


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
