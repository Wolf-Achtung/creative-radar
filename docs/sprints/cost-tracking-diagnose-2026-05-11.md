# Cost-Tracking-Diagnose (Anthropic + OpenAI) — Read-Only-Befund

**Datum**: 2026-05-11 EOD
**Sprint**: Cost-Tracking-Diagnose (Folge auf PR #116 Apify-Pay-per-Event-Fix)
**Status nach dieser Session**: Diagnose komplett, KEIN Code-Edit, KEIN
Branch, KEIN PR. Fix-Sprint ist für 2026-05-12 vorbereitet.

**Diese Datei ist der Implementations-Guide für morgen.** Sie enthält:

- Klare Diagnose der zwei Bugs (Anthropic missing + OpenAI cost=0).
- Konkrete Zeilen-Verweise für jeden Fix-Punkt.
- Pricing-Quelle-Empfehlung mit Aufwand-Estimate.
- Sprint-Skizze für morgen 12.05.

---

## 1. Bug-1: Anthropic-Cost-Logging — Brief-Pfad fehlt komplett

### 1.1 Befund

`record_anthropic_call` **existiert** in `backend/app/services/cost_log.py:210`
und wird auch konsequent aufgerufen — aber **nur aus dem
`post_analyzer.py`-Pfad** (5 Stellen: Z. 198, 226, 255, 283, 319). Der
Pfad logged Haiku- und Sonnet-Calls korrekt in die Buckets
`anthropic_haiku` / `anthropic_sonnet` / `anthropic_sonnet_vision`.

**Aber**: Der **Weekly-Brief-Pfad** in
`backend/app/services/insight_engine.py:2078-2127` (Funktion
`generate_weekly_report`) ruft `messages_create_text` direkt auf
(`claude-opus-4-7`), liest `message.usage` aus (Z. 2106-2108), berechnet
eine **lokale Cost-Schätzung** via `_estimate_cost_usd` (Z. 2109) und
gibt sie als `cost_usd_estimate` ans Frontend zurück — **schreibt aber
nie in `cost_log`**. Der `record_anthropic_call`-Aufruf fehlt.

```python
# insight_engine.py:2078
message = messages_create_text(...)
...
# Z. 2106-2109: nur In-Memory-Estimate, keine Persistenz im costlog
usage = getattr(message, "usage", None)
input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
cost = _estimate_cost_usd(input_tokens, output_tokens) if ... else None
# ← record_anthropic_call(usage, model, "weekly_brief", ...) fehlt hier
```

`_estimate_cost_usd` nutzt zwei Modul-Konstanten (Z. 376-377):

```python
_OPUS_INPUT_PER_1K_USD = 0.015     # $15 / Mtok input  → korrekt für claude-opus-4-7
_OPUS_OUTPUT_PER_1K_USD = 0.075    # $75 / Mtok output → korrekt
```

Die Konstanten sind preislich korrekt, aber **doppelt geführt**:
einmal hier hardcoded, einmal indirekt in `record_anthropic_call`
(die Opus nicht kennt — siehe 1.2). Drift-Risiko bei Preis-Änderungen.

### 1.2 Zusatz-Bug: `record_anthropic_call` kennt Opus nicht

`cost_log.py:250-262`:

```python
model_lc = (model or "").lower()
if model_lc.startswith("claude-haiku"):
    in_rate = settings.anthropic_haiku_input_per_1k_usd or 0.0      # 0.001
    out_rate = settings.anthropic_haiku_output_per_1k_usd or 0.0    # 0.005
    bucket_family = "anthropic_haiku"
elif model_lc.startswith("claude-sonnet"):
    in_rate = settings.anthropic_sonnet_input_per_1k_usd or 0.0     # 0.003
    out_rate = settings.anthropic_sonnet_output_per_1k_usd or 0.0   # 0.015
    bucket_family = "anthropic_sonnet"
else:
    in_rate = 0.0
    out_rate = 0.0
    bucket_family = "anthropic"
```

Für `claude-opus-4-7` greift weder die Haiku- noch die Sonnet-Branch.
Selbst **wenn** `generate_weekly_report` morgen `record_anthropic_call`
aufrufen würde, wäre `cost_usd_cents = 0` (gleicher Bug-Pattern wie
OpenAI in 2.1). Es fehlen `anthropic_opus_input_per_1k_usd` /
`anthropic_opus_output_per_1k_usd` in `config.py:81-84` und eine
`elif model_lc.startswith("claude-opus")`-Branch.

### 1.3 Provider-Verteilung im costlog (heute aus Wolfs psql-Diagnose)

Wolf-Befund: `SELECT DISTINCT provider FROM cost_log` → nur
`apify`, `openai`, `youtube_api`. **Kein anthropic-Bucket**. Erwartet
wären `anthropic_haiku` und `anthropic_sonnet` aus dem
post_analyzer-Pfad. Mögliche Erklärungen:

- **post_analyzer läuft aktuell nicht im 7d-Fenster** — die analyze-Pipeline
  ist aktiv nur für Re-Analyse-Sprints, nicht im laufenden Cron-Sync.
  Dann sind die `anthropic_*`-Buckets erwartungsgemäß leer.
- Falls post_analyzer **doch** läuft: silent `_persist`-Fehler (Z. 41-64
  swallow alle Exceptions in WARN-Log). Morgen vor dem Fix: psql-Check
  auf `cost_log.cost_meta -> 'model'`-Verteilung, um auszuschließen,
  dass `anthropic_*`-Rows da sind und Wolf nur nicht danach gefiltert
  hat.

### 1.4 Konsequenz

- Heute generierte 5 Weekly-Briefs → ~$0.35-0.50/Brief × 5 ≈ **$1.75-$2.50
  nicht im costlog** (heute).
- Schätzung Monat (≈30 Briefs/Tag falls Cron-Generation aktiv ist) →
  **$10-15/Tag, $300-450/Monat** ungetracked. Wolfs Schätzung
  $40-50/Monat im Sprint-Brief geht von wenigen manuellen Briefs/Tag aus
  und ist die untere Grenze.
- F0.6 Hard-Cap $50/Monat (PR #117) ist **nicht durchsetzbar** für
  Anthropic — die Cost-Summary sieht den größten Block nicht.

### 1.5 Fix-Skizze morgen

1. **Config (config.py:84+)**: Neue Settings
   ```python
   anthropic_opus_model: str = "claude-opus-4-7"
   anthropic_opus_input_per_1k_usd: float = 0.015
   anthropic_opus_output_per_1k_usd: float = 0.075
   ```
2. **cost_log.py (Z. 250-262)**: `elif model_lc.startswith("claude-opus"):`
   -Branch ergänzen + `bucket_family = "anthropic_opus"`.
3. **insight_engine.py (nach Z. 2108)**: `record_anthropic_call(usage,
   model, "weekly_brief", meta={"pair_key": pair_key, "iso_week":
   iso_week})` einfügen.
4. **insight_engine.py (Z. 376-377)**: `_OPUS_*_PER_1K_USD`-Konstanten
   entfernen und `_estimate_cost_usd` aus den neuen `settings.*`-Feldern
   speisen. Damit ist die Pricing-Quelle für Brief-Frontend-Display und
   Costlog-Persistenz **eine einzige Stelle**.
5. **Tests**: 2 neue in `test_insight_engine.py`:
   - `test_generate_weekly_report_persists_anthropic_cost`: mock
     `messages_create_text` mit fake Usage, prüfe dass ein CostLog-Row
     mit `provider="anthropic_opus"` entsteht.
   - `test_record_anthropic_call_uses_opus_rates_for_claude_opus`: gebe
     `model="claude-opus-4-7"` mit 1000/200 Tokens → expect cost == 1500 +
     1500 = 3000 nano-USD → 3 cents (siehe Bug-2 für die Rundungsfrage).

**Aufwand**: ~30-45 Min.

---

## 2. Bug-2: OpenAI-Cost = 0 trotz gefülltem cost_meta

### 2.1 Befund

`record_openai_call` in `cost_log.py:171-207` berechnet Cost korrekt
aus Tokens × Preis-pro-1k. Config-Rates (`config.py:69-70`):

```python
openai_input_per_1k_usd: float = 0.000150    # = $0.15/Mtok input  (gpt-4o-mini korrekt)
openai_output_per_1k_usd: float = 0.000600   # = $0.60/Mtok output (gpt-4o-mini korrekt)
```

`settings.openai_model` defaultet auf `"gpt-4o-mini"` (`config.py:19`).
Für `gpt-4o-mini` sind die Rates **korrekt** (Stand Sept 2024).

**Bug-Pattern**: Precision-Loss-via-Int-Cents (Z. 198-200):

```python
input_usd = (input_tokens / 1000.0) * 0.000150
output_usd = (output_tokens / 1000.0) * 0.000600
usd_cents = int(round((input_usd + output_usd) * 100))   # ← int!
```

Für einen typischen Vision-Call (Bild ~765 image-tokens + ~200 prompt
+ ~250 output) sind das:
- `input_usd = 965/1000 × 0.000150 = 0.00014475 USD`
- `output_usd = 250/1000 × 0.000600 = 0.000150 USD`
- `total = 0.00029475 USD = 0.0295 cents`
- `int(round(0.0295)) = 0`

Für einen typischen Chat-Completion-Call (~2000 in + ~500 out):
- `total = 0.0003 + 0.0003 = 0.0006 USD = 0.06 cents → int = 0`

**Jeder einzelne Call kostet unter 0.5 cents und rundet auf 0 ab.**
Das `cost_meta`-JSON bleibt befüllt (Tokens drin), aber
`cost_usd_cents = 0`.

### 2.2 Aggregierter Real-Cost (Schätzung)

- 1118 chat_completions × ~0.06 cents = **~67 cents** über 7 Tage
- 448 vision_calls × ~0.03 cents = **~13 cents** über 7 Tage
- **Real-Cost OpenAI ~$0.80 / 7 Tage ≈ $3.40/Monat**

Klein im Vergleich zu Apify und Anthropic — aber die DB-Spalte sagt
$0, was das Cost-Dashboard verzerrt und F0.6-Schwellwert-Tuning
unmöglich macht.

### 2.3 Vergleich zum Apify-Bug (PR #116)

| | Apify-Bug (gefixt) | OpenAI-Bug (offen) |
|---|---|---|
| Symptom | cost_usd_cents=0 für Pay-Per-Event-Actors | cost_usd_cents=0 für alle Calls |
| Root Cause | falsches Cost-Quellfeld (compute_units statt usageTotalUsd) | Rundungsverlust (cost/Call < 0.5 cents) |
| Fix-Strategie | `usageTotalUsd` aus Run-Response lesen | Storage-Unit verfeinern (millicents oder µcents) |

**Pattern-Verwandtschaft ist nur das Symptom (cost=0), nicht die
Ursache.** OpenAI hat keinen authoritativen Cost-Wert im SDK-Response
(siehe 3.1) — die Berechnung tokens × rate ist korrekt; nur die
**Storage-Granularität** ist zu grob.

### 2.4 Fix-Optionen

| Option | Schema-Migration? | Aufwand | Trade-Off |
|---|---|---|---|
| **A. Spalte auf `Numeric(12,4)` (USD-Decimal)** | Ja | mittel | präzise, aber API-Diff für consumers; aggregation read-paths brechen |
| **B. Spalte `cost_usd_millicents Integer`** ergänzen (additiv) | Ja, additiv | klein | additiv, `cost_usd_cents` bleibt für Backwards-Compat; cost-summary muss summieren mit /1000 |
| **C. In-Memory-Akkumulation (batch-write nur wenn ≥1 cent)** | Nein | klein-mittel | komplex, Race-Conditions, kein Audit-Trail für einzelne Calls |
| **D. Workaround: `int(round(...))` durch `math.ceil()` ersetzen** | Nein | trivial | overcharge by ~50% pro Call — schlechter als jetzt |

**Empfehlung Option B** (additive millicent-Spalte):
- Migration `XXXXXXXXXXX_add_cost_usd_millicents.py` (ein `Numeric` plus
  ein `Index` ist nicht nötig, da Cost-Summary auf Provider/Operation
  aggregiert).
- `cost_log.py:_persist` schreibt beide Felder; `cost_usd_cents = round(millicents/1000)`
  bleibt für Konsumer-Compat (gibt Integer wie heute).
- `cost-summary`-Endpoint summiert `cost_usd_millicents`, geteilt durch 1000
  ergibt sub-cent-präzise USD.
- Frontend: F0.6 Cost-Card liest die neue Spalte.

**Aufwand**: ~45-60 Min (Migration + cost_log + cost-summary +
4-5 Tests).

---

## 3. Authoritative-Read-Möglichkeit (Pricing-Quelle)

### 3.1 SDK-Response-Felder

| API | Response-Feld | Cost direkt? |
|---|---|---|
| Anthropic `messages.create` | `message.usage.input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` | **Nein** — nur Tokens |
| OpenAI `chat.completions.create` | `response.usage.prompt_tokens`, `completion_tokens`, `prompt_tokens_details` | **Nein** — nur Tokens |
| Apify (PR #116) | `run_data.usageTotalUsd` | **Ja** (server-side berechnet) |

Beide LLM-Provider liefern **keinen** Cost-Block — Cost muss aus
Tokens × Preistabelle berechnet werden (anders als Apify).

### 3.2 Pricing-Quelle: Optionen

| Option | Pro | Contra |
|---|---|---|
| **A. Hardcoded in `config.py`** (analog Anthropic-Haiku/Sonnet-Pattern, Z. 81-84) | konsistent zum Bestand; null Dependencies; explizite Audit-Trail | manuell pflegen bei Preisänderungen; Drift-Risiko (z.B. doppelte Quelle in insight_engine.py) |
| **B. `litellm.completion_cost()`** | abdeckt alle Modelle automatisch; community-maintained | neue Dependency (~5 MB); Library-Pricing-Tabelle kann lag haben |
| **C. tiktoken + manuelle Preistabelle** | präzise Token-Count; flexible | doppelt — tokens aus SDK kommen schon |

**Empfehlung A** (hardcoded config.py).

**Begründung**:
- Der Bestand führt schon `anthropic_haiku_*` / `anthropic_sonnet_*` /
  `openai_*` in `config.py`. Konsistent fortzuführen → ein neues Pärchen
  `anthropic_opus_input/output_per_1k_usd`.
- LLM-Modell-Wechsel sind seltener als monatlich (Wolf hat Anthropic
  zuletzt im April 2025 von claude-sonnet-4 auf claude-sonnet-4-6
  migriert). Manuelle Pflege ist tragbar.
- `_estimate_cost_usd` aus `insight_engine.py` soll auf die config-Felder
  umgestellt werden (siehe 1.5 Punkt 4) → eliminiert die doppelte
  Quelle und löst das Drift-Risiko.
- Litellm wäre Over-Engineering für 3 Modelle (Haiku, Sonnet, Opus +
  gpt-4o-mini) im Bestand.

---

## 4. Sprint-Skizze für 2026-05-12

### 4.1 Reihenfolge (Fix-First)

| Schritt | Aufwand | Risiko |
|---|---|---|
| **A. Anthropic Opus pricing settings + cost_log opus-branch** (config.py + cost_log.py:250-262) | ~10 Min | klein |
| **B. `generate_weekly_report` ruft `record_anthropic_call`** (insight_engine.py nach Z. 2108) | ~5 Min | klein |
| **C. `_estimate_cost_usd` auf settings umstellen + `_OPUS_*` Konstanten entfernen** (insight_engine.py:376-377, 2012-2017) | ~10 Min | klein |
| **D. `cost_log_millicents`-Migration + `_persist`-Erweiterung** (additive Spalte) | ~30 Min | mittel — Migration |
| **E. `cost-summary`-Endpoint: zusätzliches Feld für millicents-Aggregation** | ~15 Min | klein |
| **F. Tests: 4-6 neue** (Opus-Branch, generate_weekly_report-cost-persist, millicents-precision, cost-summary-aggregation) | ~30-40 Min | klein |
| **G. Smoke-Test: Brief generieren + psql-Verify** | ~10 Min | — |
| **H. Commit + Push + PR** | ~10 Min | — |

**Total Aufwand**: ~2-2.5h.

### 4.2 Vorab-Verifikation morgen früh (5 Min, vor Code-Edit)

Wolf führt aus, **bevor** der Sprint startet:

```sql
-- Verify: gibt es anthropic_* Rows im costlog (post_analyzer-Pfad)?
SELECT provider, count(*) FROM cost_log
WHERE created_at > NOW() - INTERVAL '14 days'
GROUP BY provider
ORDER BY 2 DESC;

-- Verify: OpenAI Token-Volumen für Real-Cost-Plausibilität
SELECT
  operation,
  count(*) as calls,
  sum((cost_meta->>'input_tokens')::int) as total_input,
  sum((cost_meta->>'output_tokens')::int) as total_output
FROM cost_log
WHERE provider = 'openai'
  AND created_at > NOW() - INTERVAL '7 days'
GROUP BY operation;
```

Output bestimmt:
- Wenn `anthropic_*` leer → 1.3-Hypothese (post_analyzer läuft nicht)
  bestätigt; kein zusätzlicher Fix nötig.
- Wenn Token-Volumen × Rate plausibel ist → 2.2-Schätzung valid; Option-B
  Millicents-Fix ist die richtige Lösung.
- Wenn Token-Volumen viel höher als geschätzt → ggf. ist `openai_model`
  in Production NICHT `gpt-4o-mini` und die Rates sind stale (separater
  Fix-Punkt).

### 4.3 F0.6-Schwellwert-Anpassung (out-of-scope morgen)

Wie heute morgen bei Apify (PR #117): erst **nachdem** die Cost-Quellen
zuverlässig in costlog landen, kann Wolf 1-2 Tage Real-Cost beobachten
und dann den $50-Hard-Cap kalibrieren (vermutlich $50 → $200 oder
ähnlich, abhängig von Anthropic-Brief-Frequenz).

Dieser Schritt ist **explizit nicht Teil** des morgigen Fix-Sprints.

---

## 5. Out-of-Scope-Bestätigungen

- ❌ F0.6 Hard-Cap-Schwellwert-Anpassung (kommt nach 1-2d Real-Cost-
  Beobachtung; analog zu PR #117 Apify $200→$50)
- ❌ litellm-Integration (Option B verworfen — siehe 3.2)
- ❌ Cost-Summary-Frontend-Redesign (Card kann millicents transparent
  konsumieren; UI-Änderung nicht nötig)
- ❌ Cache-Read/Write-Token-Differenzierung bei Anthropic
  (`message.usage.cache_creation_input_tokens` / `cache_read_input_tokens`
  — Opus pricing-tier "ephemeral cache" — separater Sprint, da der
  Brief-Pfad aktuell kein Caching nutzt)

---

## 6. Session-Output

| Akzeptanzkriterium | Status |
|---|---|
| Befund-Report unter `docs/sprints/cost-tracking-diagnose-2026-05-11.md` | ✅ diese Datei |
| Klare Diagnose Bug-1 (Anthropic missing) | ✅ Abschnitt 1 |
| Klare Diagnose Bug-2 (OpenAI cost=0) | ✅ Abschnitt 2 (Precision-Loss-via-Int-Cents) |
| Pricing-Quelle-Empfehlung | ✅ Abschnitt 3.2 (Option A: hardcoded config.py, konsistent zum Bestand) |
| Sprint-Skizze für morgen mit Aufwand-Estimate | ✅ Abschnitt 4 (~2-2.5h) |
| KEIN Code-Edit außer dem Doc-File | ✅ — diese Datei ist der einzige Write |
| KEIN Branch, KEIN Commit, KEIN PR | ✅ |

---

## 7. Wolf-Ping

**Befund-Report**: `docs/sprints/cost-tracking-diagnose-2026-05-11.md`

**Zwei Bugs identifiziert**:

1. **Anthropic Brief-Pfad** (`generate_weekly_report` in
   `insight_engine.py:2078-2127`) ruft `record_anthropic_call` nicht
   auf — Weekly-Briefs landen nie im costlog. Plus: `record_anthropic_call`
   kennt `claude-opus`-Modelle nicht (rate=0-Fallback). Fix ~25 Min.
2. **OpenAI Precision-Loss**: Per-Call-Cost rundet auf 0 cents ab,
   weil gpt-4o-mini ~0.03-0.06 cents/Call kostet und `cost_usd_cents`
   ein Integer ist. Fix: additive `cost_usd_millicents`-Spalte, ~45 Min
   inkl. Migration.

**Pricing-Quelle**: Hardcoded in `config.py` (Option A) — konsistent zum
Bestand für Haiku/Sonnet/OpenAI; eliminiert die doppelte Pricing-Quelle
in `insight_engine._OPUS_*_PER_1K_USD`. Kein litellm.

**Aufwand morgen**: ~2-2.5h Implementation, inkl. Tests + Smoke.

**Offene Verifikation (5 Min, von Wolf vor Sprint-Start)**: psql-Query
in 4.2 — falls `anthropic_*` Rows im costlog auftauchen, ist 1.3-Hypothese
falsch und wir brauchen einen weiteren Diagnose-Schritt.

**F0.6-Schwellwert-Anpassung**: kommt nach 1-2d Real-Cost-Beobachtung,
nicht im morgigen Sprint.
