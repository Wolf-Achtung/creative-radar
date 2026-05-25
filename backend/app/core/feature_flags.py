"""Feature-Flag-Pattern fuer schrittweise Feature-Rollouts.

Pattern: alle Flags haben sichere Defaults (off/empty). Aktivierung erfolgt
durch Setzen der entsprechenden Env-Var in Railway Production. Rollback
erfolgt durch Leeren oder Entfernen der Env-Var — kein Re-Deploy, keine
DB-Migration, keine Code-Aenderung. Edit im Railway-Dashboard, Service
liest die neue Env auf dem naechsten Worker-Restart (~10s).

Konvention: ``FEATURE_<DOMAIN>_<BEHAVIOR>``. Drei aktive Flags:

- ``FEATURE_UK_SECTION_PAIRS`` (csv): Pair-Liste, kommagetrennt — pro Pair
  feinkoernig aktivierbar (Beispiel: ``"disney,lionsgate"``).
- ``FEATURE_INDEPENDENTS_ENABLED`` (bool): einfacher An/Aus-Schalter.
- ``FEATURE_SEGMENT_ROUNDUPS_ENABLED`` (bool): An/Aus-Schalter fuer den
  Non-Pair-Segment-Roundup-Pfad (Pilot-Endpoint + Cron-Block).

Schritt-4-Rename 2026-05-25: vorher ``FEATURE_SINGLE_MARKET_SCHEMA`` — Name
stammt aus PR #155 als Schema-Branch-Idee. Die finale Funktion ist breiter
(Cron-Roundup-Pipeline fuer alle vier Default-Segmente, nicht nur Single-
Market-Schema). Umbenennung als reines Rename-ohne-Verhaltens-Change. Wolf-
Aktion beim Deploy: alte Env-Var ``FEATURE_SINGLE_MARKET_SCHEMA`` in
Railway loeschen, neue ``FEATURE_SEGMENT_ROUNDUPS_ENABLED`` anlegen — sonst
greift das Gate nicht.

Verwendung (in spaeteren Feature-Sprints):

    from app.core.feature_flags import is_uk_enabled_for_pair

    if is_uk_enabled_for_pair("disney"):
        # UK-Sektion in Brief-Generation einfuegen
        ...
"""
from __future__ import annotations

import os


def is_uk_enabled_for_pair(pair_key: str) -> bool:
    """Returns True wenn die UK-Sektion fuer diesen Pair generiert werden soll.

    Env-Var: ``FEATURE_UK_SECTION_PAIRS``
    Format: comma-separated pair_keys, z.B. ``"disney,lionsgate"``.
    Default: ``""`` → kein Pair aktiviert.

    Whitespace um die Kommas wird tolerant behandelt (``" disney , lionsgate "``
    matched ``disney`` und ``lionsgate``). Leere Tokens (z.B. nach trailing
    comma) werden ignoriert.
    """
    raw = os.getenv("FEATURE_UK_SECTION_PAIRS", "")
    enabled_pairs = [p.strip() for p in raw.split(",") if p.strip()]
    return pair_key in enabled_pairs


def is_independents_enabled() -> bool:
    """Returns True wenn die Independents-Pipeline (Beta-Pairs sichtbar,
    Single-Market-Briefs generierbar) aktiv ist.

    Env-Var: ``FEATURE_INDEPENDENTS_ENABLED``
    Format: ``"true"`` oder ``"false"`` (case-insensitive). Andere Werte
    werden defensiv als ``False`` interpretiert.
    Default: ``"false"``.
    """
    return os.getenv("FEATURE_INDEPENDENTS_ENABLED", "false").lower() == "true"


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
