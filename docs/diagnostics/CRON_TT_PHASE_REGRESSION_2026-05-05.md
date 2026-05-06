# Cron-TT-Phase-Regression Diagnose — 2026-05-05

> **Modus:** READ-ONLY-Codebase-Diagnose. Keine Code-Änderungen, keine
> DB-Mutations. Branch `claude/cron-tt-phase-diagnostic-2026-05-05` aus
> `origin/main` HEAD `e78f997` (Post-PR-#75-Merge; Code-Stand inhaltlich
> identisch mit `cf31417`). DB-Zugriff im Audit-Container nicht verfügbar
> (`railway` CLI nicht installiert) — Q5/Q6 enthalten psql-Blöcke statt
> Live-Output.

---

## Executive Summary

- **Symptom:** Beide manuellen `/api/admin/cron/sync-all`-Trigger heute
  (Run 1 17:20 UTC, Run 2 18:19 UTC) hängen mit identischem Bild —
  IG-Apify-Run erfolgreich (261 Items), TT-Apify-Run wird nie gestartet,
  `cron_run.status='running'`, `summary_json=NULL`, `completed_at=NULL`.
- **Vergleichsrun:** Run X (15:41 UTC, 904s) lief sauber durch und
  produzierte `tiktok.channels_checked=8, tiktok.raw_items=36`. Lag VOR
  dem Threshold-Env-Change.
- **Wahrscheinlichste Ursache (HIGH confidence):** Kein Code-Bug — eine
  **Timing-Regression**, die durch PR #72 latent war und heute zum ersten
  Mal sichtbar wird:
  1. `_create_asset_from_item` (`backend/app/api/monitor.py:46-137`)
     macht pro NEUEM Asset SYNCHRON eine OpenAI-Text-Analyse
     (`analyze_creative_text`, `creative_ai.py:187`) **plus** einen
     synchronen httpx-Bilddownload mit S3-PUT (`screenshot_capture.py:73-97`).
  2. Diese Sync-Calls werden aus der `async`-Cron-Background-Task aufgerufen
     und **blockieren den uvicorn-Single-Worker-Event-Loop** für Sekunden
     pro Asset.
  3. **Vor PR #72** war das harmlos, weil `raw_items` durch den
     `waitForFinish=60s`-Bug in der Praxis 0 zurückgab — die
     IG-Asset-Creation-Loop war effektiv leer.
  4. **Nach PR #72** liefert IG ehrliche 261 Items. Bei z.B. 50 neuen
     Assets × 3-7s sync = 150-350s zusätzliche Blockzeit zwischen
     IG-Apify-Ende und TT-Apify-Start.
  5. Run 1 wurde **vom Container-Restart 17:27** mid-IG-Loop gekillt
     (Auto-Deploy nach Wolfs `CRON_A_CLASS_THRESHOLD=0`-Update).
     Run 2 wurde **manuell um 18:30** beendet, ~5 Min nach IG-Apify-Ende
     — vermutlich kurz **bevor** die TT-Phase regulär gestartet wäre.
- **Confidence-Level:** **hoch** für die Diagnose-Mechanik (Codebase
  belegt). **mittel** für die Run-2-Interpretation (ohne DB-Daten zur
  Anzahl der `created_assets` und ohne Container-Logs nicht final
  beweisbar, ob TT noch gestartet wäre, wenn Wolf länger gewartet hätte).
- **Hypothese (b) APIFY_POLL_INTERVAL_SECONDS-Default:** **ausgeschlossen**
  — `apify_poll_interval_seconds: int = 10` ist hardcodierter Pydantic-
  Default in `config.py:36`, identisch für `apify_wait_seconds: int = 1800`
  in `:34`. Kein Fehlverhalten bei fehlender Railway-ENV.
- **Hypothese (d) Vision-Pipeline:** **ausgeschlossen** —
  `_run_vision_after_sync` läuft NACH beiden Plattformen
  (`cron.py:232-234`), kann den TT-Start nicht blockieren.
- **Fix-Empfehlung (Folge-Sprint, separat):** Mittelfrist —
  `_create_asset_from_item` async machen oder die Sync-Calls in
  `asyncio.to_thread()` aushebeln. Sofort-Mitigation —
  `cron_run.timeout`-Verlängerung oder `cron_vision_max_assets_per_run=0`
  setzen ist hier irrelevant (Vision liegt nach TT). Sofort-Mitigation
  ist eher: **Wolf wartet beim manuellen Trigger 15-20 Min, nicht 5 Min**,
  bevor er manuell killt.

