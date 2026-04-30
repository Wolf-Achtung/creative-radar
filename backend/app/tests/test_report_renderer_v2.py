from datetime import date, datetime, timezone

from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.models.entities import Asset, Channel, Market, Post, Title
from app.services.report_renderer_v2 import _row, generate_report_html


def _session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make_asset(session, *, evidence_url, screenshot_url=None):
    channel = Channel(name="Test", platform="instagram", url="https://example.com", market=Market.US)
    session.add(channel)
    session.commit()
    session.refresh(channel)

    post = Post(
        channel_id=channel.id,
        post_url="https://example.com/post",
        detected_at=datetime.now(timezone.utc),
    )
    session.add(post)
    session.commit()
    session.refresh(post)

    title = Title(title_original="Test Movie", active=True)
    session.add(title)
    session.commit()
    session.refresh(title)

    asset = Asset(
        post_id=post.id,
        title_id=title.id,
        visual_evidence_url=evidence_url,
        screenshot_url=screenshot_url,
        visual_analysis_status="analyzed",
        ocr_text="Some text",
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def test_renderer_row_classifies_internal_path_as_source_only_when_storage_disabled():
    assert settings.secure_storage_enabled is False
    with _session() as session:
        asset = _make_asset(
            session,
            evidence_url="/storage/evidence/abc123.jpg",
            screenshot_url="https://cdn.example/thumb.jpg",
        )
        row = _row(session, asset)
        assert row["evidence_quality"] == "source_only"
        assert row["secure"] is False
        assert row["display_image_url"] == "https://cdn.example/thumb.jpg"
        assert row["evidence_label"] == "Bildquelle vorhanden"


def test_renderer_html_does_not_embed_dead_internal_storage_path():
    """The rendered HTML must not contain raw /storage/evidence/ paths as <img src>
    while SECURE_STORAGE_ENABLED is False — those would 404 on every report view."""
    with _session() as session:
        asset = _make_asset(
            session,
            evidence_url="/storage/evidence/abc123.jpg",
            screenshot_url="https://cdn.example/thumb.jpg",
        )
        html, _ = generate_report_html(
            session=session,
            report_type="weekly_overview",
            asset_ids=[asset.id],
            date_from=date(2026, 4, 1),
            date_to=date(2026, 4, 30),
        )
        assert '<img src="/storage/evidence/' not in html
        assert 'https://cdn.example/thumb.jpg' in html


def test_renderer_html_does_not_falsely_claim_secure_evidence_when_storage_disabled():
    """Old renderer hard-coded 'Gesichertes Evidence-Bild' for any /storage/evidence/ URL.
    With SECURE_STORAGE_ENABLED=False, that claim must not appear."""
    with _session() as session:
        asset = _make_asset(
            session,
            evidence_url="/storage/evidence/abc123.jpg",
            screenshot_url="https://cdn.example/thumb.jpg",
        )
        html, _ = generate_report_html(
            session=session,
            report_type="weekly_overview",
            asset_ids=[asset.id],
            date_from=date(2026, 4, 1),
            date_to=date(2026, 4, 30),
        )
        assert "Gesichertes Evidence-Bild" not in html
        assert "Vorschau-Bildquelle" in html or "externer Bildquelle" in html


def test_renderer_html_includes_quelle_line_per_finding():
    with _session() as session:
        asset = _make_asset(session, evidence_url="https://cdn.example/external.jpg")
        html, _ = generate_report_html(
            session=session,
            report_type="weekly_overview",
            asset_ids=[asset.id],
            date_from=date(2026, 4, 1),
            date_to=date(2026, 4, 30),
        )
        assert "<strong>Quelle:</strong>" in html
        assert "Externe Bildquelle" in html
