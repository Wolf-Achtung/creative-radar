"""Tests fuer die Staging-/Dev-Umgebungs-Bausteine (Briefing 2026-08-06):

- MOCK_EXTERNAL_APIS: Connector-Gates melden konfiguriert, die Scrape-
  Funktionen liefern deterministische Fixtures in den Formen, die die
  normalize_*-Funktionen erwarten.
- _guard_staging_database: Whitelist-Boot-Check (staging + fremder
  DB-Host oder fehlende Variable -> RuntimeError).
- is_scheduler_enabled: explizites ENV gewinnt; ohne ENV nur in
  production an.
- scripts/seed_dev: idempotent, Briefe schema-valide mit Zitaten auf
  real geseedete Post-URLs.
"""
import asyncio

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.config import settings
from app.database import _guard_staging_database
from app.services import cron_scheduler
from app.services.apify_connector import (
    is_apify_configured,
    is_tiktok_configured,
    normalize_public_item,
    normalize_tiktok_item,
    run_public_channel_monitor,
    run_tiktok_profile_monitor,
)
from app.services.youtube_connector import (
    fetch_channel_videos,
    is_youtube_configured,
    normalize_youtube_video,
)


@pytest.fixture()
def mock_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "mock_external_apis", True, raising=False)
    # Sicherstellen, dass KEIN Token noetig ist — das ist der Punkt.
    monkeypatch.setattr(settings, "apify_api_token", None, raising=False)
    monkeypatch.setattr(settings, "youtube_api_key", None, raising=False)


# ---------- Mock-Modus ------------------------------------------------


def test_mock_mode_opens_configured_gates(mock_mode) -> None:
    assert is_apify_configured() is True
    assert is_tiktok_configured() is True
    assert is_youtube_configured() is True


def test_gates_stay_closed_without_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mock_external_apis", False, raising=False)
    monkeypatch.setattr(settings, "apify_api_token", None, raising=False)
    monkeypatch.setattr(settings, "youtube_api_key", None, raising=False)
    assert is_apify_configured() is False
    assert is_tiktok_configured() is False
    assert is_youtube_configured() is False


def test_mock_instagram_flows_through_real_normalizer(mock_mode) -> None:
    items = asyncio.run(run_public_channel_monitor(
        ["https://www.instagram.com/disneydeutschland/"], results_limit=6,
    ))
    assert len(items) == 6
    normalized = [normalize_public_item(i) for i in items]
    assert all(n["post_url"].startswith("https://www.instagram.com/p/") for n in normalized)
    assert all(n["published_at"] is not None for n in normalized)
    # Muster: jeder 3. Post ist statisch (keine Views).
    statics = [n for n in normalized if n["visible_views"] is None]
    assert len(statics) == 2


def test_mock_tiktok_flows_through_real_normalizer(mock_mode) -> None:
    items = asyncio.run(run_tiktok_profile_monitor(["@netflixde"], results_limit=5))
    normalized = [normalize_tiktok_item(i) for i in items]
    assert len(normalized) == 5
    assert all(n["visible_views"] for n in normalized)
    assert all(n["duration_seconds"] for n in normalized)


def test_mock_youtube_flows_through_real_normalizer(mock_mode) -> None:
    _meta, videos = fetch_channel_videos("WaltDisneyStudios", results_limit=4)
    normalized = [normalize_youtube_video(v) for v in videos]
    assert len(normalized) == 4
    assert all(n["post_url"].startswith("https://www.youtube.com/watch?v=") for n in normalized)
    assert all(n["visible_views"] for n in normalized)


def test_mock_items_are_deterministic(mock_mode) -> None:
    first = asyncio.run(run_public_channel_monitor(["https://www.instagram.com/pixar/"], 5))
    second = asyncio.run(run_public_channel_monitor(["https://www.instagram.com/pixar/"], 5))
    assert [i["likesCount"] for i in first] == [i["likesCount"] for i in second]
    assert [i["url"] for i in first] == [i["url"] for i in second]


# ---------- Boot-Check ------------------------------------------------


def test_staging_guard_noop_outside_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production", raising=False)
    _guard_staging_database("postgresql://u:p@prod-host:5432/db")  # kein Raise


def test_staging_guard_requires_expected_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "staging", raising=False)
    monkeypatch.setattr(settings, "staging_expected_db_host", None, raising=False)
    with pytest.raises(RuntimeError, match="STAGING_EXPECTED_DB_HOST"):
        _guard_staging_database("postgresql://u:p@somewhere:5432/db")


def test_staging_guard_rejects_foreign_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "staging", raising=False)
    monkeypatch.setattr(settings, "staging_expected_db_host", "staging-db.railway.internal", raising=False)
    with pytest.raises(RuntimeError, match="Boot verweigert"):
        _guard_staging_database("postgresql://u:p@prod-db.railway.internal:5432/db")


def test_staging_guard_accepts_expected_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "staging", raising=False)
    monkeypatch.setattr(settings, "staging_expected_db_host", "staging-db.railway.internal", raising=False)
    _guard_staging_database("postgresql://u:p@staging-db.railway.internal:5432/db")  # kein Raise


# ---------- Scheduler-Default -----------------------------------------


def test_scheduler_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "staging", raising=False)
    monkeypatch.setenv("ENABLE_INTERNAL_CRON_SCHEDULER", "true")
    assert cron_scheduler.is_scheduler_enabled() is True
    monkeypatch.setenv("ENABLE_INTERNAL_CRON_SCHEDULER", "false")
    assert cron_scheduler.is_scheduler_enabled() is False


def test_scheduler_default_only_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_INTERNAL_CRON_SCHEDULER", raising=False)
    monkeypatch.setattr(settings, "app_env", "production", raising=False)
    assert cron_scheduler.is_scheduler_enabled() is True
    for env in ("staging", "development"):
        monkeypatch.setattr(settings, "app_env", env, raising=False)
        assert cron_scheduler.is_scheduler_enabled() is False


# ---------- seed_dev ---------------------------------------------------


@pytest.fixture()
def seed_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_seed_dev_is_idempotent(seed_session: Session) -> None:
    from app.models.entities import Channel, InsightReport, Post, Title
    from app.schemas.insights import LLMReport
    from scripts import seed_dev

    def run_once() -> None:
        seed_dev._wipe_previous_seed(seed_session)
        seed_dev._seed_titles(seed_session)
        pair_channels = seed_dev._seed_pair_channels(seed_session, ["netflix"])
        segment_channels = seed_dev._seed_segment_channels(seed_session)
        seed_dev._seed_posts(seed_session, list(pair_channels.values()) + segment_channels, 4)
        seed_dev._seed_briefs(seed_session, ["netflix"])

    run_once()
    posts_first = len(seed_session.exec(select(Post)).all())
    run_once()  # zweiter Lauf: Reset statt Duplikate
    assert len(seed_session.exec(select(Post)).all()) == posts_first
    assert len(seed_session.exec(select(Title)).all()) == 5
    assert all(
        c.import_source == "seed_dev" for c in seed_session.exec(select(Channel)).all()
    )

    report = seed_session.exec(select(InsightReport)).one()
    assert report.model == "seed-dev"
    # llm_output ist schema-valide und zitiert real geseedete Post-URLs.
    parsed = LLMReport.model_validate(report.llm_output)
    seeded_urls = {p.post_url for p in seed_session.exec(select(Post)).all()}
    cited = parsed.trends[0].cited_post_ids
    assert cited and set(cited) <= seeded_urls
