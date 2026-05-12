# Handover: Creative Radar Race-Condition — Sprint 3b done, Sprint 3c bereit

**Datum:** 12.05.2026 ~21:00 CEST
**Zweck:** Nahtloser Restart in neuem Chat. Sprint 3b ist abgeschlossen, Bug verifiziert mit präziser Diagnose. Sprint 3c (Fix) ist als Briefing bereit, kann direkt an Claude Code.

---

## Sprint 3b — Final-Befund

### Setup
- **Datum:** 12.05.2026
- **Run-Sheet PR:** #140 (gemerged), Iter-1-Paste-Back Commit 7e93701
- **Iteration ausgeführt:** 1 von 5 geplant (Hard-Stop nach Iter 1 wegen Hypothese-Verifikation)
- **Cost-Impact:** \$3.62 (von \$10 Budget-Cap)
- **Anker-Werte:**
  - ANCHOR_UTC=2026-05-12T18:42:59Z
  - ANCHOR_BERLIN=2026-05-12 20:42:59 CEST
  - C1 started: 20:43:42 CEST
  - C2 started: 20:43:47 CEST (5s nach C1)

### Beobachtung

**Curl-Output:**

| Curl | Start | Duration | Status |
|---|---|---|---|
| C1 | 20:43:42 | 126.9s | 200 |
| C2 | 20:43:47 | 235.6s | 200 |

**DB-State nach Iter 1:**
- costlog: 2 Anthropic-Calls (rows=2, total_millicents=362312 = \$3.62)
- insight_report netflix iso_week 20: 1 row (Composite-PK verhindert zweiten Insert)

### Log-Forensik — Timeline (UTC)

Vollständige Timeline siehe docs/sprints/race-condition-smoke-2026-05-12.md (Iter 1 Paste-Back). Kernpunkte:

- 18:43:43.163 — C1 brief_request_received
- 18:43:44.044 — C1 brief_lock_acquired (wait_ms=12)
- 18:43:48.057 — C2 brief_request_received (eigener Curl, andere Source-IP)
- 18:43:48.710 — C2 brief_lock_attempt — wartet 125s auf Lock
- 18:45:49.733 — C1 brief_pipeline_done (Brief in DB)
- 18:45:49.734 — C2 brief_lock_acquired (wait_ms=125024) — Lock funktioniert!
- 18:45:49.763 — C2 brief_llm_call_start — HIER ist der Bug
- 18:47:43.473 — C2 brief_pipeline_done (überflüssiger Brief in DB)

Source-IPs: C1=100.64.0.14, C2=100.64.0.15 (unsere eigenen Curls).

### Hypothesen-Verifikation

| Hypothese | Status |
|---|---|
| A — strukturell (Test-Lücke) | offen, nicht durch Smoke testbar |
| B — Edge-Proxy-Retry | WIDERLEGT (unsere eigenen Curls mit 5s Gap, unterschiedliche IPs) |
| C — kein Logging | erledigt durch PR #138/#139 |
| D — Lock-Scope-Bug | VERIFIZIERT (präzise Code-Ursache identifiziert) |
| E — legitime Mix-Calls | WIDERLEGT (deterministisch reproduzierbar) |

### Bug-Mechanik (präzise)

**Was funktioniert:**
- Advisory-Lock pg_advisory_xact_lock greift korrekt
- C2 wartet 125 Sekunden auf den Lock (wait_ms = 125024)
- Lock wird sauber freigegeben nach C1 Transaction-Commit

