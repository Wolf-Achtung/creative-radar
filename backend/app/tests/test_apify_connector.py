"""Tests for the async-polling Apify-connector refactor (Sprint 0d).

The previous synchronous flow used Apify's ``waitForFinish`` query
parameter, which caps at 60 seconds server-side. That meant for every
actor run that took longer than a minute (the typical IG cron load),
the dataset read fired before the run had actually finished and the
cron summary recorded ``raw_items=0`` despite the actor producing
hundreds of items.

These tests pin the new behaviour: POST without waitForFinish, poll the
``/actor-runs/{id}`` endpoint until a TERMINAL_STATUS, then read the
dataset only on SUCCEEDED. ``apify_wait_seconds`` is now the total
poll-loop budget (enforced via ``asyncio.wait_for``); ``apify_poll_interval_seconds``
is the per-poll sleep.

The tests mock ``httpx.AsyncClient`` at the module-attribute boundary so
they do not require a real HTTP transport. monkeypatch automatically
restores the real attribute after each test.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.config import settings
from app.services import apify_connector


class _FakeResponse:
    """Minimal stand-in for httpx.Response — just the bits _run_actor uses."""

    def __init__(self, payload: Any, status_code: int = 200, headers: dict | None = None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://api.apify.com/v2/test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"status={self.status_code}", request=request, response=response,
            )


class _FakeAsyncClient:
    """httpx.AsyncClient drop-in for tests. Posts and gets pop from queues
    in FIFO order; calls are recorded so tests can assert on URLs/params.
    """

    def __init__(self, *, post_responses, get_responses):
        self._post_responses = list(post_responses)
        self._get_responses = list(get_responses)
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[tuple[str, dict]] = []

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, **kwargs) -> _FakeResponse:
        self.posts.append((url, kwargs))
        return self._post_responses.pop(0)

    async def get(self, url: str, **kwargs) -> _FakeResponse:
        self.gets.append((url, kwargs))
        if not self._get_responses:
            raise AssertionError(f"Unexpected GET to {url}; queue exhausted")
        return self._get_responses.pop(0)


class _AlwaysRunningClient(_FakeAsyncClient):
    """Variant whose GET always returns RUNNING, so the poll loop never
    terminates on its own. Used to exercise the asyncio.wait_for timeout."""

    async def get(self, url: str, **kwargs) -> _FakeResponse:
        self.gets.append((url, kwargs))
        return _FakeResponse({"data": {"id": "RUN1", "status": "RUNNING"}})


@pytest.fixture(autouse=True)
def _fast_settings(monkeypatch: pytest.MonkeyPatch):
    """Speed: poll interval 0 so the tests don't actually sleep. Token
    needs to be non-empty so the connector module accepts the request."""
    monkeypatch.setattr(settings, "apify_api_token", "TEST_TOKEN", raising=False)
    monkeypatch.setattr(settings, "apify_poll_interval_seconds", 0, raising=False)
    monkeypatch.setattr(settings, "apify_wait_seconds", 5, raising=False)


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: _FakeAsyncClient) -> None:
    """Replace httpx.AsyncClient inside apify_connector with a factory that
    returns the prepared fake. ``**kwargs`` swallows the timeout argument
    that _run_actor passes."""
    monkeypatch.setattr(
        apify_connector.httpx, "AsyncClient", lambda **kwargs: client,
    )


def test_run_actor_succeeds_after_multiple_polls(monkeypatch):
    """Three polls (RUNNING, RUNNING, SUCCEEDED) followed by the dataset
    read. Verifies the new flow: POST without waitForFinish, poll until
    terminal, read dataset, return items.
    """
    client = _FakeAsyncClient(
        post_responses=[
            _FakeResponse({"data": {"id": "RUN1", "status": "READY"}}),
        ],
        get_responses=[
            _FakeResponse({"data": {"id": "RUN1", "status": "RUNNING"}}),
            _FakeResponse({"data": {"id": "RUN1", "status": "RUNNING"}}),
            _FakeResponse({"data": {
                "id": "RUN1",
                "status": "SUCCEEDED",
                "defaultDatasetId": "DS1",
                "actId": "actor",
            }}),
            _FakeResponse([{"a": 1}, {"a": 2}]),  # dataset items
        ],
    )
    _patch_client(monkeypatch, client)

    items = asyncio.run(apify_connector._run_actor("actor", {"input": "x"}))

    assert items == [{"a": 1}, {"a": 2}]
    # 1 POST to start, 4 GETs (3 polls + 1 dataset read).
    assert len(client.posts) == 1
    assert len(client.gets) == 4
    # waitForFinish must not be sent — that was the whole bug.
    post_params = client.posts[0][1].get("params", {})
    assert "waitForFinish" not in post_params
    assert post_params.get("token") == "TEST_TOKEN"
    # POST URL hits the runs endpoint of the right actor.
    assert client.posts[0][0].endswith("/acts/actor/runs")
    # First poll URL hits actor-runs/{id}.
    assert client.gets[0][0].endswith("/actor-runs/RUN1")


def test_run_actor_raises_on_total_timeout(monkeypatch):
    """Poll loop never sees a terminal status → asyncio.wait_for trips,
    surfaced as RuntimeError so the cron-run failure path catches it."""
    client = _AlwaysRunningClient(
        post_responses=[
            _FakeResponse({"data": {"id": "RUN1", "status": "READY"}}),
        ],
        get_responses=[],
    )
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(settings, "apify_wait_seconds", 0.05, raising=False)

    with pytest.raises(RuntimeError, match="did not finish"):
        asyncio.run(apify_connector._run_actor("actor", {}))


def test_run_actor_raises_on_failed_status(monkeypatch):
    """Run reaches a terminal status that isn't SUCCEEDED — must raise
    instead of silently reading an empty/garbage dataset."""
    client = _FakeAsyncClient(
        post_responses=[
            _FakeResponse({"data": {"id": "RUN1", "status": "READY"}}),
        ],
        get_responses=[
            _FakeResponse({"data": {"id": "RUN1", "status": "FAILED"}}),
        ],
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(RuntimeError, match="status=FAILED"):
        asyncio.run(apify_connector._run_actor("actor", {}))


def test_run_actor_raises_on_aborted_status(monkeypatch):
    """ABORTED is also terminal-but-not-success."""
    client = _FakeAsyncClient(
        post_responses=[
            _FakeResponse({"data": {"id": "RUN1", "status": "READY"}}),
        ],
        get_responses=[
            _FakeResponse({"data": {"id": "RUN1", "status": "ABORTED"}}),
        ],
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(RuntimeError, match="status=ABORTED"):
        asyncio.run(apify_connector._run_actor("actor", {}))


def test_run_actor_raises_on_timed_out_status(monkeypatch):
    """Apify reports TIMED-OUT (with hyphen) as a terminal status. Pin it."""
    client = _FakeAsyncClient(
        post_responses=[
            _FakeResponse({"data": {"id": "RUN1", "status": "READY"}}),
        ],
        get_responses=[
            _FakeResponse({"data": {"id": "RUN1", "status": "TIMED-OUT"}}),
        ],
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(RuntimeError, match="status=TIMED-OUT"):
        asyncio.run(apify_connector._run_actor("actor", {}))


def test_run_actor_returns_empty_when_post_lacks_run_id(monkeypatch):
    """Defensive: if Apify's POST response has no ``id``, return [] rather
    than crashing in the poll loop."""
    client = _FakeAsyncClient(
        post_responses=[_FakeResponse({"data": {}})],
        get_responses=[],
    )
    _patch_client(monkeypatch, client)

    items = asyncio.run(apify_connector._run_actor("actor", {}))
    assert items == []
    assert client.gets == []  # no polling attempted


def test_run_actor_returns_empty_when_succeeded_run_has_no_dataset(monkeypatch):
    """If the run finished SUCCEEDED but has no defaultDatasetId, the
    function returns [] without trying to read a non-existent dataset."""
    client = _FakeAsyncClient(
        post_responses=[
            _FakeResponse({"data": {"id": "RUN1", "status": "READY"}}),
        ],
        get_responses=[
            _FakeResponse({"data": {"id": "RUN1", "status": "SUCCEEDED"}}),
        ],
    )
    _patch_client(monkeypatch, client)

    items = asyncio.run(apify_connector._run_actor("actor", {}))
    assert items == []
    # 1 poll, no dataset read.
    assert len(client.gets) == 1


# --- Sprint Z1: transient-failure retry tests -----------------------------


@pytest.fixture
def _no_sleep(monkeypatch: pytest.MonkeyPatch):
    """Skip the retry helper's backoff sleeps. Returns the captured wait
    list so tests can assert on the number of retries."""
    waits: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(apify_connector.asyncio, "sleep", _fake_sleep)
    return waits


def test_run_actor_retries_on_transient_502_during_poll(monkeypatch, _no_sleep):
    """Z1: 502 -> 502 -> 200 on the status-poll endpoint must surface as a
    successful run after retries. Pins the fix for the 2026-05-18 cron abort."""
    client = _FakeAsyncClient(
        post_responses=[
            _FakeResponse({"data": {"id": "RUN1", "status": "READY"}}),
        ],
        get_responses=[
            _FakeResponse({}, status_code=502),
            _FakeResponse({}, status_code=502),
            _FakeResponse({"data": {
                "id": "RUN1",
                "status": "SUCCEEDED",
                "defaultDatasetId": "DS1",
                "actId": "actor",
            }}),
            _FakeResponse([{"a": 1}]),
        ],
    )
    _patch_client(monkeypatch, client)

    items = asyncio.run(apify_connector._run_actor("actor", {}))

    assert items == [{"a": 1}]
    # 2 failed polls + 1 successful poll + 1 dataset read.
    assert len(client.gets) == 4
    assert len(client.posts) == 1


def test_run_actor_does_not_retry_post_on_502(monkeypatch, _no_sleep):
    """Z1: the actor-run POST is non-idempotent. A 502 might mean the run
    was already created upstream — retry would double-start the actor and
    double-bill under the F0.6 budget cap. Must surface immediately."""
    client = _FakeAsyncClient(
        post_responses=[
            _FakeResponse({}, status_code=502),
        ],
        get_responses=[],
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(apify_connector._run_actor("actor", {}))

    # Critically: exactly one POST attempt, no retry.
    assert len(client.posts) == 1


class _PostConnectFailsOnce(_FakeAsyncClient):
    """POST raises ConnectError on the first attempt, succeeds on the second."""

    def __init__(self, *, post_responses, get_responses):
        super().__init__(post_responses=post_responses, get_responses=get_responses)
        self._post_attempts = 0

    async def post(self, url, **kwargs):
        self._post_attempts += 1
        self.posts.append((url, kwargs))
        if self._post_attempts == 1:
            raise httpx.ConnectError("simulated TCP-level connect failure")
        return self._post_responses.pop(0)


def test_run_actor_retries_post_on_connect_error(monkeypatch, _no_sleep):
    """Z1: ConnectError on POST is TCP-level provably-unreached, so it IS
    safe to retry the non-idempotent endpoint — the upstream couldn't have
    started a run we didn't know about."""
    client = _PostConnectFailsOnce(
        post_responses=[
            _FakeResponse({"data": {"id": "RUN1", "status": "READY"}}),
        ],
        get_responses=[
            _FakeResponse({"data": {
                "id": "RUN1",
                "status": "SUCCEEDED",
                "defaultDatasetId": "DS1",
                "actId": "actor",
            }}),
            _FakeResponse([]),
        ],
    )
    _patch_client(monkeypatch, client)

    items = asyncio.run(apify_connector._run_actor("actor", {}))
    assert items == []
    assert client._post_attempts == 2  # one retry after the ConnectError