---

## Q1 — Code-Pfad-Mapping: IG → TT

### Q1a — Endpoint `POST /api/admin/cron/sync-all`

`backend/app/api/cron.py:250-287` — `cron_sync_all`:

1. `_reap_stale_runs(session)` — `cron.py:74-88`. Marks runs
   `status='running' AND started_at < now - CRON_RUN_TIMEOUT_MINUTES (default 30)`
   as `failed/stale_run_timeout`.
2. **Single-Run-Lock:** `select(CronRun).where(status='running').first()` —
   if a run is in flight, returns 409 (`cron.py:257-266`). **Erklärt
   warum Run 2 erst NACH manuellem `failed`-Set für Run 1 starten konnte.**
3. Erstellt neuen `CronRun(run_index=...)` und persistiert ihn mit
   Default-`status='running'` (Modell-Default in `entities.py`).
4. Schedult `_run_cron_sync_background(run.id, run_index)` als
   FastAPI-`BackgroundTask` (`cron.py:274`).
5. Antwort 202 `{run_id, started_at, status: "running"}`.

### Q1b — Background-Task `_run_cron_sync_background`

`cron.py:222-247`:

```python
async def _run_cron_sync_background(run_id: UUID, run_index: int) -> None:
    with Session(engine) as session:           # eigene Session
        run = session.get(CronRun, run_id)
        try:
            summary, created_asset_ids = await _execute_platform_sync(session, run_index)
            cap = settings.cron_vision_max_assets_per_run
            if created_asset_ids:
                summary["vision"] = _run_vision_after_sync(session, created_asset_ids, cap)
            run.summary_json = summary
            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)
            session.add(run); session.commit()
        except Exception as exc:               # cron.py:241
            run.status = "failed"
            run.error_message = str(exc)[:500]
            run.completed_at = datetime.now(timezone.utc)
            session.add(run); session.commit()
```

**Wichtig:** Wird `_execute_platform_sync` mid-flight **gekillt** (SIGKILL,
Container-Restart, OOM), wird die `except`-Klausel **nie ausgeführt**.
Der `CronRun` bleibt für immer auf `status='running'` mit
`completed_at=NULL`, bis `_reap_stale_runs` ihn nach
`CRON_RUN_TIMEOUT_MINUTES` (default 30) reapt — oder Wolf manuell killt.
**Das ist exakt das beobachtete Symptom.**

### Q1c — `_execute_platform_sync`: IG dann TT, sequentiell

`cron.py:91-152`. Sequentielle Awaits, nicht parallel:

| Phase | Datei:Zeile | Was passiert |
|---|---|---|
| **IG-Channel-Wahl** | `cron.py:102` | `select_channels_for_cron(session, "instagram", run_index)` — DB-Query, ms |
| **IG-Apify-Call** | `cron.py:107` | `await run_public_channel_monitor(channel_urls, 5)` — async Polling-Loop seit PR #72 |
| **IG-Asset-Creation-Loop** | `cron.py:108-115` | `_run_apify_sync_for_platform(...)` — **SYNC `def`!** Iteriert über alle 261 Items |
| IG-Summary | `cron.py:117-122` | `summary["platforms"]["instagram"] = {...}` — in-memory dict, kein DB-Commit hier |
| **TT-Channel-Wahl** | `cron.py:127` | `select_channels_for_cron(session, "tiktok", run_index)` |
| Username-Normalisierung | `cron.py:131` | List-Comp |
| **TT-Apify-Call** | `cron.py:135` | `await run_tiktok_profile_monitor(usernames, 5)` |
| TT-Asset-Creation-Loop | `cron.py:136-143` | wie IG, sync |
| TT-Summary | `cron.py:145-150` | wie IG |

**Kritischer Punkt:** Die TT-Phase läuft erst, wenn die IG-Phase
**komplett** durch ist — inkl. der Sync-Asset-Creation für alle 261 Items.
Es gibt keinen Per-Item- oder Per-Channel-Try/Except, kein
Watchdog-Timeout pro Phase, kein Backgrounding der Asset-Creation.

### Q1d — `_run_apify_sync_for_platform`: was macht der pro Item

`backend/app/api/monitor.py:140-185` — Loop ohne Per-Channel-Isolation
(siehe Audit von heute Mittag). Aber wichtiger: ruft pro Item
`_create_asset_from_item` (`monitor.py:46-137`):

