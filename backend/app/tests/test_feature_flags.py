"""Tests fuer das Feature-Flag-Helper-Modul (PR #155).

Verifiziert die Default-Sicherheit (alle Flags off ohne Env-Var), die
Parse-Toleranz (Whitespace, trailing comma) und die Case-Insensitivity
der Bool-Flags. Pattern: ``monkeypatch.setenv``/``monkeypatch.delenv`` —
keine Race mit anderen Tests, weil monkeypatch automatisch revertiert.
"""
import pytest

from app.core.feature_flags import (
    is_independents_enabled,
    is_segment_roundups_enabled,
    is_uk_enabled_for_pair,
)


class TestUKFeatureFlag:
    def test_default_returns_false(self, monkeypatch):
        monkeypatch.delenv("FEATURE_UK_SECTION_PAIRS", raising=False)
        assert is_uk_enabled_for_pair("disney") is False

    def test_empty_string_returns_false(self, monkeypatch):
        monkeypatch.setenv("FEATURE_UK_SECTION_PAIRS", "")
        assert is_uk_enabled_for_pair("disney") is False

    def test_single_pair_match(self, monkeypatch):
        monkeypatch.setenv("FEATURE_UK_SECTION_PAIRS", "disney")
        assert is_uk_enabled_for_pair("disney") is True
        assert is_uk_enabled_for_pair("netflix") is False

    def test_multiple_pairs_match(self, monkeypatch):
        monkeypatch.setenv(
            "FEATURE_UK_SECTION_PAIRS", "disney,lionsgate,warnerbros",
        )
        assert is_uk_enabled_for_pair("disney") is True
        assert is_uk_enabled_for_pair("lionsgate") is True
        assert is_uk_enabled_for_pair("warnerbros") is True
        assert is_uk_enabled_for_pair("netflix") is False

    def test_whitespace_tolerance(self, monkeypatch):
        monkeypatch.setenv(
            "FEATURE_UK_SECTION_PAIRS", " disney , lionsgate ",
        )
        assert is_uk_enabled_for_pair("disney") is True
        assert is_uk_enabled_for_pair("lionsgate") is True

    def test_trailing_comma_ignored(self, monkeypatch):
        monkeypatch.setenv("FEATURE_UK_SECTION_PAIRS", "disney,")
        assert is_uk_enabled_for_pair("disney") is True


class TestIndependentsFlag:
    def test_default_returns_false(self, monkeypatch):
        monkeypatch.delenv("FEATURE_INDEPENDENTS_ENABLED", raising=False)
        assert is_independents_enabled() is False

    def test_explicit_false(self, monkeypatch):
        monkeypatch.setenv("FEATURE_INDEPENDENTS_ENABLED", "false")
        assert is_independents_enabled() is False

    def test_explicit_true(self, monkeypatch):
        monkeypatch.setenv("FEATURE_INDEPENDENTS_ENABLED", "true")
        assert is_independents_enabled() is True

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("FEATURE_INDEPENDENTS_ENABLED", "TRUE")
        assert is_independents_enabled() is True
        monkeypatch.setenv("FEATURE_INDEPENDENTS_ENABLED", "True")
        assert is_independents_enabled() is True

    def test_invalid_value_returns_false(self, monkeypatch):
        # Defensive: alles ausser exakt "true" (case-insensitive) → False
        monkeypatch.setenv("FEATURE_INDEPENDENTS_ENABLED", "yes")
        assert is_independents_enabled() is False
        monkeypatch.setenv("FEATURE_INDEPENDENTS_ENABLED", "1")
        assert is_independents_enabled() is False


class TestSingleMarketSchemaFlag:
    def test_default_returns_false(self, monkeypatch):
        monkeypatch.delenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", raising=False)
        assert is_segment_roundups_enabled() is False

    def test_explicit_true(self, monkeypatch):
        monkeypatch.setenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", "true")
        assert is_segment_roundups_enabled() is True