def test_run_actor_does_not_retry_on_4xx(monkeypatch, _no_sleep):
    """Z1: 4xx (except 429) is a permanent client error. Must propagate on
    the first response, no retry burn."""
    client = _FakeAsyncClient(
        post_responses=[
            _FakeResponse({"data": {"id": "RUN1", "status": "READY"}}),
        ],
        get_responses=[
            _FakeResponse({}, status_code=404),
        ],
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(apify_connector._run_actor("actor", {}))

    assert len(client.gets) == 1  # no retry on 404


def test_run_actor_exhausts_retries_on_persistent_502_during_poll(monkeypatch, _no_sleep):
    """Z1: after the configured retry budget is spent, the final 502
    surfaces via raise_for_status — the run does not silently succeed."""
    client = _FakeAsyncClient(
        post_responses=[
            _FakeResponse({"data": {"id": "RUN1", "status": "READY"}}),
        ],
        get_responses=[
            _FakeResponse({}, status_code=502),
            _FakeResponse({}, status_code=502),
            _FakeResponse({}, status_code=502),
        ],
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(apify_connector._run_actor("actor", {}))

    # 3 total attempts (1 initial + 2 retries).
    assert len(client.gets) == 3


def test_run_actor_explicit_wait_seconds_overrides_settings(monkeypatch):
    """The kwarg-form of wait_seconds keeps backwards compatibility and
    overrides the settings default. Pin the override path on a too-short
    timeout to confirm it's the kwarg, not settings, that takes effect."""
    monkeypatch.setattr(settings, "apify_wait_seconds", 60, raising=False)
    client = _AlwaysRunningClient(
        post_responses=[
            _FakeResponse({"data": {"id": "RUN1", "status": "READY"}}),
        ],
        get_responses=[],
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(RuntimeError, match="did not finish within 0s"):
        asyncio.run(apify_connector._run_actor("actor", {}, wait_seconds=0.05))