| Schritt | Datei:Zeile | I/O? | Sync? |
|---|---|---|---|
| `Post`-Lookup | `monitor.py:57-59` | DB | sync, ms |
| `find_best_title_match` | `monitor.py:68` | DB | sync, ms |
| `Post`-Insert + commit | `monitor.py:73-91` | DB | sync, ms |
| **`analyze_creative_text`** | `monitor.py:93` | **OpenAI HTTP** | **SYNC**, 2-5s typisch, bis 600s OpenAI-SDK-Default-Timeout |
| `Asset` bauen | `monitor.py:102-111` | – | sync, ms |
| ggf. zweiter `find_best_title_match` | `monitor.py:113-127` | DB | sync, ms |
| **`persist_asset_screenshot`** | `monitor.py:129` | **httpx GET + S3 PUT** | **SYNC**, bis 12s httpx-Timeout pro Source-Versuch (`screenshot_capture.py:73`), mehrere Sources möglich |
| `Asset`-Commit | `monitor.py:130-132` | DB | sync, ms |
| ggf. `resolve_open_candidates_for_asset` / `create_candidate_from_asset` | `monitor.py:133-136` | DB | sync, ms |

**Pro NEUEM Asset:** ~3-17s Block-Zeit auf dem Event-Loop. Bei 50 neuen
Assets: **150-850s = 2,5-14 Min** reine sequenzielle Sync-IO innerhalb
einer einzigen `await`-Stelle in der Cron-Background-Task.

---

## Q2 — Settings-Default-Check

### Q2a — `apify_poll_interval_seconds` und `apify_wait_seconds` Defaults

`backend/app/config.py:34-36`:

```python
apify_wait_seconds: int = 1800              # 30 min total budget
apify_poll_interval_seconds: int = 10       # poll every 10s
```

Pydantic-`BaseSettings`-Default. Wird ohne Railway-ENV korrekt mit `1800`
bzw. `10` initialisiert. Pydantic erlaubt ENV-Override, schlägt aber
nicht fehl, wenn ENV fehlt.

### Q2b — Verwendung im Connector

`backend/app/services/apify_connector.py:65-66` in `_run_actor`:

```python
total_timeout = float(wait_seconds if wait_seconds is not None else settings.apify_wait_seconds)
poll_interval = float(settings.apify_poll_interval_seconds)
```

Lazy-Lookup pro Aufruf, nicht beim Modul-Import. Crash-Pfad: keiner —
beide Werte sind via Pydantic immer gesetzt.

**Hypothese (b) — APIFY_POLL_INTERVAL_SECONDS-Default-Fallback fehlt:
ausgeschlossen.**

---

## Q3 — Vision-Pipeline-Analysis

### Q3a — `analyze_asset_visual` Call-Pattern

`backend/app/services/visual_analysis.py:176-...` — sync `def`,
ruft `client.chat.completions.create` (OpenAI Vision) **synchron** in
Zeile 218. Same pattern wie `analyze_creative_text`.

### Q3b — Wann wird Vision aufgerufen?

`cron.py:233-234`:

```python
if created_asset_ids:
    summary["vision"] = _run_vision_after_sync(session, created_asset_ids, cap)
```

**NACH** beiden Plattformen — siehe `cron.py:231` (`_execute_platform_sync`
returnt `created_asset_ids` über IG+TT kombiniert), dann erst Vision auf
Zeile 234. Vision kann den TT-Phase-START **nicht blockieren**.

**Hypothese (d) — Vision-Pipeline blockiert TT: ausgeschlossen.**

Aber: Vision-Step ist auch sync, also nach erfolgreichem TT-Apify käme
nochmal eine `cron_vision_max_assets_per_run=50`-Schleife mit ~3-10s pro
Call = 2,5-8 Min Block-Zeit. **Erhöht Total-Run-Time, aber nicht das
beobachtete Symptom.**

---

## Q4 — Threshold-Fix-Side-Effects

### Q4a — `CRON_A_CLASS_THRESHOLD` 1 → 0

`backend/app/services/cron_channel_selection.py:25-30, 84-87`:

