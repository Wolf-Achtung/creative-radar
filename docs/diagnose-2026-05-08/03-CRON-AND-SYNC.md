# 03 — Cron / Sync-Pipeline

## Was wurde geprüft

- `backend/app/api/cron.py` (sync-all + runs).
- `backend/app/services/cron_channel_selection.py` (A/B-Class-Auswahl).
- `backend/app/services/apify_connector.py` (IG + TT).
- `backend/app/services/youtube_connector.py` (YT, separat).
- `backend/app/api/admin.py` (manueller YT-Sync-Endpoint).
- `.github/workflows/cron-sync.yml` (Schedule).

## Befunde

### Schedule

`.github/workflows/cron-sync.yml:16` → `cron: '0 6 */3 * *'` →
**alle 3 Tage, 06:00 UTC**, Trigger via GitHub Actions, das den Endpoint
`POST {CR_API_URL}/api/admin/cron/sync-all` mit Bearer-Token aufruft.

`workflow_dispatch` ist ebenfalls definiert (manueller Trigger).

`compute_run_index` (`cron_channel_selection.py:107-117`) berechnet
`(days_since_2026-01-01 // CRON_SYNC_INTERVAL_DAYS) % 3` → rotiert die
B-Class-Drittel über drei Läufe. Default-Interval = 3 Tage (per ENV
`CRON_SYNC_INTERVAL_DAYS`).

→ **Der Wochenend-Cadence-Sprint braucht zwei Änderungen:**

1. Cron-Expression auf z. B. `0 4 * * 0` (Sonntag 04:00 UTC, Reports stehen
   Montag früh).
2. `CRON_SYNC_INTERVAL_DAYS` und `compute_run_index` an das neue Intervall
   anpassen (wöchentlich → kein Drittel-Rotieren mehr nötig, oder über
   `iso_week % 3` rotieren).

### Plattform-Abdeckung im Cron

`api/cron.py:_execute_platform_sync` (Zeilen 93-154):

- **Instagram**: aktiv, sofern `is_apify_configured()` (Apify-Token + IG-Actor-ID).
- **TikTok**: aktiv, sofern `is_tiktok_configured()` (Apify-Token + TT-Actor-ID).
- **YouTube**: **nicht im Cron**. Modul-Docstring `cron.py:14-16` ist
  explizit: "Scope (Sprint 5.3.5): Apify-driven IG + TikTok only. YouTube
  cron lands in a separate sprint." `grep -n "youtube" backend/app/api/cron.py`
  → 0 Treffer.

### YouTube-Status

- `youtube_connector.py:65` `is_youtube_configured()` → prüft
  `settings.youtube_api_key`. **needs live verification**: ENV gesetzt?
- `fetch_channel_videos(handle_or_id, results_limit)` kostet **3 Quota-Units
  pro Channel** (channels.list + playlistItems.list + videos.list). Quota
  Free-Tier 10k/Tag → 3000 Channel-Syncs/Tag möglich, weit überdimensioniert.
- Manueller Endpoint: `POST /api/admin/youtube/sync/{channel_id}`
  (`api/admin.py:161`). Validiert `channel.platform == "youtube"`
  (`admin.py:203-206`).
- `acquisition_strategy` der YT-Channels in der DB: aus Code allein nicht
  feststellbar. Im Tests-Fixture (`test_api_channels_backwards_compat.py:89`)
  wird `youtube_api` als Wert verwendet — Migrations-Default ist `apify`.
  **Risiko:** wenn YT-Channels mit Default `apify` reingelaufen sind, würde
  ein zukünftiger YT-Cron-Job sie fälschlich an Apify schicken (oder gar
  nicht abdecken, je nach Filter-Logik). **needs live verification.**
- Wolfs Vorab-Notiz: "YouTube-Channels in der DB: Erwartung `youtube_api`,
  häufig fälschlich `apify`" — **bestätigt sich strukturell über den
  Default-Wert in Migration `7e3b2c4a8f51:93`**: `server_default="apify"`.
  Jeder Channel, der vor der bewussten Acquisition-Strategy-Setzung
  reinkam, hat `apify` als Wert.

### Apify-Pipeline

- IG-Actor: `settings.apify_instagram_actor_id` (Default per `.env.example`:
  `apify~instagram-scraper`).
- TT-Actor: `settings.apify_tiktok_actor_id` (Default: `clockworks~tiktok-scraper`).
- Pro Cron-Lauf: `CRON_RESULTS_LIMIT_PER_CHANNEL = 5` (`cron.py:49`).
- Async-Refactor (Block 2.5, in jüngsten Commits sichtbar): `_run_actor`
  pollt `actor-runs/{id}` bis Terminal-Status, dataset-Lesen erst nach
  Run-Ende; `asyncio.wait_for` als hartes Total-Timeout (`apify_wait_seconds`,
  Default 60s lt. `.env.example`).
- Retry: keine in `_run_actor` selbst — ein FAILED/ABORTED-Run wirft
  `RuntimeError`. Auf Cron-Ebene fängt der Top-Level-Handler in
  `_run_cron_sync_background` ab und schreibt `status="failed"` in `cron_run`.
- **Letzte 5 Cron-Läufe**: `GET /api/admin/cron/runs?limit=5` —
  **needs live verification.**

### Vision-Pipeline nach Sync

`cron.py:157-221` `_run_vision_after_sync`: nach jedem erfolgreichen Sync
werden bis zu `CRON_VISION_MAX_ASSETS_PER_RUN` neue Assets durch
`analyze_asset_visual` geschickt. Fehler werden isoliert pro Asset gezählt
(`succeeded, text_fallback, fetch_failed, vision_error`), kein Hard-Stop.

