# Diagnose: `insight-engine-call` Sub-Logger Fehlende End-Events

**Status:** Diagnose abgeschlossen, Fix pending
**Datum:** 2026-05-12
**Operating-Mode:** Diagnose-only, kein Code-Touch
**Vorgänger:** Sprint 3b Log-Forensik

---

## TL;DR

Kein „Sub-Logger" im Sinne einer eigenen `logging.getLogger(...)`-Instanz. `insight-engine-call` ist nur der `msg`-String eines einzelnen `logger.info(...)`-Aufrufs in `generate_weekly_report` direkt vor dem Anthropic-SDK-Call. Es gibt **keine** schreibende Stelle für ein End-Event mit diesem Namen — weder als `*-done`, `*-end`, `*-success` noch in einem try/finally. Ursache (a) trifft zu: End-Logging-Code wurde nie geschrieben.

Drift zur neueren Logger-Architektur: PR #138 hat eine voll-symmetrische `brief_*`-Family eingeführt (Start/Done-Paare für Lock, Pipeline, LLM-Call, Persist). Die alte `insight-engine-*`-Family von Sprint 1 wurde dabei nicht angefasst. `insight-engine-call` ist heute der einzige asymmetrische Start-Event im Modul.

Fix-Aufwand: **S** (~10 Min).

---

## Q1: Wo wird `insight-engine-call` initial geloggt?

**Datei:** `backend/app/services/insight_engine.py`
**Zeilen:** 2197-2205
**Funktion:** `generate_weekly_report`

```python
user_prompt = _build_user_prompt(agg)
logger.info(
    "insight-engine-call",
    extra={
        "pair": pair_key,
        "window_days": window_days,
        "model": model,
        "prompt_chars": len(user_prompt),
    },
)
message = messages_create_text(
    model=model,
    system=SYSTEM_PROMPT,
    user_message=user_prompt,
    max_tokens=max_tokens,
)
```

Wichtig: `logger` ist `logging.getLogger(__name__)` aus Z. 60, also die Modul-Logger-Instanz `app.services.insight_engine`. Es gibt im gesamten Backend **keine** `getLogger("insight-engine-call")`-Stelle (`grep` bestätigt). Der String „insight-engine-call" ist die `msg` des Log-Aufrufs, nicht der Name eines Logger-Objekts.

---

## Q2: Welcher Code-Pfad sollte das End-Event loggen?

Die natürliche symmetrische Stelle wäre unmittelbar nach `messages_create_text(...)` zurückkehrt — also Z. ~2211, vor der `raw_text`-Extraktion. Eine zweite plausible Stelle wäre am Funktions-Ende vor dem `return InsightReport(...)` in Z. 2258.

**Der Code-Pfad existiert nicht.** Zwischen dem Start-Log (Z. 2197) und dem `return` (Z. 2258) finden sich:

- `messages_create_text(...)` — Anthropic-SDK-Call (Z. 2206)
- `try` / `except` für Content-Extraction → logged nur bei Fehler als `insight-engine-content-extract-failed` (Z. 2222)
- `try` / `except` für JSON-Parse → logged nur bei Fehler als `insight-engine-json-parse-failed` (Z. 2231)
- `record_anthropic_call(...)` — Cost-Log-Write (Z. 2247)
- `return InsightReport(...)` (Z. 2258)

Kein `logger.info("insight-engine-call-done", ...)` oder ähnlich. Keine `try` / `finally`-Klammer um den ganzen Block. Kein Context-Manager. Kein `with logger.timer(...)`.

---

## Q3: Warum fehlt das End-Event?

**(a) End-Logging-Code existiert nicht — vergessen bei initialer Implementierung.**

Negative Verifikation der anderen Optionen:

- **(b) Exception schluckt den End-Event?** Nein. Die zwei `except`-Blöcke (Z. 2221-2222 und Z. 2230-2231) loggen ihre eigenen Fehler-Events, schlucken aber kein hypothetisches `*-done`-Event. Der „Happy Path" durchläuft die `try`-Blöcke ohne Exception und gelangt zu Z. 2258 — ohne dass irgendwo ein End-Log emittiert würde.
- **(c) Anderer Event-Name?** Nein. `grep -rn "insight-engine-" backend/app/` liefert nur drei Treffer (Z. 2197 Start, Z. 2222 + Z. 2231 Fehler-Events). Kein `*-done`, `*-success`, `*-completed`, `*-end`. Auch keine fuzzigen Varianten wie `insight_engine_call_done`.
- **(d) Logger-Config filtert es weg?** Nein. Nach PR #139 ist die Root-Logger-Stufe auf INFO konfiguriert (`backend/app/main.py`), Stream geht auf stdout. `app.services.insight_engine.logger.info(...)` propagiert ungehindert. Andere `logger.info`-Calls im selben Modul (z.B. `brief_pipeline_start` Z. 2483) erscheinen in Railway-Logs, wie Wolf in Sprint 3a-bugfix verifiziert hat.

→ Ursache (a) ist die einzige, die mit allen Beobachtungen konsistent ist.

