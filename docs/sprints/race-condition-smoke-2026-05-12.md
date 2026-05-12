# Race-Condition-Smoke Run-Sheet 2026-05-12

**Modus:** Run-Sheet (Copy-Paste-Workflow), NICHT Diagnose-Doc
**Pair:** netflix
**Budget-Cap:** $10 / 5 Iterationen (hart)
**Vorgänger:** PRs #137 (Diagnose) → #138 (Logging) → #139 (stdout-Fix)
**Final-Befund:** unten am Ende des Docs, fülle ich nach allen Paste-Backs

---

## Hard-Stop-Matrix (sichtbar lassen während des Runs)

| Status nach Iteration | Aktion |
|---|---|
| Hypothese B/D/E nach Iter 1 oder Iter 2 verifiziert | **STOP**, nicht weiter testen, paste-back vollständig, ich übernehme |
| Iter 2 fertig, Bug nicht reproduziert | **STOP**, Wolf-Ping: Budget erweitern oder anderer Ansatz? |
| Kumulativer Cost > $8 | **STOP**, paste-back was da ist, Wolf entscheidet weiteres |
| Railway-Logs zeigen kein `brief_*` trotz PR #139 | **STOP**, dritte Diagnose-Runde nötig |
| Cache-Hit (`outcome=cache_hit`) bei `force=true` | Eigener Bug — flaggen, im Final-Befund vermerken |

| Iter | Curls | Kumulativ-Cost (Schätzung) |
|---|---|---|
| 1 | 2 | ~$4 |
| 2 | 2 | ~$8 |
| 3+ | 2 ea | **>$10 → Budget gesprengt, Wolf-Ping** |

---

## Pre-Run-Setup (einmalig)

### → Im Terminal ausführen

```bash
source ~/.creative-radar/db.env
export API_TOKEN=$(security find-generic-password -s creative-radar-api-token -w)
mkdir -p /tmp/smoke-3b
cd /tmp/smoke-3b

# Berlin-Zeit Helper (alle Zeitstempel im Doc danach in Europe/Berlin)
berlin_now() { TZ='Europe/Berlin' date '+%Y-%m-%d %H:%M:%S %Z'; }
utc_now()    { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

# Cost-Delta seit Zeitpunkt T (Berlin-Zeit, ISO-Format)
costlog_delta_since() {
  local since="$1"
  psql "$CR_DB_URL" -A -F'|' -c "
    SET search_path TO creative_radar, public;
    SELECT
      COUNT(*)                                     AS rows,
      COALESCE(SUM(cost_usd_millicents), 0)        AS total_millicents,
      ROUND(COALESCE(SUM(cost_usd_millicents),0) / 100000.0, 2) AS total_usd
    FROM costlog
    WHERE provider = 'anthropic_opus'
      AND operation = 'weekly_brief'
      AND timestamp > '${since}'::timestamptz;
  "
}

# Report-Anzahl + jüngste IDs seit Zeitpunkt T
report_count_since() {
  local since="$1"
  psql "$CR_DB_URL" -A -F'|' -c "
    SET search_path TO creative_radar, public;
    SELECT
      id,
      pair_key,
      iso_week,
      generated_at AT TIME ZONE 'Europe/Berlin' AS generated_berlin,
      EXTRACT(EPOCH FROM (generated_at - LAG(generated_at) OVER (ORDER BY generated_at))) AS secs_after_prev
    FROM insight_report
    WHERE pair_key = 'netflix'
      AND generated_at > '${since}'::timestamptz
    ORDER BY generated_at DESC;
  "
}

echo "Setup ok. Berlin now: $(berlin_now)"
```

### → Im psql ausführen — Schema-Sanity-Check (einmalig vor Iter 1)

```sql
\d creative_radar.costlog
\d creative_radar.insight_report
```

**Paste-Back-Slot Schema-Check** (nur falls eine der Spalten anders heißt als unten angenommen, sonst leer lassen):

```
[paste hier, falls Schema-Abweichung auffällt]
```

> Annahmen im Doc: `costlog.timestamp`, `costlog.provider='anthropic_opus'`, `costlog.operation='weekly_brief'`, `costlog.cost_usd_millicents`. `insight_report.id`, `pair_key`, `iso_week`, `iso_year`, `generated_at`. Falls eine Spalte anders heißt: die Helper-Funktionen oben kurz korrigieren bevor Iter 1 startet.

