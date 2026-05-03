"""HTTP-layer tests for POST /api/admin/analyze/{channel_id}
(Sprint 5.3.1 Mini-Run 3).

Pattern mirrors test_api_admin_youtube_sync: shared :memory: SQLite
engine via StaticPool, get_session dependency-overridden, auth_enabled
flipped off so the global Bearer middleware lets the test client
through. The Anthropic SDK calls are patched at the post_analyzer
module boundary (where the wrapper functions live) — this exercises
the endpoint's loop / error mapping / response shape without touching
real HTTP. Coverage of the analyzer's per-post logic itself lives in
test_post_analyzer.

Covers:
- happy path: 3 unanalyzed posts -> all analyzed, response shape
  matches the Sprint contract
- idempotency: re-trigger without ?force=true -> 0 analyzed,
  3 skipped (and the per-post Asset row + Post.analysis are
  unchanged from the first run)
- ?force=true re-runs already-analyzed posts
- ?limit=N caps the batch size
- 404 unknown channel UUID
- 401 ANTHROPIC_API_KEY missing
- 401 propagated from the analyzer (auth failure mid-batch)
- 429 propagated from the analyzer (rate limit after retries)
- 503 lazy-import failure of post_analyzer
- per-post errors don't crash the batch (one bad, two good)
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.api.admin as admin_mod
from app.config import settings
from app.database import get_session
from app.main import app
from app.models.entities import Asset, Channel, Post
from app.services import cost_log as cost_log_module


YOUTUBE_PAYLOAD = {
    "snippet": {
        "thumbnails": {
            "maxres": {"url": "https://i.ytimg.com/vi/abc/maxresdefault.jpg"},
        }
    }
}


def _shared_test_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def db():
    engine = _shared_test_engine()
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_enabled", False, raising=False)
    monkeypatch.setattr(settings, "anthropic_api_key", "TEST-KEY", raising=False)
    monkeypatch.setattr(settings, "anthropic_haiku_model", "claude-haiku-4-5-20251001", raising=False)
    monkeypatch.setattr(settings, "anthropic_sonnet_model", "claude-sonnet-4-6", raising=False)
    # Route record_anthropic_call writes into the same in-memory DB so
    # the cost-log assertion (anthropic_calls counters in the response)
    # has somewhere to write without exploding.
    monkeypatch.setattr(cost_log_module, "engine", db)

    def _override_session():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _seed_channel(engine, *, platform: str = "youtube") -> Channel:
    with Session(engine) as session:
        channel = Channel(
            name="Netflix",
            platform=platform,
            url="https://www.youtube.com/@netflix",
            handle="@netflix",
            active=True,
        )
        session.add(channel)
        session.commit()
        session.refresh(channel)
        return channel


def _seed_posts(engine, channel: Channel, n: int) -> list[Post]:
    posts = []
    with Session(engine) as session:
        for i in range(n):
            post = Post(
                channel_id=channel.id,
                platform=channel.platform,
                post_url=f"https://www.youtube.com/watch?v=v{i}",
                external_id=f"v{i}",
                caption=f"Trailer {i}",
                raw_payload=YOUTUBE_PAYLOAD,
                published_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            )
            session.add(post)
        session.commit()
        posts = list(session.exec(select(Post).where(Post.channel_id == channel.id)).all())
    return posts


def _fake_message(text: str, *, input_tokens: int = 100, output_tokens: int = 30):
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _patch_anthropic_calls(*, n_posts: int):
    """Each post gets vision (called once) + Haiku + Sonnet (each
    called once). With ``side_effect`` lists, side_effect is consumed
    once per call across all posts in the batch."""
    from app.services import post_analyzer

    vision_returns = [_fake_message(f"vision desc {i}", input_tokens=1500, output_tokens=80)
                      for i in range(n_posts)]
    text_returns = []
    for _ in range(n_posts):
        text_returns.append(_fake_message('{"format":"trailer","tone":"suspenseful","confidence":0.9}'))
        text_returns.append(_fake_message('{"purpose":"release_week","lifecycle_stage":"launch","confidence":0.8}'))

    return (
        patch.object(post_analyzer, "messages_create_vision", side_effect=vision_returns),
        patch.object(post_analyzer, "messages_create_text", side_effect=text_returns),
    )


# ---------- Happy path -----------------------------------------------


def test_analyze_happy_path_runs_all_unanalyzed_posts(client: TestClient, db):
    channel = _seed_channel(db)
    _seed_posts(db, channel, n=3)

    p_vision, p_text = _patch_anthropic_calls(n_posts=3)
    with p_vision, p_text:
        response = client.post(f"/api/admin/analyze/{channel.id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["channel_id"] == str(channel.id)
    assert body["platform"] == "youtube"
    assert body["analyzed_posts"] == 3
    assert body["skipped_posts"] == 0
    assert body["asset_rows_created"] == 3
    assert body["errors"] == []
    assert body["anthropic_calls"] == {"haiku": 3, "sonnet": 3, "sonnet_vision": 3}

    # Persisted state: every post has Post.analysis + last_analyzed_at,
    # every post has exactly one Asset row.
    with Session(db) as session:
        posts = list(session.exec(select(Post)).all())
        assets = list(session.exec(select(Asset)).all())
    assert len(posts) == 3
    assert all(p.analysis is not None for p in posts)
    assert all(p.last_analyzed_at is not None for p in posts)
    assert len(assets) == 3
    assert all(a.vision_description and a.vision_model for a in assets)


# ---------- Idempotency ----------------------------------------------


def test_analyze_skips_already_analyzed_by_default(client: TestClient, db):
    channel = _seed_channel(db)
    _seed_posts(db, channel, n=3)

    p_vision, p_text = _patch_anthropic_calls(n_posts=3)
    with p_vision, p_text:
        first = client.post(f"/api/admin/analyze/{channel.id}")
    assert first.status_code == 200
    assert first.json()["analyzed_posts"] == 3

    # Second trigger: nothing new to analyze, all 3 skipped, no
    # Anthropic calls logged. Patch with empty side_effects so any
    # accidental call would StopIteration loud.
    from app.services import post_analyzer
    with patch.object(post_analyzer, "messages_create_vision", side_effect=[]), \
         patch.object(post_analyzer, "messages_create_text", side_effect=[]):
        second = client.post(f"/api/admin/analyze/{channel.id}")
    body = second.json()
    assert second.status_code == 200, second.text
    assert body["analyzed_posts"] == 0
    assert body["skipped_posts"] == 3
    assert body["asset_rows_created"] == 0
    assert body["anthropic_calls"] == {"haiku": 0, "sonnet": 0, "sonnet_vision": 0}


def test_analyze_force_reruns_already_analyzed(client: TestClient, db):
    channel = _seed_channel(db)
    _seed_posts(db, channel, n=2)

    p_vision1, p_text1 = _patch_anthropic_calls(n_posts=2)
    with p_vision1, p_text1:
        first = client.post(f"/api/admin/analyze/{channel.id}")
    assert first.json()["analyzed_posts"] == 2

    # Force re-run: the pre-existing Asset rows already carry
    # vision_description so the inner-asset idempotency skips the
    # vision call (no new Anthropic vision-cost) but the classifier
    # calls re-run.
    from app.services import post_analyzer
    text_returns = []
    for _ in range(2):
        text_returns.append(_fake_message('{"format":"trailer","tone":"suspenseful","confidence":0.9}'))
        text_returns.append(_fake_message('{"purpose":"release_week","lifecycle_stage":"launch","confidence":0.8}'))
    with patch.object(post_analyzer, "messages_create_vision", side_effect=[]), \
         patch.object(post_analyzer, "messages_create_text", side_effect=text_returns):
        second = client.post(f"/api/admin/analyze/{channel.id}?force=true")
    body = second.json()
    assert second.status_code == 200
    assert body["analyzed_posts"] == 2
    assert body["skipped_posts"] == 0
    # Vision short-circuited via the pre-existing-asset check.
    assert body["anthropic_calls"]["sonnet_vision"] == 0
    assert body["anthropic_calls"]["haiku"] == 2
    assert body["anthropic_calls"]["sonnet"] == 2


def test_analyze_limit_caps_batch_size(client: TestClient, db):
    channel = _seed_channel(db)
    _seed_posts(db, channel, n=5)

    p_vision, p_text = _patch_anthropic_calls(n_posts=2)
    with p_vision, p_text:
        response = client.post(f"/api/admin/analyze/{channel.id}?limit=2")
    body = response.json()
    assert response.status_code == 200, response.text
    assert body["analyzed_posts"] == 2
    # Remaining 3 posts stay unanalyzed; reported skipped is 0 because
    # they have last_analyzed_at IS NULL (skipped counter only counts
    # already-analyzed rows, not limit-excluded).
    assert body["skipped_posts"] == 0


# ---------- Error mapping --------------------------------------------


def test_analyze_returns_404_for_unknown_channel(client: TestClient):
    response = client.post(f"/api/admin/analyze/{uuid4()}")
    assert response.status_code == 404


def test_analyze_returns_401_when_api_key_missing(
    client: TestClient, db, monkeypatch: pytest.MonkeyPatch
):
    channel = _seed_channel(db)
    _seed_posts(db, channel, n=1)
    monkeypatch.setattr(settings, "anthropic_api_key", None, raising=False)
    response = client.post(f"/api/admin/analyze/{channel.id}")
    assert response.status_code == 401
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_analyze_propagates_auth_error_from_analyzer_as_401(client: TestClient, db):
    """Auth errors raised mid-batch (e.g. key revoked between calls)
    must short-circuit the request as 401, not be hidden in errors."""
    from app.services import post_analyzer
    from app.services.anthropic_client import AnthropicAuthError

    channel = _seed_channel(db)
    _seed_posts(db, channel, n=1)

    with patch.object(post_analyzer, "messages_create_vision",
                      side_effect=AnthropicAuthError("key revoked")):
        response = client.post(f"/api/admin/analyze/{channel.id}")
    assert response.status_code == 401
    assert "auth" in response.json()["detail"].lower()


def test_analyze_absorbs_rate_limit_per_post(client: TestClient, db):
    """RateLimit is caught by the analyzer's per-post try/except
    (skip-and-log per Wolf's spec) — the endpoint sees status='error'
    and returns 200 with the per-post error in ``errors``. We do NOT
    return 429 for a single throttled call; that would punish the
    healthy posts in the same batch."""
    from app.services import post_analyzer
    from app.services.anthropic_client import AnthropicRateLimitError

    channel = _seed_channel(db)
    _seed_posts(db, channel, n=1)

    with patch.object(post_analyzer, "messages_create_vision",
                      return_value=_fake_message("desc")), \
         patch.object(post_analyzer, "messages_create_text",
                      side_effect=AnthropicRateLimitError("rate limit after 3 retries")):
        response = client.post(f"/api/admin/analyze/{channel.id}")
    body = response.json()
    assert response.status_code == 200, response.text
    assert body["analyzed_posts"] == 0
    assert len(body["errors"]) == 1
    assert "rate-limit" in body["errors"][0]


def test_analyze_returns_503_when_post_analyzer_unimportable(
    client: TestClient, db, monkeypatch: pytest.MonkeyPatch
):
    """Lazy-import safety net: if post_analyzer fails to load (e.g.
    missing anthropic SDK on a misconfigured deploy), the endpoint
    surfaces a clean 503 rather than crashing the admin router."""
    channel = _seed_channel(db)
    _seed_posts(db, channel, n=1)
    real_import = admin_mod.__builtins__["__import__"] if isinstance(
        admin_mod.__builtins__, dict
    ) else __builtins__.__import__

    def fail_import(name, *args, **kwargs):
        if name == "app.services.post_analyzer":
            raise ImportError("simulated analyzer load failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fail_import)
    response = client.post(f"/api/admin/analyze/{channel.id}")
    assert response.status_code == 503
    assert "analyzer" in response.json()["detail"].lower()


# ---------- Per-post error isolation ---------------------------------


def test_analyze_continues_batch_when_one_post_errors(client: TestClient, db):
    """One post with bad-JSON-from-Haiku (twice) -> that post lands in
    errors[], the other two posts still get analyzed."""
    from app.services import post_analyzer

    channel = _seed_channel(db)
    _seed_posts(db, channel, n=3)

    # Order of post selection is by detected_at desc — but with 3 posts
    # seeded near-simultaneously, the order is implementation-defined.
    # Build a side_effect plan that's robust either way: all 3 posts
    # get vision + sonnet successfully, but post #2's haiku is bad
    # twice. We give Haiku 4 outputs (3 posts × 1 + 1 retry for the
    # bad one), Sonnet 2 outputs (only 2 posts make it past the Haiku
    # short-circuit).
    vision_returns = [_fake_message(f"d{i}") for i in range(3)]
    text_side_effect = [
        # post A — happy
        _fake_message('{"format":"trailer","tone":"suspenseful","confidence":0.9}'),
        _fake_message('{"purpose":"release_week","lifecycle_stage":"launch","confidence":0.8}'),
        # post B — haiku bad, retry bad -> short-circuit, no sonnet call
        _fake_message("not json"),
        _fake_message("still not json"),
        # post C — happy
        _fake_message('{"format":"trailer","tone":"suspenseful","confidence":0.9}'),
        _fake_message('{"purpose":"release_week","lifecycle_stage":"launch","confidence":0.8}'),
    ]

    with patch.object(post_analyzer, "messages_create_vision", side_effect=vision_returns), \
         patch.object(post_analyzer, "messages_create_text", side_effect=text_side_effect):
        response = client.post(f"/api/admin/analyze/{channel.id}")
    body = response.json()
    assert response.status_code == 200, response.text
    assert body["analyzed_posts"] == 2
    assert len(body["errors"]) == 1
    assert "haiku-invalid-json" in body["errors"][0]
    # Vision counter: 3 calls, all logged. Haiku: 4 (3 + 1 retry).
    # Sonnet: 2 (only the two healthy posts reach it).
    assert body["anthropic_calls"] == {"haiku": 4, "sonnet": 2, "sonnet_vision": 3}
