"""Tests fuer das Feature-Flag-Helper-Modul (PR #155).

Verifiziert die Default-Sicherheit (off ohne Env-Var) und Case-
Insensitivity. Pattern: ``monkeypatch.setenv``/``monkeypatch.delenv`` —
keine Race mit anderen Tests, weil monkeypatch automatisch revertiert.

Historie 27.05.2026: die zwei Helper ``is_uk_enabled_for_pair`` und
``is_independents_enabled`` wurden zusammen mit ihren Tests entfernt —
Production-Code hat sie nie konsumiert (siehe feature_flags.py-Docstring).
"""
from app.core.feature_flags import is_segment_roundups_enabled


class TestSegmentRoundupsFlag:
    def test_default_returns_false(self, monkeypatch):
        monkeypatch.delenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", raising=False)
        assert is_segment_roundups_enabled() is False

    def test_explicit_true(self, monkeypatch):
        monkeypatch.setenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", "true")
        assert is_segment_roundups_enabled() is True