---

## Iteration 1 — Sequential, 5s gap (Original-Reproduktion)

### Pre-Run-Anker

#### → Im Terminal ausführen

```bash
ITER1_START=$(utc_now)
echo "iter1 start (UTC): $ITER1_START"
echo "iter1 start (Berlin): $(berlin_now)"
```

#### → Im psql ausführen (Pre-State)

```sql
SET search_path TO creative_radar, public;

-- Existierender Brief für aktuelle ISO-Woche?
SELECT pair_key, iso_year, iso_week,
       generated_at AT TIME ZONE 'Europe/Berlin' AS gen_berlin,
       EXTRACT(EPOCH FROM (NOW() - generated_at)) AS age_seconds
FROM insight_report
WHERE pair_key = 'netflix'
ORDER BY generated_at DESC
LIMIT 3;
```

**Paste-Back-Slot Pre-State Iter 1:**

```
[paste]
```

### Curls — Sequential 5s gap

#### → Im Terminal ausführen

```bash
( curl -s "https://api.creative-radar.de/api/insights/weekly?pair=netflix&force=true" \
    -H "Authorization: Bearer $API_TOKEN" \
    -o /tmp/smoke-3b/iter1-call1.json \
    -w "[iter1-c1] http=%{http_code} time=%{time_total}s\n"
) &
CALL1_PID=$!
sleep 5
( curl -s "https://api.creative-radar.de/api/insights/weekly?pair=netflix&force=true" \
    -H "Authorization: Bearer $API_TOKEN" \
    -o /tmp/smoke-3b/iter1-call2.json \
    -w "[iter1-c2] http=%{http_code} time=%{time_total}s\n"
) &
CALL2_PID=$!
wait $CALL1_PID $CALL2_PID
ITER1_END=$(utc_now)
echo "iter1 end (UTC): $ITER1_END"
echo "iter1 end (Berlin): $(berlin_now)"
```

**Paste-Back-Slot Curl-Output Iter 1:**

```
[iter1-c1] http=... time=...
[iter1-c2] http=... time=...
```

### Post-Verify — DB-Delta

#### → Im psql ausführen

```sql
SET search_path TO creative_radar, public;

-- Cost-Delta (Anthropic weekly_brief) seit ITER1_START
SELECT
  COUNT(*)                                              AS anthropic_calls,
  COALESCE(SUM(cost_usd_millicents), 0)                 AS total_millicents,
  ROUND(COALESCE(SUM(cost_usd_millicents),0) / 100000.0, 2) AS total_usd
FROM costlog
WHERE provider = 'anthropic_opus'
  AND operation = 'weekly_brief'
  AND timestamp > '<ITER1_START aus Bash einsetzen>'::timestamptz;

-- Reports-Delta für netflix seit ITER1_START
SELECT id, pair_key, iso_week,
       generated_at AT TIME ZONE 'Europe/Berlin' AS generated_berlin,
       EXTRACT(EPOCH FROM (generated_at - LAG(generated_at) OVER (ORDER BY generated_at))) AS secs_after_prev
FROM insight_report
WHERE pair_key = 'netflix'
  AND generated_at > '<ITER1_START>'::timestamptz
ORDER BY generated_at DESC;
```

Alternativ aus dem Terminal mit den Helper-Funktionen:

```bash
echo "--- Cost-Delta seit iter1 ---"
costlog_delta_since "$ITER1_START"
echo "--- Reports seit iter1 ---"
report_count_since "$ITER1_START"
```

**Paste-Back-Slot Cost-Delta Iter 1:**

```
[paste — Format: rows|total_millicents|total_usd]
```

**Paste-Back-Slot Reports-Delta Iter 1:**

```
[paste]
```

### Railway-Logs Iter 1

#### → Im Railway-Web-UI

Filter setzen für das Zeitfenster `ITER1_START` bis `ITER1_END` + Suchstring `brief_`. Mindestens diese zwei Event-Namen sind nach PR #138/#139 zuverlässig:
- `brief_request_received`
- `brief_lock_attempt`

