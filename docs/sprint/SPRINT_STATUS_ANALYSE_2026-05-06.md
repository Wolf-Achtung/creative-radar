# Sprint-Status-Analyse Creative Radar — 2026-05-06

Read-only-Diagnose nach dem DB-Migrations-/Token-Recovery-Marathon vom 06.05.2026.
Zentrale Frage: **Was ist tatsächlich auf `main` gemerged und aktiv — was nicht?**

## TL;DR

- **Auf `main` aktiv:** Block 1 (Trailerhaus-Voice-Prompt, PR #78) + Hotfix-Pool-Config (PR #81). Die Pool-Config aus Block 2.5 ist also faktisch schon auf main (über den Hotfix), nur der Async-Refactor-Code fehlt.
- **Reverted:** Block 2 (Async-Refactor + Per-Channel-Isolation, PR #79 → Revert-Commit `7f71845`). Async-Code ist NICHT mehr auf main.
- **Offen / nicht gemerged:** Block 2.5 (PR #80, Draft) — Async-Refactor v2 mit Pool-Tuning. Branch existiert, ein Commit ahead. Mergeable, kein DB-Konflikt mit PR #81 (Pool-Config identisch), aber Rebase auf `07918ff` nötig.
- **Nicht gestartet (kein Code, keine Branch):** Block 4 (Briefing-Templating, Frontend-Multi-View). Block 5 (Title-Tagging-Sprint) — Diagnose-Doc vorhanden, aber kein Sprint-Briefing oder Code.
- **Wolf-Aufgabe (nicht Code):** Block 3 (Producer-Quality-Pass) — Output-Datei `docs/feedback/PRODUCER_QUALITY_PASS_2026-05-06.md` **fehlt** (in keinem Branch).

## Stand main-Branch

- `origin/main` HEAD: **`07918ff`** — `Add files via upload` (2026-05-06 17:39:16 +0200, Wolf Hohl) — fügt nur die vier Briefing-Texte unter `docs/sprint/briefings/` hinzu, kein Produktionscode.
- Letzte 5 Commits:
  - `07918ff` Add files via upload (Briefings 2/2.5/3/4)
  - `ba2b04e` hotfix(db): add pool_recycle=300 … (#81)
  - `7f71845` Revert "Merge pull request #79 …" (Async-Refactor)
  - `a88f8cb` Merge pull request #79 (Async-Refactor + Per-Channel-Isolation, später revertet)
  - `9066b58` refactor(monitor+cron): async asset-creation + per-channel error isolation
- `origin/main` wurde im Fetch als **forced update** registriert (`a7faa84 → 07918ff`) — das passt zum „Add files via upload"-Commit und ist konsistent mit einem Web-UI-Upload, kein Hinweis auf zerstörten History.

## ⭐ Merge-Status-Übersicht (Hauptergebnis)

| PR | Branch | PR-State | Merge-Commit | Revert-Commit | Auf main aktiv? | Block | Status-Kategorie |
|---|---|---|---|---|---|---|---|
| #78 | `claude/trailerhaus-prompt-sprint-v1-DNgdW` | merged | `60051ee` | — | ✅ ja | Block 1 | 🟢 merged-active |
| #79 | `claude/async-refactor-per-channel-isolation-2026-05-06` | merged | `a88f8cb` | `7f71845` | ❌ nein | Block 2 | 🔴 merged-reverted |
| #80 | `claude/async-refactor-pool-tuning-2026-05-06` | open (draft) | — | — | ❌ nein | Block 2.5 | 🟡 open |
| #81 | `hotfix/db-pool-pre-ping-2026-05-06` | merged (squash) | `ba2b04e` | — | ✅ ja | (Hotfix) | 🟢 merged-active |
| #76 | `claude/cron-tt-phase-diagnostic-2026-05-05` | open | — | — | ❌ nein (Doku) | (Diagnose) | 🟡 open |
| #75 | `claude/repo-status-audit-z9a44` | merged | `e78f997` | — | ✅ ja | (Diagnose-Doku) | 🟢 merged-active |
| #77 | `claude/insight-engine-multi-pair-2026-05-06` | merged | `013f87d` | — | ✅ ja | (Insight-Engine S2) | 🟢 merged-active |
| #74 | `claude/insight-engine-polish-2026-05-05` | merged | `07c285c` | — | ✅ ja | — | 🟢 merged-active |
| #73 | `claude/insight-engine-mvp-warnerbros-mDKMD` | merged | `0c10995` | — | ✅ ja | — | 🟢 merged-active |
| #72 | `claude/apify-async-polling-2026-05-05` | merged | `9440fae` | — | ✅ ja | — | 🟢 merged-active |
| #71 | `claude/scraper-coverage-diagnostics-2026-05-05` | merged | `fd0bd42` | — | ✅ ja | (Diagnose-Doku) | 🟢 merged-active |
| #70 | `claude/title-tagging-diagnostics-2026-05-05` | merged | `cf31417` | — | ✅ ja | (Diagnose-Doku, Block 5 Vorgänger) | 🟢 merged-active |

Andere offene Branches ohne PR-Eintrag:

| Branch | Letzter Commit | Datum | Status |
|---|---|---|---|
| `claude/fix-orb-asset-blocking-EAzpy` | `53d02c2` | 2026-05-02 | ⚫ abandoned (>14 Tage alt? — knapp; Inhalt ist Doku, die schon via PR #47 in main ist) |

Klartext-Zusammenfassung des Merge-Status:

- **Aktiv auf main:** PR #78 (Block 1, Trailerhaus-Prompt), PR #81 (Pool-Config-Hotfix), alle PRs #50–#77 außer #79 (siehe oben).
- **Reverted:** Nur PR #79 (Block 2). Revert via `7f71845`. Code ist im Git-Log sichtbar, aber nicht mehr auf `main` aktiv.
- **Offen / nicht gemerged:** PR #80 (Block 2.5, Draft) und PR #76 (Diagnose-Doku, vermutlich abandoned-Kandidat).
- **Nicht gestartet:** Block 4 (Briefing-Templating Frontend) und Block 5 (Title-Tagging-Sprint im engen Sinne) — keine Branches gefunden.
- **Wolf-Aufgabe nicht erledigt:** Block 3 (Producer-Quality-Pass) — keine Markdown-Datei unter `docs/feedback/`.

## Branch-Inventar

Liste der `claude/`/`hotfix/`/`feat/`/`docs/`/`chore/` Branches auf origin (sortiert nach letztem Commit):

| Branch | Letzter Commit | Datum (UTC) | Status (vs main) |
|---|---|---|---|
| `claude/async-refactor-pool-tuning-2026-05-06` | `e291d20` | 2026-05-06 10:06 | 🟡 open (PR #80, Draft) — 1 Commit ahead |
| `hotfix/db-pool-pre-ping-2026-05-06` | `65a42c0` | 2026-05-06 10:40 | ⚪ merged-and-gone-able (PR #81 squash auf main als `ba2b04e`); Branch-HEAD selbst nicht reachable, kann gelöscht werden |
| `claude/async-refactor-per-channel-isolation-2026-05-06` | `9066b58` | 2026-05-06 08:43 | 🔴 merged-reverted (PR #79 → Revert `7f71845`) |
| `claude/trailerhaus-prompt-sprint-v1-DNgdW` | `be42d8b` | 2026-05-06 07:41 | 🟢 merged-active (PR #78 → Merge `60051ee`) |
| `claude/insight-engine-multi-pair-2026-05-06` | `f452773` | 2026-05-05 19:37 | 🟢 merged-active (PR #77) |
| `claude/cron-tt-phase-diagnostic-2026-05-05` | `8e7704b` | 2026-05-05 18:50 | 🟡 open (PR #76, reine Diagnose-Doku) |
| `claude/repo-status-audit-z9a44` | `b05cf70` | 2026-05-05 17:02 | 🟢 merged-active (PR #75) |
| `claude/insight-engine-polish-2026-05-05` | `2172404` | 2026-05-05 16:31 | 🟢 merged-active (PR #74) |
| `claude/insight-engine-mvp-warnerbros-mDKMD` | `45733be` | 2026-05-05 15:48 | 🟢 merged-active (PR #73) |
| `claude/apify-async-polling-2026-05-05` | `3bd4675` | 2026-05-05 15:34 | 🟢 merged-active (PR #72) |
| `claude/scraper-coverage-diagnostics-2026-05-05` | `ec78476` | 2026-05-05 12:58 | 🟢 merged-active (PR #71) |
| `claude/title-tagging-diagnostics-2026-05-05` | `46d5c5a` | 2026-05-05 12:26 | 🟢 merged-active (PR #70) |
| `claude/whitelist-data-hygiene-Wv7Ip` | `51d8d34` | 2026-05-05 08:40 | 🟢 merged-active (PR #69) |
| `claude/research-briefing-channel-whitelist-yt13` | `92a0408` | 2026-05-04 17:40 | 🟢 merged-active (PR #68) |
| `claude/match-key-slug-consistency-yt12` | `c2e0de5` | 2026-05-04 17:10 | 🟢 merged-active (PR #67) |
| `claude/cron-auto-vision-yt11` | `1e5e4b3` | 2026-05-04 15:07 | 🟢 merged-active (PR #66) |
| `claude/vision-prompt-v2-yt9` | `92c223c` | 2026-05-04 13:23 | 🟢 merged-active (PR #65) |
| `claude/comparison-panel-activate-yt8` | `3e93e5f` | 2026-05-04 12:35 | 🟢 merged-active (PR #64) |
| `claude/frontend-quick-wins-yt7` | `f0e22cd` | 2026-05-04 12:33 | 🟢 merged-active (PR #63) |
| `claude/github-actions-cron-yt6` | `dea6948` | 2026-05-04 11:24 | 🟢 merged-active (PR #62) |
| `claude/cron-background-task-yt5` | `4bceee6` | 2026-05-04 10:55 | 🟢 merged-active (PR #61) |
| `claude/cron-auto-sync-yt4` | `8cd07a3` | 2026-05-04 10:07 | 🟢 merged-active (PR #60) |
| `claude/asset-url-as-image-source-yt3` | `72e8f5b` | 2026-05-04 09:45 | 🟢 merged-active (PR #59) |
| `claude/youtube-capture-support-yt2` | `2dc250a` | 2026-05-04 09:26 | 🟢 merged-active (PR #58) |
| `claude/screenshot-persistence-sync-9cLei` | `831beab` | 2026-05-03 20:38 | 🟢 merged-active (PR #57) |
| `claude/channel-bulk-import-BdYaU` | `1269c55` | 2026-05-03 17:19 | 🟢 merged-active (PR #56) |
| `claude/cross-platform-ai-pipeline-pE9vn` | `75de2d3` | 2026-05-03 14:24 | 🟢 merged-active (PR #55) |
| `feat/youtube-data-api` | `80af3ed` | 2026-05-03 12:51 | 🟢 merged-active (PR #54) |
| `claude/auto-migration-release-command-WTKDz` | `bbff24b` | 2026-05-03 12:17 | 🟢 merged-active (PR #53) |
| `claude/adjust-channel-tests-z94y7` | `faed70c` | 2026-05-02 19:47 | 🟢 merged-active (PR #52) |
| `claude/extend-sqlalchemy-pydantic-xVVX6` | `9bc47dc` | 2026-05-02 18:16 | 🟢 merged-active (PR #51) |
| `chore/remove-throwaway-alembic-upgrade-endpoint` | `b52d6f9` | 2026-05-02 17:59 | 🟢 merged-active (PR #50) |
| `chore/temporary-alembic-upgrade-endpoint` | `98d73d1` | 2026-05-02 17:36 | 🟢 merged-active (PR #49) |
| `feat/sprint-5-2-1-channel-registry-migration` | `7cd556e` | 2026-05-02 17:07 | 🟢 merged-active (PR #48) |
| `docs/phase-5-backlog-orb-findings` | `eaf09b4` | 2026-05-02 16:50 | 🟢 merged-active (PR #47) |
| `claude/fix-orb-asset-blocking-EAzpy` | `53d02c2` | 2026-05-02 15:23 | ⚫ abandoned — Doku-Branch, Inhalt schon via PR #47 in main, kann gelöscht werden |
| `claude/creative-radar-phase-5-DK4bq` | `0d6c71a` | 2026-05-02 14:35 | 🟢 merged-active (PR #45) |
| `claude/creative-radar-phase-4-w4-task-4-5-cleanup` | `cb138a9` | 2026-05-02 13:26 | 🟢 merged-active (PR #44) |

(Branches älter als 4 Tage, die alle merged-active sind, sind in der Tabelle nur noch zur Vollständigkeit gelistet.)

## PR-Inventar (relevante PRs der letzten 7 Tage)

| PR | Titel | State | Merge-Commit auf main | Notiz |
|---|---|---|---|---|
| #81 | hotfix(db): pool_recycle to prevent SSL-EOF on stale connections | merged 2026-05-06 10:44Z | `ba2b04e` (squash) | Pool-Config-only Hotfix nach #79-Revert |
| #80 | refactor(monitor+cron): async asset-creation v2 with pool tuning (Block 2.5) | open (Draft) | — | Block 2.5; 1 Commit ahead, Smoke-Test gegen Railway-DB ausstehend |
| #79 | refactor(monitor+cron): async asset-creation + per-channel error isolation | merged 2026-05-06 09:09Z, **reverted** | `a88f8cb` → revertet `7f71845` | Block 2; verursachte Pool-Storm um 09:24 UTC |
| #78 | feat(insight-engine): Trailerhaus-Prompt-Sprint v1 — voice, glossary, role sections | merged 2026-05-06 08:25Z | `60051ee` | Block 1 |
| #77 | feat(insight-engine): Sprint 2 — generalize to 6 enabled Tier-A pairs | merged 2026-05-05 19:40Z | `013f87d` | Insight-Engine Sprint 2 |
| #76 | docs(diagnostics): cron TT-phase regression 2026-05-05 | open | — | Reine Diagnose-Doku, vermutlich abandoned |
| #75 | docs(diagnostics): repo-status audit 2026-05-05 | merged | `e78f997` | |
| #74 | fix(insight-engine): Sprint 1 polish | merged | `07c285c` | |
| #73 | feat(insight-engine): Sprint 1 MVP — weekly brief warnerbros | merged | `0c10995` | |
| #72 | fix(apify): async-polling refactor for runs longer than 60s | merged | `9440fae` | |

## Block-für-Block

### Block 1 — Trailerhaus-Voice-Prompt v1
- **Status: ✅ Done — auf main aktiv.**
- DoD-Belege auf `main`:
  - `backend/app/services/insight_engine.py` — `SYSTEM_PROMPT` enthält Voice-Anker (Z. 209+: „Du bist Senior-Trailer-Marketing-Stratege … Trailerhaus … 'Audiovisual Communication, Made Emotional'"), Glossar (Z. 222–227: Hook, Pace, Beat, …), Anti-Pattern (Z. 229–234: „Brand-Storytelling, Engagement-Drivers, …" verboten), Tonalitäts-Pool, Längenvorgabe.
  - `backend/app/schemas/insights.py` — neue Felder `tonalitaet`, `watch_outs`, `fuer_cutter`, `fuer_motion_designer`, `fuer_creative_producer`, `vergleichbare_posts` (Z. 192–197).
  - `backend/app/tests/test_insight_engine.py` — Sprint-Trailerhaus-Tests Z. 417+ (`assert "Audiovisual Communication" in prompt or "Trailerhaus" in prompt` Z. 586).
  - `backend/scripts/ab_test_insight_prompt.py` (255 Zeilen) — A/B-Test-Skript existiert.
- Was fehlt: nichts (in Block 1 Scope). Sprint-Caching wäre ein optionaler Folge-Sprint.

### Block 2 — Async-Refactor + Per-Channel-Error-Isolation
- **Status: 🚫 Reverted — NICHT auf main aktiv.**
- Code-Spuren auf `main`: nur in Git-Log (Commits `9066b58`, `a88f8cb`) und im Revert-Diff selbst (`7f71845` zeigt Entfernung der Async-Funktionen + 342 Tests). Aktuelle `backend/app/api/monitor.py` auf main hat `def _create_asset_from_item(...)` synchron in Z. 46 — keine `_create_asset_from_item_async`, kein `asyncio.gather`.
- Warum reverted: Connection-Pool-Storm am 06.05.2026 09:24 UTC (siehe Marathon-Kontext, Section 0).
- **Block 2 ist faktisch obsolet — Block 2.5 ist sein Replacement.**

### Block 2.5 — Async-Refactor mit Pool-Tuning
- **Status: 🟡 Open — auf main NICHT aktiv, aber Code vollständig auf `claude/async-refactor-pool-tuning-2026-05-06` (PR #80, Draft).**
- DoD-Belege auf Branch:
  - `backend/app/api/monitor.py:243` — `async def _create_asset_from_item_async(...)`, wieder eingeführt mit Phase-Konsolidierung.
  - `backend/app/api/monitor.py:59-60` — `ASSET_CREATION_OPENAI_CONCURRENCY = 3` und `ASSET_CREATION_HTTPX_CONCURRENCY = 5` (laut Briefing reduziert von 5 → 3 bzw. 10 → 5).
  - `backend/app/api/monitor.py:389` — `asyncio.gather(*tasks, return_exceptions=True)`, Per-Channel-Try/Except-Pattern, `failed_channels`-Liste in Summary.
  - `backend/app/database.py` — Pool-Config (`pool_size=10, max_overflow=10, pool_recycle=300, pool_pre_ping=True, pool_use_lifo=True`) plus Budget-Konstanten `DB_POOL_TOTAL_BUDGET = 20`.
  - `backend/scripts/smoke_test_async_pool.py` (263 Zeilen) — lokaler Smoke-Test gegen Railway-DB.
  - `backend/app/tests/test_async_refactor_per_channel.py` (412 Zeilen, +70 ggü. PR #79 für Pool-Tests).
- **Wichtige Beobachtung — Konflikt mit PR #81:** Die `database.py`-Änderungen aus Block 2.5 sind 1:1 dieselben wie die im Hotfix PR #81 (selber Pool-Config-Block, selbe Konstanten). `git diff origin/main origin/claude/async-refactor-pool-tuning-2026-05-06 -- backend/app/database.py` ist daher leer — die Pool-Config ist via PR #81 schon auf main. Beim Rebase auf aktuellen `main` sollte der `database.py`-Hunk sauber als „bereits angewendet" wegfallen, kein Merge-Konflikt erwartbar.
- **Branch-Base ist `7f71845` (Revert-Commit).** Main ist seitdem 2 Commits voraus (`ba2b04e` PR #81, `07918ff` Briefings-Upload). Block 2.5 hat die Briefing-Texte unter `docs/sprint/briefings/` NICHT — bei Merge würden sie technisch entfernt werden. Lösung: Rebase auf `origin/main` HEAD vor Merge.
- Was fehlt zum Mergen:
  1. Rebase `claude/async-refactor-pool-tuning-2026-05-06` auf `origin/main` (`07918ff`), damit PR #81-Hotfix und Briefings übernommen werden.
  2. Lokaler Smoke-Test (`backend/scripts/smoke_test_async_pool.py`) gegen die neue Railway-DB im `overflowing-unity`-Project.
  3. Draft → Ready-for-Review, Merge.

### Block 3 — Producer-Quality-Pass
- **Status: ❓ Wolf-Aufgabe nicht erledigt — keine Output-Datei vorhanden.**
- Erwartung laut Briefing (`docs/sprint/briefings/Block-3.txt:31-96`): Datei `docs/feedback/PRODUCER_QUALITY_PASS_2026-05-06.md` mit Feedback über 6 Tier-A-Pairs.
- Befund: `git ls-tree -r origin/main --name-only | grep feedback` ist leer; `git log --all -- docs/feedback/PRODUCER_QUALITY_PASS_2026-05-06.md` ist leer (auf KEINEM Branch).
- Dies ist Vorbedingung für Block 4 (Briefing-Templating soll laut Block-4-Briefing Z. 49–52 die „Sofort-Anpassungen für Block 4"-Sektion aus dieser Markdown-Notiz konsumieren).
- Kein Code, keine Branch — das ist eine pure Wolf-Lese-Session.

### Block 4 — Briefing-Templating-Feature (Multi-View-Selektoren)
- **Status: ❌ Not started — kein Code, keine Branch.**
- Erwartete Artefakte laut Briefing:
  - Branch `claude/briefing-templating-2026-05-06` — **existiert nicht**.
  - Frontend-File `frontend/src/components/insights/viewModes.js` (6 View-Modes × 3 Detailtiefen) — **existiert nicht** (`git ls-tree -r origin/main --name-only | grep -iE "viewMode"` leer; auch in keinem anderen Branch via `git log --all -- frontend/src/components/insights/viewModes.js`).
  - Komponenten `ViewModeSelector.jsx`, `DetailLevelSelector.jsx`, Sektion-Komponenten — keine.
- Dependencies: laut Block-4-Briefing Vorbedingung „Block 1 ✓, Block 2 ✓, Block 3 docs/feedback/PRODUCER_QUALITY_PASS_2026-05-06.md verfügbar". Aktuell sind Block 1 ✓, Block 2 ✗ (reverted, ersetzt durch 2.5), Block 3 ✗.

### Block 5 — Title-Tagging-Sprint
- **Status: ❌ Not started als Sprint — nur Diagnose-Vorgänger auf main.**
- Briefing-Datei: `docs/sprint/briefings/` enthält **kein** `Block-5*.txt` oder `title-tagging-sprint*.txt`. (Stimmt mit Memory-Hinweis „nur Stub-Briefing, finale Version geplant für Dienstag-Abend" überein.)
- Auf main vorhanden: `docs/diagnostics/TITLE_TAGGING_STATUS_2026-05-05.md` (PR #70, Diagnose) — kein Sprint-Code.
- Kein Branch mit „title-tag" im Namen außer `claude/title-tagging-diagnostics-2026-05-05` (bereits gemerged als PR #70).

## Cross-Cutting-Befunde

- **Hardcoded DB-Hostnames:** Suche nach `shuttle.proxy.rlwy.net|tramway.proxy.rlwy.net|postgres-_hao|postgres.railway.internal|postgres-creative-radar` über `backend/` und `frontend/` auf main: **keine Treffer**. DB-URL wird ausschließlich über `DATABASE_URL` env-var bezogen (`backend/app/database.py:resolve_database_url`). Sauber für die abgeschlossene Migration.
- **Hardcoded Token-Strings:** Suche nach `be21f559` und `08859342` im gesamten main-Tree: **keine Treffer**. Token kommen nur aus env-vars; weder im Code noch in Tests stehen sie hardcoded.
- **Migrations-Skripte (`backend/migrations/versions/`):** 11 Versionen, neueste ist `e5d8f1a36b40_whitelist_expansion_2026_05_05.py` (PR #69, 2026-05-05). Keine neuen ungetrackten Migrations seitdem. Migration-Set ist konsistent mit der DB-Migration in den `overflowing-unity`-Postgres (Counts laut Marathon-Kontext: channel 168, post 564, asset 561, title 134 — entspricht den Schemas der vorhandenen Versionen).
- **Build-Artefakte:** keine `frontend/dist`- oder `frontend/build`-Verzeichnisse im Repo (sauber, Build läuft auf Vercel).
- **Workflows:** `.github/workflows/cron-sync.yml` einziger Workflow (PR #62). Keine Phantom-Workflows.
- **Backup-Files in Repo:** keine `.dump`/`backup`-Dateien getrackt — der 261,6 MB-Dump aus dem Marathon liegt also korrekt nur lokal.
- **Sonstige Anomalien:** Der `main`-Force-Update-Hinweis im Fetch (`a7faa84...07918ff main -> origin/main (forced update)`) ist erklärbar durch den „Add files via upload"-Commit über die GitHub-Web-UI. Kein Hinweis auf zerstörte History — `7f71845`, `ba2b04e`, `60051ee`, `a88f8cb`, `9066b58` sind alle erreichbar.

## Token-Hygiene-Status

- **`API_TOKEN` / `CR_API_TOKEN` (`be21f559…`)**: laut Marathon-Kontext mehrfach durch Chat + DevTools-History sichtbar geworden. Im Repo selbst keine Spur (Code-Suche oben). Empfehlung: mittelfristig rotieren — neuer Wert in Railway-`API_TOKEN` und GitHub-Secret `CR_API_TOKEN` synchron, danach Frontend-Rebuild auf Vercel.
- **Postgres-Passwort:** laut Marathon-Kontext heute (06.05.2026) frisch mit `openssl rand -hex` rotiert. Hygiene OK.
- **TablePlus:** alte Connection im `overflowing-unity`-Project hat den vorherigen Pool-Storm-Token gespeichert; Auth-Error beim Reconnect erwartbar — User muss in TablePlus die Verbindung mit dem neuen DSN updaten.
- **public-Schema in `postgres-creative-radar`:** ~250 MB ki-sicherheit-Reste aus dem Restore. Optional aufräumen via `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` — kein blockierender Befund, nur Speicher-Hygiene.

## Empfehlung Reihenfolge nächste Session

Priorisiert nach Production-Stabilität → Block-Dependencies → Aufwand:

1. **PR #80 (Block 2.5) abschließen.** Zwei Schritte:
   - Rebase auf `origin/main` (Briefings + PR #81 Pool-Config kommen rein, Pool-Config-Hunk fällt sauber raus).
   - Smoke-Test `backend/scripts/smoke_test_async_pool.py` gegen die neue Railway-DB im `overflowing-unity`-Project laufen lassen. Erst wenn der grün ist, Draft → Ready-for-Review → Merge.
   - Begründung: liefert die Latenz-Halbierung der Asset-Creation (~13 min → ~3-5 min) ohne Pool-Storm-Risiko, weil Pool-Config schon via PR #81 stabil läuft.
2. **Branch-Cleanup (5 Min, low-risk).**
   - `hotfix/db-pool-pre-ping-2026-05-06` löschen (PR #81 squash-merged, Branch-HEAD selbst nicht Ancestor von main, daher in `--no-merged` aufgeführt — aber inhaltlich überholt).
   - `claude/async-refactor-per-channel-isolation-2026-05-06` löschen (PR #79 reverted, Block 2.5 ist Replacement).
   - `claude/fix-orb-asset-blocking-EAzpy` löschen (Doku schon via PR #47 auf main).
   - PR #76 (`claude/cron-tt-phase-diagnostic-2026-05-05`) entweder mergen oder schließen — reine Diagnose-Doku, blockiert nichts.
3. **Block 3 (Wolf-Lese-Session, 60 Min).** Sechs Tier-A-Briefe lesen, `docs/feedback/PRODUCER_QUALITY_PASS_2026-05-06.md` ausfüllen und committen. Vorbedingung für Block 4.
4. **Block 4 (Briefing-Templating, 2-4h Frontend).** Erst wenn Block 3 als Markdown vorliegt — die View-Mode-Default-Sektionen pro Detailtiefe sind laut Briefing aus den Block-3-Erkenntnissen abzuleiten.
5. **Block 5 (Title-Tagging-Sprint).** Wartet auf das finale Briefing von Wolf. Aktuell nur Stub.
6. **Token-Rotation `API_TOKEN`/`CR_API_TOKEN`** (15 Min, async). Geringe Dringlichkeit, aber wegen Marathon-Hygiene mittelfristig.
7. **Optional:** `DROP SCHEMA public CASCADE` in `postgres-creative-radar` (~250 MB Speicher zurück), TablePlus-Connection mit neuem DSN updaten.

## Offene Fragen an Wolf

- **PR #76 (cron-tt-phase-diagnostic):** mergen (auch wenn nur Diagnose-Doku) oder schließen? Steht offen seit 2026-05-05.
- **Block 2.5 Smoke-Test:** möchtest du den Smoke-Test selbst gegen Railway laufen lassen, oder soll Claude das in der Folge-Session orchestrieren (mit env-vars / DSN aus dem `overflowing-unity`-Project)?
- **Block 5 Briefing:** noch nicht da. Plan unverändert „Dienstag-Abend"? Falls verschoben, sollte das im Backlog vermerkt werden.
- **Block 2-Branch-Löschung:** OK, `claude/async-refactor-per-channel-isolation-2026-05-06` zu löschen, oder als Reverted-Reference behalten?
