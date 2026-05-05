# Repo-Status-Audit Creative Radar — 2026-05-05

> **Modus:** READ-ONLY. Keine Code-Änderungen, kein DB-Zugriff im Container.
> Scope: Stand `origin/main` nach den heutigen 6 Merges (#69, #70, #71, #72,
> #73, #74). Alle Aussagen sind mit Quellen verlinkt (Datei:Zeile,
> Commit-SHA, PR-Nummer); Behauptungen ohne Quelle wurden weggelassen.

---

## Executive Summary

- **Sprint-Tracks heute (6 PRs, alle gemerged):** 0a Title-Tagging-Diagnose
  (#70), 0a Scraper-Coverage-Diagnose (#71), Whitelist-Expansion + Hygiene
  (#69), 0d Apify Async-Polling (#72), Sprint 1 Insight-Engine MVP (#73),
  Sprint 1 Polish (#74).
- **Code auf `origin/main`:** Alle 6 PRs sind in `origin/main` gemerged
  (HEAD `cf31417` 16:52 UTC). Lokales `main` war veraltet → nach
  `git fetch origin main` ist die Realität konsistent (forced update von
  `a7faa84` auf `cf31417`).
- **Drift identifiziert:** Kein semantischer Drift gegenüber den
  Briefing-DoDs der heutigen Sprints. Alle Code-Pfade aus 0d, Sprint 1 und
  Polish sind im erwarteten Zustand. Drei Backlog-Items sind explizit
  benannt und im Code messbar (siehe Q5).
- **Production-Health:** Im Audit-Container kein DB-Zugriff verfügbar
  (kein DATABASE_URL/`PG*`-ENV gesetzt). Q3 deshalb leer + psql-Block für
  Wolf-Verifikation im Anhang.
- **Sprint-2-Readiness:** **JA, mit Scope-Reduktion.** Insight-Engine ist
  bis auf die `PAIRS`-Konstante Pair-agnostisch (Code-Pfade, Frontend-Route
  via Regex `/insights/weekly/:pair`). Empfehlung: erst die 2-3 Pairs mit
  bestätigter TT-Coverage zuschalten, parallel die Per-Channel-Error-
  Isolation als Backlog-#1 abräumen, bevor die Pairs mit dünner Coverage
  reingenommen werden.

---

## Q1 — Git-History (heute, alle Branches)

Alle Commits stammen vom 2026-05-05 (UTC). Sortiert chronologisch
absteigend. PR-Spalte aus `mcp__github__list_pull_requests` (alle 6
heutigen PRs sind `state=closed`, `merged_at` gesetzt).

| Commit | Zeit (UTC) | Subject | PR | Auf `origin/main`? |
|---|---|---|---|---|
| `cf31417` | 16:52 | Merge PR #70 — title-tagging diagnostic | **#70** | ✅ HEAD |
| `07c285c` | 16:34 | Merge PR #74 — insight-engine polish | **#74** | ✅ |
| `2172404` | 16:31 | fix(insight-engine): polish — max_tokens 8000, drop 'unknown', frontend slow-hint | (in #74) | ✅ |
| `0c10995` | 16:20 | Merge PR #73 — Sprint 1 MVP | **#73** | ✅ |
| `45733be` | 15:48 | feat(insight-engine): Sprint 1 MVP — weekly brief endpoint + view (warnerbros) | (in #73) | ✅ |
| `9440fae` | 15:39 | Merge PR #72 — apify async-polling | **#72** | ✅ |
| `3bd4675` | 15:34 | fix(apify): async-polling refactor for runs longer than 60s | (in #72) | ✅ |
| `fd0bd42` | 13:08 | Merge PR #71 — scraper coverage diagnostic | **#71** | ✅ |
| `ec78476` | 12:58 | docs(diagnostics): scraper coverage diagnostic | (in #71) | ✅ |
| `46d5c5a` | 12:26 | docs(diagnostics): title-tagging status (read-only diagnose) | (in #70) | ✅ |
| `d8aa231` | 12:10 | fix(model): add UK to Market enum (sync with DB ALTER TYPE from #69) | direct on main | ✅ |
| `3aea749` | 09:37 | fix(migration): use `public` schema for market/priority CAST (schema drift) | direct on main | ✅ |
| `8391388` | 09:33 | fix(migration): re-assign @netflix posts before DELETE (FK violation fix) | direct on main | ✅ |
| `0363267` | 09:16 | fix(migration): use `public.market` for ALTER TYPE (schema drift fix) | direct on main | ✅ |
| `fda8ac6` | 08:57 | Merge PR #69 — whitelist expansion + data hygiene | **#69** | ✅ |
| `51d8d34` | 08:40 | feat(channels): whitelist expansion + data hygiene | (in #69) | ✅ |
| `fdbdd88` | 07:52 | docs(research): v2.3 — Phase-1-Output + Schema-Fixes | direct on main | ✅ |

**Branch-Inventur (lokal + remote):**

```
* claude/repo-status-audit-z9a44     (audit-only branch, this report)
  main                                (after fetch: synced with origin/main @ cf31417)
  remotes/origin/claude/repo-status-audit-z9a44
  remotes/origin/main                 @ cf31417
```

Keine zurückgebliebenen Feature-Branches im lokalen Clone — alle PR-Quell-
Branches wurden GitHub-seitig gemerged + gelöscht.

**Beobachtung — Mergereihenfolge vs. PR-Nummer:** PR #70
(title-tagging-diag) wurde erst um 16:52 gemerged, also nach #71/#72/#73/#74,
obwohl früher geöffnet. Die History ist linear und konsistent (alle PRs auf
`base=main`, kein Merge-Commit-Drift).

---

## Q2 — Active Code auf `origin/main`

### Q2a — Title-Tagging-Diagnose (PR #70)

- **Datei vorhanden:** `docs/diagnostics/TITLE_TAGGING_STATUS_2026-05-05.md`
  (672 Zeilen, Commit `46d5c5a`, in #70 gemerged).
- **Reine Analyse, keine Code-Änderung.** Diff in #70 enthält genau eine
  Datei: den Markdown-Report.
- **Kern-Befunde:**
  1. **Schema-Anomalie:** `creative_radar.post` hat **keine** `title_id`-
     Spalte; Title-Tagging existiert ausschließlich auf Asset-Ebene
     (`asset.title_id`). Q1 in der ursprünglichen Form ist nicht
     beantwortbar (Quelle: `entities.py:60-142`,
     `cf842bbfaeb5_baseline_from_sqlmodel_state.py`).
  2. **Tagging-Mechanismus 3-stufig:** synchron beim Asset-Insert
     (`whitelist_matcher.find_best_title_match` mit
     `is_safe_auto_match`-Guard ≥0.95), opportunistisch im Vision-Lauf
     (`visual_analysis._find_title_match`, naive substring auf
     ocr_text+caption), nachträglich via
     `POST /api/admin/titles/rematch-assets`
     (`rematch_unassigned_assets`).
  3. **Kein Auto-Rematch-Cron** — größter struktureller Quick-Win
     (~10 Zeilen Patch in `cron.py:222`).
  4. **Empfehlung KF5 als Decision-Tree** (Variante A „Format-Trends" vs.
     Variante B „Pro-Title-Briefing") — abhängig von noch zu erhebenden
     C₁/C₂/C₃-Schwellen aus den psql-Outputs.
- **DB-Output noch offen:** Tabellen-Slots `_<wolf>_` warten auf Wolfs psql-
  Run (Read-only Q1-Q5 Blocks im Report sind execution-ready).

### Q2b — Scraper-Coverage-Diagnose (PR #71)

- **Datei vorhanden:** `docs/diagnostics/SCRAPER_COVERAGE_2026-05-05.md`
  (719 Zeilen, Commit `ec78476`).
- **Reine Analyse, keine Code-Änderung.**
- **Kern-Befunde:**
  1. **A-Class-Cap (`CRON_A_CLASS_MAX=30`)** ist eine harte Throttle. Bei
     ~147 mvp-Channels = max 30 A-Class + 1/3 B-Class pro Run → ~9-Tage-
     Coverage-Zyklus.
  2. **Apify-Errors sind nicht persistiert.** `cost_log` wird nur bei
     erfolgreichen Runs geschrieben (`apify_connector.py:117-121`); Per-
     Channel-Failures landen ausschließlich in Container-Logs und in
     `cron_run.error_message` (Top-Level, nicht pro Channel). →
     Hypothese (c) "Apify-Fail per Channel" ist strukturell nicht aus der
     DB beantwortbar.
  3. **Identifier-Drift-Risiko:** Migration `e5d8f1a36b40` (PR #69) setzte
     17 TT-Channels mit Handles teils CamelCase / Punkten / URL-Slugs
     (`marvel_uk`, `sonypictures.uk`, `20thcentury`); Apify-Actor
     `clockworks~tiktok-scraper` ist handle-sensitiv.
  4. **4 Hypothesen mit parametrisierten Schwellen** + Decision-Tree für
     Folge-Briefing 0b. Default-Reihenfolge: erst (a) prüfen (5 Min
     Trigger + abwarten), dann (c) angehen (Per-Channel-Error-Isolation).

### Q2c — 0d Apify Async-Polling (PR #72)

- **Auf `origin/main`:** ✅ (Merge `9440fae` 15:39 UTC).
- **`_run_actor` refactored** (`backend/app/services/apify_connector.py:48-122`):
  - POST ohne `waitForFinish` (server-side cap = 60s entfällt).
  - `_poll_until_terminal()` schleife (`apify_connector.py:31-45`)
    pollt `/actor-runs/{id}` alle `apify_poll_interval_seconds`,
    bricht bei einem Status in `TERMINAL_STATUSES = {SUCCEEDED, FAILED,
    ABORTED, TIMED-OUT, TIMED_OUT}`.
  - `asyncio.wait_for(...)` enforced den Total-Timeout
    (`apify_connector.py:88-96`); `asyncio.TimeoutError` wird in einen
    sprechenden `RuntimeError` gewrappt.
  - Dataset-Read passiert erst NACH `status == "SUCCEEDED"`
    (`apify_connector.py:104-122`).
- **`config.py`** (`backend/app/config.py:27-36`):
  - `apify_wait_seconds: int = 1800` (war 60 — Doku-Kommentar L28-33
    erklärt die geänderte Semantik: jetzt Total-Budget, nicht per-Request-
    Timeout).
  - `apify_poll_interval_seconds: int = 10` — neu.
- **Tests** (`backend/app/tests/test_apify_connector.py`, 257 Zeilen,
  8 Testfälle):
  - `test_run_actor_succeeds_after_multiple_polls`
  - `test_run_actor_raises_on_total_timeout`
  - `test_run_actor_raises_on_failed_status`
  - `test_run_actor_raises_on_aborted_status`
  - `test_run_actor_raises_on_timed_out_status`
  - `test_run_actor_returns_empty_when_post_lacks_run_id`
  - `test_run_actor_returns_empty_when_succeeded_run_has_no_dataset`
  - `test_run_actor_explicit_wait_seconds_overrides_settings`
- **Breaking-Change-Risk:** keiner für Caller — `wait_seconds` kwarg-
  Position blieb erhalten, nur die Semantik (Total-Budget) ist neu, was im
  Docstring `apify_connector.py:55-64` dokumentiert ist.

### Q2d — Sprint 1 Insight-Engine MVP (PR #73)

- **Auf `origin/main`:** ✅ (Merge `0c10995` 16:20 UTC).
- **Backend-Service** (`backend/app/services/insight_engine.py`, 28969 B,
  ~720 Zeilen, Commit `45733be`):
  - `PAIRS`-Registry hardcoded (`insight_engine.py:58-71`) — derzeit nur
    `warnerbros` mit zwei Channels (`warnerbros` US, `warnerbrosdeutschland`
    DE), Plattform `tiktok`. Doc-Kommentar L52-57 markiert das explizit
    als „Sprint-2-promotes-this-to-DB-config".
  - Funktionen:
    `_extract_hashtags` (L136), `_engagement_sum` (L165),
    `_duration_bucket` (L174), `_excerpt` (L186), `_find_channel` (L196),
    `_channel_stats` (L213), `_cross_market_matches` (L373),
    `_title_coverage` (L484), `aggregate_pair` (L550),
    `_build_user_prompt` (L616), `_strip_codefence` (L630),
    `_estimate_cost_usd` (L645), `generate_weekly_report` (L653).
  - **Pair-agnostisch außer `PAIRS`:** `aggregate_pair` /
    `_channel_stats` / `_cross_market_matches` / `_title_coverage` lesen
    Channel/Markt aus dem Pair-Spec, keine hardcoded warnerbros-Strings.
- **Backend-API** (`backend/app/api/insights.py:28-61`):
  - `GET /api/insights/weekly?pair=<key>&window_days=<int>&dry_run=<bool>`
  - Validierung: `pair not in PAIRS` → 404.
  - Anthropic-Fehler-Mapping: `AnthropicAuthError`→401,
    `AnthropicRateLimitError`→429, `AnthropicAPIError`→502.
- **Schema** (`backend/app/schemas/insights.py:140-143`):
  `InsightReport` exposet `aggregation: PairAggregation`,
  `llm_output: Optional[LLMReport]`, `raw_llm_text` (Fallback).
- **Frontend-Komponente** (`frontend/src/InsightWeekly.jsx`, 320 Zeilen):
  - Components: `CoverageBanner`, `ChannelStatsCard`, `CrossMarketCard`,
    `LLMOutput`, `InsightWeekly` (Default-Export).
  - Coverage-Banner mit Tone-Stufen `cov >= 70 ? good : >= 30 ? warn : bad`
    (`InsightWeekly.jsx:24-26`) + Eyebrow (Window-Days, KW/Year), Pills
    (model, cost-estimate, dry-run).
- **Frontend-Routing** (`frontend/src/App.jsx:11-18`):
  - Mini-Router via `path.match(/^\/insights\/weekly\/([\w.-]+)\/?$/)` —
    der Capture-Group `pair` ist generisch, **kein hardcoded warnerbros**.
- **Tests** (`backend/app/tests/test_insight_engine.py` 387 Zeilen, 12
  Testfälle; `test_insights.py` 125 Zeilen, 8 Testfälle):
  - `test_aggregate_basic_counts_and_top_post`,
    `test_hashtag_frequency_is_lowercased_and_counted`,
    `test_duration_buckets`,
    `test_cross_market_match_picks_up_shared_match_key`,
    `test_cross_market_excludes_unknown_match_key` (← Polish #74),
    `test_coverage_pct_reflects_assets_with_title`,
    `test_unknown_pair_raises`,
    `test_missing_channels_record_notes_but_dont_crash`,
    `test_window_filters_old_posts`,
    `test_dry_run_skips_llm_and_returns_aggregation`,
    `test_generate_with_mocked_llm`,
    `test_generate_handles_codefence_wrap`,
    `test_generate_surfaces_raw_text_on_parse_failure`.

### Q2e — Sprint 1 Polish (PR #74)

- **Auf `origin/main`:** ✅ (Merge `07c285c` 16:34 UTC).
- **`max_tokens=8000` Default** (`insight_engine.py:660`), durchgereicht zu
  `messages_create_text(..., max_tokens=max_tokens)` in `L711` (zuvor
  `4000` laut Commit-Message von `2172404`).
- **'unknown'-Filter im Cross-Market-Join**
  (`insight_engine.py:412-444`):
  - SQL-Where-Klausel: `Asset.de_us_match_key IS NOT NULL AND
    LOWER(Asset.de_us_match_key) != 'unknown'` für DE- und US-Asset-
    Selects (`L423`, `L431`).
  - Python-Side-Filter via `_MATCH_KEY_EXCLUDED = {"unknown", ""}` für die
    `de_by_key` / `us_by_key` Dicts (case-insensitive Strip).
  - Kommentar L412-415 erklärt: der Match-Key-Builder schreibt "unknown"
    als Sentinel, Joins darauf produzieren spurious cross-market-Matches.
- **Frontend-Status-Machine** (`InsightWeekly.jsx:208-235`):
  - States: `idle | loading | slow | done | error` (`L211`).
  - 60s-Slow-Hint via `SLOW_THRESHOLD_MS = 60_000`
    (`InsightWeekly.jsx:206`); `setTimeout` schaltet `loading → slow`
    (`L222`); UI rendert Card "Dauert länger" L267-272.
  - Keine `AbortController` — bewusst, Kommentar L202-205 begründet das
    (Opus-Calls dürfen 60-90s dauern, Abort wäre misleading „failed").
- **Test** `test_cross_market_excludes_unknown_match_key`
  (`test_insight_engine.py:210-239`) — Mixed-Case-Sentinel `"Unknown"` /
  `"unknown"` werden ausgeschlossen, der legit `mk2-trailer-1` bleibt
  drin.

---

## Q3 — Production-Realität

**DB-Zugriff im Audit-Container nicht verfügbar.** `which psql` zeigt
`/usr/bin/psql`, aber keine `DATABASE_URL` / `PG*`-ENV-Variablen sind
gesetzt — nur `CLAUDE_CODE_*` und `ANTHROPIC_*`.

→ Wolf-Verifikations-Block siehe **Anhang**.

Empfohlene Fragen für die manuelle Verifikation:

1. **Letzter erfolgreicher Cron-Run** mit `started_at`, `completed_at`,
   `duration_seconds`, `summary_json["platforms"]["instagram"]["raw_items"]`
   und `summary_json["platforms"]["tiktok"]["raw_items"]` — verifiziert,
   ob das 0d-Polling-Fix für lange IG-Runs greift.
2. **mvp=true Channel-Counts** pro Plattform/Markt — verifiziert die
   Behauptung „147 mvp-Channels" aus dem Scraper-Coverage-Briefing.
3. **TT-Channels mit 0 Posts in den 7 Tier-A-Pairs** — Sprint-2-Risiko-
   Check (siehe Q6).

---

## Q4 — Drift-Analyse: DoD-Checks

### 0d Apify Async-Polling (PR #72) — DoD aus Briefing

| DoD | Status | Quelle |
|---|---|---|
| `_run_actor` refactored auf Async + Polling | ✅ | `apify_connector.py:48-122` |
| `config.py: apify_wait_seconds` dokumentiert als Total-Timeout | ✅ | `config.py:28-34` (Doc-Kommentar erklärt Semantik-Wechsel) |
| `config.py: apify_poll_interval_seconds` neu | ✅ | `config.py:35-36`, Default 10s |
| Existierende Tests grün (cron_sync, asset_screenshot, alembic) | ⚠️ | nicht im Container ausführbar; zur Verifikation siehe Anhang `pytest`-Block |
| Neue Tests für Polling-Verhalten grün | ⚠️ | 8 Tests in `test_apify_connector.py` vorhanden; Run nicht im Container |
| PR mit klarem Commit-Message + Verlinkung Apify-Docs | ✅ | Commit-Message + Code-Kommentar erwähnen "waitForFinish caps at 60s" |

**Drift:** keiner.

### Sprint 1 Insight-Engine (PR #73) — DoD aus Briefing

| DoD | Status | Quelle |
|---|---|---|
| Endpoint `GET /api/insights/weekly?pair=warnerbros` produziert validen `InsightReport` | ✅ Code-strukturell | `api/insights.py:28-61` + `schemas/insights.py:140-143` |
| Frontend-Route `/insights/weekly/warnerbros` rendert | ✅ | `App.jsx:13-15` (Regex `^/insights/weekly/([\w.-]+)/?$`) → `InsightWeekly pair={...}` |
| Wolf-Ping zur Quality des ersten Reports beantwortet (A-Grade per Wolf-Statement) | ⚠️ Außerhalb des Audit-Scopes (Wolf bestätigt) | — |
| Coverage-Caveat-Banner zeigt korrekt | ✅ | `InsightWeekly.jsx:23-37` (`cov_pct → tone good/warn/bad` mit Pills) |

**Drift:** keiner. Hinweis: `aggregate_pair` schreibt explizit Notes
(„Datenbasis DE schwach", „Keine de_us_match_key-Treffer im Fenster")
in das Aggregat (`insight_engine.py:589-594`), die das Frontend in der
`insight-notes`-Card rendert (`InsightWeekly.jsx:291-296`).

### Sprint 1 Polish (PR #74) — DoD aus Briefing

| DoD | Status | Quelle |
|---|---|---|
| `max_tokens`-Bump auf 8000 | ✅ | `insight_engine.py:660` |
| `cross_market_matches` filtert `'unknown'` | ✅ | `insight_engine.py:412-444` (SQL + Python) |
| Frontend-Status-Machine | ✅ | `InsightWeekly.jsx:211` (`'idle' | 'loading' | 'slow' | 'done' | 'error'`) |
| 60s-Slow-Hint vorhanden | ✅ | `InsightWeekly.jsx:206`, `:222`, `:267-272` |
| Test `test_cross_market_excludes_unknown_match_key` | ✅ | `test_insight_engine.py:210-239` |

**Drift:** keiner.

---

## Q5 — Backlog-Inventur

Items, die heute explizit als Backlog notiert wurden und im Code messbar
sind. Quelle und Priorität sind aus den Briefings + Codebase abgeleitet.

| # | Item | Quelle | Codebase-Beleg | Priorität |
|---|---|---|---|---|
| 1 | **Per-Channel-Error-Isolation in `_run_apify_sync_for_platform`** | Scraper-Cov-Diag § Q4.4, `SCRAPER_COVERAGE_2026-05-05.md:651-656` | `monitor.py:140-185` hat **keine** try/except pro Channel; `run_public_channel_monitor(channel_urls, …)` ist ein Bulk-Aufruf, ein Fehler reißt alle Channels mit. | **HIGH** — größter struktureller Hebel laut beiden Diagnosen, blockiert sicheres Sprint-2-Erweitern |
| 2 | **`match_key.py`-Bug — Generator schreibt weiter "unknown"** | implizit aus Polish-Filter | `visual_analysis.py:298`: `_as_text(data.get("de_us_match_key"), "")` lässt LLM-Output `"unknown"` durchschreiben (truthy → keine slugify-Korrektur). Polish #74 filtert nur LESEND in `_cross_market_matches`. | **MEDIUM** — Workaround greift, aber DB akkumuliert weiter "unknown"-Sentinels |
| 3 | **DE-Title-Coverage 0% — Whitelist-Audit/-Erweiterung** | Title-Tagging-Diag § Quick-Wins #3, `TITLE_TAGGING_STATUS_2026-05-05.md:640-642` | `seeds.py:27-38` zeigt das Schema (Aliases als list); Wolf-Aktion, kein Code-Patch | MEDIUM |
| 4 | **Median statt Mean für Engagement** | implizit (Briefing-Snippet) | `insight_engine.py:347`: `avg_engagement = sum(eng) / len(engagements)` — arithmetic mean, kein median. `_engagement_sum` selbst (L165) ist ok | LOW |
| 5 | **IG-Channel-Coverage-Lücken (54 von 114)** | Briefing-Memory; siehe Scraper-Cov-Diag § Q1c | im Container nicht verifizierbar (kein DB-Zugriff); Hypothese (a) Default-Reihenfolge im Briefing | MEDIUM |
| 6 | **Vision fetch_failed bei TT-CDN-URL-Expiry** | bekannt aus früheren Sessions; `visual_analysis.py` führt status `fetch_failed` | Code akzeptiert `fetch_failed` als terminal status (`visual_analysis.py:282-285`). Refresh-Strategie nicht implementiert | LOW (operativ) |
| 7 | **TT-Channels mit 0 Posts (11 von 14)** | Briefing-Memory | im Container nicht verifizierbar | siehe Q6 — direkter Sprint-2-Blocker |
| 8 | **Auto-Rematch-Cron** | Title-Tagging-Diag § Quick-Wins #1, `TITLE_TAGGING_STATUS_2026-05-05.md:632-635` | `cron.py:222` ruft kein `rematch_unassigned_assets` nach Vision auf | MEDIUM (~10 Zeilen Patch) |
| 9 | **Vision-`_find_title_match` schwach (substring)** | Title-Tagging-Diag § Quick-Wins #2, `:636-639` | `visual_analysis.py:106-117`: substring-Tagger; ersetzen durch `find_best_title_match` | LOW-MEDIUM |
| 10 | **`TitleCandidate`-Review-UI** | Title-Tagging-Diag § Quick-Wins #4, `:643-646` | Kein klar identifizierbarer Endpoint, der Candidates systematisch resolvet — Wolf-Verifikation offen | LOW |

---

## Q6 — Pre-Sprint-2-Readiness

**Sprint-2-Ziel:** Generalisierung der Insight-Engine auf alle 7 Tier-A
DE+US TT-Pairs.

**Pair-Inventar** (aus Migration `e5d8f1a36b40`, Commit `51d8d34`):

| # | DE-Handle | US-Handle | Notes-Pair-Hinweis |
|---|---|---|---|
| 1 | `warnerbrosdeutschland` | `warnerbros` | bereits in `PAIRS`, A-Grade-bestätigt |
| 2 | `universalpicturesde` | `universalpictures` | "Pair zu universalpictures US/TT" |
| 3 | `paramountpicturesgermany` | `paramountpics` | "Pair zu paramountpics US/TT" |
| 4 | `sonypicturesgermany` | `sonypictures` | "Pair zu sonypictures US/TT" |
| 5 | `disneyde` | `disneystudios` (oder `disneyanimation`) | "Pair zu disneyde + disneydeutschland (IG)" |
| 6 | `netflixde` | `netflix` | (US-Handle existierte vor #69) |
| 7 | `primevideode` | `primevideo` | (US-Handle existierte vor #69) |

### Pro

- ✅ **0d-Polling-Fix auf main** (`9440fae`), schließt eine Hauptursache
  für leere TT-Aggregations (lange Apify-Runs).
- ✅ **Insight-Engine Code-Pfade Pair-agnostisch** außer `PAIRS`-Konstante
  (`insight_engine.py:58-71`). Sprint 2 = config-only Erweiterung.
- ✅ **Frontend-Route ist `:pair`-parametrisch**
  (`App.jsx:13`, Regex-Capture-Group), kein UI-Code-Change nötig.
- ✅ **Tests sind Pair-agnostisch parametrisierbar** — Seed-Helper
  `_seed_warnerbros_pair` ist die einzige hardcoded Stelle in
  `test_insight_engine.py`; die `aggregate_pair`-Aufrufe selbst akzeptieren
  jeden Pair-Key.
- ✅ **'unknown'-Filter** bremst falsche Cross-Market-Matches in den neuen
  Pairs.

### Contra

- ⚠️ **TT-Channels mit 0 Posts (Briefing: 11 von 14)** — solange das
  zutrifft, würde Sprint 2 für die betroffenen Pairs Reports mit
  „Datenbasis DE/US schwach" + leerer Cross-Market-Section produzieren.
  Insight-Engine geht damit grazios um (`insight_engine.py:589-594` schreibt
  Notes), aber das Wolf-Quality-Signal sinkt.
- ⚠️ **Per-Channel-Error-Isolation noch nicht implementiert**
  (`monitor.py:140-185`). Sobald Sprint 2 mehr Channels in den Cron-Loop
  zieht und einer davon einen Apify-Fehler triggert, fällt die ganze
  Plattform-Sync aus → größeres Blast-Radius als heute.
- ⚠️ **Whitelist/Title-Tagging-Issues bekannt** — werden in den neuen
  Pair-Reports als Caveats sichtbar (`title_coverage` < 70 → Banner-Tone
  `warn` / `bad` in der UI).
- ⚠️ **`match_key.py`-Bug schreibt weiter "unknown"** (Backlog-Item #2);
  der Read-Filter aus #74 maskiert das, aber DB-seitige Hygiene driftet
  weiter.

### Empfehlung

> **Sprint 2 mit Scope-Reduktion: zuerst die 2-3 Pairs mit bestätigter
> TT-Coverage zuschalten, parallel Per-Channel-Error-Isolation als
> Backlog-#1 abräumen, bevor die Pairs mit dünner Coverage hinzukommen.**

Begründung:

- Die *Pair-agnostische* Engine ist bereit, aber sie ist nur so gut wie
  die Daten, die der Cron-Loop liefert. Vor der Verifikation der TT-Post-
  Counts (Anhang Q3-Block) ist „Sprint 2 für alle 7" ein Blindflug.
- Der **größte strukturelle Hebel** ist Backlog-#1 (Per-Channel-Error-
  Isolation). Wenn der Sprint heute Abend zwei Tasks bekommt, ist die
  Reihenfolge: (1) Per-Channel-Error-Isolation, (2) Sprint 2 mit den
  Pairs, deren Coverage in der Verifikation OK ist.
- Mit Read-Filter (Polish #74) ist die Risiko-Reduktion auf der Insight-
  Seite ausreichend; die DB-Hygiene-Drift (`match_key.py`-Bug) kann in
  einem späteren, fokussierten Sprint adressiert werden.

**Konkrete Sprint-2-Schritte (sobald freigegeben):**

1. `PAIRS`-Konstante in `insight_engine.py:58-71` um die qualifizierten
   Pairs erweitern. Empfehlung: pro Pair einen `enabled: bool`-Flag, damit
   un-coverage Pairs schnell deaktiviert werden können, ohne Code-Push.
2. Pro neuem Pair einen Seed-Helper-Klon in `test_insight_engine.py` —
   oder besser, den Seed parametrisieren und ein
   `pytest.mark.parametrize`-Sweep über alle aktivierten Pairs.
3. Frontend braucht keine Änderung; `FALLBACK_LABEL` in
   `InsightWeekly.jsx:4-6` sollte um die neuen Labels erweitert werden,
   damit der Pre-Fetch-State eine sinnvolle Überschrift zeigt.

---

## Anhang — Schnell-Verify-Befehle für Wolf

### A1 — psql-Block: mvp-Channel-Realität

```sql
-- A. mvp=true Channel-Counts pro Plattform/Markt (verifiziert "147 mvp-Channels")
SELECT platform, market::text, COUNT(*) AS channels
FROM creative_radar.channel
WHERE mvp = TRUE AND active = TRUE
GROUP BY platform, market::text
ORDER BY platform, market::text;

-- B. TT-Channels mit 0 Posts in den 7 Tier-A-Pairs (Sprint-2-Blocker-Check)
WITH tier_a_handles AS (
  SELECT unnest(ARRAY[
    'warnerbrosdeutschland','warnerbros',
    'universalpicturesde','universalpictures',
    'paramountpicturesgermany','paramountpics',
    'sonypicturesgermany','sonypictures',
    'disneyde','disneystudios','disneyanimation',
    'netflixde','netflix',
    'primevideode','primevideo'
  ]) AS handle
)
SELECT c.handle, c.market::text, c.platform,
       COUNT(p.id) AS post_count_30d
FROM creative_radar.channel c
LEFT JOIN creative_radar.post p
  ON p.channel_id = c.id
 AND COALESCE(p.published_at, p.detected_at) >= NOW() - INTERVAL '30 days'
WHERE c.handle IN (SELECT handle FROM tier_a_handles)
  AND c.platform = 'tiktok'
GROUP BY c.handle, c.market::text, c.platform
ORDER BY post_count_30d ASC, c.handle;
```

### A2 — psql-Block: Letzter Cron-Run (0d-Verifikation)

```sql
-- Verifiziert, ob das Polling-Fix aus PR #72 lange IG-Runs > 60s sauber durchhält
SELECT id,
       started_at,
       completed_at,
       EXTRACT(EPOCH FROM (completed_at - started_at)) AS duration_s,
       status,
       error_message,
       summary_json->'platforms'->'instagram'->>'raw_items' AS ig_raw,
       summary_json->'platforms'->'instagram'->>'created_assets' AS ig_created,
       summary_json->'platforms'->'tiktok'->>'raw_items' AS tt_raw,
       summary_json->'platforms'->'tiktok'->>'created_assets' AS tt_created
FROM creative_radar.cron_run
ORDER BY started_at DESC
LIMIT 10;
```

### A3 — curl-Block: Insight-Engine End-to-End

```bash
# Aggregation-Only (kein LLM-Cost, prüft die deterministische Pipeline)
curl -s "https://<api-host>/api/insights/weekly?pair=warnerbros&dry_run=true" | jq '.aggregation | {
  pair_key, pair_label, iso_week, iso_year,
  de: .de_channel.posts_count, us: .us_channel.posts_count,
  matches: (.cross_market_matches | length),
  coverage_pct: .title_coverage.overall_coverage_pct,
  notes: .notes
}'

# Voll-Run mit LLM (Opus 4.7) — dauert 30-90s, das ist erwartet
time curl -s "https://<api-host>/api/insights/weekly?pair=warnerbros" \
  | jq '{headline: .llm_output.headline, model, cost: .cost_usd_estimate, coverage: .coverage_pct}'

# 404 für unbekannten Pair (verifiziert das Pair-Validation-Gate)
curl -i "https://<api-host>/api/insights/weekly?pair=does_not_exist"
```

### A4 — pytest-Block: lokale Test-Verifikation

```bash
cd backend
pytest -q app/tests/test_apify_connector.py            # 8 Tests
pytest -q app/tests/test_insight_engine.py             # 12 Tests
pytest -q app/tests/test_insights.py                   # 8 Tests
pytest -q app/tests/test_match_key.py                  # 8 Tests
pytest -q                                              # alle Tests, Vollverifikation
```

---

## Slack-fähiger Tagesbericht

> **Repo-Status-Audit Creative Radar — 2026-05-05.** 6 PRs heute auf
> `origin/main` (HEAD `cf31417`): Whitelist+Hygiene #69, Scraper-Coverage-
> Diagnose #71, Apify Async-Polling #72 (Total-Timeout 1800s, Poll 10s, 8
> Tests), Insight-Engine MVP #73 (Endpoint + Aggregation + UI für
> warnerbros), Polish #74 (max_tokens=8000, 'unknown'-Filter, Frontend-
> Status-Machine + 60s-Slow-Hint), Title-Tagging-Diagnose #70. Kein Drift
> gegen DoDs der heutigen Sprints. DB-Verifikation offen (kein Container-
> Zugriff, psql-Blöcke im Audit-Anhang). Backlog-Top-3:
> Per-Channel-Error-Isolation in `_run_apify_sync_for_platform`,
> `match_key.py`-Bug („unknown" landet weiter im DB-Schreibpfad,
> Read-Filter maskiert das nur), Auto-Rematch-Cron. **Sprint-2-Empfehlung:
> JA mit Scope-Reduktion** — zuerst die 2-3 Pairs mit bestätigter
> TT-Coverage, parallel Per-Channel-Error-Isolation, dann die Pairs mit
> dünner Coverage. Engine ist Pair-agnostisch außer `PAIRS`-Konstante;
> Frontend-Route schon `:pair`-parametrisch. Audit unter
> `docs/diagnostics/REPO_STATUS_AUDIT_2026-05-05.md`.