Historisch: Der Start-Event wurde in Sprint-1 (Insight-Engine-MVP, vor PR #122) hinzugefügt, als das Logging im Modul minimal war (3 Events insgesamt). Die voll-symmetrische `brief_*`-Family ist erst mit PR #138 (Sprint 3a) entstanden. `insight-engine-call` wurde dabei nicht mit-umgebaut.

---

## Q4: Andere Sub-Logger im selben Modul mit demselben Pattern

Inventar aller `logger.*`-Aufrufe in `backend/app/services/insight_engine.py`:

### `brief_*`-Family (PR #138 / #141, symmetrisch ✓)

| Event | Typ | Gepaart mit |
|---|---|---|
| `brief_pipeline_start` (Z. 2483) | start | `brief_pipeline_done` (Z. 2522 / Z. 2589) ✓ |
| `brief_pipeline_done` (Z. 2522, 2589) | end | outcomes: `cache_hit`, `lock_dedup`, `fresh_generation` ✓ |
| `brief_lock_attempt` (Z. 2395) | start | `brief_lock_acquired` (Z. 2424) ODER `brief_lock_timeout` (Z. 2411) ✓ |
| `brief_lock_acquired` (Z. 2424) | end (success) | trägt `wait_ms` ✓ |
| `brief_lock_timeout` (Z. 2411) | end (failure) | trägt `wait_ms` + `timeout_s` ✓ |
| `brief_precheck_done` (Z. 2505) | one-shot | n/a |
| `brief_llm_call_start` (Z. 2536) | start | `brief_llm_call_done` (Z. 2551) ✓ |
| `brief_llm_call_done` (Z. 2551) | end | trägt `tokens_in/out` + `duration_ms` ✓ |
| `brief_cost_logged` (Z. 2569) | one-shot | n/a |
| `brief_report_persisted` (Z. 2579) | one-shot (implizit Lock-Release) | n/a |

→ **Vollständig symmetrisch.** Keine Asymmetrie in diesem Family.

### `insight-engine-*`-Family (Sprint-1, ❌ asymmetrisch)

| Event | Typ | Status |
|---|---|---|
| **`insight-engine-call`** (Z. 2197) | **start** | **❌ kein End-Event** — Gegenstand dieser Diagnose |
| `insight-engine-content-extract-failed` (Z. 2222) | exception-only | nur bei Extract-Fehler |
| `insight-engine-json-parse-failed` (Z. 2231) | exception-only | nur bei Parse-Fehler |

→ Genau ein asymmetrisches Start-Event im Modul: `insight-engine-call`.

### `insight-report-*`-Family

| Event | Typ | Status |
|---|---|---|
| `insight-report-persist-skipped` (Z. 2324) | one-shot exception path | n/a |

→ Keine Asymmetrie.

### Außerhalb von `insight_engine.py`

`grep -rn "insight-engine-" backend/app/` liefert nur Treffer in `insight_engine.py`. Kein anderes Modul nutzt den Prefix.

---

## Bonus-Befund: Wolf's Sprint-3b-Beobachtung

Wolf notierte in Sprint 3b Briefing, dass „brief_llm_call_done, brief_pipeline_done wegen Sub-Logger-Konflikt nach insight-engine-call" fehlen.

Diese Diagnose zeigt: das ist nicht durch den hier diskutierten asymmetrischen Event verursacht. Die `brief_*`-Events haben ihre Log-Aufrufe in `generate_and_persist_report`, NICHT in `generate_weekly_report` (wo `insight-engine-call` lebt). Wenn `brief_llm_call_done` / `brief_pipeline_done` in Production-Logs nicht erscheinen, ist die Ursache anderswo — vermutlich Log-Buffering oder ein Race in der Log-Propagierung zwischen dem `record_anthropic_call(...)`-Sub-Session (Z. 2247, öffnet eigene `Session(engine)` für costlog) und dem weiteren `logger.info`-Stream. Das ist out-of-scope für diese Diagnose, aber relevant als Folge-Diagnose-Kandidat.

---

## Empfehlung für Folge-Sprint

**Fix-Aufwand: S (~10 Min).** Zwei Optionen, je nach Sauberkeits-Anspruch:

### Option Minimal: Event-End hinzufügen
Nach Z. 2211 (nach `messages_create_text` returns), kurzes `time.perf_counter()`-Delta:

```python
call_started = time.perf_counter()  # vor messages_create_text
message = messages_create_text(...)
logger.info(
    "insight-engine-call-done",
    extra={
        "pair": pair_key,
        "duration_ms": int((time.perf_counter() - call_started) * 1000),
        "output_blocks": len(message.content or []),
    },
)
```

Aufwand ~5 Min, Tests bleiben grün (kein Verhalten geändert).

### Option Konsolidierung: Asymmetrie mit `brief_*`-Family vereinheitlichen
`insight-engine-call` und ein neues `insight-engine-call-done` umbenennen auf `brief_anthropic_call_start` / `brief_anthropic_call_done` — analog zum `brief_llm_call_*`-Paar in `generate_and_persist_report`. Konsequenz: alle Lifecycle-Events im Modul folgen dem `brief_*`-Schema.

Aufwand ~10-15 Min. Risiko: ein paar Tests könnten den alten Event-Namen prüfen — laut `grep` aber nicht (`grep -rn "insight-engine-call" backend/app/tests/` ist leer).

**Empfehlung:** Option Konsolidierung. Der Drift zwischen den zwei Logger-Familien wird sonst beim nächsten Sprint, der dieses Modul anfasst, wieder aufschlagen. Kosten ist minimal (Aufwand-Delta zu Option Minimal ~5 Min), Nutzen ist eine einzige Lifecycle-Konvention im ganzen Modul.

---

## Was NICHT in dieser Diagnose

- Kein Fix-Code, kein Re-Hookup.
- Keine Untersuchung der Sprint-3b-Beobachtung „brief_pipeline_done fehlt in Railway" — siehe Bonus-Befund, eigener Diagnose-Sprint nötig.
- Keine Erweiterung auf andere Module (`api/admin.py`, `api/insights.py`).
