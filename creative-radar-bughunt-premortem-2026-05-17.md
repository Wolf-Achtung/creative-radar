# Creative Radar — Bug-Hunt + Premortem (Stand 17.05.2026)

> **Modus:** READ-ONLY · keine Code-Änderungen · keine DB-Writes · keine Brief-Gen-Trigger
> **Repo-Branch:** `claude/creative-radar-debug-AUdsR` @ `f83a1b8` (worktree clean)
> **Zeitstempel:** 17.05.2026, ca. 13:00 CEST

---

## Executive Summary

**Bug-Diagnose: H1 ist bestätigt.** Der Sonntag-Cron ruft Brief-Generierung **gar nicht** auf — der Code-Pfad existiert nicht. Beweis: `backend/app/api/cron.py:557-617` (`_run_cron_sync_background`) ruft sequenziell `compute_apify_monthly_spend` → `_execute_platform_sync` (IG/TT/YT-Scrape) → `_run_vision_after_sync` → `_run_rematch_after_sync` → Cost-Aggregation auf. **Kein einziger Aufruf von `generate_and_persist_report`** (oder anderer Funktion aus `app.services.insight_engine`) im Cron-Pfad. Die Lücke ist im Code dokumentiert: Docstring von `regenerate_insights` (`backend/app/api/admin.py:437-441`) sagt explizit *„Auto-Trigger im Cron-Lauf folgt im Cadence-Sprint; bis dahin füllt Wolf den Cache manuell über diesen Endpoint"* — der Cadence-Sprint ist nie passiert.

Briefs entstehen heute auf zwei Wegen: (a) **lazy via UI-Aufruf** — wenn ein User `GET /api/insights/weekly?pair=X` aufruft und für die laufende ISO-Woche noch kein Brief in `insight_report` existiert, generiert der Endpoint synchron einen Brief, persistiert ihn und antwortet; folgende Aufrufe in derselben KW liefern den Cache-Hit (`backend/app/services/insight_engine.py:2467-2614`). (b) **manuell** via `POST /api/admin/insights/regenerate?pair=all` (admin.py:437-519). Die zwei beobachteten Bursts (10.5. Sonntag-Abend, 6 Briefs; 13.5. Mittwoch-Vormittag, 9 Briefs) sind also manuelle Trigger oder UI-Klick-Burst, **nicht** Cron-Output. H2/H3/H4 sind damit gegenstandslos für den Brief-Gen-Bug; H4 (ISO-Wochen-Inkonsistenz) bleibt als latentes Design-Risiko für jede künftige Cadence-Implementierung relevant. Frontend zeigt `generated_at` rotzig roh, ohne Stale-Warning (`frontend/src/InsightWeekly.jsx:1003`) — deshalb sieht der Cutter den 4-Tage-alten Disney-Brief ohne Hinweis.

Empfehlung: Backend-Cadence-Sprint + Frontend-Stale-Warning als parallele Top-2-Tickets vor dem 24.05., damit DP2 für die F0.7-Anthropic-Cap-Entscheidung sauber entsteht und die Cutter-vertrauensschädigende UI-Lücke geschlossen ist.

---

## 1. Bestandsaufnahme (Phase A)

### A1. Cron-Pipeline-Entrypoint

| Aspekt | Befund | Pfad |
|---|---|---|
| Trigger | GitHub Actions, Cron `0 6 * * 0` (Sonntag 06:00 UTC = 08:00 CEST Winter / 07:00–08:00 CEST Sommer mit DST-Drift) | `.github/workflows/cron-sync.yml:18` |
| Trigger-Mechanik | `curl POST $CR_API_URL/api/admin/cron/sync-all` mit Bearer-Token aus Repo-Secret | `.github/workflows/cron-sync.yml:26-30` |
| Endpoint | FastAPI-Router `/api/admin/cron/sync-all`, Antwort 202, eigentliche Arbeit in `BackgroundTasks` | `backend/app/api/cron.py:619-657` |
| Single-Run-Lock | `CronRun.status == 'running'` → 409; Stale-Reap nach `CRON_RUN_TIMEOUT_MINUTES` (Default 30) | `backend/app/api/cron.py:128-143, 626-635` |
| Background-Owner | `_run_cron_sync_background(run_id, run_index)` öffnet eigene `Session(engine)` | `backend/app/api/cron.py:557-617` |
| Stages (Reihenfolge) | (1) Apify-Budget-Pre-Flight → (2) `_execute_platform_sync` IG/TT/YT → (3) Vision-Loop → (4) Rematch → (5) Cost-Aggregat (Apify/Anthropic/OpenAI) → Status `completed` | `cron.py:570-602` |
| **Brief-Gen-Aufruf** | **NICHT VORHANDEN** — kein Import, kein Funktionsaufruf aus `insight_engine` im gesamten Cron-Modul | `grep` über `backend/app/api/cron.py` zeigt nur Apify/YouTube/Vision/Budget |
| `run_index` für Channel-Rotation | `compute_run_index()` aus `CRON_SYNC_INTERVAL_DAYS` (Default 3) — bezieht sich nur auf Channel-Bucket-Rotation, nicht Brief-Cadence | `cron.py:639` + `backend/app/services/cron_channel_selection.py:109-113` |

### A2. Brief-Gen-Code-Pfad

| Aspekt | Befund |
|---|---|
| Ort | `backend/app/services/insight_engine.py` (2629 Zeilen, ein File) |
| Persistenz-Funktion | `generate_and_persist_report` (`insight_engine.py:2459-2614`) — Cache-First + Postgres-Advisory-Lock + Double-Check-Locking (Sprint 3c fix) |
| Pair-Liste | `PAIRS` dict, hardcoded (`insight_engine.py:88-478`), aktuell 9 Pairs alle `enabled=True`: `warnerbros, sonypictures, primevideo, disney, netflix, paramountpictures, universalpictures, paramountplus, lionsgate` — **matcht DB exakt** (Sektion 11 Briefing) |
| ISO-Wochen-Berechnung | `now or datetime.now(timezone.utc)` → `now.isocalendar()` (`insight_engine.py:1880`) — wird **vor** dem Lock-Acquire in `aggregate_pair` berechnet und an Persistenz weitergereicht (intern konsistent, kein Drift möglich) |
| Modell | `OPUS_MODEL_ALIAS` als Konstante (`insight_engine.py:__all__` zeigt Export); Override per Param möglich (`generate_and_persist_report(... model=...)`); aktuell `claude-opus-4-7` |
| Upsert-Semantik | Delete-then-Insert auf Composite-PK (`_persist_report`, `insight_engine.py:2327-2378`) — Last-Write-Wins; SQLite-Portable, daher keine echte Postgres-ON-CONFLICT-Klausel |
| Cost-Logging | `record_anthropic_call` läuft in `generate_weekly_report` (eigene Session) → Cost geht in `costlog`; zusätzlich `cost_usd_cents` direkt in `insight_report`-Row gestempelt (`insight_engine.py:2354`) |

