# Feature Flags

Konvention: alle Feature-Flags folgen dem Pattern `FEATURE_<DOMAIN>_<BEHAVIOR>`, sind via Env-Var in Railway konfigurierbar, und haben einen sicheren Default (off/empty).

Helper-Modul: `backend/app/core/feature_flags.py` (PR #155)

## Aktive Flags

### `FEATURE_UK_SECTION_PAIRS`

- **Typ:** Comma-separated pair_keys, z.B. `"disney,lionsgate"`
- **Default:** `""` (kein Pair aktiviert)
- **Helper:** `is_uk_enabled_for_pair(pair_key)`
- **Zweck:** Aktiviert UK-Sektion in Brief-Generation für gelistete Pairs
- **Einführung:** PR #155 (Pattern), Nutzung kommt mit UK-Sprint
- **Entfernung:** Nach UK-Sprint-Vollintegration (vermutlich Phase 4 Roadmap), wenn alle UK-relevanten Pairs strukturell auf `markets=["DE","US","UK"]` umgestellt sind

### `FEATURE_INDEPENDENTS_ENABLED`

- **Typ:** `"true"` oder `"false"` (case-insensitive)
- **Default:** `"false"`
- **Helper:** `is_independents_enabled()`
- **Zweck:** Aktiviert Independents-Pipeline (Beta-Pairs sichtbar, Single-Market-Briefs generierbar)
- **Einführung:** PR #155 (Pattern), Nutzung kommt mit Independents-Sprint (Phase 3a Roadmap)
- **Entfernung:** Nach Independents-Full-Rollout (Phase 3b Roadmap)

### `FEATURE_SEGMENT_ROUNDUPS_ENABLED`

- **Typ:** `"true"` oder `"false"` (case-insensitive)
- **Default:** `"false"`
- **Helper:** `is_segment_roundups_enabled()`
- **Zweck:** Aktiviert den Non-Pair-Segment-Roundup-Pfad — Gate für den
  Pilot-Endpoint `POST /api/admin/roundups/generate` UND den Cron-Block in
  `_run_cron_sync_background`. Off → Cron-Lauf verhält sich exakt wie ohne
  Roundup-Erweiterung; Endpoint liefert 503.
- **Einführung:** PR #155 (Pattern als `FEATURE_SINGLE_MARKET_SCHEMA`),
  Master-Plan-Schritt-4 (2026-05-25) umbenannt + im Cron verdrahtet.
- **Entfernung:** Wenn Segment-Roundups Standardbestandteil des Cron-Laufs
  sind und kein Rollback-Hebel mehr nötig ist.
- **Migrations-Hinweis Deploy 2026-05-25:** Beim Deploy von Schritt 4 muss
  in Railway die alte Variable `FEATURE_SINGLE_MARKET_SCHEMA` gelöscht und
  die neue `FEATURE_SEGMENT_ROUNDUPS_ENABLED` gesetzt werden — sonst
  greift das Gate nicht.

## Hinzufügen neuer Flags

1. Helper-Funktion in `backend/app/core/feature_flags.py` ergänzen
2. Tests in `backend/app/tests/test_feature_flags.py` ergänzen
3. Eintrag in diesem Dokument
4. Env-Var in Railway Production setzen (initial off)
5. Code-Stelle die das Flag nutzt mit kurzem Kommentar markieren:

   ```python
   # FEATURE_FLAG: <flag-name> — entfernen wenn <Bedingung>
   if is_feature_xy_enabled():
       ...
   ```

## Entfernen von Flags

Wenn ein Feature vollständig aktiviert wurde und stabil läuft:

1. Helper-Aufrufe aus dem Code entfernen, conditional branches kollabieren
2. Tests für die Helper-Funktion entfernen (optional — können auch bleiben)
3. Env-Var aus Railway entfernen
4. Eintrag in diesem Dokument als "entfernt am YYYY-MM-DD" markieren oder löschen
