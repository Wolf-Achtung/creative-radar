"""Commit-Stand in /api/health (22.08.2026).

Beim Deploy-Vorfall am 21.08. (Pre-Deploy schlug fehl, der alte
Container lief weiter) war von aussen nicht erkennbar, WELCHER
Code-Stand antwortet. Railway setzt ``RAILWAY_GIT_COMMIT_SHA`` in
jeden Container — /api/health zeigt die ersten 7 Zeichen.
"""
from __future__ import annotations

from app.api.health import _commit_sha, health


def test_health_zeigt_kurzen_commit_wenn_railway_ihn_setzt(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "9b5646bd5dc814deeaa06085acb9fb6615f4c65a")

    payload = health()

    assert payload["commit"] == "9b5646b", (
        "Ohne den Commit im Health-Payload ist nach einem Deploy nicht "
        "pruefbar, ob der neue Stand wirklich antwortet (Vorfall 21.08.)."
    )


def test_health_ohne_railway_variable_liefert_null(monkeypatch):
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)

    assert _commit_sha() is None
    assert health()["commit"] is None