### A3. Manuelle Brief-Gen-Trigger

| Trigger | Pfad | Auth |
|---|---|---|
| Admin-Endpoint (alle Pairs oder einer) | `POST /api/admin/insights/regenerate?pair={all\|netflix\|...}` (`admin.py:437-519`) | `auth_middleware`, Bearer-Token |
| UI lazy-generate | `GET /api/insights/weekly?pair=X` — bei Cache-Miss synchron, sonst Cache-Hit (`insights.py:108-201`) | `auth_middleware` |
| Force-Reload | `GET /api/insights/weekly?pair=X&force=true` — überspringt optionalen Pre-Lock-Read, hält aber Composite-PK ein (Sprint 3c-Fix) (`insights.py:191`) | dito |
| Dry-Run (ohne LLM) | `GET /api/insights/weekly?pair=X&dry_run=true` — nur Aggregation, keine Persistenz | dito |

**10.05.2026 + 13.05.2026 Bursts:** Aus den DB-Befunden plus Code-Pfad: Beide passen am besten zu `POST /api/admin/insights/regenerate?pair=all` (Doku in Docstring `admin.py:437-444` „Wolf füllt den Cache manuell"). 10.05-Burst war 6 Briefs in 10 min → entweder waren damals 3 Pairs noch `enabled=False` (heute alle 9 aktiv) oder Wolf hat `pair`-Liste-für-Liste durchlaufen. 13.05-Burst war 9 Briefs in 35 min → vollständiger `pair=all`-Lauf nach der UK-Phase-A-Erweiterung. Die ~$14.73 Anthropic-Cost vom 13.05. = ~$1.64/Brief × 9 ≈ Opus-Pricing-Ballpark.

### A4. UI-Anzeige-Pfad

| Aspekt | Befund | Pfad |
|---|---|---|
| Komponente | `InsightWeekly.jsx` rendert `/insights/weekly/{pair_key}` | `frontend/src/InsightWeekly.jsx` |
| Backend-Endpoint | `GET /api/insights/weekly?pair=X` | `backend/app/api/insights.py:108-201` |
| „Stand: …"-Render | `<p className="insight-meta-stand">Stand: {formatStand(report.generated_at)}</p>` (Zeile 1003) — Format `dd.mm.yyyy, hh:mm` in lokaler Zeit | `InsightWeekly.jsx:55-67, 1003` |
| Zweite Anzeige | Footer: „generiert {formatDateISO(report.generated_at)}" | `InsightWeekly.jsx:1065` |
| **Stale-Warning** | **KEIN Code** — kein `if generated_at < now - X days`, kein Vergleich gegen ISO-Wochen-Boundary, keine visuelle Hervorhebung bei alten Briefs | `grep -rni "stale\|outdated\|veraltet" frontend/src/` → 0 Treffer in JSX, nur CSS-Wort „standardmäßig" |
| Disney-Befund | UI-Stand „13.5.2026, 10:31" = `insight_report.generated_at = 2026-05-13 08:31:53 UTC` (UTC→CEST mit DST: +2h ergibt 10:31) — exakt der erste Brief des KW-20-Bursts, identifizierbar als Disney via `pair_key` | DB + UI-Cross-Check |

**Warum heute (17.05.) der 13.05.-Brief erscheint:** 2026-05-17 ist Sonntag, ISO-KW 20 (Mo 11.5.–So 17.5.). Beim UI-Aufruf läuft `generate_and_persist_report` → `aggregate_pair` berechnet `iso_week=20, iso_year=2026` → Lookup `(disney, 2026, 20)` → bestehender Brief vom 13.05. → Cache-Hit, kein LLM-Call. Hydrat zurück, `generated_at` aus Row → UI rendert „13.5.2026, 10:31".

### A5. Open PRs

- **PR #143** (`chore/backend-ci-gate`) — Draft, eröffnet 12.05.2026 19:57, letzte Aktivität 12.05. 20:06. Fügt `.github/workflows/backend-tests.yml` mit Postgres-13-Service hinzu. Lokal verifizierter Baseline-Stand: 561 passed, 2 skipped (post-PR #141). Branch-Protection-Aktivierung steht als Wolf-UI-Aktion aus. **Status: blockiert auf Wolf-Approve + erste-grüne-Run-Verifikation.**
- **PR #80** (im Briefing erwähnt, „pool-tuned async") — **nicht** in der `state=open`-Liste. Entweder gemerged oder geschlossen. Keine weitere Aktion in dieser Phase.
- Sonst: keine weiteren offenen PRs.

### A6. Cost-Tracking

| Bereich | Status | Pfad |
|---|---|---|
| Apify Hard-Cap (F0.6) | **Vorhanden** — `compute_apify_monthly_spend()` mit Soft (80%) + Hard (100% mit Kill-Switch `apify_budget_enforced`), Cron-Pre-Flight bricht mit Status `budget_exceeded` ab | `backend/app/services/budget_check.py:82-150` + Cron-Pre-Flight `cron.py:570-588` |
| Anthropic Cap (F0.7) | **NICHT VORHANDEN** — `budget_check.py` hat nur `aggregate_anthropic_costs_since` (read-only Summe für Cron-Summary), keinen Cap, kein Kill-Switch, kein Pre-Flight | `budget_check.py:175 ff` |
| OpenAI Cap | dito — nur Aggregation, kein Cap | `budget_check.py` |
| Vision Cost | Heute $0.75 pro Cron-Run angefallen, **kein Cap** — Vision-Loop hat `cron_vision_max_assets_per_run` als Soft-Cap auf Anzahl Assets, aber das ist eine Anti-Long-Tail-Bremse, kein Cost-Limit | `cron.py:573` + `settings.cron_vision_max_assets_per_run` |
| Cost-Doppelschreibung | Brief-Gen schreibt sowohl `insight_report.cost_usd_cents` (über `_persist_report`) **als auch** `costlog` (über `record_anthropic_call` in `generate_weekly_report`) — beide Pfade existieren parallel | `insight_engine.py:2354` + `generate_weekly_report`-Pfad |

### A7. Railway / ENV-Config (aus Code-Sicht)

- Brief-Gen-relevante Flags: **keine gefunden** — `grep ENABLE_BRIEF_GEN / BRIEF_GEN_MODE / WEEKLY_BRIEF_ENABLED` über `backend/` → 0 Treffer. H2 ist damit schon strukturell widerlegt.
- Cron-relevant: `CRON_SYNC_INTERVAL_DAYS` (Default 3), `CRON_RUN_TIMEOUT_MINUTES` (Default 30) — beide nur für Channel-Rotation und Stale-Reap, nicht für Brief-Cadence.
- `.env.example` (66 Zeilen) deckt alle Settings ab; keine versteckten Brief-Gen-Toggles.

### A8. Tests-Stand

- pytest, lokal-Baseline 561 passed, 2 skipped (post-PR #141, in PR #143 dokumentiert).
- Tests für Brief-Gen: `backend/app/tests/test_api_insights_weekly.py` + `test_insight_engine.py` + `test_api_pairs.py` — decken aggregierten Flow, Cache-Hit-Pfad, dry-run, Pair-not-activated.
- **Keine Tests für „Cron-Pfad ruft Brief-Gen auf"** — passt zur Diagnose: der Pfad existiert nicht, also gibt's auch nichts zu testen.
- Backend-CI-Gate (PR #143) noch nicht aktiviert; Tests laufen heute nur lokal.

### A9. Doku-Stand

- `docs/negative-findings.md` (post-cron-Eintrag vom 17.05.2026) ist aktuell und bestätigt exakt unsere Diagnose. Zitat aus dem Eintrag: *„Anthropic: 0 Calls, $0.00 — F0.7-Anthropic-Cap-Entscheidung verschiebt sich auf nächsten Sonntag (24.05.) als echten DP2 mit Briefs. Klärungsbedarf vor 24.05.: Soll regulärer Sonntag-Cron Briefs generieren oder nur Assets? Wenn Brief-Generation by-design im Sonntag-Cron passieren soll → Bug, eigene Diagnose."* — Wolf hat in der Dokumentation schon die offene Frage formuliert; dieser Report ist die Antwort.
- README, Roadmap, Diagnosis sind alt aber konsistent für die hier untersuchten Teilbereiche.

### Cross-Project-Coupling (ki-sicherheit.jetzt-Reste)

- Treffer nur in `CREATIVE_RADAR_DIAGNOSIS.md` und `CREATIVE_RADAR_ROADMAP.md` als **historisches Dokument** (Beschreibung der Pre-Migration-DB-Sharing-Situation). Kein Treffer in `backend/app/`, `frontend/src/`, Configs oder Tests. Coupling im Code-Pfad ist sauber.
- `_HAO`-Suffix-Treffer nur 1× im historischen Diagnosis-Dokument („Postgres-_HAO" als Beschreibung des Railway-Hostings). Kein aktiver Code-Bezug.

---

## 2. Bug-Hunt (Phase B)

### 2.1 H1-Test — Ist Brief-Gen im Cron-Code überhaupt drin?

**Methodik.** Cron-Entrypoint aus A1 (`backend/app/api/cron.py`) zeilen-genau gelesen. Stages-Sequenz im Background-Task identifiziert. Grep nach allen Symbolen aus `insight_engine`-Modul im gesamten `backend/app/api/cron.py`.

**Befund.**

`backend/app/api/cron.py:1-617` enthält **keinen** Import von `app.services.insight_engine`. Imports sind ausschließlich:

- `app.services.apify_connector` (Scrape)
- `app.services.budget_check` (Cap + Aggregation)
- `app.services.cron_channel_selection` (Rotation)
- `app.services.title_rematch` (Rematch)
- `app.services.visual_analysis` (Vision)
- `app.services.youtube_connector` (YT-Adapter)
- `app.api.monitor` (Adapter-Helper)

Die Background-Task-Funktion `_run_cron_sync_background` (cron.py:557-617) ruft sequenziell auf:

```
compute_apify_monthly_spend(session)                          # Pre-Flight, ggf. abort
  ├── (Apify-Cap erreicht?) → CronRun.status='budget_exceeded', return
_execute_platform_sync(session, run_index)                    # IG/TT/YT scrape + persist
_run_vision_after_sync(session, created_asset_ids, cap)       # Vision-Loop bis Cap
_run_rematch_after_sync(session)                              # Title-Rematch
aggregate_apify_costs_since(session, run.started_at)          # Cost-Summary
aggregate_anthropic_costs_since(session, run.started_at)      # nur Aggregation, kein Trigger
aggregate_openai_costs_since(session, run.started_at)         # dito
run.status='completed'; commit
```

**Kein Aufruf von `generate_and_persist_report`, `generate_weekly_report` oder irgendeiner anderen Funktion aus `insight_engine`.** Die Aggregator-Funktion `aggregate_anthropic_costs_since` ist **read-only über `costlog`** und triggert keinen LLM-Call.

**Beweis-Kette.**

- `backend/app/api/cron.py:557-617` — `_run_cron_sync_background` ohne Brief-Gen-Aufruf
- `backend/app/api/admin.py:437-444` — Docstring von `regenerate_insights`: *„Auto-Trigger im Cron-Lauf folgt im Cadence-Sprint; bis dahin füllt Wolf den Cache manuell über diesen Endpoint."* — **explizite Eingeständnis der Lücke**
- DB: `costlog`-Aggregation für 17.05. ergäbe 0 Anthropic-Calls (laut Diagnose-Sektion 0 + negative-findings.md 17.05.-Eintrag: „Anthropic: 0 Calls, $0.00")

**Verdikt: H1 bestätigt.** Brief-Gen ist nicht implementiert im Cron-Pfad. Die Funktionalität war als „Cadence-Sprint" geplant und nie umgesetzt.

### 2.2 H2-Test — Ist Brief-Gen an eine Condition gekoppelt?

Da H1 bestätigt, ist H2 **gegenstandslos** für den Cron-Pfad. Vollständigkeitshalber:

- `grep ENABLE_BRIEF_GEN / BRIEF_GEN_MODE / WEEKLY_BRIEF_ENABLED / FEATURE_BRIEF` über `backend/` → 0 Treffer
- `generate_and_persist_report` selbst hat **keine** Top-Level-Condition (kein `if not enabled: return`); nur Cache-Hit-Short-Circuit (`insight_engine.py:2523-2541`)
- Einzige „Aus-Schalter" sind im UI-Endpoint: `dry_run`, `pair.enabled=False`, `pair not in PAIRS`

Kein Condition-Gate, das einen vorhandenen Cron-Aufruf blockieren würde.

### 2.3 H3-Test — Schlägt Brief-Gen still fehl?

Da H1 bestätigt, ist H3 **gegenstandslos**. Zur Bestätigung:

- `cron_run.summary_json` des heutigen Runs `bd5d0a6a-…` enthält laut negative-findings.md die Blöcke `platforms` (IG/TT/YT), `vision`, `rematch`, `apify`, `anthropic`, `openai`, `budget` — **keinen** Brief-Gen-Block. Das ist konsistent: kein Code, kein Block.
- Exception-Handler in `_run_cron_sync_background` (cron.py:606-616) ist Top-Level (`except Exception`) — würde ein silent-fail nur dann verstecken, wenn der Aufruf existiert. Tut er nicht.
- `costlog` für 17.05. zeigt $0.00 Anthropic — kein gestarteter LLM-Call, der mit PK-Konflikt abgebrochen wäre (`record_anthropic_call` läuft im LLM-Pfad und käme **vor** der Persistenz-Phase, würde also auch bei PK-Konflikt Spuren hinterlassen). Wenn doch ein silent fail vorläge, sähen wir Anthropic-Cost ohne `insight_report`-Row.

H3-Pfad ist sauber abgedeckt.

### 2.4 H4-Test — ISO-Wochen-Schedule-Inkonsistenz

**Aktuell latent — kein akuter Schaden wegen H1**, aber für die Cadence-Implementierung kritisch.

**Befund.**

`aggregate_pair` (`insight_engine.py:1858-1916`) berechnet:

```python
now = now or datetime.now(timezone.utc)
iso_year, iso_week, _ = now.isocalendar()
```

- Sonntag 17.05.2026 06:00 UTC → `isocalendar()` liefert `(2026, 20, 7)` (Sunday ist day-7 der KW 20, KW 20 = Mo 11.5.–So 17.5.).
- Falls Cron-Cadence „diese KW" briefen würde (= `iso_week=20`), würde der Composite-PK gegen die vorhandenen 13.05.-Briefs kollidieren. Double-Check-Locking + Last-Write-Wins (`_persist_report:2370-2372`) würde sie **überschreiben** — kein silent fail, aber Cost-Doppelausgabe und Daten-Verlust des Mittwochs-Briefs.
- Falls Cadence „letzte abgeschlossene KW" briefen würde (= `iso_week=19` aus `now - 1 day`), würde gegen die 10.05.-Briefs kollidieren — gleiches Problem.
- Falls Cadence Montag-statt-Sonntag schedule (`0 6 * * 1` = Mo 06:00 UTC, ISO-KW 21 Tag 1), würde ein Brief für die frisch begonnene KW geschrieben — **datenarm**, weil das Datenfenster `window_days=30` zwar 30 Tage in die Vergangenheit reicht, der Brief aber zur „falschen" KW assoziiert wäre und Cutter beim Montag-Lesen verwirrt würde.

**Beste Cadence-Variante:** Cron-Schedule auf **Montag 06:00 UTC** verschieben (`0 6 * * 1`) und `aggregate_pair`-Aufruf mit `now = datetime.now(tz=UTC) - timedelta(days=1)` aufrufen, sodass `isocalendar()` die **gerade abgeschlossene** KW erwischt. Dadurch werden Briefs für KW N am Montag früh erzeugt, sobald die Datenwoche komplett ist. Vorteil: keine Kollision mit existierenden Bursts (die ja Mittwoch oder Sonntag-Abend liefen) und semantisch sauber „Wochenrückblick".

**Verdikt H4:** Aktuell harmlos (kein Cron-Pfad), aber das Cadence-Fix-Ticket **muss diese Logik explizit adressieren**, sonst tritt H4 sofort auf, wenn der Pfad implementiert wird.

### 2.5 UI-Anzeige-Pfad Bestätigung

- `InsightWeekly.jsx:1003` rendert `formatStand(report.generated_at)` ohne Vergleich gegen `Date.now()`.
- `report.generated_at` kommt aus dem Pydantic-Modell `InsightReport` (`backend/app/schemas/insights.py:458 ff`), 1:1 aus `insight_report.generated_at` (timestamptz, UTC).
- Format-Funktion verwendet `toLocaleDateString('de-DE')` + `toLocaleTimeString('de-DE', {hour:'2-digit',minute:'2-digit'})` → Browser-Local-TZ (Berlin) ergibt die im UI sichtbare „13.5.2026, 10:31".
- Keine Stale-Logik. Bestätigt.

### 2.6 Restliche Validation

**Channel-Whitelist vs. Datenrealität.** Nicht ausgeführt (read-only-SQL wäre erlaubt, aber Wolf hat aus der heutigen psql-Session schon die FU-1-Surface verifiziert: IG 1 zero_yield, TT 0, YT 3 failed). Die kuratierte Liste in PAIRS deckt die DB ab. Vertieften UK-Channel-Drift-Check empfehle ich als eigenes Premortem-Folge-Ticket (#11).

**Cross-Project-Coupling-Reste.** 0 Treffer im aktiven Code-Pfad (`backend/app`, `frontend/src`, `.github`, `netlify.toml`, `railway.json`); Treffer nur in historischen Markdowns. Kein Handlungsbedarf.

**Apify-Cap-Pfad.** F0.6-Pre-Flight greift im **Cron-Pfad** (`cron.py:570-588`). Sie greift **nicht** in den manuellen Trigger-Pfaden (`POST /api/admin/insights/regenerate` und `GET /api/insights/weekly`) — dort wird **kein** `compute_apify_monthly_spend` aufgerufen, und Brief-Gen löst sowieso keinen Apify-Spend aus (LLM-Cost = Anthropic, nicht Apify). Logisch konsistent. **Aber:** ein manueller Force-Trigger wie `?force=true` auf alle 9 Pairs umgeht jeglichen Cost-Cap, weil F0.7 noch nicht existiert.

### 2.7 Bug-Diagnose-Fazit

| Hypothese | Status | Beweis |
|---|---|---|
| **H1** Brief-Gen-Aufruf im Cron-Code fehlt | **BESTÄTIGT** | `backend/app/api/cron.py:557-617` ohne `insight_engine`-Aufruf + `admin.py:441` Docstring-Eingeständnis |
| **H2** Brief-Gen an Condition gekoppelt | widerlegt / gegenstandslos | kein Flag-Treffer; `generate_and_persist_report` hat keinen Aus-Schalter |
| **H3** Brief-Gen schlägt still fehl | widerlegt / gegenstandslos | kein `costlog`-Eintrag = kein gestarteter Call; `summary_json` ohne Brief-Block |
| **H4** ISO-Wochen-Inkonsistenz | latent, nicht akut | aktuell harmlos (kein Pfad); kritisch für Cadence-Fix-Ticket |

**Empfohlener Fix-Pfad (für Sprint-Planung, nicht in dieser Phase umsetzen):** Cadence-Implementation am Ende von `_run_cron_sync_background` (nach Rematch, vor Cost-Aggregation): Schleife über `[k for k,v in PAIRS.items() if v.get('enabled')]`, per Pair Aufruf `generate_and_persist_report(session, k, window_days=30, now=now_minus_1_day)`. **Schedule auf Montag 06:00 UTC** verschieben. Zusätzlich Cron-Schedule-Toggle/Kill-Switch (`ENABLE_BRIEF_GEN_IN_CRON`-ENV-Var) für die ersten 1–2 Wochen, damit man bei Problemen schnell zurückrollen kann ohne Deploy. Aufwand-Schätzung: 4–6 Stunden inkl. Tests + Cost-Floor-Alert.

---

## 3. Premortem (Phase C)

> **Setting:** Sonntag, 14.06.2026, 14:00 CEST. Creative Radar im Krisenmodus. Was hat uns dorthin gebracht?

### 3.1 Failure-Mode-Inventur

Score = P × I (HH=9, HM=6, MM=4, ML=2, LL=1). Sortiert nach Score absteigend.

| # | Failure-Mode | P | I | Score | Frühwarnsignale | Mitigation-Richtung |
|---|---|---|---|---|---|---|
| 1 | **Stale-Brief in Live-UI ohne Warnsignal** — Cutter trifft Schnitt-Entscheidung auf 2–4 Wochen altem Insight, ohne dass UI ihn warnt; bemerkt erst nach erstem peinlichen Meeting | H | H | **9** | `MAX(generated_at)` > 8 Tage; User-Beschwerde; Cutter zitiert veraltete TT-Trends | Stale-Warning-Komponente vor `LLMOutput`, plus Toast wenn `generated_at < weekStart - 2d`. Defensive Schicht — unabhängig von Backend-Cadence |
| 2 | **Brief-Gen-Bug bleibt nach Fix bestehen / regrediert** — Backend-CI-Gate paused (PR #143), kein Integration-Test für Cron-→-Brief-Pfad → Refactor in 3 Wochen schlägt unbemerkt zu | H | H | **9** | Anthropic-Cost $0.00 an einem Cron-Tag; `summary_json` ohne Brief-Block; manueller User-Report | (a) Integration-Test „Cron triggert Brief-Gen für aktive Pairs"; (b) PR #143 merge + Branch-Protection; (c) Cost-Floor-Alert „Sonntag-Cron < $5 Anthropic" |
| 3 | **F0.7-Anthropic-Cap-Lücke bleibt offen, Force-Trigger explodiert** — Wolf testet Voice-V2 mit `force=true&pair=all` und triggert versehentlich $50+ in einem Lauf, kein Cap stoppt es | M | H | **6** | `costlog` zeigt > $10 Anthropic in < 1h; manuelle Wolf-Notice | F0.7 implementieren mit `aggregate_anthropic_costs_since(month_start)` analog F0.6; Hard-Cap + Kill-Switch ENV; Pre-Flight in `generate_and_persist_report` und in `regenerate_insights` (admin-Pfad) |
| 4 | **ISO-Wochen-Schedule-Inkonsistenz im Cadence-Fix** — neuer Cron-Trigger schreibt für „diese KW", überschreibt Mittwochs-Manuell-Brief, Cost-Doppelausgabe + Datenverlust | M | H | **6** | PK-Konflikt-Warning in Logs (Last-Write-Wins is silent); plötzlicher Cost-Spike auf $30/Woche; Diff-Audit der Briefs ergibt Verschwinden des manuellen Briefs | Cadence-Fix-Ticket muss `now - 1 day` verwenden + Schedule auf Mo 06:00 UTC; Pre-Flight-Check `existing is not None and not force` → skip (statt overwrite) |
| 5 | **Apify-Cap-Bypass durch manuellen Trigger** — Wolf ruft Monitor-Endpoint vor Cron, kein Pre-Flight-Cap dort, überschreitet F0.6 | M | M | **4** | Monatsende: `compute_apify_monthly_spend > $50`; budget_warning=True ohne Hard-Stop | Pre-Flight in allen Apify-aufrufenden Endpoints, nicht nur Cron; oder zentraler `assert_apify_budget()`-Decorator |
| 6 | **Scrape-blocked-Pattern wächst** — bad_robot ist Vorbote, in 4 Wochen 10+ Channels mit verifizierten Profilen + 0 Posts | M | M | **4** | `zero_yield_channels` > 10 in einem Cron; FU-1-Surface zeigt Klumpung bei IG-Verified-Accounts | Eigene „scrape-blocked"-Kategorie in Channel-Status + Apify-Actor-Alternative oder Backoff-Strategie; Diagnose-Ticket eröffnen |
| 7 | **YouTube-API-Quota-Erschöpfung** — marvel + paramountplus 500-Pattern eskaliert, weitere Channels stranden | M | M | **4** | `quota_units_used` nähert sich 10000/Tag; `failed_channels[].error_class='quota_exceeded'` häufiger | Quota-Monitoring im Cron-Summary (existiert ansatzweise); per-Tag-Cap im YT-Connector mit Pause; Channel-Bucket-Rotation YT-spezifisch tunen |
| 8 | **UK-Channel-Whitelist-Drift** — aktive Channels mit 0 Posts kosten Apify ohne Yield | M | M | **4** | FU-2-Bucket-Classifier zeigt > 5 Channels in Bucket B („totes Profil"); manueller Wolf-Browser-Check listet weitere | Monatlicher FU-2-Run + automatische Deactivation nach 60 Tagen 0-Yield (mit Wolf-Override-Liste); negative-findings.md als Audit-Trail |
| 9 | **DB-Migration-Regression** — Sprint-Sequence ohne ausreichende Pool-Tuning-Tests, Sprint-3b/c-Race kommt zurück | L | H | **6** | `brief_lock_timeout` in Logs; doppelte LLM-Calls für gleiche (pair, week); 429-Häufung | Smoke-Test-Skript `smoke_test_async_pool.py` ausführen + Test-Coverage erweitern; PR #143 = Voraussetzung |
| 10 | **Anthropic-Modell-Deprecation** — `claude-opus-4-7` wird Q3 2026 ersetzt; hardcoded in `OPUS_MODEL_ALIAS` | L | M | **2** | Anthropic-Deprecation-Notice; 410 / 400 mit `model_not_found` | Modell-String per ENV-Var override-fähig machen (ist es teilweise via `model=`-Param schon); Monitoring auf Anthropic-API-Statusseite |
| 11 | **Token-Rotation-Pending (seit 06.05.)** — Apify/Anthropic/Railway-Tokens nicht rotiert; ein Leak nutzt die alten Schlüssel | M | H | **6** | Apify-Konsole zeigt API-Calls aus ungewöhnlichen IPs; Anthropic-Cost-Spike ohne Wolf-Aktion | Token-Rotation-Ticket vor 24.05.; alle drei Provider; auto-Pre-Deployment-Check |
| 12 | **Trailerhaus-Voice-Prompt-Regression** — Prompt-Iteration ohne A/B-Snapshot, schleichende Stil-Drift | M | M | **4** | `docs/voice-snapshots/` läuft auseinander; Wolf-stichprobe: Brief liest „daneben"; LLM-`text`-Block-Volumen-Drift | Voice-Snapshot-Diff-Check vor jedem Prompt-Push (`ab_test_insight_prompt.py` existiert); Output-Quality-Check als CI-Step |
| 13 | **Single-Adapter-Failure-Kaskade** — YT-Adapter crasht (Auth-Error oder Library-Bug), kein Try/Except auf Stage-Ebene, kompletter Cron failt | L | H | **6** | `cron_run.status='failed'` mit Exception-Message zeigt YT-Trace; nachfolgende Stages laufen nicht | Stage-Isolation: jede Plattform-Stage in eigenem Try/Except (IG/TT existiert, YT-Loop-Internal-Catch existiert, **aber `_execute_youtube_sync`-Top-Level-Fehler** würde Cron killen) |
| 14 | **Vision-Cost-Drift** — heute $0.75/Run, kein Cap, neue Asset-Bursts (z.B. Marvel-Mega-Drop) treiben auf $20+ | M | M | **4** | Vision-Cost > $5/Run; `attempted >> succeeded` (= Fetch-Fail-Pattern); Asset-Burst-Tag | Hard-Cap in `_run_vision_after_sync` (`cron_vision_max_assets_per_run` existiert; **Cost-Cap parallel** ergänzen analog F0.7) |
| 15 | **Apify-Actor-Breaking-Change** — `clockworks~tiktok-scraper` ohne Pin, Actor-Author pusht Schema-Break | L | H | **6** | TT-Cron-Summary mit `raw_items=0` trotz vorher 100+; Apify-Konsole zeigt Actor-Version-Update | Actor-Version-Pin in Settings; Smoke-Test-Sample bei jedem Cron-Start |
| 16 | **PR #80 Reanimation triggert Race-Storm** — falls jemand PR #80 reaktiviert ohne Sprint-3c-Pattern | L | H | **6** | parallele `brief_pipeline_start` für gleiches `(pair, week)`; doppelte Anthropic-Calls; `lock_dedup`-Outcome häufig | PR #80 review-gate; double-check-locking-Pattern-Compliance prüfen |
| 17 | **Schema-Drift ohne Backend-CI** — solange PR #143 paused, mergt eine Migration ungeprüft und kollidiert mit Prod-Schema | M | H | **6** | Alembic-`upgrade head`-Fail in `preDeployCommand`; Railway-Deploy-Loop | PR #143 unblock; CI auf Migrations-Diff-Test ausweiten |
| 18 | **Doku-Drift** — negative-findings/memory/README divergieren; neuer Engineer (oder Wolf in 4 Wochen) liest falsche Annahme | M | M | **4** | Kommentare im Code referenzieren „cadence-sprint" als zukünftig (faktisch); README erwähnt Cron-Briefs als fertig | Single-Source-of-Truth ist `docs/handovers/` mit Sprint-Datum; quarterly Doku-Audit |
| 19 | **Black-Swan: Railway-Service-Termination** — Service-Quota oder Account-Lock, kompletter Backend-Ausfall | L | H | **6** | Railway-Status-Notice; Health-Endpoint 502+ stabil | Backup-Postgres-Snapshots regelmäßig (existiert?); Render-/Fly.io-Migration-Plan im Schubladenkasten |
| 20 | **Black-Swan: Apify-Account-Sperrung** wegen Anti-Scraping-Policy-Verschärfung | L | M | **2** | Apify-Konsole-Notice; cron-summary mit 100% failed_channels (Auth-Error) | Account-Diversifikation; Vertragsklausel-Check; Backup-Provider-Recherche |

### 3.2 Top-5 Recommended Diagnose/Fix-Tickets

Priorisiert nach `Score × Aufwand-Inversion`, gefiltert auf „realistisch vor 24.05./31.05. machbar".

#### Ticket 1 — Backend: Cadence-Implementation für Sonntag-Cron (Score 9, FM #2)

- **Was:** In `_run_cron_sync_background` (`backend/app/api/cron.py:557-617`) nach Rematch-Stage, vor Cost-Aggregation, eine Brief-Generation-Stage einbauen, die für alle `enabled` Pairs `generate_and_persist_report(session, k, window_days=30, now=now-1d)` aufruft. Schedule in `.github/workflows/cron-sync.yml:18` auf Montag 06:00 UTC (`0 6 * * 1`) verschieben. ENV-Toggle `ENABLE_BRIEF_GEN_IN_CRON` als Kill-Switch. Per-Pair-Try/Except, Summary-Block `summary["briefs"]` mit `generated`/`skipped`/`failed`-Countern.
- **Aufwand:** 4–6 h Code + 1–2 h Tests + 1 h Smoke-Run (eingeschaltete ENV in Staging, falls existiert; sonst Mo 18.05. manuell triggern und beobachten)
- **Output:** Sonntag-→-Montag-Cron generiert automatisch 9 Briefs für die abgeschlossene KW; DP2 für F0.7-Cap-Entscheidung am 24.05. kommt aus dem Cron, nicht aus manuellem Trigger
- **Abhängigkeiten:** PR #143 ideal vorher gemerged (Test-Gate); H4-Lösung (now-1d + Mo-Schedule) muss im Ticket explizit dokumentiert sein
- **Sprint-Zuordnung:** **vor 24.05.** — sonst rutscht DP2 um eine Woche

#### Ticket 2 — Frontend: Stale-Brief-Warning-Komponente (Score 9, FM #1)

- **Was:** In `frontend/src/InsightWeekly.jsx` zwischen Header und LLM-Output eine Warnung rendern, wenn `generated_at` älter als der Anfang der aktuellen ISO-Woche ist. Verstärkte Warnung („Brief aus letzter Woche" → orange Box) bei > 7 Tagen; Notiz („Brief vom …, älteres Datenfenster" → graue Box) bei 1–7 Tagen alt im selben KW-Slot. Logik: Date-fns oder Luxon `getISOWeek`-Vergleich. Optional: Trigger-Button „Brief neu generieren" mit `?force=true` aufruf (Admin-only via Auth-Header).
- **Aufwand:** 2–3 h Code + Vitest-Snapshot-Test
- **Output:** Cutter sieht beim Öffnen sofort visuell, wenn ein Brief alt ist; defensive Schicht funktioniert auch wenn Backend-Cadence aus irgendeinem Grund nicht greift
- **Abhängigkeiten:** keine — kann unabhängig zu Ticket 1 deployed werden (Netlify-Auto-Deploy)
- **Sprint-Zuordnung:** **vor 24.05.** — Cutter-Trust-Risk akut

#### Ticket 3 — Backend: F0.7-Anthropic-Hard-Cap (Score 6, FM #3)

- **Was:** Analog zu F0.6 `compute_apify_monthly_spend` eine `compute_anthropic_monthly_spend`-Funktion in `budget_check.py` ergänzen (read `costlog` für `provider='anthropic'` im Kalendermonat). Hard-Cap-Pre-Flight in (a) `_run_cron_sync_background` vor Brief-Gen-Stage (Ticket 1) und (b) `regenerate_insights` (admin.py:437) und (c) `generate_and_persist_report` Top-Level. ENV: `ANTHROPIC_MONTHLY_BUDGET_USD` (Default $100), `ANTHROPIC_BUDGET_ENFORCED` (Default True).
- **Aufwand:** 3–4 h Code + 1 h Tests (Strukturkopie aus F0.6)
- **Output:** Force-Trigger auf alle Pairs kann nicht mehr $50+ in einem Lauf verbrennen; DP2-Entscheidung am 24.05. fließt direkt in den ENV-Wert
- **Abhängigkeiten:** Ticket 1 ideal vorher (sonst greift Cron-Pre-Flight ins Leere)
- **Sprint-Zuordnung:** **vor 31.05.** — nicht 24.05.-blocking, aber spätestens nach DP2

#### Ticket 4 — Backend: Cost-Floor-Alert + Brief-Gen-Summary-Block (Score 9, FM #2 sekundär)

- **Was:** Im Cron-`summary_json` neuen Block `summary["briefs"]` ergänzen mit `{generated: int, skipped_cache_hit: int, failed: int, cost_usd_cents: int}`. Logger-Alert (zunächst nur Logger, später ggf. Slack/Email) wenn an einem Cron-Sonntag `summary["briefs"]["generated"] == 0 AND summary["anthropic"]["total_cost_usd_cents"] < 500` — das ist das Frühwarnsignal #1 in der Failure-Mode-Tabelle.
- **Aufwand:** 2 h, kombiniert mit Ticket 1 trivial (gleiche Datei)
- **Output:** Brief-Gen-Stille wird beim ersten Auftreten sichtbar, nicht erst nach 4 Tagen UI-Diagnose
- **Abhängigkeiten:** Ticket 1
- **Sprint-Zuordnung:** **kombiniert mit Ticket 1**

#### Ticket 5 — Repo: PR #143 unblock + Brief-Gen-Integration-Test (Score 9 + 6, FM #2 + FM #17)

- **Was:** (a) PR #143 ready-for-review setzen und Wolf-Approve einholen; CI-Run beobachten; bei grün Branch-Protection aktivieren (Wolf-UI-Aktion). (b) Neuen Test `test_cron_triggers_brief_gen.py` ergänzen, der den Cron-Background-Task gegen ein Mock-LLM laufen lässt und verifiziert, dass `insight_report` für alle aktiven Pairs befüllt wird. (c) Test in der Backend-CI als Required-Check markieren.
- **Aufwand:** 1 h PR-Pflege + 2–3 h Integration-Test + 0,5 h Branch-Protection
- **Output:** Cron-→-Brief-Pfad ist regressionsgesichert; nächste Refactor-Welle kann ihn nicht unbemerkt killen
- **Abhängigkeiten:** Ticket 1 (Test braucht Ziel-Code)
- **Sprint-Zuordnung:** **vor 31.05.**

**Zwischenpriorisierung für die Sprintplanung:**

| Reihenfolge | Ticket | Deadline | Begründung |
|---|---|---|---|
| 1 | **#2 Frontend Stale-Warning** | so früh wie möglich, < 18.05. | unabhängige defensive Schicht; minimaler Aufwand |
| 2 | **#1 + #4 Backend Cadence + Summary-Block** | < 24.05. | DP2-Quelle für F0.7-Entscheidung; ISO-Schedule-Frage explizit lösen |
| 3 | **#5 PR #143 + Integration-Test** | < 31.05. | Regression-Schutz, verhindert dass #1/#2 stillen Rückbau erleiden |
| 4 | **#3 F0.7-Anthropic-Cap** | < 31.05. nach DP2 | Wert für Cap kommt aus DP2-Auswertung |

---

## 4. Anhang: SQL-Queries (read-only, copy-paste-ready)

```sql
-- costlog vs insight_report Konsistenz für den heutigen Cron-Run
SELECT created_at, provider, model, cost_usd_millicents
FROM creative_radar.costlog
WHERE created_at > '2026-05-17 06:00:00+00'
  AND created_at < '2026-05-17 11:00:00+00'
ORDER BY created_at;

-- cron_run summary für heutigen Run
SELECT id, started_at, completed_at, status,
       (summary_json::jsonb) AS summary
FROM creative_radar.cron_run
WHERE id = 'bd5d0a6a-896b-4c1f-8c09-b0fd1de00adc';

-- Letzte 5 Cron-Runs mit Anthropic-Cost-Block (zur Cadence-Verifikation nach Ticket 1)
SELECT id, started_at, status,
       summary_json -> 'anthropic' AS anthropic_block,
       summary_json -> 'briefs'    AS briefs_block
FROM creative_radar.cron_run
ORDER BY started_at DESC
LIMIT 5;

-- Brief-Alter pro Pair (Frontend-Stale-Warning-Vorbereitung)
SELECT pair_key, iso_year, iso_week,
       generated_at,
       EXTRACT(EPOCH FROM (NOW() - generated_at)) / 86400 AS age_days,
       cost_usd_cents
FROM creative_radar.insight_report
ORDER BY pair_key, generated_at DESC;

-- Channel-Whitelist-Drift (FU-2 Light)
SELECT c.handle, c.platform, c.market, COUNT(p.id) AS posts_30d
FROM creative_radar.channel c
LEFT JOIN creative_radar.post p
  ON p.channel_id = c.id
  AND p.published_at > NOW() - INTERVAL '30 days'
WHERE c.active = true
GROUP BY c.handle, c.platform, c.market
HAVING COUNT(p.id) = 0
ORDER BY c.platform, c.market;

-- Anthropic-Monatsspend (DP2-Vorbereitung für F0.7-Cap)
SELECT date_trunc('day', created_at) AS day,
       COUNT(*) AS calls,
       SUM(cost_usd_millicents) / 100000.0 AS cost_usd
FROM creative_radar.costlog
WHERE provider = 'anthropic'
  AND created_at >= date_trunc('month', NOW())
GROUP BY day
ORDER BY day;
```

---

## 5. Anhang: Open Questions

Punkte, die read-only nicht abschließend beantwortbar waren — Wolf-Klärung empfohlen:

1. **War der 10.05.-Burst (6 Briefs) ein `regenerate?pair=all`-Lauf mit damals 3 disabled Pairs, oder ein UI-Click-Loop über 6 spezifische Pair-Seiten?** Beantwortbar via Railway-Application-Logs vom 10.05. 20:18–20:28 UTC (suche nach `brief_request_received` oder `regenerate-insight failed`).
2. **War PR #80 (pool-tuned async) gemerged oder geschlossen?** Nur in MCP `state=open` geschaut; falls Wolf den Status braucht: `mcp__github__pull_request_read` mit `pullNumber=80, method=get`.
3. **Soll der Brief-Gen-Pre-Flight in `generate_and_persist_report` (synchroner UI-Pfad) auch F0.7 prüfen, oder nur der Cron-Pfad und der Admin-Bulk-Pfad?** Vorschlag: alle drei (Schutz auch gegen Bot-/Crawler-DoS auf die UI).
4. **Welche Schedule-Variante bevorzugt Wolf: Montag 06:00 UTC (saubere abgeschlossene KW) oder Sonntag spät-abends 22:00 UTC (KW noch „warm" im Kopf, Berichte erscheinen Sonntag-Spätabend)?** Reine Präferenz-Entscheidung, Code-Logik identisch.
5. **Soll der ENV-Toggle `ENABLE_BRIEF_GEN_IN_CRON` Default=True oder Default=False sein nach erfolgreichem ersten Cron-Lauf?** Initial-Default=True mit der Option, ihn bei Problemen schnell auf False zu stellen, ist defensiv und passt zur Wolf-üblichen Vorgehensweise.

---

## Daily Report — 17.05.2026 (Bug-Hunt + Premortem-Sprint)

### Done

- **Phase A** komplett: Cron-Pipeline, Brief-Gen-Code-Pfad, manuelle Trigger, UI-Anzeige, Open-PRs (#143 only), Cost-Tracking, ENV-Inventur, Tests, Doku — alles inventarisiert
- **Phase B**: H1 als Bug-Ursache bestätigt mit zwei unabhängigen Beweisen (Code-Inspektion + Docstring-Eingeständnis); H2/H3 als gegenstandslos widerlegt; H4 als latentes Cadence-Risiko dokumentiert; UI-Stale-Pfad bestätigt
- **Phase C** komplett: 20 Failure-Modes (inkl. 2 Black-Swans), Top-5-Tickets priorisiert mit Aufwand und Sprint-Zuordnung

### Findings Highlights

- **Bug-Befund: H1 bestätigt.** `backend/app/api/cron.py:557-617` ruft kein `insight_engine`. `backend/app/api/admin.py:441` Docstring sagt explizit „Auto-Trigger im Cron-Lauf folgt im Cadence-Sprint" — der Cadence-Sprint wurde nie umgesetzt. Briefs entstehen heute nur lazy (UI) oder manuell (admin-regenerate). Die zwei Bursts am 10.05. und 13.05. waren manuelle Trigger.
- **Frontend-Lücke**: `InsightWeekly.jsx:1003` rendert `generated_at` roh, **keine** Stale-Warning. Disney-UI zeigt 4 Tage alten Brief ohne Hinweis — Cutter-Trust-Risk.
- **F0.7 Anthropic-Cap fehlt komplett** — nur F0.6 Apify-Cap implementiert. Force-Trigger `regenerate?pair=all&force=true` kann unbegrenzt Cost verbrennen.

### Open Questions an Wolf

Siehe Anhang 5 oben (5 Punkte).

### Recommended Next Sprint

- **Ticket 1** Backend Cadence-Implementation: `backend/app/api/cron.py:557-617` + `.github/workflows/cron-sync.yml:18` — **4–6 h** — vor 24.05.
- **Ticket 2** Frontend Stale-Warning: `frontend/src/InsightWeekly.jsx` — **2–3 h** — so früh wie möglich
- **Ticket 3** F0.7 Anthropic-Hard-Cap: `backend/app/services/budget_check.py` + Cron + Admin-Pfade — **3–4 h** — vor 31.05.
- **Ticket 4** Brief-Summary-Block + Cost-Floor-Alert: kombiniert mit Ticket 1
- **Ticket 5** PR #143 unblock + Cron-→-Brief Integration-Test: **3–4 h** — vor 31.05.

### Validation-Checkliste

- [x] Keine Code-Änderungen committed/gepusht (`git status` clean)
- [x] Keine DB-Writes ausgeführt (nur Lesungen aus Sektion 0 Briefing; keine eigene SQL ausgeführt)
- [x] Keine Railway-Config-Änderungen
- [x] Keine Brief-Gen-Trigger ausgeführt
- [x] Alle SQL-Queries im Output sind `SELECT`-only
- [x] Pfade und Zeilennummern stichprobenhaft verifiziert
- [x] Berlin-Timezone-Stempel an Zeitangaben gepflegt
- [x] Top-5-Tickets haben Aufwandsschätzung
- [x] Bug-Diagnose-Fazit ist mit konkreten Code-Beweisen belegt (Cron-Code + Admin-Docstring)
