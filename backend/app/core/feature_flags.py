"""Feature-Flag-Pattern fuer schrittweise Feature-Rollouts.

Pattern: alle Flags haben sichere Defaults (off/empty). Aktivierung erfolgt
durch Setzen der entsprechenden Env-Var in Railway Production. Rollback
erfolgt durch Leeren oder Entfernen der Env-Var — kein Re-Deploy, keine
DB-Migration, keine Code-Aenderung. Edit im Railway-Dashboard, Service
liest die neue Env auf dem naechsten Worker-Restart (~10s).

Konvention: ``FEATURE_<DOMAIN>_<BEHAVIOR>``. Aktive Flags:

- ``FEATURE_TRAILER_INTELLIGENCE_ENABLED`` (bool): An/Aus-Schalter fuer
  die Trailer-Intelligence-Auswertung (Stufe 1). In Staging an, in
  Production aus, bis die Muster-Ansicht abgenommen ist.
- ``FEATURE_SEGMENT_ROUNDUPS_ENABLED`` (bool): An/Aus-Schalter fuer den
  Non-Pair-Segment-Roundup-Pfad (Pilot-Endpoint + Cron-Block).
- ``FEATURE_CUTTER_WEEKLY_ENABLED`` (bool): An/Aus-Schalter fuer den
  Cutter-Wochenbriefing-Cron-Block (Trockenlauf).
- ``FEATURE_DESIGNER_WEEKLY_ENABLED`` (bool): An/Aus-Schalter fuer den
  Designer-Wochenbriefing-Cron-Block (Trockenlauf, Sprint 2026-07-06).
  Default off, unabhaengig vom Cutter-Flag — Wolf review't den Prompt-
  Output separat, bevor das hier in Production Kosten verursacht.
- ``FEATURE_WIR_PROJEKTE_ENABLED`` (bool): Titel-Markierung "Unser
  Projekt" + projektweises Wir-Segment (PR #412).
- ``FEATURE_PROJEKT_START_BRIEF_ENABLED`` (bool): Start-Brief-Button
  je Wir-Projekt (PR #414, setzt Wir-Projekte voraus).
- ``FEATURE_KAMPAGNEN_TIMING_ENABLED`` (bool): Kampagnen-Timing-
  Auswertung im Monitoring (PR #415).
- ``FEATURE_SOUND_TRENDS_ENABLED`` (bool): TikTok-Sound-Trends im
  Monitoring (PR #416).
- ``FEATURE_KATALOG_NACHLADEN_ENABLED`` (bool): Fehlende Titel
  bedarfsgetrieben aus TMDb nachladen (``POST /api/titles/
  katalog-nachladen``). Legt Titel an und ordnet Assets zu — der
  wirksamste Schalter im System, deshalb Staging zuerst.
- ``FEATURE_REFERENZ_SUCHE_ENABLED`` (bool): Facetten-Suche ueber die
  analysierten Posts (``GET /api/insights/referenzen``) — die
  Moodboard-Werkbank fuer Cutter/Designer. Rein lesend, kein LLM-Call.
- ``FEATURE_RELEASE_COUNTDOWN_ENABLED`` (bool): Release-Countdown je
  Wir-Projekt (``GET /api/admin/release-countdown``) — Zeitleiste aus
  Release-Datum, Markt-Kampagnenstart und Phasen-Mustern.

Arbeitsregel seit 23.08.2026 (Wolfs Freigabe-Modell, siehe CLAUDE.md
"Arbeitsregel Feature-Flags"): JEDES neue Feature bekommt beim Bau ein
Flag nach diesem Schema, Default aus — in Staging an, in Production
erst nach Wolfs Abnahme. Bugfixes, Wartung und unsichtbare
Infrastruktur (z. B. Empfehlungs-Snapshots) sind ausgenommen.

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


def is_trailer_intelligence_enabled() -> bool:
    """Returns True wenn die Trailer-Intelligence-Auswertung aktiv ist
    (Stufe 1, Entscheidung 20.08.2026: Entwicklung auf main hinter
    diesem Flag, an nur in Staging — Produktion bleibt unberuehrt, bis
    Wolf das Flag dort setzt).

    Env-Var: ``FEATURE_TRAILER_INTELLIGENCE_ENABLED``
    Format: ``"true"`` oder ``"false"`` (case-insensitive). Andere Werte
    werden defensiv als ``False`` interpretiert.
    Default: ``"false"``.

    Der Flag gate't die neuen ``/api/insights/patterns``-Endpunkte und
    das zugehoerige Dashboard-Panel (das Frontend liest den Zustand aus
    ``GET /api/health`` → ``features``). Kein Cron-Block und keine
    LLM-Kosten, solange nur die SQL-Aggregation existiert.
    """
    return os.getenv("FEATURE_TRAILER_INTELLIGENCE_ENABLED", "false").lower() == "true"


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


def is_cutter_weekly_enabled() -> bool:
    """Returns True wenn der Cutter-Wochenbriefing-Block im Montags-Cron
    aktiv ist (Master-Plan-Sprint 2026-06-12, Trockenlauf-Phase).

    Env-Var: ``FEATURE_CUTTER_WEEKLY_ENABLED``
    Format: ``"true"`` oder ``"false"`` (case-insensitive). Andere Werte
    werden defensiv als ``False`` interpretiert.
    Default: ``"false"``.

    Der Flag schaltet NUR den Cron-Block — es gibt bewusst keinen
    public Endpoint (Trockenlauf: persistieren + Admin-Lesezugriff, kein
    Frontend-Pfad bis zur Kalibrierung der Evidenzschwelle).
    """
    return os.getenv("FEATURE_CUTTER_WEEKLY_ENABLED", "false").lower() == "true"


def is_designer_weekly_enabled() -> bool:
    """Returns True wenn der Designer-Wochenbriefing-Block im Montags-Cron
    aktiv ist (Sprint 2026-07-06, Trockenlauf-Phase, mirror von
    ``is_cutter_weekly_enabled``).

    Env-Var: ``FEATURE_DESIGNER_WEEKLY_ENABLED``
    Format: ``"true"`` oder ``"false"`` (case-insensitive). Andere Werte
    werden defensiv als ``False`` interpretiert.
    Default: ``"false"`` — unabhaengig vom Cutter-Flag, damit dieses
    Feature nicht versehentlich mit anspringt, bevor der Prompt-Output
    reviewt ist.

    Der Flag schaltet NUR den Cron-Block — es gibt bewusst keinen
    public Endpoint (Trockenlauf: persistieren + Admin-Lesezugriff, kein
    Frontend-Pfad bis zur Kalibrierung der Evidenzschwelle).
    """
    return os.getenv("FEATURE_DESIGNER_WEEKLY_ENABLED", "false").lower() == "true"


def is_wir_projekte_enabled() -> bool:
    """Returns True wenn die Wir-Projekt-Markierung aktiv ist: der
    Quellen-Block "Wir-Projekte markieren", ``PATCH /api/titles/{id}``
    (einziges Feld: ``is_own_project``) und der projektweise Zaehl-Weg
    im Wir-Segment.

    Env-Var: ``FEATURE_WIR_PROJEKTE_ENABLED``; ``"true"``/``"false"``
    (case-insensitive), Default ``"false"``. Nachtraeglich geflaggt am
    23.08.2026 — das Feature lief kurz ungeflaggt in Production (Wolfs
    Freigabe-Modell war verletzt), Datenbestand bleibt erhalten.
    """
    return os.getenv("FEATURE_WIR_PROJEKTE_ENABLED", "false").lower() == "true"


def is_projekt_start_brief_enabled() -> bool:
    """Returns True wenn der Projekt-Start-Brief aktiv ist (Button an
    markierten Wir-Projekten + ``GET /api/admin/projekt-start-brief``).
    Setzt inhaltlich Wir-Projekte voraus — ohne markierte Titel gibt es
    keinen Ankerpunkt fuer den Button.

    Env-Var: ``FEATURE_PROJEKT_START_BRIEF_ENABLED``;
    ``"true"``/``"false"`` (case-insensitive), Default ``"false"``.
    """
    return os.getenv("FEATURE_PROJEKT_START_BRIEF_ENABLED", "false").lower() == "true"


def is_kampagnen_timing_enabled() -> bool:
    """Returns True wenn die Kampagnen-Timing-Auswertung aktiv ist
    (Monitoring-Sektion + ``GET /api/admin/kampagnen-timing``).

    Env-Var: ``FEATURE_KAMPAGNEN_TIMING_ENABLED``;
    ``"true"``/``"false"`` (case-insensitive), Default ``"false"``.
    """
    return os.getenv("FEATURE_KAMPAGNEN_TIMING_ENABLED", "false").lower() == "true"


def is_sound_trends_enabled() -> bool:
    """Returns True wenn die Sound-Trend-Auswertung aktiv ist
    (Monitoring-Sektion + ``GET /api/admin/sound-trends``).

    Env-Var: ``FEATURE_SOUND_TRENDS_ENABLED``; ``"true"``/``"false"``
    (case-insensitive), Default ``"false"``.
    """
    return os.getenv("FEATURE_SOUND_TRENDS_ENABLED", "false").lower() == "true"


def is_katalog_nachladen_enabled() -> bool:
    """Returns True wenn fehlende Titel bedarfsgetrieben aus TMDb
    nachgeladen werden duerfen (``POST /api/titles/katalog-nachladen``).

    Der Schalter gehoert zu den wirksamsten im System: er legt Titel an
    und ordnet Assets zu. Deshalb Default aus und Staging zuerst, nach
    der Arbeitsregel vom 23.08.2026.

    Env-Var: ``FEATURE_KATALOG_NACHLADEN_ENABLED``; ``"true"``/``"false"``
    (case-insensitive), Default ``"false"``.
    """
    return os.getenv("FEATURE_KATALOG_NACHLADEN_ENABLED", "false").lower() == "true"


def is_referenz_suche_enabled() -> bool:
    """Returns True wenn die Referenz-Suche aktiv ist
    (``GET /api/insights/referenzen`` + Panel im Dashboard).

    Rein lesende Facetten-Suche ueber die vorhandenen Auswertungen
    (Lift, Vision-Cover, Caption-Mechanik, Genre) — kein LLM-Call,
    keine Schreiboperation. Trotzdem Default aus nach der Arbeitsregel
    vom 23.08.2026: Wolf sieht jedes neue Panel zuerst in Staging.

    Env-Var: ``FEATURE_REFERENZ_SUCHE_ENABLED``; ``"true"``/``"false"``
    (case-insensitive), Default ``"false"``.
    """
    return os.getenv("FEATURE_REFERENZ_SUCHE_ENABLED", "false").lower() == "true"


def is_release_countdown_enabled() -> bool:
    """Returns True wenn der Release-Countdown je Wir-Projekt aktiv ist
    (``GET /api/admin/release-countdown`` + Monitoring-Sektion).

    Verknuepft drei vorhandene Auswertungen zu einem Plan pro Projekt:
    Release-Datum (Wir-Projekt), Markt-Kampagnenstart (Kampagnen-
    Timing) und Phasen-Muster (Trailer-Patterns). Rein lesend, kein
    LLM-Call; Default aus nach der Arbeitsregel vom 23.08.2026.

    Env-Var: ``FEATURE_RELEASE_COUNTDOWN_ENABLED``; ``"true"``/``"false"``
    (case-insensitive), Default ``"false"``.
    """
    return os.getenv("FEATURE_RELEASE_COUNTDOWN_ENABLED", "false").lower() == "true"