### Briefing-Auto-Generierung

**Es gibt keinen Auto-Trigger.** Der Cron-Endpoint führt Sync + Vision aus
und endet. Der Insight-Engine-Endpoint (`/api/insights/weekly?pair=...`) ist
**Pull-only**: das Frontend ruft ihn an, sobald der GF die Studio-Karte
öffnet. Persistenz: keine (siehe `02-INSIGHT-ENGINE-AUDIT.md`).

→ **Folge für Cadence-Sprint:** Wenn die GF-Briefings am Montag früh stehen
sollen, muss der Sonntag-Cron-Lauf nach erfolgreichem Sync **zusätzlich**
für jeden Pair-Key einen Briefing-Lauf starten und das Ergebnis persistieren
(neue Tabelle oder `weeklyreport` reaktivieren). Sonst öffnet der GF Montag
früh die Studio-Karte, der Brief wird live generiert, und das dauert
30-90s (laut `InsightWeekly.jsx:422` `SLOW_THRESHOLD_MS = 60000`).

### Channel-Auswahl pro Lauf

`cron_channel_selection.py:42-104`:

- Nur `mvp=true AND active=true` Channels werden überhaupt betrachtet.
- A-Class: Channels mit `assets_30d >= CRON_A_CLASS_THRESHOLD` (Default 1)
  — sortiert nach Asset-Count, gedeckelt durch `CRON_A_CLASS_MAX` (Default 30).
- B-Class: Rest, in Drittel rotiert via `run_index`.
- Wenn `len(b_class) < 3` → alle B-Channels jeden Lauf (kein Rotieren).

→ **Bei aktuell wenig Daten** (Threshold-Default 1 wegen ~31 Posts in der
DB Anfang Mai) tendiert die A-Class praktisch jeden mvp-Channel zu
inkludieren. Bei wachsendem Volumen wird A-Class echt selektiv.

### Lock + Stale-Reaper

- Single-run-Lock: `cron_sync_all` returnt 409 wenn ein `cron_run` mit
  `status='running'` existiert.
- Stale-Reaper: Runs > `CRON_RUN_TIMEOUT_MINUTES` (Default 30) alt werden
  beim nächsten Trigger als `failed` markiert (`cron.py:76-90`).

## Inkonsistenzen / Lücken

1. **YouTube nicht im Cron-Lauf** — manueller Sync nur per Admin-Endpoint.
   Wolfs Vorab-Notiz nennt "YouTube-Aktivierung" als Sprint 4. Vorbereitung
   ist im Code da (Connector, Endpoint, ENUM-Wert), aber kein Hook im
   Cron-Pfad.
2. **`acquisition_strategy`-Default `apify`** in Migration setzt
   YT-Channels potenziell auf den falschen Wert. Vor dem YT-Sprint einmal
   konsistent setzen (Update-Statement, kein Migrations-Risiko).
3. **Kein Briefing-Trigger nach Sync** — siehe oben.
4. **Cron-Frequency 3 Tage ist Nicht-Wochenend-passend** — Cadence-Sprint
   muss `0 4 * * 0` und `compute_run_index` neu denken.
5. **Apify-Retry ist 0**: ein einzelner FAILED-Run hängt den Run der
   Plattform — kein Auto-Retry. Frequenz alle 3 Tage gleicht das aus, beim
   Wochen-Cadence wird ein verlorener Sonntag-Lauf sichtbar.
6. **`raw_payload` wird gespeichert (`Post.raw_payload: dict`)** — Quelle
   für Ranking-Refinements, ohne dass eine zweite Apify-Anfrage nötig ist.
   Gut für `05-RANKING-FEASIBILITY.md`.

## Risiken

- **Sonntag-Sync fällt aus** → Montag-Briefing zeigt Daten von Donnerstag.
  Kein Auto-Retry, kein Alert. Cron-Run-Failure landet nur in
  `cron_run.error_message` und im GitHub-Actions-Log.
- **YT-Aktivierung ohne Strategy-Backfill** könnte alte YT-Channels durch
  einen frischen YT-Cron doppelt synchronisieren oder gar nicht (je nach
  Filter-Bedingung).
- **Cost-Drift im Cron**: pro Lauf passiert Sync (Apify-Cost) + Vision
  (OpenAI-Cost). Beide werden in `costlog` geschrieben, aber **kein Hard-Cap**
  (Modul-Docstring: "Phase 5+ work").

## Empfehlung für Sprint-Planung

1. **YouTube-Sprint vor Cadence-Sprint einplanen.** Reihenfolge: erst
   YT-Cron-Hook in `_execute_platform_sync` (analog zu IG/TT), dann
   Cadence-Schedule auf Sonntag. Sonst hat die Wochen-Cadence keine
   YT-Daten.
2. **Pre-Flight für YT-Aktivierung**: Idempotentes UPDATE-Statement
   `acquisition_strategy='youtube_api' WHERE platform='youtube'`. Eine
   Zeile, kein Risiko, vor dem ersten YT-Cron-Lauf.
3. **Briefing-Persistenz** als eigener Mini-Sprint **vor** Cadence:
   ohne Persistenz produziert Cadence keine Wertschöpfung (Brief existiert
   erst, wenn GF ihn anfragt).
4. **Cadence-Cron-Expression** als reine Config-Änderung in
   `.github/workflows/cron-sync.yml` + ENV — keine Code-Änderung im
   Backend nötig, solange B-Class-Rotation neu kalibriert wird.
