"""Feature-Flag-Pattern fuer schrittweise Feature-Rollouts.

Pattern: alle Flags haben sichere Defaults (off/empty). Aktivierung erfolgt
durch Setzen der entsprechenden Env-Var in Railway Production. Rollback
erfolgt durch Leeren oder Entfernen der Env-Var — kein Re-Deploy, keine
DB-Migration, keine Code-Aenderung. Edit im Railway-Dashboard, Service
liest die neue Env auf dem naechsten Worker-Restart (~10s).

Konvention: ``FEATURE_<DOMAIN>_<BEHAVIOR>``. Drei Patterns sind dokumentiert:

- ``FEATURE_UK_SECTION_PAIRS`` (csv): Pair-Liste, kommagetrennt — pro Pair
  feinkoernig aktivierbar (Beispiel: ``"disney,lionsgate"``).
- ``FEATURE_INDEPENDENTS_ENABLED`` (bool): einfacher An/Aus-Schalter.
- ``FEATURE_SINGLE_MARKET_SCHEMA`` (bool): einfacher An/Aus-Schalter
  fuer einen Schema-Branch im Brief-Generator.

Verwendung (in spaeteren Feature-Sprints):

    from app.core.feature_flags import is_uk_enabled_for_pair

    if is_uk_enabled_for_pair("disney"):
        # UK-Sektion in Brief-Generation einfuegen
        ...

Dieser Sprint (PR #155) liefert nur das Pattern + Tests. Keine Production-
Code-Stelle nutzt die Helper. Aktivierung erfolgt erst, wenn die UK- und
Independents-Sprints die jeweilige Feature-Logik einbauen.
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


def is_single_market_schema_enabled() -> bool:
    """Returns True wenn die ``single_market_insight``-Schema-Branch im
    Brief-Generator fuer Pairs mit ``pair_type='single_market'`` genutzt
    werden soll.

    Env-Var: ``FEATURE_SINGLE_MARKET_SCHEMA``
    Format: ``"true"`` oder ``"false"`` (case-insensitive). Andere Werte
    werden defensiv als ``False`` interpretiert.
    Default: ``"false"``.
    """
    return os.getenv("FEATURE_SINGLE_MARKET_SCHEMA", "false").lower() == "true"
