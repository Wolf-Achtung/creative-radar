# Scraper-Coverage-Diagnose — 2026-05-05

> **Container-Hinweis:** Wie bei der Title-Tagging-Diagnose: kein Live-DB-
> Zugriff im Diagnose-Container. **Q3 (Cron-Selektionslogik)** ist
> vollständig aus der Codebase abgeleitet und damit definitiv. **Q1, Q2,
> Q4, Q5** sind als execution-ready psql-Blöcke ausgeliefert; Output-
> Tabellen-Slots sind als `_<wolf>_` markiert. Hypothesen-Match ist als
> parametrisierter Decision-Tree mit klaren Schwellenwerten formuliert,
> sodass Wolf nach 5-10 Min psql-Output direkt das Folge-Briefing 0b
> ableiten kann.

---

## Executive Summary

- **Behauptete Datenlage** (aus Briefing): 147 mvp=true Channels, davon
  15 mit Posts, ~132 ohne Posts. Im Container nicht direkt verifizierbar
  → Q1 ist die Verifikations-Query.
- **Stärkste Codebase-Befunde:**
  1. **A-Class-Cap (`CRON_A_CLASS_MAX=30`)** ist eine harte Throttle.
     Bei 147 mvp-Channels werden pro Cron-Run maximal 30 A-Class-Channels
     gesynct (alle, die ≥1 Asset in 30 Tagen haben), plus 1/3 der
     B-Class-Channels rotierend.
  2. **Apify-Errors sind nicht persistiert.** `cost_log` wird nur bei
     erfolgreichen Runs geschrieben (`apify_connector.py:44-48`). Pro-
     Channel-Failures (404, gelöschter Account, falscher Handle) landen
     ausschließlich in Container-Logs und im `cron_run.error_message`-
     Feld auf Top-Level (nicht pro Channel). → Hypothese (c) ist
     **strukturell nicht aus der DB beantwortbar**.
  3. **Identifier-Drift-Risiko**: Migration `e5d8f1a36b40` (PR #69)
     setzte 17 TT-Channels mit Handles teils CamelCase (`marvel_uk`),
     teils mit Punkten (`sonypictures.uk`), teils wie URL-Slugs
     (`20thcentury`). Apify-Actor `clockworks~tiktok-scraper` ist
     handle-sensitiv.
- **Empfehlung Briefing 0b** (Decision-Tree):
  - Wenn Q1 zeigt **>50% der mvp=true frisch** (created_at jung): Cron-
    Coverage-Zyklus noch nicht durch → "Wait & See" + Trigger einen
    Voll-Sync.
  - Wenn die mvp=true alt sind und **A-Class-Slots überfüllt** sind:
    `CRON_A_CLASS_MAX` erhöhen oder A-Class-Threshold raufdrehen.
  - Wenn alle Cron-Runs `summary_json["platforms"]["X"]["raw_items"]` ≥
    Channel-Count zeigen, aber Posts trotzdem fehlen: Identifier-Issue
    (d) — Spot-Check 5 leere Channels via Q5.

---

## Pre-Commitments-Check

| PC | Status |
|---|---|
| **Read-only** | ✅ Nur SELECT, kein Cron-Trigger, kein Apify-Run |
| **Schema-Drift-aware** | ✅ Verifikations-Block für Schema-Drift (siehe unten); ENUMs `market`/`priority` in `public`, `channel_role`/`quality_tier`/`acquisition_strategy` in `creative_radar` |
| **Codebase-Recherche zwingend** | ✅ Q3 vollständig aus `cron.py` + `cron_channel_selection.py` + `apify_connector.py` + `monitor.py` + `cost_log.py` |
| **Tagesbericht** | ✅ Eine Markdown-Datei, kein Zwischengeplapper |

---

## Schema-Drift-Vorab-Verifikation (PC-konform)

```sql
-- Channel-Spalten-Realität (erwartet: ja-Spalten unten)
SELECT column_name, data_type, udt_schema, udt_name, is_nullable
FROM information_schema.columns
WHERE table_schema = 'creative_radar' AND table_name = 'channel'
ORDER BY ordinal_position;
```

Erwartete Realität (aus `entities.py` + Migrations):

| Spalte | Typ | UDT-Schema | Anomalie |
|---|---|---|---|
| `id` | uuid | – | – |
| `name` | varchar | – | – |
| `platform` | varchar | – | – |
| `url` | varchar | – | – |
| `handle` | varchar | – | – |
| `market` | USER-DEFINED | **`public`** | Schema-Drift |
| `channel_type` | varchar | – | – |
| `priority` | USER-DEFINED | **`public`** | Schema-Drift |
| `active` | boolean | – | – |
| `mvp` | boolean | – | – |
| `notes` | varchar | – | – |
| `channel_role` | USER-DEFINED | `creative_radar` | – |
| `quality_tier` | USER-DEFINED | `creative_radar` | – |
| `acquisition_strategy` | USER-DEFINED | `creative_radar` | – |
| `monitoring_enabled` | boolean | – | – |
| `category` | varchar | – | – |
| `import_source` | varchar | – | – |
| `created_at` / `updated_at` | timestamp | – | – |

**Bekanntermaßen NICHT existent**: `channel_priority` (Wolf-Briefing-
Hinweis bestätigt — die Spalte heißt `priority`).

```sql
-- CronRun-Spalten (für Q2)
SELECT column_name, data_type FROM information_schema.columns
WHERE table_schema = 'creative_radar' AND table_name = 'cron_run'
ORDER BY ordinal_position;
```

Aus `entities.py:410-426`: `id`, `started_at`, `completed_at`, `status`
(running/completed/failed), `run_index`, `summary_json` (jsonb),
`error_message`.

---

## Q1 — mvp-Flag-Verteilung verstehen

### Q1a — Counts

```sql
SELECT
  COUNT(*)                                           AS channels_total,
  COUNT(*) FILTER (WHERE mvp = TRUE)                 AS mvp_true,
  COUNT(*) FILTER (WHERE mvp = FALSE)                AS mvp_false,
  COUNT(*) FILTER (WHERE mvp = TRUE  AND active = TRUE) AS mvp_active,
  COUNT(*) FILTER (WHERE mvp = TRUE  AND active = FALSE) AS mvp_inactive,
  COUNT(*) FILTER (WHERE mvp = TRUE  AND monitoring_enabled = TRUE) AS mvp_monitored
FROM creative_radar.channel;
```

| channels_total | mvp_true | mvp_false | mvp_active | mvp_inactive | mvp_monitored |
|---:|---:|---:|---:|---:|---:|
| _<wolf>_ | _<wolf>_ | _<wolf>_ | _<wolf>_ | _<wolf>_ | _<wolf>_ |

### Q1b — Pivot Plattform × Markt für mvp=true

```sql
SELECT platform,
       market::text AS market,
       COUNT(*) AS channels,
       COUNT(*) FILTER (WHERE active = TRUE)  AS active,
       COUNT(*) FILTER (WHERE active = FALSE) AS inactive
FROM creative_radar.channel
WHERE mvp = TRUE
GROUP BY platform, market::text
ORDER BY platform, market;
```

| Platform | Market | Channels | Active | Inactive |
|---|---|---:|---:|---:|
| _<wolf>_ |  |  |  |  |

### Q1c — Channels mit/ohne Posts (Hauptbefund)

```sql
SELECT
  COUNT(*)                                                            AS mvp_total,
  COUNT(*) FILTER (
    WHERE id IN (SELECT DISTINCT channel_id FROM creative_radar.post)
  )                                                                   AS with_posts,
  COUNT(*) FILTER (
    WHERE id NOT IN (SELECT DISTINCT channel_id FROM creative_radar.post)
  )                                                                   AS without_posts
FROM creative_radar.channel
WHERE mvp = TRUE AND active = TRUE;
```

| mvp_total | with_posts | without_posts |
|---:|---:|---:|
| _<wolf>_ | _<wolf>_ | _<wolf>_ |

### Q1d — Pro Plattform: Coverage-Pivot

```sql
SELECT c.platform,
       COUNT(*)                                                       AS mvp_channels,
       COUNT(*) FILTER (WHERE p.posts > 0)                            AS with_posts,
       COUNT(*) FILTER (WHERE COALESCE(p.posts, 0) = 0)               AS without_posts,
       ROUND(100.0 * COUNT(*) FILTER (WHERE p.posts > 0)
             / NULLIF(COUNT(*), 0), 1)                                AS coverage_pct
FROM creative_radar.channel c
LEFT JOIN (
  SELECT channel_id, COUNT(*) AS posts
  FROM creative_radar.post
  GROUP BY channel_id
) p ON p.channel_id = c.id
WHERE c.mvp = TRUE AND c.active = TRUE
GROUP BY c.platform
ORDER BY c.platform;
```

| Platform | mvp_channels | with_posts | without_posts | Coverage % |
|---|---:|---:|---:|---:|
| instagram | _<wolf>_ |  |  |  |
| tiktok | _<wolf>_ |  |  |  |
| youtube | _<wolf>_ |  |  |  |

### Q1e — Letzter Post-Zeitstempel pro Channel (Cluster-Analyse)

```sql
SELECT c.handle, c.platform, c.market::text AS market,
       c.created_at::date              AS channel_created,
       MAX(p.detected_at)              AS last_post_at,
       COUNT(p.id)                     AS posts_total
FROM creative_radar.channel c
LEFT JOIN creative_radar.post p ON p.channel_id = c.id
WHERE c.mvp = TRUE AND c.active = TRUE
GROUP BY c.id, c.handle, c.platform, c.market, c.created_at
ORDER BY last_post_at DESC NULLS LAST, c.handle
LIMIT 50;
```

→ Tabelle leer lassen (Wolf paste output). Cluster-Zweck: zeigt ob es
einen klaren Schnittpunkt (z.B. "alle vor 2026-04-15 angelegten haben
Posts, alle danach nicht") gibt → Hinweis auf Cron-Coverage-Zyklus
oder Cron-Pause.

### Q1f — Verteilung nach `import_source`

```sql
SELECT COALESCE(import_source, '<NULL>')              AS source,
       platform,
       COUNT(*)                                       AS channels,
       COUNT(*) FILTER (
         WHERE id IN (SELECT DISTINCT channel_id FROM creative_radar.post)
       )                                              AS with_posts,
       COUNT(*) FILTER (
         WHERE id NOT IN (SELECT DISTINCT channel_id FROM creative_radar.post)
       )                                              AS without_posts
FROM creative_radar.channel
WHERE mvp = TRUE AND active = TRUE
GROUP BY source, platform
ORDER BY source, platform;
```

→ Falls `whitelist_expansion_2026_05_05` die ganzen `without_posts`
dominiert → Bestätigung Hypothese (a)/(b) (frische Channels noch nicht
durchgelaufen).

---

## Q2 — Cron-Run-Historie

### Q2a — Letzte 30 Cron-Runs

```sql
SELECT id, started_at, completed_at, status, run_index,
       error_message,
       summary_json->'platforms'->'instagram'->>'channels_checked' AS ig_checked,
       summary_json->'platforms'->'instagram'->>'raw_items'        AS ig_items,
       summary_json->'platforms'->'instagram'->>'created_assets'   AS ig_created,
       summary_json->'platforms'->'instagram'->>'reason'           AS ig_skip_reason,
       summary_json->'platforms'->'tiktok'->>'channels_checked'    AS tt_checked,
       summary_json->'platforms'->'tiktok'->>'raw_items'           AS tt_items,
       summary_json->'platforms'->'tiktok'->>'created_assets'      AS tt_created,
       summary_json->'platforms'->'tiktok'->>'reason'              AS tt_skip_reason
FROM creative_radar.cron_run
ORDER BY started_at DESC
LIMIT 30;
```

| started_at | status | run_index | ig_checked | ig_items | ig_created | tt_checked | tt_items | tt_created | reason |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| _<wolf>_ |  |  |  |  |  |  |  |  |  |

### Q2b — Per-Run Aggregat (welche Plattform skipped wann)

```sql
SELECT
  COUNT(*)                                                              AS runs_total,
  COUNT(*) FILTER (WHERE status = 'completed')                          AS completed,
  COUNT(*) FILTER (WHERE status = 'failed')                             AS failed,
  COUNT(*) FILTER (WHERE summary_json->'platforms'->'tiktok'->>'reason'
                        IS NOT NULL)                                    AS tt_skipped_runs,
  COUNT(*) FILTER (WHERE summary_json->'platforms'->'instagram'->>'reason'
                        IS NOT NULL)                                    AS ig_skipped_runs,
  COUNT(*) FILTER (WHERE (summary_json->'platforms'->'tiktok'->>'raw_items')::int = 0
                     AND summary_json->'platforms'->'tiktok'->>'reason' IS NULL) AS tt_zero_items,
  COUNT(*) FILTER (WHERE (summary_json->'platforms'->'instagram'->>'raw_items')::int = 0
                     AND summary_json->'platforms'->'instagram'->>'reason' IS NULL) AS ig_zero_items
FROM creative_radar.cron_run
WHERE started_at > NOW() - INTERVAL '30 days';
```

### Q2c — Welche mvp-Channels sind nie im Cron-Run angefasst worden

> **Limitation:** `cron_run.summary_json` enthält Channel-Counts, aber
> **keine Liste von Channel-IDs**. Welcher konkrete Channel wann
> gescraped wurde, ist nicht persistiert. Proxy-Frage: welche Channels
> haben `created_at < (vorletzter erfolgreicher Cron-Run)` und immer noch
> 0 Posts?

```sql
WITH last_2_runs AS (
  SELECT started_at FROM creative_radar.cron_run
  WHERE status = 'completed' ORDER BY started_at DESC LIMIT 2
),
cutoff AS (SELECT MIN(started_at) AS ts FROM last_2_runs)
SELECT c.handle, c.platform, c.market::text AS market,
       c.created_at::date AS created,
       (SELECT ts FROM cutoff)::date AS cron_cutoff_date
FROM creative_radar.channel c, cutoff
WHERE c.mvp = TRUE AND c.active = TRUE
  AND c.created_at < cutoff.ts
  AND c.id NOT IN (SELECT DISTINCT channel_id FROM creative_radar.post)
ORDER BY c.platform, c.market, c.handle
LIMIT 50;
```

→ Zeigt jene mvp-Channels, die **schon vor dem vorletzten Cron-Run
existierten** und **trotzdem nie Posts produziert haben**. Diese sind
die starken Indikatoren für Hypothese (c) Apify-Fail oder (d)
Identifier-Issue, nicht (b) Cron-Schedule-Lag.

---

## Q3 — Cron-Selektionslogik (Codebase, definitiv)

### 3.1 Eintritts-Punkt

`POST /api/admin/cron/sync-all` (`backend/app/api/cron.py:250-287`):

1. `_reap_stale_runs(session)` — Runs in Status `running` älter als
   `CRON_RUN_TIMEOUT_MINUTES` werden auf `failed` gesetzt.
2. Lock-Check: existiert ein `running`-Run → 409, kein neuer Run.
3. `compute_run_index()` — Rotations-Index ∈ {0, 1, 2}.
4. CronRun-Row anlegen mit `status="running"`.
5. `BackgroundTask(_run_cron_sync_background, run.id, run_index)` →
   sofort 202 zurück.

### 3.2 Background-Task

`_run_cron_sync_background` (`cron.py:222-247`):

```python
with Session(engine) as session:
    run = session.get(CronRun, run_id)
    try:
        summary, created_asset_ids = await _execute_platform_sync(session, run_index)
        if created_asset_ids:
            summary["vision"] = _run_vision_after_sync(...)
        run.summary_json = summary
        run.status = "completed"
        run.completed_at = datetime.now(timezone.utc)
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)[:500]
        run.completed_at = datetime.now(timezone.utc)
```

→ Top-Level-Catch: **eine** unerwartete Exception bricht den **gesamten
Run** auf `failed`. Per-Channel-Errors werden im `_run_apify_sync_for_platform`
(monitor.py:140-185) abgefangen und als `skipped_other` gezählt — aber
**ohne Channel-ID**.

### 3.3 Platform-Sync-Loop

`_execute_platform_sync` (`cron.py:91-152`) — sequentiell IG dann TT:

```python
# IG-Block:
if not is_apify_configured():
    summary["platforms"]["instagram"] = {"skipped": True, "reason": "apify_not_configured"}
else:
    ig_channels = select_channels_for_cron(session, "instagram", run_index)
    if not ig_channels:
        summary[...] = {"skipped": True, "reason": "no_channels", "channels_checked": 0}
    else:
        channel_urls = [c.url for c in ig_channels if c.url]
        raw_items = await run_public_channel_monitor(channel_urls, CRON_RESULTS_LIMIT_PER_CHANNEL)
        sync = _run_apify_sync_for_platform(...)
        summary[...] = {channels_checked, raw_items, ...}

# TT-Block analog mit usernames statt urls
```

`CRON_RESULTS_LIMIT_PER_CHANNEL = 5` (cron.py:49) — **maximal 5 Posts
pro Channel pro Run**.

### 3.4 Channel-Selektion (der Kern)

`select_channels_for_cron(session, platform, run_index)` (`cron_channel_selection.py:41-103`):

```python
candidates = (
    SELECT * FROM creative_radar.channel
    WHERE mvp = TRUE AND active = TRUE AND platform = :platform
    ORDER BY handle
)

# Asset-Counts pro Channel in den letzten 30 Tagen
counts = SELECT post.channel_id, COUNT(asset.id)
         FROM post JOIN asset ON asset.post_id = post.id
         WHERE asset.created_at > NOW() - INTERVAL '30 days'
         GROUP BY post.channel_id

# A-Class: counts >= CRON_A_CLASS_THRESHOLD (default 1)
#         sortiert nach (-count, handle), capped at CRON_A_CLASS_MAX (default 30)
a_class = top_n(counts >= threshold, max_a)

# B-Class: alles andere
b_class = candidates - a_class

# B-Class-Rotation: in 3 Drittel teilen, run_index ∈ {0, 1, 2} wählt eines
if len(b_class) < 3:
    b_third = b_class       # bei wenigen Channels: alle pro Run
else:
    third_size = ceil(len(b_class) / 3)
    b_third = b_class[run_index * third_size : (run_index+1) * third_size]

return a_class + b_third
```

**Konsequenzen:**

| Szenario | Verhalten |
|---|---|
| 30 mvp-Channels, alle aktiv | A-Class hat alle, B-Class leer → jeder Run scrapt alle |
| 100 mvp-Channels, 5 mit Assets | A-Class = 5, B-Class = 95 → ~32 Channels pro Run, 3 Runs für vollen Zyklus |
| 147 mvp-Channels, 30 mit Assets (CRON_A_CLASS_MAX-Cap) | A-Class = 30, B-Class = 117 → ~30+39 = 69 pro Run |
| 200 mvp-Channels, 50 mit Assets | A-Class = 30 (gecappt!), B-Class = 170 → 88 pro Run, **20 mit Assets fallen aus A-Class** und konkurrieren in B-Class |

→ Bei 147 mvp-Channels mit den default-Limits: **jeder Channel wird im
Mittel alle 9 Tage einmal gescraped** (3-Tage-Cron × 3 Drittel).

### 3.5 Env-Vars (Was sie genau machen)

| Env | Default | Wirkung |
|---|---:|---|
| `CRON_A_CLASS_THRESHOLD` | `1` | Min. Asset-Count in 30d, ab dem ein Channel A-Class ist |
| `CRON_A_CLASS_MAX` | `30` | Max. Anzahl A-Class-Channels pro Run (harte Apify-Cost-Cap) |
| `CRON_SYNC_INTERVAL_DAYS` | `3` | Wird von `compute_run_index()` für die Modulo-Rotation genutzt; nicht der GitHub-Workflow-Schedule! |
| `CRON_RUN_TIMEOUT_MINUTES` | `30` | Stale-Run-Reaper |
| `apify_results_limit_per_channel` | `5` | Max. Posts pro Channel-API-Call (`apify_results_limit_per_channel` ist ein Settings-Feld, kein Env-Var-Override im Code sichtbar) |
| `apify_wait_seconds` | `60` | `waitForFinish` für Apify-Actor-Run |
| `cron_vision_max_assets_per_run` | `50` | Vision-Cap per Run |

### 3.6 Throttling

- **Implizites Throttling**: A-Class-Cap (max 30) + B-Class-Drittelung.
- **Kein per-Channel-Backoff** bei Apify-Errors — der nächste Run versucht
  denselben Channel wieder.
- **Kein Circuit-Breaker** für komplett tote Channels.

---

## Q4 — Apify-Failure-Pattern

### 4.1 Strukturelle Limitation: Errors landen NICHT in der DB

**`record_apify_run` wird nur am Ende eines erfolgreichen Actor-Runs
aufgerufen** (`apify_connector.py:44-48`). Bei HTTP-Error in
`run_response.raise_for_status()` (Zeile 30) propagiert die Exception
nach oben, die Funktion returnt nie, und `cost_log` bekommt keinen
Eintrag.

→ **Es gibt keine pro-Channel-Apify-Error-Persistierung in der DB.** Die
einzigen Spuren:
1. Container-Logs (Railway): `logger.exception("cron run %s failed", run_id)`
2. `cron_run.error_message` (nur Top-Level-Failure, ohne Channel-ID)
3. `cron_run.summary_json["platforms"][p]["skipped_other"]` (Per-Channel-
   Failures **ohne Channel-ID**, nur als Counter)

### 4.2 Was per DB diagnostizierbar ist

```sql
-- Failed Cron-Runs der letzten 30 Tage
SELECT id, started_at, error_message,
       summary_json->'platforms' AS platforms_summary
FROM creative_radar.cron_run
WHERE started_at > NOW() - INTERVAL '30 days'
  AND status = 'failed'
ORDER BY started_at DESC
LIMIT 20;
```

```sql
-- Apify-Cost-Log-Korrelation: an welchen Runs gab es überhaupt
-- erfolgreiche Apify-Calls? (cost_log.timestamp ≈ Run-Zeit)
SELECT DATE_TRUNC('hour', cl.timestamp) AS hour_bucket,
       cl.operation,
       COUNT(*) AS calls,
       SUM((cl.cost_meta->>'items_count')::int) AS total_items
FROM creative_radar.cost_log cl
WHERE cl.provider = 'apify'
  AND cl.timestamp > NOW() - INTERVAL '7 days'
GROUP BY hour_bucket, cl.operation
ORDER BY hour_bucket DESC;
```

→ Diff zwischen `cost_log` apify-call-count und cron-run-count zeigt:
wie oft Apify überhaupt aufgerufen wurde vs. wie oft der Cron lief.

### 4.3 Spot-Check: 3 Channels mit 0 Posts

```sql
WITH zero_post_channels AS (
  SELECT c.id, c.handle, c.platform, c.market::text AS market,
         c.url, c.notes, c.created_at::date AS created
  FROM creative_radar.channel c
  WHERE c.mvp = TRUE AND c.active = TRUE
    AND c.id NOT IN (SELECT DISTINCT channel_id FROM creative_radar.post)
  ORDER BY c.created_at DESC
  LIMIT 3
)
SELECT * FROM zero_post_channels;
```

Manuelle Verifikation (außerhalb DB, durch Wolf):
- IG: `https://www.instagram.com/{handle}/` aufrufen → 200/404?
- TT: `https://www.tiktok.com/@{handle}` aufrufen → existiert das Profil?
- handle-Format passt zur URL? (CamelCase, Punkte, Underscores)

### 4.4 Wo Apify-Errors im Code abgefangen werden

| Stelle | Verhalten bei Error |
|---|---|
| `_run_actor` (apify_connector.py:23-49) | Exception propagiert hoch — kein Catch |
| `run_public_channel_monitor` (apify_connector.py:52-66) | Exception propagiert hoch |
| `run_tiktok_profile_monitor` (apify_connector.py:69-114) | Versucht 2 Input-Schemas (`profiles` / `usernames`); wenn beide fehlschlagen → letzte Exception wird geworfen |
| `_execute_platform_sync` (cron.py:91-152) | Kein lokaler Catch — Exception propagiert ins Background-Task-Top-Level |
| `_run_cron_sync_background` (cron.py:222-247) | Top-Level-Catch → `cron_run.status = "failed"`, `error_message = str(exc)[:500]` |
| `_run_apify_sync_for_platform` (monitor.py:140-185) | **Per-Item-Catch fehlt** — wenn `_create_asset_from_item` für ein Item wirft, läuft die ganze Loop in den Top-Level-Catch |

**Konsequenz:** Ein einziger fehlerhafter Channel oder ein einziges
Apify-Item kann den gesamten Cron-Run umkippen. Es gibt kein Per-Channel-
Error-Isolation.

---

## Q5 — Identifier-Sanity

### Q5a — Spot-Check: 5 leere Channels — handle vs URL vs external_id

```sql
SELECT id, handle, url, platform, market::text AS market,
       created_at::date AS created,
       import_source, notes
FROM creative_radar.channel
WHERE mvp = TRUE AND active = TRUE
  AND id NOT IN (SELECT DISTINCT channel_id FROM creative_radar.post)
ORDER BY created_at DESC
LIMIT 5;
```

| Handle | URL | Platform | Market | Created | import_source | notes |
|---|---|---|---|---|---|---|
| _<wolf>_ |  |  |  |  |  |  |

Konsistenz-Check pro Zeile:

- IG-Channels: `url == 'https://www.instagram.com/' || handle || '/'` ?
- TT-Channels: `url == 'https://www.tiktok.com/@' || handle` ?
- Handle ohne `@`-Prefix?
- Handle ohne Trailing-Slash?

### Q5b — Handle-Pattern-Anomalien

```sql
-- TT-Handles mit Sonderzeichen (Punkte, Underscores)
SELECT handle, url, market::text AS market, created_at::date
FROM creative_radar.channel
WHERE platform = 'tiktok'
  AND mvp = TRUE
  AND (handle ~ '[._-]' OR handle ~ '[A-Z]')
ORDER BY handle;
```

Bekannte Risiko-Handles aus PR #69 Migration `e5d8f1a36b40`:
- `sonypictures.uk` (Punkt)
- `sonypictures.de` (Punkt)
- `studiocanal.de` (Punkt)
- `marvel_uk` (Underscore)
- `20thcentury` (Zahl-Prefix)

Apify-Actor `clockworks~tiktok-scraper` reagiert empfindlich auf
Handle-Format. Falls eine dieser Anomalien in 0-Posts-Channels
überrepräsentiert ist → Indiz für Hypothese (d).

### Q5c — Handle/URL-Konsistenz-Check programmatisch

```sql
SELECT id, handle, url, platform,
       CASE
         WHEN platform = 'instagram'
              AND url != 'https://www.instagram.com/' || handle || '/'
              THEN 'mismatch_ig'
         WHEN platform = 'tiktok'
              AND url != 'https://www.tiktok.com/@' || handle
              THEN 'mismatch_tt'
         WHEN platform = 'youtube'
              AND url NOT LIKE '%' || handle || '%'
              THEN 'mismatch_yt'
         ELSE 'ok'
       END AS consistency
FROM creative_radar.channel
WHERE mvp = TRUE AND active = TRUE AND handle IS NOT NULL
ORDER BY consistency, platform, handle;
```

→ Alle Zeilen mit `consistency != 'ok'` sind sofortige Identifier-Issue-
Kandidaten.

---

## Hypothesen-Match (Decision-Tree)

> Auswertungs-Schemata sind so formuliert, dass Wolf nach Q1-Q5-psql-
> Output direkt das Folge-Briefing 0b ableiten kann.

### (a) Historischer mvp-Flag-Drift

**Bestätigt wenn:**
- Q1f zeigt: `import_source = 'whitelist_expansion_2026_05_05'`-Channels
  haben überproportional `without_posts`, AND
- Q1e zeigt: deren `created_at` > Datum des vorletzten Cron-Runs.

**Widerlegt wenn:**
- mvp=true Verteilung ist über alle `import_source`-Werte gleichmäßig
  ohne Posts.

**Mögliches Szenario:** Wolf hat heute manuell `mvp=true` auf 132
neue Channels gesetzt (z.B. `UPDATE channel SET mvp=true WHERE
import_source='whitelist_expansion_2026_05_05'`), Cron-Coverage-Zyklus
braucht aber 9 Tage → noch nicht durchgelaufen.

→ **Quick-Fix bei (a):** Manueller Cron-Trigger jetzt + Status in 9 Tagen
checken. Kein Code-Change nötig.

### (b) Cron-Selektion (Throttle/Logic-Bug)

**Bestätigt wenn:**
- Q2a zeigt durchgängig `channels_checked` ≪ `mvp_total` aus Q1 (z.B.
  `ig_checked: 30` bei 50 mvp-IG-Channels), AND
- Q1c zeigt: viele Channels mit `created_at` viel älter als der älteste
  Cron-Run und trotzdem 0 Posts.

**Widerlegt wenn:**
- Q2a zeigt `channels_checked ≈ mvp_total` (alle werden angefasst), aber
  `created_assets` ist trotzdem 0.

**Quick-Fix bei (b):** `CRON_A_CLASS_MAX` von 30 auf z.B. 80 oder 200
hochsetzen (Apify-Cost-Awareness vorausgesetzt). Oder
`select_channels_for_cron`-Logic überarbeiten: B-Class-Rotation in
3 Drittel verkleinern auf 1 Drittel = 1 Run = alle.

### (c) Apify-Fail per Channel

**Bestätigt wenn:**
- Q4.2 zeigt: `cost_log` apify-Calls existieren (Apify wird
  aufgerufen), AND
- Q2a zeigt durchgängig `raw_items: 0` für die Plattform mit hoher
  `without_posts`-Rate.

**Widerlegt wenn:**
- `cost_log` zeigt items_count > 0 in den meisten Apify-Calls (Items
  kommen) und `created_assets` > 0 in den Cron-Summaries, aber
  Channels-ohne-Posts bleiben.

**Quick-Fix bei (c):**
- **Strukturell**: Per-Channel-Error-Isolation in `_run_apify_sync_for_platform`
  einbauen (try/except pro item, errored items in cron_run.summary_json
  als list mit channel_handle persistieren).
- **Operativ**: Wolf checkt Apify-Console für die letzten Runs des
  fehlerhaften Actors.

### (d) Identifier-Issue (Handle-Format-Mismatch)

**Bestätigt wenn:**
- Q5b zeigt: TT-Channels mit Sonderzeichen-Handles sind
  überrepräsentiert in `without_posts`, OR
- Q5c zeigt: signifikante Anzahl Channels mit `consistency != 'ok'`.

**Widerlegt wenn:**
- Q5c zeigt: alle Handles sind URL-konsistent, AND
- Q5b zeigt: Sonderzeichen-Handles gleich verteilt zwischen with/without
  posts.

**Quick-Fix bei (d):** UPDATE-Migration die handle-Spalte pro betroffenem
Channel korrigiert + handle-URL-Konsistenz als CHECK-Constraint
(separater Sprint).

---

## Empfehlung Folge-Briefing 0b

Auf Basis der Hypothesen-Diagnose:

| Befund | 0b-Sprint-Vorschlag | Aufwand |
|---|---|---|
| **(a) bestätigt, alleinstehend** | "0b-Wait" — Manueller Cron-Trigger + Status-Check in 9 Tagen, kein Code | 5 Min |
| **(b) bestätigt, alleinstehend** | "0b-Cron-Tune" — `CRON_A_CLASS_MAX` raufdrehen oder `select_channels_for_cron` überarbeiten | 1-2h |
| **(c) bestätigt** | "0b-Error-Isolation" — Per-Channel-try/except in `_run_apify_sync_for_platform`, Errored-Channels in `cron_run.summary_json` persistieren | 2-3h |
| **(d) bestätigt** | "0b-Identifier-Cleanup" — UPDATE-Migration für betroffene Handles, plus optional handle-Spalten-CHECK-Constraint | 1-3h je nach Anzahl |
| **Mehrere bestätigt** | "0b-Diagnose-Detail" — kleinerer fokussierter Sprint pro Hypothese, beginnen mit größtem Hebel (vermutlich (b) oder (c)) | – |

**Empfehlung Default-Reihenfolge** (wenn Wolf keine starke Präferenz):

1. Zuerst (a) prüfen — billigster Test (5 Min Trigger + abwarten).
2. Falls (a) widerlegt: (c) angehen — Error-Isolation hat den größten
   Hebel für **alle künftigen** Sprints (auch nicht-coverage-bezogen).
3. (b) und (d) parallel als kleinere Backlog-Items.

---

## Daily-Report (Slack-fähig)

> Scraper-Coverage-Diagnose 2026-05-05: Codebase-Befunde definitiv,
> DB-Zahlen offen (psql-Blöcke im Report). Strukturelle Hauptbefunde:
> A-Class-Cap (`CRON_A_CLASS_MAX=30`) throttled auf max 30+1/3-B
> Channels pro Run, bei 147 mvp-Channels = ~9-Tage-Coverage-Zyklus.
> Apify-Errors sind nicht per-Channel persistiert — `cost_log`
> bekommt nur erfolgreiche Runs, Failures landen nur in Container-
> Logs und in `cron_run.error_message` (Top-Level). Decision-Tree
> mit 4 Hypothesen + parametrisierten Schwellen liegt im Report,
> Folge-Briefing 0b ist nach 5-10 Min psql-Output ableitbar. Größter
> struktureller Hebel: Per-Channel-Error-Isolation im Apify-Sync
> (Hypothese c), unabhängig vom Befund 0a.

---

## Offene Punkte / Wolf-To-Dos

1. Q1-Q5-psql-Outputs in die `_<wolf>_`-Slots ergänzen (~10 Min).
2. Hypothesen-Match basierend auf Outputs ausführen → 0b-Briefing-Typ
   wählen.
3. Optional: Container-Logs-Export für die letzten 5 Cron-Runs (zeigt
   Apify-Error-Class-Pattern, das in der DB nicht persistiert ist).
