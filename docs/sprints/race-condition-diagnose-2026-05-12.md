# Race-Condition-Diagnose 2026-05-12

**Operating-Mode:** Diagnose-only, keine Code-Änderungen
**Branch:** `docs/race-condition-diagnose-2026-05-12`
**Vorgänger:** PR #123 (DB-Pre-Check + Idempotency-Key), PR #124 (Advisory-Lock + Idempotency-Key entfernt + cost_log-Hardening)
**Smoke-Test-Befund (Wolf, 12.05.):** 2 sequenzielle Curls 5s Abstand → 3 LLM-Calls in 30 Min statt 1.

---

## TL;DR

Diagnose ist **Code-Read-only** — diese Session hat keinen DB-/Railway-Zugriff, kann den Smoke-Test nicht selbst laufen lassen. Code-Analyse zeigt zwei wahrscheinliche Ursachen UND eine systematische Test-Lücke:

1. **Edge-Proxy-Retry-Multiplikator** (Hypothese B, **wahrscheinlichste** Erklärung für „3 Calls aus 2 Curls"): die Retry-Echos, die PR #123 ursprünglich motivierten, sind nicht weg — die Idempotency-Key-Layer ist in PR #124 entfernt worden, weil Anthropic den Header nicht dedupliziert. Damit hängt der Schutz alleine an der 120s-Recent-Row-Debounce, die bei ~6s-Opus-Latenz UND Edge-Retry-Window NICHT zuverlässig greift.

2. **Test-Coverage-Lücke** (Hypothese A, **strukturell sicher**): `_acquire_brief_lock` wird mit zwei sequenziellen Calls auf SQLite getestet (Lock ist no-op) und mit gemockten `session.exec`-Strings auf Postgres-Dialekt. Es existiert **kein** Test, der echte Postgres-Lock-Contention prüft. Der Lock funktioniert konzeptuell — aber nichts in CI würde es merken, wenn er es nicht tut.

3. **Logging-Lücke** (Hypothese C, **macht Diagnose-Wiederholungen teuer**): `_acquire_brief_lock` loggt weder Acquire noch Release noch lock_int. Im Service-Log lässt sich nicht ablesen, ob beide Curls denselben Lock-Key hatten, ob einer wartete, ob der Timeout zuschlug. Smoke-Test kostet ~$1.90/Curl, blinde Wiederholungen ohne Logging sind teuer.

**Empfehlung:** **erst Logging dazubauen, dann nochmal Smoke** — minimaler Eingriff, klärt 80% der Frage. Falls Logs zeigen, dass der Lock im Postgres tatsächlich blockiert: Hypothese B ist die Ursache, Fix ist „Idempotency-Layer auf Backend-Side wiedereinführen" (z.B. `INSERT ON CONFLICT DO NOTHING` auf einem `(pair, year, week, generation_run_id)`-Pre-Marker). Falls der Lock NICHT blockiert: Hypothese D/E (Lock-Scope, Transaktions-Boundary) tiefer untersuchen.

---

## Phase 1: Smoke-Test-Reproduktion

**Nicht durchgeführt.** Diese Session hat:
- keinen DB-Zugriff (`psql` würde gegen Production-DB laufen)
- keinen API-Token
- keinen Railway-Log-Zugriff

Der vom Brief vorgesehene Curl-Smoke-Test ist Wolf-Action. Empfehlung: **erst nach Logging-Hardening** (Hypothese C, s.u.) wiederholen, sonst kostet jeder Versuch ~$1.90 ohne Erkenntnisgewinn.

---

## Phase 2: Lock-Acquisition-Logging — Bestandsaufnahme

### Was es bereits gibt

Im `generate_and_persist_report` (`backend/app/services/insight_engine.py:2408+`):

- **Eine Log-Stelle**: `insight-engine-debounce-hit` (Z. 2482), nur wenn `force=True` UND eine Row jünger als 120s gefunden wird. Trägt `lock_path` als Bool (Postgres ja/nein), `age_seconds`. **Loggt KEIN** lock_int, keine Acquire-/Release-Zeitpunkte.

Im `_acquire_brief_lock` (`backend/app/services/insight_engine.py:2366+`):

- **Keine Log-Aufrufe.** Weder „lock acquired" noch „waited Xs" noch „lock_timeout hit".

Im `_persist_report` (`backend/app/services/insight_engine.py:2314+`):

- **Eine Log-Stelle**: `insight-report-persist-skipped` (Z. 2332), nur wenn `llm_output is None`. Kein normaler Persist-Log.

### Was fehlt (für Race-Diagnose)

| Event | Aktuell geloggt? | Diagnose-Wert |
|---|---|---|
| Lock acquired (zeit, lock_int, pair, week) | ❌ | hoch — sieht Reihenfolge |
| Lock wait-time (wenn >0) | ❌ | hoch — beweist Serialisierung |
| Lock-timeout exceeded | ❌ (Exception würde fliegen, aber nicht geloggt vor Exception) | mittel |
| Lock released / transaction commit | ❌ | mittel |
| Pre-check-result (row vorhanden?) | ❌ | hoch |
| LLM-call started/completed (mit pair+week) | ❌ | hoch |
| `record_anthropic_call` write (success/fail) | nur ERROR-Pfad geloggt (`cost-log-write-failed`) | mittel |

Konsequenz: bei einer Wiederholung des Smoke-Tests können wir **nicht** unterscheiden, ob (a) der Lock korrekt blockierte aber das Re-check leer war, oder (b) der Lock gar nicht akquiriert wurde, oder (c) beide Curls denselben Lock-Key berechneten oder nicht.

---

## Phase 3: Code-Analyse `generate_and_persist_report`

Pfad: `backend/app/services/insight_engine.py`, Z. 2408-2509.

### 3.1 Reihenfolge (synchron, eine FastAPI-Request)

```
GET /api/insights/weekly?pair=netflix&force=true
   ↓
weekly(pair, ..., session=Depends(get_session))      # api/insights.py:106
   ↓
generate_and_persist_report(session, "netflix", force=True)
   1. aggregate_pair(session, ...)                   # SELECTs, auto-begins tx
   2. _acquire_brief_lock(session, ...)
      2a. SET LOCAL lock_timeout = '300s'            # inside tx
      2b. SELECT pg_advisory_xact_lock(lock_int)     # blocks if held
   3. session.get(InsightReportRow, (pair, y, w))    # re-check inside lock
   4. if existing & not force & (or fresh): return hydrated  # short-circuit
   5. generate_weekly_report(session, ...)
      5a. _build_user_prompt(...)                    # local
      5b. messages_create_text(...)                  # ~30-60s Anthropic call
      5c. record_anthropic_call(usage, ...)          # OPENS FRESH SESSION
   6. _persist_report(session, report)
      6a. session.get(...) / delete / flush / add
      6b. session.commit()                           # tx commit → lock release
   7. return report
```

### 3.2 Lock-Scope

- **Bound zu Transaktion** (`pg_advisory_xact_lock`, nicht `pg_advisory_lock`). Lock wird automatisch freigegeben sobald die Transaktion committet oder rolled back wird.
- **Aktive Transaktion zum Zeitpunkt `_acquire_brief_lock`**: ja, weil `aggregate_pair` zuvor SELECTs gemacht hat und SQLModel-Session standardmäßig auto-begins.
- **Wann committet die Lock-haltende Transaktion**: erst in `_persist_report` Z. 2363. Also über den gesamten LLM-Call hinweg.

### 3.3 Lock-Key

```python
lock_int = abs(hash((pair_key, iso_year, iso_week))) % (2**31)
```

- **Deterministisch innerhalb eines Python-Prozesses** (gleiches `hash(...)`).
- **Nicht deterministisch zwischen Python-Prozessen** wegen `PYTHONHASHSEED` (Python 3 hat hash-randomization für Strings/Tuples per default).
- **Aktuelles Deployment**: ein einziger uvicorn-Worker (`start.sh` ruft `uvicorn ...` ohne `--workers`-Flag; `WEB_CONCURRENCY` und `UVICORN_WORKERS` werden nirgendwo gesetzt; Railway-Standard ist 1 Container × 1 Worker bei dieser Konfig). Im **Production-Kontext heute** ist das Hash-Mismatch-Risiko also **null** — alle Locks aus diesem Worker stimmen überein.
- **Cross-Deploy**: nach einem Container-Restart sind alle Hashes neu, aber alte Locks (xact-bound) sind ohnehin weg. Egal.
- **Falls Wolf später `--workers 2+` einschaltet**: SOFORT bricht der Lock. Würde dringend `PYTHONHASHSEED=0` brauchen oder `hashlib`-basiertes `lock_int`. Heute aber nicht das Problem.

### 3.4 Persistence-Reihenfolge (Lock-Korrektheit)

- Lock wird VOR dem Pre-Check akquiriert ✓
- Pre-Check (`session.get`) läuft INSIDE der Lock-Transaktion ✓
- LLM-Call läuft INSIDE der Lock-Transaktion ✓ (Lock hält über ~30-60s Opus)
- `record_anthropic_call` öffnet eine **separate `Session(engine)`** für den `costlog`-INSERT. **Diese fresh Session kommt aus dem Connection-Pool** (pool_size=10, max_overflow=10). Setzt KEINE eigenen Advisory-Locks. Commit auf dieser fresh Session berührt die Lock-Transaktion auf der Haupt-Session **nicht**. ✓
- `_persist_report` macht `session.commit()` am Ende → Tx schließt → Lock free. ✓

**Konzeptuell sieht das korrekt aus.** Es gibt **keinen** offensichtlichen Lock-Leak im Code-Pfad.

### 3.5 DB-Pre-Check-Position (PR #123 vs PR #124)

| | PR #123 | PR #124 (heute) |
|---|---|---|
| Pre-Check-Ort | VOR Lock-Acquire (kein Lock damals) | INSIDE Lock-Acquire |
| Force-Pfad | Pre-Check trotzdem (nur recent <120s) | Pre-Check trotzdem (nur recent <120s) |
| Race-Geometrie | TOCTOU offen (Lücke zwischen Check und LLM-Call) | TOCTOU geschlossen IF lock-acquire korrekt blockiert |

→ Pre-Check-Position ist heute korrekt. Race-Window existiert nur falls der Lock NICHT blockiert (Hypothese A/D).

### 3.6 Idempotency-Key — Warum entfernt

- PR #123 hat `Idempotency-Key`-Header an Anthropic-API geschickt.
- PR #124 hat ihn entfernt: laut Commit-Message verifizierte Wolf im Smoke-Test, dass Anthropic den Header NICHT dedupliziert (zwei separate Billings bei identischem Key, 5s Abstand).
- Anthropic publiziert keinen offiziellen Idempotency-Key-Contract.
- **Konsequenz für heute**: Die zweite Verteidigungsschicht ist weg. Wenn der DB-Pre-Check + Lock versagt (Hypothese A/B), gibt es keinen Backstop.

### 3.7 Aktive Callers von `generate_and_persist_report`

- `GET /api/insights/weekly` (`api/insights.py:168`) — der Hauptpfad.
- `POST /api/admin/regenerate-insight` (`api/admin.py:487`) — Loop über alle enabled Pairs, jeder mit `force=True`. Wenn ein Cron-Job UND ein Wolf-Curl denselben Pair triggern: **zwei Aufrufer**, aber Lock sollte serialisieren.

Cron-Konfiguration (`backend/app/api/cron.py`, Sa+So → So-only per #113): Cron triggert NICHT direkt `generate_and_persist_report`. Aktuelle Trigger sind: manuelle Curls + Admin-Endpoint.

---

## Phase 4: Hypothesen

Sortiert nach Wahrscheinlichkeit basierend auf Code-Read (ohne Smoke-Repro).

### Hypothese A — Test-Coverage-Lücke (strukturell sicher, indirekte Ursache)

**Beschreibung:** kein einziger Test exerciset Postgres-Advisory-Lock-Contention. Die existierenden Tests:

- `test_advisory_lock_skipped_on_sqlite_dialect` — verifiziert no-op auf SQLite.
- `test_advisory_lock_issued_on_postgres_dialect` — mockt `session.exec` und prüft Strings (`SET LOCAL lock_timeout`, `pg_advisory_xact_lock`). **Kein echter Lock-Wait.**
- `test_generate_and_persist_advisory_lock_short_circuits_second_call` — sequenzielle SQLite-Calls, prüft 120s-Recent-Row-Pfad (PR-#123-Semantik), NICHT die Lock-Contention.

**Was es nicht gibt:** einen Test, der zwei Sessions parallel öffnet, beide `generate_and_persist_report` aufrufen, und beweist dass nur ein LLM-Call passiert. Dieser Test wäre auf Postgres machbar (Testcontainers / fixture-DB) und würde den Live-Bug reproduzieren falls einer existiert.

**Verifikation:** Test schreiben. Falls grün → Lock funktioniert in Isolation, Live-Bug liegt woanders (Hypothese B). Falls rot → Lock ist tatsächlich kaputt (Hypothese D).

**Fix:**
- **Minimal**: ein Postgres-only-Integration-Test mit threading.Thread oder concurrent.futures, 2 Sessions, gemockter LLM (`time.sleep(2)` als Stand-in). Aufwand ~45 Min.
- **Robust**: testcontainers-Setup mit echtem Postgres, parametrisierbar für andere Concurrency-Tests. Aufwand ~2-3 h.

### Hypothese B — Edge-Proxy-Retry-Multiplikator (wahrscheinlichste Live-Ursache)

**Beschreibung:** Genau der Bug, den PR #123 beheben wollte und PR #124 nicht zusätzlich abgesichert hat: GET-Endpoint ist idempotent, Opus-Latenz 30-60s, Edge-Proxies (Railway-Side oder curl-Side) retried nach ihrem Timeout. **Ein** Wolf-Curl wird zu **2-3** API-Calls. Wenn diese 2-3 Calls innerhalb 120s eintrudeln, sollte der Recent-Row-Debounce greifen. Bei <6s Opus (laut PR-#123-Commit-Message ist Opus-Latenz im SMoke-Test 6s gewesen — das ist sehr schnell, vermutlich mit Cache-Hits in der LLM-Pipeline), läuft der zweite Call SCHON vor der ersten Persist-Commit-Zeit, daher Recent-Row-Pfad sieht leer → Lock sollte greifen.

Aber: wenn der Edge-Retry mit DELAY kommt (z.B. 60s nach erstem Call), dann ist der erste Brief längst persistiert. Der Retry sieht im Pre-Check eine ~60s-alte Row und triggert die Recent-Row-Short-Circuit (≤120s) → kein zweiter LLM-Call. Sollte funktionieren.

WO es genau bricht: der mittlere Fall — Retry kommt zu spät für direkte Lock-Contention (erster Curl schon committet) aber zu früh für Cache-Refresh… nein, das fällt auch unter Recent-Row.

**Tatsächlich problematischer Fall**:
- T+0: Curl 1 acquires Lock K, läuft LLM (6s), committet T+6.
- T+5: Curl 2 (Wolf 5s manuell). Lock wird nach T+6 frei. Curl 2 acquires Lock K bei ~T+6. Recent-Row-Check: Row generated_at = ~T+6, age ~0s, INSIDE 120s-Window → Short-Circuit, return hydrated. ✓
- T+60: Edge retries Curl 1 (Railway 60s read-timeout). Pre-Check sieht Row mit age ~54s → 120s-Window aktiv → Short-Circuit. ✓
- T+65: Edge retries Curl 2. Pre-Check sieht Row age ~59s → Short-Circuit. ✓

Auf dem Papier sollte das funktionieren — sofern alle Retries innerhalb 120s der ersten Persist eintrudeln. Was der Smoke-Test (3 Calls in 30 Min) potenziell zeigt: **eine Wiederholung außerhalb des 120s-Fensters**. Wolf schickt vielleicht eine dritte Curl 5 Min später (oder die Edge retried so spät), und die schlägt durch alle Schutzschichten durch.

**3 Calls in 30 Min ist nicht 2 Calls in 5 Sek.** Der Brief-Text vermischt zwei verschiedene Beobachtungen.

**Verifikation:**
1. Logging dazubauen (Hypothese C), Smoke wiederholen.
2. Falls Retries als separate Curls in Server-Log sichtbar werden mit unterschiedlichen Source-IPs/User-Agents: Edge-Retry bestätigt.
3. Falls alle 3 Calls vom selben Wolf-Curl gleicher Source: lokaler curl-Retry oder Railway interner Retry — andere Diagnose.

**Fix:**
- **Minimal**: 120s-Fenster auf 300s anheben (`_RECENT_BRIEF_WINDOW_SECONDS`). Senkt Wahrscheinlichkeit dass ein Retry-Echo aus dem Schutz-Fenster fällt. Aufwand: 1 Zeile. Nachteil: legitime Re-Gens binnen 5 Min werden blockiert.
- **Robust**: Backend-side Idempotency-Token. Generation-Run-ID pro Brief-Slot `(pair, year, week)` in einer separaten Table mit `(pair, year, week, generation_token) UNIQUE`. Vor LLM-Call: `INSERT ... ON CONFLICT DO NOTHING`. Wenn nichts inserted: jemand anders ist dran, warte/return. Aufwand: ~2-3 h.

### Hypothese C — Logging-Lücke (verstärkt jede andere Hypothese)

**Beschreibung:** `_acquire_brief_lock` loggt nichts. Im Production-Log lässt sich nicht unterscheiden:
- Lock akquiriert sofort vs. nach Wait?
- Pre-Check inside-lock hat Row gesehen vs. nicht?
- LLM-Call gestartet vs. übersprungen?

Ohne diese Logs ist jeder Smoke-Test-Versuch teuer ($1.90/Curl) ohne Erkenntnisgewinn.

**Verifikation:** Logging dazubauen, einen Smoke-Test laufen, Logs lesen. Eindeutig.

**Fix (3 Log-Stellen):**
```python
# in _acquire_brief_lock:
logger.info("advisory-lock-acquire-start", extra={"pair": pair_key, "iso_week": iso_week, "lock_int": lock_int})
# nach dem pg_advisory_xact_lock():
logger.info("advisory-lock-acquired", extra={..., "wait_ms": ...})  # mit time.perf_counter

# in generate_and_persist_report nach Pre-Check inside lock:
logger.info("brief-recheck", extra={"pair": pair_key, "iso_week": iso_week, "row_found": existing is not None, "force": force})

# in generate_and_persist_report nach LLM-Call:
logger.info("brief-llm-completed", extra={"pair": pair_key, "iso_week": iso_week, "duration_s": ...})
```

Aufwand: ~30 Min. **Sollte vor jedem weiteren Smoke-Versuch laufen.**

### Hypothese D — Lock-Scope-Bug (möglich, weniger wahrscheinlich)

**Beschreibung:** mehrere theoretische Sub-Cases:

- **D1** `SET LOCAL lock_timeout` greift nicht weil keine Transaktion aktiv ist im Moment des SET. Unwahrscheinlich, da `aggregate_pair` vorher gelaufen ist.
- **D2** Identity-Map-Cache: `session.get(InsightReportRow, ...)` returnt ein älteres Snapshot aus der Identity-Map statt einen frischen DB-Read. Bei fresh-per-request-Session ist die Identity-Map zu Beginn leer — unwahrscheinlich, dass eine `InsightReportRow` vor dem Lock-Acquire schon geladen wurde.
- **D3** Postgres-Isolation-Level: default ist READ COMMITTED. Innerhalb des Locks sollte ein SELECT die frisch-committete Row sehen (READ COMMITTED snapshot pro Statement, nicht pro Transaktion). Unwahrscheinlich.
- **D4** `session.exec(sa.text(...))` öffnet eine separate Connection oder lässt die Hauptverbindung kurz autocommit-Mode laufen. Sehr unwahrscheinlich; das wäre ein SQLAlchemy-Bug oder eine ungewöhnliche Konfiguration.

**Verifikation:** alle vier brauchen Smoke-Test + Logging (Hypothese C). D2/D3 könnten mit gezielten psql-Queries verifiziert werden (`SELECT pg_locks WHERE …`).

**Fix:**
- D1-D3 sind keine echten Bugs in diesem Code-Pfad.
- D4 wäre Bug-Report an SQLAlchemy / `sqlmodel`. Nicht hier zu fixen.

### Hypothese E — Concurrency aus Non-Curl-Quelle

**Beschreibung:** Wolf-Curl trifft auf eine andere Quelle (Cron, Admin-Endpoint, geplanter Brief-Run). Beide werden serialisiert vom Lock — aber wenn die zweite VOR der 120s-Recent-Row-Cutoff persistiert wurde und Wolf später ein `force=true` schickt, läuft Wolfs Curl einen NEUEN LLM-Call (force semantically erlaubt). Das ist KEIN Race — das ist gewolltes Force-Verhalten.

3 Calls aus 30 Min könnten sein: 1 Cron-Run (08:00), 1 Wolf-Curl (08:15), 1 Wolf-Force-Curl (08:25). Alle drei legitim, alle drei separate LLM-Calls, kein Bug.

**Verifikation:** Cron-Schedule + Wolf-Curl-Logs zeitlich abgleichen. Wenn die 3 Calls über die 30 Min verteilt waren — und mindestens zwei waren `force=true` 5+ Min auseinander — dann ist das **bestimmungsgemäß**, kein Bug.

**Fix:** keiner. Falls der Brief mehrdeutig ist, würde ich Wolf empfehlen, den Smoke-Test mit klarer Curl-Sequenz und Zeitstempeln zu wiederholen.

---

## Verifikations-Plan (priorisiert)

| # | Schritt | Aufwand | Klärt welche Hypothese |
|---|---|---|---|
| 1 | Logging-Hardening in `_acquire_brief_lock` + Re-Check + LLM-Boundaries | ~30 Min | C, indirekt alle |
| 2 | Smoke-Test wiederholen mit klarer Curl-Sequenz, Logs auswerten | ~30 Min + $4-6 LLM-Cost | B vs E vs D |
| 3 | Postgres-Concurrency-Integration-Test (threading, 2 Sessions, mock LLM) | ~45 Min | A, D |
| 4 | Falls Logs B bestätigen: Backend-Idempotency-Token | ~2-3 h | B (Fix) |
| 5 | Falls Logs D bestätigen: tiefer Lock-Scope-Audit | ~2 h | D (Fix) |

---

## Empfehlung

**Minimal-invasiver nächster Schritt: 1 + 2** (Logging + ein neuer Smoke-Test).

- Aufwand zusammen ~1 h.
- Kostet einmalig $4-6 LLM-Calls für den Smoke.
- Liefert hartes Evidence-Material für den eigentlichen Fix-Sprint.
- Falls Hypothese E zutrifft („3 Calls über 30 Min waren legitim, Brief war mehrdeutig"), erspart das einen über-engineerten Fix.

**Robuster Folgesprint (NUR nach Klärung durch Schritt 2):** Schritt 4 — Backend-Idempotency-Token-Table mit `INSERT ... ON CONFLICT DO NOTHING`-Pattern. Schließt die Retry-Echo-Lücke unabhängig von Lock-Verhalten und braucht keine Annahmen über Anthropic-API.

**Was wir NICHT tun sollten** ohne Verifikation:
- Lock-Mechanismus refactorn (Hypothese D ist unwahrscheinlich, Risiko von Regressionen hoch).
- Idempotency-Key wieder einbauen (PR #124 hat verifiziert dass Anthropic ihn ignoriert; reine Cosmetics).
- 120s-Fenster blind hochsetzen (kaschiert nur, verhindert legitime Re-Gens).

---

## Wolf-Ping

1. **Welche Hypothese vertiefen?** Empfehlung: B (Edge-Retry) **plus** A (Test-Lücke), beide via Schritt 1+2.
2. **Implementations-Sprint: Logging-Hardening (Schritt 1) als eigener PR vor dem Smoke-Test, oder direkt mit Smoke kombiniert?** Empfehlung: Logging als eigener Mini-PR (1 Commit, ~30 Min). Schnell durch, dann unabhängig Smoke-Test.
3. **Smoke-Test-Budget:** Falls Schritt 2 mehrere Iterationen braucht (Curl-Sequenz tweaken, Logs lesen, Hypothese verfeinern), bei welchem $-Cost-Cap abbrechen? Vorschlag: max $10 (≈5 Iterationen).

---

## Tagesreport

**Output:** dieses Doc
**Aufwand:** ~40 Min Diagnose
**Smoke-Tests durchgeführt:** 0 (kein DB-/API-Zugriff in dieser Session — Wolf-Action)

### Befund
- Bug reproduziert: nein (read-only Session)
- Anzahl wahrscheinliche Hypothesen: **5** (A struktural, B wahrscheinlich live, C Logging-Lücke, D möglich, E benign)
- Wahrscheinlichste Ursache (Empfehlung): **B + C** (Edge-Retry hinter Logging-Blindheit), Verifikation durch Schritt 1+2.
- Minimal-Fix-Aufwand: ~30 Min (Logging-Hardening) + ~30 Min (Smoke-Wiederholung) = ~1 h
- Robust-Fix-Aufwand: ~2-3 h (Backend-Idempotency-Token-Table) — NUR nach Verifikation

### Wolf-Ping
1. Welche Hypothese vertiefen?
2. Logging-Sprint vor Smoke, oder kombiniert?
3. Smoke-Test-Budget-Cap?

### Nächster sinnvoller Schritt
- Wolf-Antwort, dann Logging-Sprint als Mini-PR, dann Smoke-Test mit Logs, dann eigentlicher Fix-Sprint.