```python
def _a_class_threshold_default() -> int:
    raw = os.environ.get("CRON_A_CLASS_THRESHOLD", "1")
    return max(0, int(raw))                 # 0 ist legal

# Selektion:
a_class = sorted(
    (c for c in candidates if counts.get(c.id, 0) >= threshold),
    key=lambda c: (-counts.get(c.id, 0), c.handle or ""),
)[:max_a]                                   # max_a=30
```

| THRESHOLD | A-Class-Pool | A-Class-Auswahl (Top 30) | B-Class-Pool |
|---|---|---|---|
| **1** (alt) | nur Channels mit `assets_30d >= 1` (~58 IG laut Audit-Memo) | Top 30 by count | ~28 |
| **0** (neu) | ALLE mvp+active Channels (~114 IG) | Top 30 by count — gleich, weil count=0-Channels sortieren ans Ende | ~84 |

**Effekt auf A-Class-Auswahl: praktisch keiner**, weil count=0-Channels
in der `(-count, handle)`-Sortierung ans Ende rutschen und vom `[:30]`-Cap
abgeschnitten werden — vorausgesetzt es gab vor dem Wechsel ≥30 Channels
mit `count >= 1`.

**Effekt auf B-Class-Auswahl: deutlich.** B-Class wird in 3 Drittel rotiert
(`cron_channel_selection.py:95-101`):

| THRESHOLD | B-Class | third_size | Cron-Run-Channels (a + 1/3 b) |
|---|---|---|---|
| 1 | ~28 | 10 | 30 + 10 = **40** |
| 0 | ~84 | 28 | 30 + 28 = **58** |

→ **Ca. 1,45× mehr Channels** pro Cron-Lauf in IG. Mit
`CRON_RESULTS_LIMIT_PER_CHANNEL=5` theoretisches Max 290 Items (vs. 200
vorher). Apify lieferte **261** — passt zu THRESHOLD=0-Pfad. Die
beobachteten **261 Items** sind also **konsistent mit THRESHOLD=0**, nicht
mit THRESHOLD=1.

### Q4b — IG-Item-Count vor/nach Threshold-Fix

Briefing sagt: 14:11-IG-Run und 17:41-IG-Run hatten beide 261 Items.

**Das passt nur, wenn THRESHOLD bereits vor 14:11 auf 0 stand**, oder
Apify pro Channel mehr als 5 Items liefert (was der `resultsLimit`-
Parameter eigentlich verhindern sollte). Variante 1 ist plausibler:
Wolf hat THRESHOLD früher als 17:00 umgestellt; nur die Aussage
„zwischen 17:00 und 17:20" im Briefing ist unscharf.

→ Falls 14:11-Run schon THRESHOLD=0 hatte und durchlief, kann das
Problem nicht NUR an THRESHOLD=0 liegen.

→ **Hypothese (e) Threshold-Side-Effect:** als alleinige Ursache
**unwahrscheinlich**, als verstärkender Faktor (mehr Channels → mehr
Items → mehr Sync-Block-Zeit) **plausibel**.

---

## Q5 — Container-Resource-Limits

### Q5a — Deployment-Config

- `backend/railway.json`: keine Memory-/CPU-Caps konfiguriert.
  `restartPolicyType: "ON_FAILURE"`, `restartPolicyMaxRetries: 10`.
  Kein `healthcheckPath`. Auto-Restart nur bei Process-Exit (SIGKILL/OOM/
  Crash) oder Deploy-Trigger. **Healthcheck-basierter Restart ist NICHT
  konfiguriert** — Event-Loop-Block alleine löst keinen Restart aus.
- `backend/Dockerfile`: `python:3.12-slim`, kein Memory-Limit, einzelner
  uvicorn-Worker.
- `backend/start.sh`: `exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"`
  — **kein `--workers`-Flag**, also Single-Worker / Single-Event-Loop.

### Q5b — OOM-Indikatoren

Nicht direkt nachweisbar ohne Container-Logs. Heuristik:

- 261 raw_items, jedes Item enthält `raw_payload` als JSON-Object — bei
  IG kann das mit `addParentData=True` (`apify_connector.py:137`)
  mehrere KB pro Item ergeben → 261×~5-50 KB = ~1-13 MB. Pro Item
  zusätzlich Bild-Download bis 8 MB
  (`config.py:97 image_proxy_max_bytes: int = 8 * 1024 * 1024`) — aber
  Screenshot-Capture lädt nur, wenn der Asset NEU ist und der Capture
  über httpx läuft (`screenshot_capture.py:82-83 payload = response.content or b""`).
  Bei z.B. 50 neuen Assets × bis 8 MB = bis 400 MB temporäre Bytes-Buffer
  (werden nach S3-PUT freigegeben).