**Was kaputt ist:**
- Nach Lock-Acquire macht C2 brief_precheck_done und geht direkt zu brief_llm_call_start
- Der Pre-Check (PR #123) wurde mit force=true umgangen
- C2 macht den vollen LLM-Call obwohl C1 frischer Brief schon in der DB ist
- Beim Persist findet Composite-PK-Konflikt statt — Brief wird überschrieben

**Konzeptuelles Problem:**
Pre-Check ist als First-Read-Optimization vor dem Lock designt, nicht als Double-Check-Locking nach dem Lock. Bei force=true wird der Pre-Check ignoriert — richtig bei einem einzelnen Force-Call, falsch bei parallelen Force-Calls.

### Cost-Impact

- **Diagnose-Cost heute:** \$3.62 (für 2 parallele Force-Curls in Iter 1)
- **Production-Impact bei parallelen Force-Curls:** \$1.81 pro überflüssigem Call
- **Cron-Impact:** vernachlässigbar (Cron läuft sequentiell)

---

## Sprint 3c-Briefing — Race-Condition-Fix

### Ziel
Double-Check-Locking-Pattern implementieren: nach Lock-Acquire IMMER prüfen, ob ein frischer Brief existiert (auch bei force=true), bevor LLM-Call gestartet wird.

### Scope

**Datei:** backend/app/services/insight_engine.py generate_and_persist_report (Z. ~2289-2347)

**Konzeptueller Fix:**

Aktuell (vereinfacht):

    def generate_and_persist_report(pair_key, iso_week, force=False, ...):
        if not force:
            existing = check_existing_report(pair_key, iso_week)
            if existing:
                return existing
        with advisory_lock(pair_key, iso_week):
            if not force:
                existing = check_existing_report(pair_key, iso_week)
                if existing:
                    return existing
            result = anthropic_call(...)
            persist(result)
            return result

Fix:

    def generate_and_persist_report(pair_key, iso_week, force=False, ...):
        if not force:
            existing = check_existing_report(pair_key, iso_week)
            if existing:
                return existing
        with advisory_lock(pair_key, iso_week):
            # Post-Lock Pre-Check IMMER, auch bei force=true
            existing = check_existing_report(pair_key, iso_week)
            if existing:
                log_pipeline_done(outcome='lock_dedup')
                return existing
            result = anthropic_call(...)
            persist(result)
            return result

**Sub-Frage zu klären — was zählt als frischer Brief?**

- Option a: existing iso_week + iso_year row in insight_report → return (einfach)
- Option b: existing row UND generated_at > NOW() - INTERVAL '5 minutes' → return (defensiver)

Empfehlung: Option a. force=true heißt ignoriere First-Check, nicht ignoriere Composite-PK.

**Logging-Ergänzung:**
- Neuer outcome-Wert: outcome=lock_dedup in brief_pipeline_done

### Tests

Neue Tests in backend/app/tests/test_insight_engine.py:

1. test_force_call_after_existing_returns_existing — pre-create insight_report row, generate_and_persist mit force=true, expect: existing zurück, KEIN LLM-Call
2. test_force_call_no_existing_creates_new — keine row, generate_and_persist mit force=true, expect: LLM-Call + persist
3. test_double_check_locking_concurrent — simuliere 2 parallele force=true Calls (mocked Anthropic, mocked Lock), expect: nur 1 LLM-Call

Test 3 ist der entscheidende.

### Verifikations-Smoke nach Merge

Wiederhole Sprint 3b Iter 1 mit demselben Pattern:
- 2 sequentielle Force-Curls auf sonypictures (damit netflix iso_week 20 nicht verfälscht), 5s Gap
- **Erwartung:** costlog rows=1, nicht 2. Cost-Halbierung von \$3.62 auf \$1.81

### Operating-Mode

- Autonomous für Implementation
- Branch: fix/race-condition-double-check-locking
- One-Fix-Per-Commit:
  - Commit 1: Pre-Check nach Lock immer ausführen (auch force=true)
  - Commit 2: outcome=lock_dedup Logging
  - Commit 3: Tests (3 neue)

### Pre-Commitments

- outcome-Name: lock_dedup
- force=true Verhalten unverändert für single-call
- Backwards-Compat: existing API-Contract bleibt

### Stop-Bedingungen

1. Code-Struktur stark anders als oben angenommen → Wolf-Ping
2. PR #123 Pre-Check ist an unerwarteter Stelle → Wolf-Ping
3. Tests können pg_advisory_xact_lock nicht in SQLite-Tests simulieren → Wolf-Ping

---

## Memory-Update-Vorschlag (nach Sprint 3c Merge)

**REPLACE Tech-Debt-Eintrag:**
Creative Radar Tech-Debt 12.05.2026 EOD: UNIQUE-Constraint PR #134 ✓. Test-Failures PR #136 ✓ (Baseline 558/0). PR #117 gemerged ✓. Race-Condition PR #XXX (Double-Check-Locking) ✓. Sub-Logger nach insight-engine-call End-Logs fehlt. F0.6 nach So 17.05. Backend-CI-Gate offen.

---

## Was sonst noch offen

| Sprint | Status |
|---|---|
| Sprint 1 (PR #134) UNIQUE-Constraint | ✓ |
| Sprint 2 (PR #135/#136) Test-Failures | ✓ |
| Sprint 3 (PR #137-#140) Race-Condition-Diagnose+Logging+Run-Sheet | ✓ |
| Sprint 3b-Run | ✓ Iter 1 durch, Hypothese D verifiziert |
| Sprint 4 (PR #117) | ✓ |
| Sprint 5 (gitignore) | ✓ |
| Negativ-Befund-Doc | ✓ commit 885ac9d |
| **Sprint 3c: Race-Condition Fix** | nächste Station, Briefing oben |
| Sprint 3d: Backend-CI-Gate | nach 3c |
| Sub-Logger-Konflikt-Diagnose | nach 3c |
| B2 UK Cross-Market-Matching | nach Reihe + 17.05.-Daten |
| F0.6 Schwellwert kalibrieren | passiv nach So 17.05. |

---

## Restart-Prompt für neuen Chat

Wir setzen Creative Radar fort. Sprint 3b-Run ist abgeschlossen — Race-Condition-Bug verifiziert (Hypothese D, präzise Diagnose, Memory #27). Sprint 3c-Briefing (Race-Condition-Fix mit Double-Check-Locking-Pattern) ist in docs/handovers/race-condition-sprint-3b-done.md bereit zum Übergeben an Claude Code.

Vorschlag erste Action im neuen Chat: Sprint 3c-Briefing finalisieren und an Claude Code übergeben.