(Die End-of-Pipeline-Events können wegen Sub-Logger-Konflikt fehlen — kein Blocker für Hypothesen B/D/E.)

**Paste-Back-Slot Railway-Logs Iter 1** (alle `brief_*`-Zeilen, source-IPs intakt lassen):

```
[paste — Format pro Zeile: <timestamp> INFO app.api.insights brief_request_received {pair: ..., force: ..., forwarded_for: ..., forwarded_ip: ..., user_agent: ...}]
```

### Entscheidungs-Matrix Iter 1

| Beobachtung in Logs/DB | Verifiziert | Aktion |
|---|---|---|
| 1× `brief_request_received` total (nur 1 trotz 2 Curls) | — | Edge schluckt Curl 2 oder Token-Auth versagt — eskalieren |
| 2× `brief_request_received`, **gleiche** forwarded_ip+UA, Abstand <100ms | **B (Edge-Proxy-Retry)** | STOP, paste-back vollständig |
| 2× `brief_request_received`, **unterschiedliche** forwarded_ip | externe Drittquelle | weiter analysieren mit Wolf |
| 2× `brief_request_received`, gleiche IP, Abstand ~5s | erwartetes Wolf-Pattern | Lock-Block-Verhalten als nächstes prüfen |
| `brief_lock_attempt` 2× → 1× `brief_lock_timeout` + (irgendwann später) Acquired | Lock blockt korrekt | weiter mit Iter 2 — Race konnte nicht ausgelöst werden |
| `brief_lock_attempt` 2× → **2× acquired** für selben `pair+iso_week` ohne Timeout | **D (Lock-Scope-Bug)** | STOP, paste-back vollständig |
| Cost-Delta = 1 anthropic_opus call bei 2 Curls + 1 Report | Lock + Recheck haben gegriffen | weiter mit Iter 2 |
| Cost-Delta = 2 anthropic_opus calls bei 2 Curls + 1 Report | **Doppel-LLM trotz nur 1 persistierter Row** → Last-Write-Wins, Lock versagt | STOP, paste-back, ich übernehme |
| Cost-Delta = 2 calls + 2 Reports | Composite-PK-Duplikat? (sollte nicht möglich sein, aber prüfen) | STOP, paste-back |
| `force=true` und Logs zeigen `outcome=cache_hit` (falls Pipeline-Done-Logs ankommen) | Force-Flag-Bug | im Final-Befund flaggen, weiter |

**Wolf-Entscheidung nach Iter 1**: weiter zu Iter 2 / STOP / Wolf-Ping?

```
[ankreuzen / Notiz]
```

---

## Iteration 2 — Parallel, 0s gap (max. Race-Wahrscheinlichkeit)

Nur durchführen falls Iter 1 nicht zu STOP geführt hat.

### Pre-Run-Anker

#### → Im Terminal ausführen

```bash
ITER2_START=$(utc_now)
echo "iter2 start (UTC): $ITER2_START"
echo "iter2 start (Berlin): $(berlin_now)"
```

### Curls — Parallel 0s gap

#### → Im Terminal ausführen

```bash
( curl -s "https://api.creative-radar.de/api/insights/weekly?pair=netflix&force=true" \
    -H "Authorization: Bearer $API_TOKEN" \
    -o /tmp/smoke-3b/iter2-call1.json \
    -w "[iter2-c1] http=%{http_code} time=%{time_total}s\n" ) &
( curl -s "https://api.creative-radar.de/api/insights/weekly?pair=netflix&force=true" \
    -H "Authorization: Bearer $API_TOKEN" \
    -o /tmp/smoke-3b/iter2-call2.json \
    -w "[iter2-c2] http=%{http_code} time=%{time_total}s\n" ) &
wait
ITER2_END=$(utc_now)
echo "iter2 end (UTC): $ITER2_END"
echo "iter2 end (Berlin): $(berlin_now)"
```

**Paste-Back-Slot Curl-Output Iter 2:**

```
[iter2-c1] http=... time=...
[iter2-c2] http=... time=...
```

### Post-Verify

#### → Im Terminal ausführen

```bash
echo "--- Cost-Delta seit iter2 ---"
costlog_delta_since "$ITER2_START"
echo "--- Reports seit iter2 ---"
report_count_since "$ITER2_START"
```