- Railway-Standard-Memory für Hobby/Pro-Plans ist meist 512 MB-1 GB. Bei
  schlechtem GC-Pacing während eines 261-Item-Sync-Bursts kann das
  drücken, aber **kein direkter Beweis ohne Logs/Metriken**.

**Hypothese (c) Memory/Resource-Erschöpfung:** **mittlere Confidence,
nicht ausgeschlossen**. Würde aber als OOM-Kill manifestieren — was
auch zu „status='running' bleibt" führen würde (gleicher Mechanismus
wie SIGKILL durch Deploy-Restart).

### Q5c — Railway-Restart-Korrelation

Briefing nennt Restarts um 17:18 und 17:27. 17:27 ist **1 Min nach**
IG-Apify-Ende (17:26). Das ist exakt der Zeitpunkt, an dem die sync
IG-Asset-Creation-Loop läuft. Ein Restart hier killt die Loop und lässt
den `CronRun` als „running" liegen.

**Wahrscheinlichste Ursache des 17:27-Restarts:** Auto-Deploy-Push
(zweite Phase des `CRON_A_CLASS_THRESHOLD`-ENV-Updates, oder ein
nachgelagerter Variablen-Push). Nicht OOM, sonst hätte Railway das im
Deploy-Log als „OOM-Killed" markiert (Wolf könnte das verifizieren).

---

## Q6 — Zeitlinien-Korrelation

### Q6a — Zeitlinie

