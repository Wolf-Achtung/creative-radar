from types import SimpleNamespace

from app.config import settings
from app.services.report_selector import (
    EVIDENCE_LABELS,
    EVIDENCE_WARNINGS,
    _displayable_image_url,
    _evidence_quality,
    _has_secure_evidence,
    _is_analysis_done,
    _suitability_label,
)


def _asset(**overrides):
    base = dict(
        visual_evidence_url=None,
        screenshot_url=None,
        thumbnail_url=None,
        visual_source_url=None,
        visual_analysis_status="pending",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_evidence_quality_external_for_http_url():
    asset = _asset(visual_evidence_url="https://cdn.instagram.com/foo.jpg")
    assert _evidence_quality(asset) == "external"
    assert _displayable_image_url(asset) == "https://cdn.instagram.com/foo.jpg"


def test_evidence_quality_source_only_for_internal_path_when_storage_disabled():
    assert settings.secure_storage_enabled is False
    asset = _asset(visual_evidence_url="/storage/evidence/abc123.jpg")
    assert _evidence_quality(asset) == "source_only"
    assert _has_secure_evidence(asset) is False


def test_evidence_quality_secure_only_when_flag_enabled(monkeypatch):
    monkeypatch.setattr(settings, "secure_storage_enabled", True)
    asset = _asset(visual_evidence_url="/storage/evidence/abc123.jpg")
    assert _evidence_quality(asset) == "secure"
    assert _has_secure_evidence(asset) is True


def test_evidence_quality_missing_when_no_sources():
    assert _evidence_quality(_asset()) == "missing"


def test_evidence_quality_source_only_for_thumbnail_only():
    asset = _asset(thumbnail_url="https://x/thumb.jpg")
    assert _evidence_quality(asset) == "source_only"


def test_displayable_image_url_prefers_external_over_dead_internal_path():
    asset = _asset(
        visual_evidence_url="/storage/evidence/dead.jpg",
        thumbnail_url="https://cdn/thumb.jpg",
    )
    assert _displayable_image_url(asset) == "https://cdn/thumb.jpg"


def test_displayable_image_url_returns_none_when_only_internal_path():
    asset = _asset(visual_evidence_url="/storage/evidence/dead.jpg")
    assert _displayable_image_url(asset) is None


def test_suitability_capped_when_no_secure_evidence():
    # Even with high score and a title, external evidence cannot be 'hoch'.
    assert _suitability_label(0.9, "weekly_overview", "external", [], has_title=True) == "mittel"


def test_suitability_eingeschraenkt_when_missing_evidence():
    assert _suitability_label(0.9, "weekly_overview", "missing", [], has_title=True) == "eingeschränkt"


def test_suitability_visual_kinetics_caps_to_eingeschraenkt_without_secure():
    assert _suitability_label(0.9, "visual_kinetics", "external", [], has_title=True) == "eingeschränkt"


def test_suitability_remains_hoch_only_with_secure_and_title():
    assert _suitability_label(0.9, "weekly_overview", "secure", [], has_title=True) == "hoch"


def test_suitability_no_title_caps_to_eingeschraenkt_without_secure():
    assert _suitability_label(0.9, "weekly_overview", "external", [], has_title=False) == "eingeschränkt"


def test_is_analysis_done_accepts_legacy_and_current_states():
    assert _is_analysis_done(_asset(visual_analysis_status="done")) is True
    assert _is_analysis_done(_asset(visual_analysis_status="analyzed")) is True
    assert _is_analysis_done(_asset(visual_analysis_status="text_fallback")) is True
    assert _is_analysis_done(_asset(visual_analysis_status="pending")) is False
    assert _is_analysis_done(_asset(visual_analysis_status="error")) is False


def test_evidence_label_and_warning_are_consistent():
    # Every classifiable quality has a label; non-secure qualities also have a warning.
    for quality in ("secure", "external", "source_only", "missing"):
        assert quality in EVIDENCE_LABELS
    for quality in ("external", "source_only", "missing"):
        assert quality in EVIDENCE_WARNINGS
    assert "secure" not in EVIDENCE_WARNINGS  # secure produces a tag, not a warning
