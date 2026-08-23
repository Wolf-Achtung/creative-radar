"""Arbeitsregel Feature-Flags (23.08.2026) — Wolfs Freigabe-Modell.

Jedes neue Feature kommt hinter ein Flag (Schema
``FEATURE_<NAME>_ENABLED``, Default aus): in Staging an, in Production
erst nach Wolfs Abnahme. Diese Tests nageln die Mechanik fest:

- Alle Flags defaulten auf AUS — ein frisches Deployment zeigt nichts
  Unfreigegebenes.
- ``/api/health -> features`` kennt jedes UI-relevante Flag — das
  Frontend blendet ausschliesslich darueber ein; fehlt ein Schluessel,
  ist das Feature nirgends sichtbar, auch nicht in Staging.
"""
from __future__ import annotations

import pytest

from app.api.health import health
from app.core import feature_flags as ff

_NEUE_FLAGS = {
    "FEATURE_WIR_PROJEKTE_ENABLED": ff.is_wir_projekte_enabled,
    "FEATURE_PROJEKT_START_BRIEF_ENABLED": ff.is_projekt_start_brief_enabled,
    "FEATURE_KAMPAGNEN_TIMING_ENABLED": ff.is_kampagnen_timing_enabled,
    "FEATURE_SOUND_TRENDS_ENABLED": ff.is_sound_trends_enabled,
}


@pytest.mark.parametrize("env_var,helper", sorted(_NEUE_FLAGS.items()))
def test_flags_defaulten_auf_aus_und_schalten_per_env(env_var, helper, monkeypatch):
    monkeypatch.delenv(env_var, raising=False)
    assert helper() is False, f"{env_var} muss ohne Setzung AUS sein — Default ist Production."

    monkeypatch.setenv(env_var, "true")
    assert helper() is True

    monkeypatch.setenv(env_var, "TRUE")
    assert helper() is True, "case-insensitive wie die Bestands-Flags"

    monkeypatch.setenv(env_var, "1")
    assert helper() is False, "Nur 'true' schaltet — alles andere defensiv AUS."


def test_health_kennt_jedes_ui_relevante_flag(monkeypatch):
    """Das Frontend blendet NUR ueber /api/health -> features ein.
    Fehlt hier ein Schluessel, ist das Feature auch in Staging
    unsichtbar — das Flag waere dann wirkungslos statt gestuft."""
    for env_var in _NEUE_FLAGS:
        monkeypatch.delenv(env_var, raising=False)

    features = health()["features"]

    erwartet = {
        "trailer_intelligence",
        "wir_projekte",
        "projekt_start_brief",
        "kampagnen_timing",
        "sound_trends",
    }
    assert erwartet <= set(features), (
        f"features fehlt: {sorted(erwartet - set(features))} — das "
        "Frontend kann diese Flags dann nirgends einblenden."
    )
    for name in erwartet - {"trailer_intelligence"}:
        assert features[name] is False, "Default ist AUS (Production-Sicht)."