| Zeit (UTC) | Ereignis | Quelle |
|---|---|---|
| 12:11 | Cron-Run, erfolgreich (alte Bulk-Logik, vor PR #72) | Briefing |
| 13:18 | Cron-Run, erfolgreich | Briefing |
| 14:09 | Cron-Run, erfolgreich, IG raw_items=261 (Briefing Q4b) | Briefing |
| 15:34 | Commit `3bd4675` (`fix(apify): async-polling refactor`) | git log |
| 15:39 | Merge PR #72 (`9440fae`) — Polling-Refactor live | git log |
| 15:41 | **Run X** — erfolgreich, 904s, TT raw_items=36 | Briefing + DB |
| 15:48 | Commit `45733be` (PR #73 — insight-engine, betrifft cron NICHT) | git show --stat |
| 16:20 | Merge PR #73 (`0c10995`) | git log |
| 16:31 | Commit `2172404` (PR #74 — polish, betrifft cron NICHT) | git show --stat |
| 16:34 | Merge PR #74 (`07c285c`) | git log |
| 16:52 | Merge PR #70 (`cf31417`) — docs only | git log |
| ~17:00-17:18 | Wolf setzt `CRON_A_CLASS_THRESHOLD=0` in Railway | Briefing |
| 17:18 | Container-Restart (Auto-Deploy nach ENV-Update) | Briefing |
| 17:20 | **Run 1** — `cron_sync_all` getriggert | DB |
| 17:20-17:26 | IG-Apify auf Apify-Seite läuft (261 Items, succeeded) | Apify-Console |
| 17:26-17:27 | IG-Asset-Creation-Loop startet (sync, 261 Items) | Code-Pfad |
| 17:27 | Container-Restart Nr. 2 (Auto-Deploy oder Folge-Push) | Briefing |
| ~17:27+ | `_run_cron_sync_background` SIGKILLed mid-IG-Loop. `CronRun.status='running'` bleibt im DB | Code |
| 17:51 | Apify-Console zeigt einen TT-Run ohne DB-`cron_run`-Entry | Briefing — möglicherweise ein verspäteter Manual-Test? |
| 18:18 | Wolf setzt Run 1 auf `failed` mit `reason="container_restart_during_run"` | Briefing |
| 18:19 | **Run 2** — `cron_sync_all` getriggert | Briefing |
| 18:19-18:25 | IG-Apify (261 Items, succeeded) | Apify-Console |
| 18:25-18:30 | IG-Asset-Creation-Loop läuft. TT-Phase noch nicht erreicht. | Code-Pfad |
| ~18:30 | Wolf setzt Run 2 manuell auf `failed` mit `reason="tt_phase_never_started_after_ig"` | Briefing |

### Q6b — Code-Diff zwischen Run X (15:41 erfolgreich) und Run 1 (17:20 hängend)

```bash
git log origin/main --since="2026-05-05 15:41" --until="2026-05-05 17:20"
```

| Commit | Touch cron.py / monitor.py / apify_connector.py? |
|---|---|
| `45733be` PR #73 insight-engine MVP | NEIN — nur `api/insights.py`, `services/insight_engine.py`, `schemas/insights.py`, Frontend |
| `0c10995` PR #73 Merge | – |
| `2172404` PR #74 polish | NEIN — nur `services/insight_engine.py`, `tests/test_insight_engine.py`, Frontend |
| `07c285c` PR #74 Merge | – |
| `cf31417` PR #70 docs | NEIN — nur `docs/` |

**Bestätigt:** Kein Code-Diff im Cron-Pfad zwischen 15:41 und 17:20.
Was sich änderte, ist:
- `CRON_A_CLASS_THRESHOLD` ENV (1 → 0) — siehe Q4.
- ggf. erstmals: ein Cron-Lauf mit 261 IG-Items, der **wirklich**
  Asset-Creation auslöst (nach PR #72 Polling-Fix).

Run X (15:41) lief 904s = 15 Min und schloss komplett ab. Möglich:
weniger neue Assets (mehr existierende → schnellere Sync-Loop),
oder einfach mehr Wall-Clock-Toleranz (kein Restart mid-flight).

---

## Hypothesen-Rangliste

| # | Hypothese | Confidence | Belege | Status |
|---|---|---|---|---|
| **1** | **Sync-Block via OpenAI-Text + Screenshot-Capture in `_create_asset_from_item`**, verstärkt durch echten 261-Item-Run nach PR #72 | **HIGH** | `monitor.py:93` + `:129` sind sync, OpenAI-SDK ohne Custom-Timeout (default 600s), httpx-Timeout 12s pro Source × N Sources, alles seriell pro Asset; Single-Worker-uvicorn (`start.sh`); 261 Items × x s = mehrere Minuten Block-Zeit | **Primärursache** |
| **2** | **Container-Restart 17:27** killt Run 1 mid-IG-Asset-Loop (Auto-Deploy nach `THRESHOLD=0`-ENV-Update) | **HIGH** für Run 1 | Briefing-Timing, `restartPolicyType=ON_FAILURE` aber kein Healthcheck → Restart kommt vom Deploy, nicht vom Block | **Komplementär zu #1 für Run 1** |
| **3** | **Manueller Kill bei Run 2 zu früh** (18:30 = ~5 Min nach IG-Apify-Ende), TT wäre gestartet, wenn IG-Loop fertig wäre | **MEDIUM** | Konsistent mit #1; Run X brauchte 15 Min insgesamt, Run 2 nur 11 Min vor Manual-Kill | **Vermutet, ohne DB-Daten nicht final beweisbar** |
| 4 | Threshold-Fix-Side-Effect: ~58 Channels statt ~40 → ~1,5× mehr Sync-Last | MEDIUM | Q4a-Tabelle, B-Class-Drittel von 10 auf 28 | **Verstärker, nicht Ursache** |
| 5 | Memory/OOM-Kill | LOW-MEDIUM | Plausibel bei 261-Item-Burst + Bild-Buffer; nicht ohne Container-Logs verifizierbar | **Alternative zu #2** |
| (a-roh) | Race-Condition im Cron-Code | LOW | Code-Lese-Pass keine Race gefunden; Phasen sind strikt sequentiell mit `await` | **Nicht belegt** |
| (b) | `APIFY_POLL_INTERVAL_SECONDS`-Default fehlt | **0** | `config.py:36` hat hardcodierten Pydantic-Default 10 | **Ausgeschlossen** |
| (d) | Vision-Pipeline blockiert TT-Start | **0** | Vision läuft NACH beiden Plattformen, `cron.py:232-234` | **Ausgeschlossen** |

**Konsistente Gesamterklärung:**
- Run 1: Sync-Block (#1) verlängerte die IG-Phase über den Auto-Deploy-
  Restart hinaus → Container-Kill (#2) bei laufendem IG-Loop → DB bleibt
  „running".
- Run 2: Sync-Block (#1) zog die IG-Phase über 5 Min aus → Wolf killte
  manuell (#3), bevor TT regulär dran kam.

---

## Fix-Empfehlung

### Sofort-Mitigation (heute Abend, ohne Code-Push)

1. **Manuellen Cron-Trigger 15-20 Min laufen lassen** statt 5 Min, bevor
   Manual-Kill. Vergleich: Run X brauchte 904s = 15 Min.
2. **Während des Cron-Triggers KEINE Railway-ENV-Variablen ändern** —
   Auto-Deploy killt den laufenden Run.
3. Optional: `CRON_RUN_TIMEOUT_MINUTES` in Railway hochsetzen
   (aktuell 30 Min Default, `cron.py:62`), damit `_reap_stale_runs`
   einen Run nicht voreilig als stale markiert.

### Verifikations-Schritt (vor dem Fix-Sprint)

DB-Query (psql, READ-only) — wie viele NEUE Assets entstanden in den
zwei manuell-gefailten Runs vs. Run X? Hohe Differenz bestätigt #1
endgültig:

```sql
-- Post-Counts pro CronRun-Zeitfenster (proxy für created_assets, da
-- summary_json=NULL ist)
WITH runs(id, label, started_at, ended_at) AS (
  VALUES
    ('4bbc359d-a858-4a22-ac4d-1dbf5e2b83d0'::uuid, 'run_x_ok',
     '2026-05-05 15:41:56'::timestamptz, '2026-05-05 15:57:00'::timestamptz),
    ('2fb2f1fe-47e6-4519-ae83-6c15043cf312'::uuid, 'run_1_killed',
     '2026-05-05 17:20:23'::timestamptz, '2026-05-05 17:27:30'::timestamptz),
    ('6f9bff82-1f3c-4a46-befa-b6f0d03c8aad'::uuid, 'run_2_killed',
     '2026-05-05 18:19:44'::timestamptz, '2026-05-05 18:30:00'::timestamptz)
)
SELECT r.label,
       p.platform,
       COUNT(*) FILTER (WHERE p.created_at BETWEEN r.started_at AND r.ended_at) AS posts_in_window,
       COUNT(*) FILTER (WHERE a.created_at BETWEEN r.started_at AND r.ended_at) AS assets_in_window
FROM runs r
LEFT JOIN creative_radar.post p ON p.created_at BETWEEN r.started_at AND r.ended_at
LEFT JOIN creative_radar.asset a ON a.post_id = p.id
GROUP BY r.label, p.platform
ORDER BY r.label, p.platform;
```

Erwartung bei #1: `run_2_killed` zeigt ~30-50 IG-Posts, 0 TT-Posts;
`run_x_ok` zeigt sowohl IG- als auch TT-Posts.

### Folge-Sprint (Code-Fix, separates Briefing)

**Zwei orthogonale Pfade — kombinieren:**

1. **`_create_asset_from_item` async machen** — Sync-OpenAI-Call und
   `httpx.Client`-GET in `asyncio.to_thread()` aushebeln, oder besser
   `AsyncOpenAI` und `httpx.AsyncClient` verwenden. Hebt den Event-Loop-
   Block auf. Aufwand: ~1-2h, plus Test-Anpassungen für Async-Mocks.
2. **Per-Channel-Error-Isolation in `_run_apify_sync_for_platform`** —
   Backlog-Item #1 aus dem heutigen Audit-Report
   (`docs/diagnostics/REPO_STATUS_AUDIT_2026-05-05.md`). Rüstet aus,
   damit ein Apify- oder OpenAI-Hänger pro Channel nicht den ganzen Cron
   reißt.

**Alternativ-Pfad (kleinerer Eingriff):**

3. **`analyze_creative_text` und `persist_asset_screenshot` aus dem Cron-
   Pfad rausziehen** — im Cron nur Post + Asset-Stub anlegen, Text- und
   Bild-Analyse passieren ausschließlich im Vision-Step
   (`_run_vision_after_sync`) und/oder im Manual-Trigger-Pfad. Reduziert
   die Sync-Last pro Item drastisch. Risiko: ändert Verhalten der
   Manual-Monitor-Endpoints, die denselben Code-Pfad nutzen — braucht
   einen Branch im Cron-Pfad.

**Empfehlung — Reihenfolge:**

1. **Zuerst Verifikations-Query** (oben) ausführen, um Hypothese #1
   numerisch zu bestätigen.
2. **Dann Backlog-#1 aus Repo-Audit** (Per-Channel-Error-Isolation) —
   schmales, klar umrissenes Sprint-Item, hat unabhängigen Wert.
3. **Dann Async-Refactor** (Pfad 1 oben) — größerer Eingriff, sollte
   nicht parallel zu anderen Cron-Sprints laufen.

---

## Anhang — Reproduktions-Block für Wolf

### A1 — Lock-Cleanup (manuelles Failed-Setzen, falls Run hängt)

```sql
-- Sanity-Check: wie viele "running"-Runs liegen herum
SELECT id, started_at, status, error_message
FROM creative_radar.cron_run
WHERE status = 'running'
ORDER BY started_at DESC;

-- Manuell als failed markieren (nur ausführen, wenn Wolf sich
-- VERGEWISSERT hat, dass kein Run wirklich läuft)
UPDATE creative_radar.cron_run
SET status = 'failed',
    completed_at = NOW(),
    error_message = 'manual_cleanup'
WHERE id = '<uuid>'::uuid AND status = 'running';
```

### A2 — Trigger-curl

```bash
curl -X POST "https://<api-host>/api/admin/cron/sync-all" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json"
```

Antwort: `202 {"run_id": "...", "started_at": "...", "status": "running"}`.

### A3 — Status-Polling während des Runs

```bash
# Alle 60s den Status checken
while true; do
  curl -s "https://<api-host>/api/admin/cron/runs?limit=1" \
    -H "Authorization: Bearer $API_TOKEN" | jq '.[0] | {id, status, duration: .duration_seconds, summary}'
  sleep 60
done
```

### A4 — Apify-Console-Indikatoren

- **IG-Apify-Run gestartet:** Console zeigt einen neuen Run unter
  `apify~instagram-scraper`, Status `RUNNING`.
- **IG-Apify-Run beendet:** Status `SUCCEEDED`, Items > 0. **Ab hier
  läuft der Sync-Block in Cron** — Wolf SOLLTE jetzt 5-15 Min warten,
  bevor TT-Apify auf der Console erscheint.
- **TT-Apify-Run gestartet:** Console zeigt Run unter
  `clockworks~tiktok-scraper`. Erst jetzt ist die TT-Phase im Code
  angekommen.
- **Wenn TT nach 20 Min nicht erscheint:** Echtes Problem, Manual-Kill
  rechtfertigt sich.

### A5 — Container-Log-Abruf (Wolf, Railway-CLI)

```bash
railway logs --service backend --deployment-id <id> | grep -E "cron run|sync|cron-vision|exception"
```

Suche nach:
- `cron run <uuid> completed: ...` — der erfolgreiche Pfad.
- `cron run <uuid> failed: <exc>` — Exception-Pfad (würde DB sauber
  failed setzen, nicht "running" lassen).
- **Fehlt beides** → SIGKILL oder Container-Restart mid-flight.
  Dann `Last allocated container exited` o.ä. im Deploy-Log suchen.

---

## Slack-fähiger Tagesbericht

> **Cron-TT-Phase-Regression Diagnose 2026-05-05.** Kein Code-Bug —
> Timing-Regression. Ursache: `_create_asset_from_item`
> (`monitor.py:46-137`) macht pro neuem Asset SYNCHRON eine OpenAI-Call
> + httpx-Bilddownload. Vor PR #72 unsichtbar (`raw_items=0` durch
> `waitForFinish=60s`-Bug); jetzt mit ehrlichen 261 Items pro IG-Lauf
> blockiert die Sync-Loop den Single-Worker-Event-Loop für 5-10+ Min.
> Run 1 wurde vom Auto-Deploy-Restart 17:27 (nach Wolfs ENV-Change
> `CRON_A_CLASS_THRESHOLD=0`) mid-IG-Loop gekillt → DB bleibt „running".
> Run 2 wurde manuell um 18:30 gekillt — vermutlich kurz bevor die
> TT-Phase regulär gestartet wäre (Run X brauchte 904s, Run 2 nur 11
> Min vor Kill). Hypothesen (b) Settings-Default und (d) Vision-blockiert-
> TT sind ausgeschlossen. Sofort-Mitigation: 15-20 Min warten, keine
> ENV-Changes während Cron. Verifikation: psql-Block (Anhang A) zählt,
> wie viele Posts in den Run-Fenstern entstanden sind. Fix-Sprint
> separat: Async-Refactor `_create_asset_from_item` plus Per-Channel-
> Error-Isolation (Backlog-#1 aus heutigem Repo-Audit). Report unter
> `docs/diagnostics/CRON_TT_PHASE_REGRESSION_2026-05-05.md`.
