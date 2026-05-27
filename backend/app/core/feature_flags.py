"""Feature-Flag-Pattern fuer schrittweise Feature-Rollouts.

Pattern: alle Flags haben sichere Defaults (off/empty). Aktivierung erfolgt
durch Setzen der entsprechenden Env-Var in Railway Production. Rollback
erfolgt durch Leeren oder Entfernen der Env-Var — kein Re-Deploy, keine
DB-Migration, keine Code-Aenderung. Edit im Railway-Dashboard, Service
liest die neue Env auf dem naechsten Worker-Restart (~10s).

Konvention: ``FEATURE_<DOMAIN>_<BEHAVIOR>``. Ein aktiver Flag:

- ``FEATURE_SEGMENT_ROUNDUPS_ENABLED`` (bool): An/Aus-Schalter fuer den
  Non-Pair-Segment-Roundup-Pfad (Pilot-Endpoint + Cron-Block).

Historie: PR #155 hat das Pattern eingefuehrt, mit zwei zusaetzlichen
Helpern ``is_uk_enabled_for_pair`` und ``is_independents_enabled``. Beide
wurden nie im Production-Code konsumiert — der UK-Pair-Rollout (UK-B1,
2026-05-12) hat UK direkt in die PAIRS-Registry gehoben statt das Per-Pair-
Toggle zu nutzen, und die Independents-Pipeline laeuft direkt ueber
``settings.cron_roundup_segments``. Im Cleanup-PR vom 27.05.2026 sind die
zwei toten Helper + die zugehoerigen Env-Vars (in Railway bereits entfernt)
zusammen mit den Tests rausgeflogen.
"""
from __future__ import annotations

import os


def is_segment_roundups_enabled() -> bool:
    """Returns True wenn der Non-Pair-Segment-Roundup-Pfad aktiv ist —
    Gate fuer Pilot-Endpoint ``POST /api/admin/roundups/generate`` UND
    den Cron-Block in ``_run_cron_sync_background``.

    Env-Var: ``FEATURE_SEGMENT_ROUNDUPS_ENABLED``
    Format: ``"true"`` oder ``"false"`` (case-insensitive). Andere Werte
    werden defensiv als ``False`` interpretiert.
    Default: ``"false"``.

    Master-Plan-Schritt-4-Rename: vorher ``FEATURE_SINGLE_MARKET_SCHEMA``.
    Funktion war von Anfang an Roundup-spezifisch, der Name aus PR #155
    war historisch enger. Wolf-Action beim Deploy: alte Env-Var
    ``FEATURE_SINGLE_MARKET_SCHEMA`` loeschen, neue
    ``FEATURE_SEGMENT_ROUNDUPS_ENABLED`` setzen.
    """
    return os.getenv("FEATURE_SEGMENT_ROUNDUPS_ENABLED", "false").lower() == "true"
