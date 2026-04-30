from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.proxy import _host_is_allowed
from app.main import app


client = TestClient(app)


# -- Whitelist (sync, no HTTP) -------------------------------------------------

def test_host_whitelist_accepts_exact_match():
    assert _host_is_allowed("cdninstagram.com") is True


def test_host_whitelist_accepts_subdomain():
    assert _host_is_allowed("scontent-ord5-2.cdninstagram.com") is True
    assert _host_is_allowed("p16-common-sign.tiktokcdn-us.com") is True


def test_host_whitelist_rejects_unrelated_host():
    assert _host_is_allowed("example.com") is False
    assert _host_is_allowed("evil.com") is False


def test_host_whitelist_rejects_spoof_with_suffix_in_label():
    """A host like 'fakecdninstagram.com' must not match 'cdninstagram.com'."""
    assert _host_is_allowed("fakecdninstagram.com") is False


def test_host_whitelist_rejects_spoof_with_suffix_as_subdomain_label():
    """A host like 'cdninstagram.com.evil.com' must not match 'cdninstagram.com'."""
    assert _host_is_allowed("cdninstagram.com.evil.com") is False


def test_host_whitelist_is_case_insensitive():
    assert _host_is_allowed("SCONTENT.CDNINSTAGRAM.COM") is True


# -- Endpoint (HTTP) -----------------------------------------------------------

def _mock_response(status_code: int, body: bytes, content_type: str = "image/jpeg", content_length: str | None = None):
    headers = {"content-type": content_type}
    if content_length is not None:
        headers["content-length"] = content_length
    response = httpx.Response(status_code=status_code, content=body, headers=headers)
    return response


def test_proxy_rejects_invalid_scheme():
    r = client.get("/api/img", params={"url": "ftp://cdninstagram.com/foo.jpg"})
    assert r.status_code == 400


def test_proxy_rejects_non_whitelisted_host():
    r = client.get("/api/img", params={"url": "https://example.com/foo.jpg"})
    assert r.status_code == 403


def test_proxy_rejects_missing_url_param():
    r = client.get("/api/img")
    assert r.status_code == 422  # FastAPI validation


def test_proxy_streams_whitelisted_response():
    body = b"\x89PNG\r\n\x1a\n" + b"x" * 32
    fake = _mock_response(200, body, "image/png")
    with patch("app.api.proxy.httpx.AsyncClient") as ac:
        instance = ac.return_value
        instance.get = AsyncMock(return_value=fake)
        instance.aclose = AsyncMock()
        r = client.get("/api/img", params={"url": "https://scontent.cdninstagram.com/v/foo.jpg"})
    assert r.status_code == 200
    assert r.content == body
    assert r.headers["content-type"].startswith("image/png")
    assert "max-age=3600" in r.headers["cache-control"]


def test_proxy_returns_502_when_upstream_4xx():
    fake = _mock_response(404, b"not found")
    with patch("app.api.proxy.httpx.AsyncClient") as ac:
        instance = ac.return_value
        instance.get = AsyncMock(return_value=fake)
        instance.aclose = AsyncMock()
        r = client.get("/api/img", params={"url": "https://scontent.cdninstagram.com/v/dead.jpg"})
    assert r.status_code == 502
    assert "404" in r.json()["detail"]


def test_proxy_returns_502_when_upstream_transport_error():
    with patch("app.api.proxy.httpx.AsyncClient") as ac:
        instance = ac.return_value
        instance.get = AsyncMock(side_effect=httpx.ConnectTimeout("timeout"))
        instance.aclose = AsyncMock()
        r = client.get("/api/img", params={"url": "https://scontent.cdninstagram.com/v/slow.jpg"})
    assert r.status_code == 502


def test_proxy_returns_502_when_content_length_exceeds_cap():
    huge = "999999999"  # ~1 GB declared
    fake = _mock_response(200, b"x", content_length=huge)
    with patch("app.api.proxy.httpx.AsyncClient") as ac:
        instance = ac.return_value
        instance.get = AsyncMock(return_value=fake)
        instance.aclose = AsyncMock()
        r = client.get("/api/img", params={"url": "https://scontent.cdninstagram.com/v/huge.jpg"})
    assert r.status_code == 502
    assert "too large" in r.json()["detail"].lower()