**Paste-Back-Slot Cost-Delta Iter 2:**

```
[paste]
```

**Paste-Back-Slot Reports-Delta Iter 2:**

```
[paste]
```

### Railway-Logs Iter 2

#### → Im Railway-Web-UI

Fenster `ITER2_START` bis `ITER2_END`, Suche `brief_`.

**Paste-Back-Slot Railway-Logs Iter 2:**

```
[paste — alle brief_*-Zeilen mit forwarded_ip + user_agent]
```

### Entscheidungs-Matrix Iter 2

Identische Tabelle wie Iter 1 (siehe oben). 0s-Parallel maximiert die Race-Wahrscheinlichkeit — wenn hier Lock + Recheck halten, ist B/D unwahrscheinlich.

**Wolf-Entscheidung nach Iter 2**: STOP (Hypothese verifiziert) / Wolf-Ping (Budget-Cap erreicht) / Iter 3 starten (nur falls Budget Wolf-bestätigt erweitert)?

```
[Notiz]
```

---

## Iteration 3 — Sequential, 30s gap (nur nach Wolf-Ping wegen Budget)

> ⚠️ Bei 2 Curls × ~$2 würden wir hier den $8-Threshold reißen. Nur durchführen wenn Wolf nach Iter 2 explizit grünes Licht gibt.

### Pre-Run-Anker

#### → Im Terminal ausführen

```bash
ITER3_START=$(utc_now)
echo "iter3 start (UTC): $ITER3_START"
echo "iter3 start (Berlin): $(berlin_now)"
```

### Curls — Sequential 30s gap

#### → Im Terminal ausführen

```bash
( curl -s "https://api.creative-radar.de/api/insights/weekly?pair=netflix&force=true" \
    -H "Authorization: Bearer $API_TOKEN" \
    -o /tmp/smoke-3b/iter3-call1.json \
    -w "[iter3-c1] http=%{http_code} time=%{time_total}s\n" ) &
sleep 30
( curl -s "https://api.creative-radar.de/api/insights/weekly?pair=netflix&force=true" \
    -H "Authorization: Bearer $API_TOKEN" \
    -o /tmp/smoke-3b/iter3-call2.json \
    -w "[iter3-c2] http=%{http_code} time=%{time_total}s\n" ) &
wait
ITER3_END=$(utc_now)
echo "iter3 end (Berlin): $(berlin_now)"
```

**Paste-Back-Slots Iter 3** (Curl-Output, Cost-Delta, Reports-Delta, Railway-Logs):

```
[Curl-Output]
```

```
[Cost-Delta]
```

```
[Reports-Delta]
```

```
[Railway-Logs brief_*]
```

### Entscheidungs-Matrix Iter 3

| Beobachtung | Schluss |
|---|---|
| 30s ist genau Recent-Row-Window (120s); zweiter Call sollte `brief_pipeline_done outcome=debounce_hit` oder `cache_hit` zeigen | Debounce-Schicht greift, Lock irrelevant |
| 2× `brief_llm_call_start` bei 30s-Gap | Recent-Row-Debounce versagt — eigener Bug |
| Cost-Delta = 1 call, 1 Report | erwartet bei sauberer Debounce |

---

## Iteration 4 — Sequential, 60s gap

> ⚠️ Budget — nur nach Wolf-Ping.

#### → Im Terminal ausführen

```bash
ITER4_START=$(utc_now)
( curl -s "https://api.creative-radar.de/api/insights/weekly?pair=netflix&force=true" \
    -H "Authorization: Bearer $API_TOKEN" \
    -o /tmp/smoke-3b/iter4-call1.json \
    -w "[iter4-c1] http=%{http_code} time=%{time_total}s\n" ) &
sleep 60
( curl -s "https://api.creative-radar.de/api/insights/weekly?pair=netflix&force=true" \
    -H "Authorization: Bearer $API_TOKEN" \
    -o /tmp/smoke-3b/iter4-call2.json \
    -w "[iter4-c2] http=%{http_code} time=%{time_total}s\n" ) &
wait
ITER4_END=$(utc_now)
echo "iter4 end (Berlin): $(berlin_now)"
```

**Paste-Back-Slots Iter 4:**

```
[Curl-Output]
```

