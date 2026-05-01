from types import SimpleNamespace

from app.config import settings
from app.services.report_selector import (
    EVIDENCE_LABELS,
    EVIDENCE_WARNINGS,
    _displayable_image_candidates,
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


def test_displayable_image_candidates_returns_all_http_urls_in_priority_order():
    asset = _asset(
        visual_evidence_url="/storage/evidence/dead.jpg",
        visual_source_url="https://cdn.a/source.jpg",
        screenshot_url="https://cdn.b/shot.jpg",
        thumbnail_url="https://cdn.c/thumb.jpg",
    )
    candidates = _displayable_image_candidates(asset)
    assert candidates == [
        "https://cdn.a/source.jpg",
        "https://cdn.b/shot.jpg",
        "https://cdn.c/thumb.jpg",
    ]


def test_displayable_image_candidates_deduplicates_repeated_urls():
    asset = _asset(
        screenshot_url="https://cdn/x.jpg",
        thumbnail_url="https://cdn/x.jpg",
    )
    assert _displayable_image_candidates(asset) == ["https://cdn/x.jpg"]


def test_displayable_image_candidates_returns_empty_when_only_dead_internal_path():
    asset = _asset(visual_evidence_url="/storage/evidence/dead.jpg")
    assert _displayable_image_candidates(asset) == []


def test_displayable_image_url_matches_first_candidate():
    asset = _asset(
        visual_source_url="https://cdn.a/source.jpg",
        thumbnail_url="https://cdn.c/thumb.jpg",
    )
    candidates = _displayable_image_candidates(asset)
    assert candidates[0] == _displayable_image_url(asset)


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


# --- W3 / F0.4: Object-Key recognition (parallel to legacy path) ---


def test_evidence_quality_secure_for_object_key_when_flag_enabled(monkeypatch):
    monkeypatch.setattr(settings, "secure_storage_enabled", True)
    asset = _asset(visual_evidence_url="evidence/asset_123_abc.jpg")
    assert _evidence_quality(asset) == "secure"
    assert _has_secure_evidence(asset) is True


def test_evidence_quality_source_only_for_object_key_when_flag_disabled():
    assert settings.secure_storage_enabled is False
    asset = _asset(visual_evidence_url="evidence/asset_123_abc.jpg")
    assert _evidence_quality(asset) == "source_only"


def test_evidence_quality_external_unchanged_for_http_url(monkeypatch):
    monkeypatch.setattr(settings, "secure_storage_enabled", True)
    asset = _asset(visual_evidence_url="https://cdn.instagram.com/foo.jpg")
    assert _evidence_quality(asset) == "external"
    assert _has_secure_evidence(asset) is False


def test_displayable_image_candidates_resolves_object_key_when_flag_enabled(monkeypatch):
    monkeypatch.setattr(settings, "secure_storage_enabled", True)
    monkeypatch.setattr(settings, "storage_backend", "local", raising=False)
    asset = _asset(visual_evidence_url="evidence/asset_123_abc.jpg")
    candidates = _displayable_image_candidates(asset)
    assert "/storage/evidence/asset_123_abc.jpg" in candidates


def test_displayable_image_candidates_skips_object_key_when_flag_disabled():
    assert settings.secure_storage_enabled is False
    asset = _asset(visual_evidence_url="evidence/asset_123_abc.jpg",
                   thumbnail_url="https://cdn/thumb.jpg")
    candidates = _displayable_image_candidates(asset)
    # bare object key MUST NOT appear; only the http thumbnail should
    assert "evidence/asset_123_abc.jpg" not in candidates
    assert candidates == ["https://cdn/thumb.jpg"]


def test_legacy_path_still_recognised_as_secure_when_flag_enabled(monkeypatch):
    """Backwards-compat: pre-F0.1 '/storage/evidence/...' paths must keep
    working until backfill + 14d stability are confirmed."""
    monkeypatch.setattr(settings, "secure_storage_enabled", True)
    asset = _asset(visual_evidence_url="/storage/evidence/legacy.jpg")
    assert _evidence_quality(asset) == "secure"
    candidates = _displayable_image_candidates(asset)
    assert "/storage/evidence/legacy.jpg" in candidates


def test_is_analysis_failed_includes_new_w3_statuses():
    """Selector must filter out the W3 honest-status failures."""
    from app.services.report_selector import _is_analysis_failed
    assert _is_analysis_failed(_asset(visual_analysis_status="vision_empty")) is True
    assert _is_analysis_failed(_asset(visual_analysis_status="vision_timeout")) is True
    assert _is_analysis_failed(_asset(visual_analysis_status="vision_error")) is True
    assert _is_analysis_failed(_asset(visual_analysis_status="image_unreachable")) is True
    assert _is_analysis_failed(_asset(visual_analysis_status="image_invalid")) is True