```
[Cost-Delta]
```

```
[Reports-Delta]
```

```
[Railway-Logs brief_*]
```

---

## Iteration 5 — Sequential, 120s gap (Negativ-Kontrolle)

> ⚠️ Budget — nur nach Wolf-Ping. Negativ-Kontrolle: bei >2min Abstand sollte der Lock unabhängig greifen.

#### → Im Terminal ausführen

```bash
ITER5_START=$(utc_now)
( curl -s "https://api.creative-radar.de/api/insights/weekly?pair=netflix&force=true" \
    -H "Authorization: Bearer $API_TOKEN" \
    -o /tmp/smoke-3b/iter5-call1.json \
    -w "[iter5-c1] http=%{http_code} time=%{time_total}s\n" ) &
sleep 120
( curl -s "https://api.creative-radar.de/api/insights/weekly?pair=netflix&force=true" \
    -H "Authorization: Bearer $API_TOKEN" \
    -o /tmp/smoke-3b/iter5-call2.json \
    -w "[iter5-c2] http=%{http_code} time=%{time_total}s\n" ) &
wait
ITER5_END=$(utc_now)
echo "iter5 end (Berlin): $(berlin_now)"
```

**Paste-Back-Slots Iter 5:**

```
[Curl-Output]
```

```
[Cost-Delta]
```

```
[Reports-Delta]
```

```
[Railway-Logs brief_*]
```

### Entscheidungs-Matrix Iter 5

| Beobachtung | Schluss |
|---|---|
| Cost-Delta = 2 anthropic_opus calls bei 120s-Gap | Lock GREIFT nicht zuverlässig — Hypothesen-Hierarchie umstoßen |
| Cost-Delta = 1 call, 1 Report | erwartetes Verhalten, Lock funktioniert |
| 2 calls + 2 Reports | Composite-PK-Insertion-Problem — separater Bug |

---

## Pause-Bedingungen während des Runs

- Iter 1 Cost-Delta zeigt mehr anthropic-Calls als erwartet → **PAUSE**, paste-back, ich analysiere bevor Iter 2 startet
- Beliebige Iter zeigt `forwarded_ip` aus AWS/CloudFlare-Range (nicht Wolfs lokale IP) → **PAUSE**, Hypothese B sehr wahrscheinlich
- Beliebige Iter zeigt `brief_lock_timeout` WARNING → **PAUSE**, das ist neu und relevant
- Railway-Logs zeigen nichts trotz HTTP 200 → **PAUSE**, Logging-Pfad ist noch immer kaputt

---

## Cumulative Cost Tracker

Nach jeder Iteration eintragen (`cost_usd_millicents` Summe / 100000):

| Iteration | Curls | Cost (USD) kumulativ |
|---|---|---|
| 1 | 2 | $___ |
| 2 | 2 | $___ |
| 3 | 2 | $___ |
| 4 | 2 | $___ |
| 5 | 2 | $___ |

**Hard-Stop bei kumulativ > $8.**

---

## Finaler Befund-Slot

> Diesen Abschnitt fülle ich (Claude) nach allen Paste-Backs. Bis dahin leer lassen.

### Hypothesen-Verifikation

| Hypothese | Status | Evidenz (Iter-Nummer + Log-Zitat) |
|---|---|---|
| A — Tests-Lücke | nicht durch Smoke testbar | — |
| B — Edge-Proxy-Retry | tbd | |
| D — Lock-Scope-Bug | tbd | |
| E — Legitime Mix-Calls | tbd | |
| Force-Cache-Hit-Anomalie | tbd | |

### Empfohlener Fix-Pfad

(Minimal vs. Robust mit Aufwand — fülle ich)

### Wolf-Ping

(2-3 Fragen — fülle ich)

---

## Hand-Off

1. Wolf-Run von Setup + Iter 1.
2. Paste-Back in die Slots oben (direkt in dieser Doc-Datei, oder im Chat — beides ok).
3. Ich analysiere nach Iter 1, schlage Iter 2 oder STOP vor.
4. Wiederholen bis Hypothese verifiziert oder Budget erschöpft.
5. Ich schreibe Final-Befund + Wolf-Ping in den Slot am Doc-Ende, dann eigener Fix-Sprint.
