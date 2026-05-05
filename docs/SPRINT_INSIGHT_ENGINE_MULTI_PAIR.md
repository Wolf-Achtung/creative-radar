# Sprint 2 — Insight-Engine Multi-Pair (2026-05-06)

> **Sprint-Modus:** Konfig-Only. Kein Refactor an `aggregate_pair`,
> `_cross_market_matches`, `_channel_stats`. Erweiterung der `PAIRS`-
> Registry + `enabled`-Flag mit 503-Handling + Frontend-Label-Map +
> parametrisierte Tests.

---

## Scope

Insight-Engine-Endpoint von einem auf sechs aktivierte Tier-A-DE+US-TT-
Pairs erweitert. Ein weiterer Pair (`universalpictures`) ist als
`enabled=false`-Placeholder hinterlegt.

| # | Pair-Key | DE-Handle | US-Handle | Status | Reason (falls disabled) |
|---|---|---|---|---|---|
| 1 | `warnerbros` | warnerbrosdeutschland | warnerbros | ✅ enabled | — |
| 2 | `sonypictures` | sonypicturesgermany | sonypictures | ✅ enabled | — |
| 3 | `primevideo` | primevideode | primevideo | ✅ enabled | — |
| 4 | `disney` | disneyde | disney | ✅ enabled | — |
| 5 | `netflix` | netflixde | netflix | ✅ enabled | — |
| 6 | `paramountpictures` | paramountpicturesgermany | paramountpics | ✅ enabled | — |
| 7 | `universalpictures` | universalpicturesde | universalpictures | ⛔ disabled | US-Channel @universalpictures has 0 posts/30d, pair will activate when both channels have ≥3 posts/30d |

---

## Code-Änderungen

| Datei | Änderung |
|---|---|
| `backend/app/services/insight_engine.py` | `PAIRS` von 1 auf 7 Einträge erweitert; jeder Eintrag bekommt `enabled: bool` und `reason: str \| None`. Doc-Kommentar dokumentiert das Schema. |
| `backend/app/api/insights.py` | 503-Handling für `enabled=False`-Pairs. Strukturierter JSON-Body `{error: "pair_not_activated", pair, reason}` ohne DB- oder LLM-Aufruf. 404 bleibt für unbekannte Pair-Keys; Fehlerliste zeigt nur die enabled keys. |
| `frontend/src/InsightWeekly.jsx` | `FALLBACK_LABEL` für alle 7 Pairs. Neue Helper-Funktion `parsePairNotActivated` parst den 503-Body aus `err.message` (api/client.js wirft den Body als String); Error-Card rendert dann eine "Pair noch nicht aktiviert. Grund: …"-Notiz statt eines generischen Stack-Trace. |
| `backend/app/tests/test_insight_engine.py` | Zwei parametrisierte Tests: (1) `test_pairs_registry_schema` — jeder Eintrag hat das Sprint-2-Schema; (2) `test_aggregate_pair_handles_missing_channels_for_all_enabled_pairs` — `aggregate_pair` läuft gegen leere DB für jeden aktivierten Pair und liefert Notes statt zu crashen. |
| `backend/app/tests/test_api_insights_weekly.py` | **Neu.** TestClient-basierte Endpoint-Tests: 404 für unknown, 503 mit strukturiertem Body (parametrisiert über alle disabled pairs), 200 + Aggregation-only via `dry_run=true` (parametrisiert über alle enabled pairs), 200 mit gemocktem LLM. Plus eine Sentinel-Garantie, dass disabled pairs niemals den LLM-Wrapper erreichen (Cost-Awareness). |

Keine Änderungen an `aggregate_pair`, `_cross_market_matches`,
`_channel_stats`, `generate_weekly_report`, `_build_user_prompt`,
`SYSTEM_PROMPT`, `OPUS_MODEL_ALIAS` — die Pair-agnostischen Code-Pfade
aus Sprint 1 bleiben unangetastet (siehe Audit-Report 2026-05-05 Q2d).

---

## Test-Ergebnisse (lokal, sqlite-Fallback)

```
app/tests/test_insight_engine.py             26 passed   (vorher 13)
app/tests/test_api_insights_weekly.py        10 passed   (neu)
app/tests/test_insights.py                    8 passed
                                            ─────────
                                             44 passed
```

Insgesamt: 343 passed im Backend (2 pre-existing Failures in
`test_migration_extend_channel_registry.py` reproduzieren auf
`origin/main` ohne diese Änderung — alembic-batch-mode-SQLite-Topo-Sort,
unrelated).

Frontend: `npm run build` clean (200ms, 238 kB main bundle, gzip 73 kB).

---

## Cost-Awareness — Smoke-Test-Plan

**Im Code (kostenlos):** Alle 6 enabled Pairs sind via Dry-Run-Test
(parametrisiert) gegen leere DB grün — bestätigt, dass der Endpoint-
Pfad und die Aggregation für jeden Pair funktionieren.

**Auf Production (kostenpflichtig, Wolf-Quality-Gate):** 5 echte
Opus-Calls für die neuen Pairs (warnerbros ist bereits A-bewertet).

| Schritt | Pair | Cost-Schätzung |
|---|---|---|
| 1 | `sonypictures` | ~$0.20-0.40 |
| 2 | `primevideo` | ~$0.20-0.40 |
| 3 | `disney` | ~$0.20-0.40 |
| 4 | `netflix` | ~$0.20-0.40 |
| 5 | `paramountpictures` | ~$0.20-0.40 |
| **Total** | **5 × Opus 4.7** | **~$1.00-2.00** |

Der `universalpictures`-Pair löst keinen Opus-Call aus (503-short-circuit
ist im Test `test_disabled_pair_short_circuits_before_llm` mit Sentinel-
Exception abgesichert).

**Smoke-Test-Befehl pro Pair (Wolf nach Deploy):**

```bash
curl -s "https://api.creative-radar.de/api/insights/weekly?pair=sonypictures" \
  -H "Authorization: Bearer $API_TOKEN" \
  | jq '{pair: .pair_key, headline: .llm_output.headline, tldr: .llm_output.tldr, coverage: .coverage_pct, cost: .cost_usd_estimate}'
```

Wiederholen für `primevideo`, `disney`, `netflix`, `paramountpictures`.
Ergebnisse aus dem `headline`+`tldr`-Snippet in den PR-Body als
Quality-Gate-Sektion eintragen, A/B/C-Grade pro Pair entscheiden.

**503-Negativtest (kostenlos):**

```bash
curl -s -i "https://api.creative-radar.de/api/insights/weekly?pair=universalpictures" \
  -H "Authorization: Bearer $API_TOKEN"
# Erwartet: HTTP/1.1 503 Service Unavailable
# Body: {"detail":{"error":"pair_not_activated","pair":"universalpictures","reason":"…"}}
```

---

## Hinweise zur Pair-Konfig

- **Disney US-Handle:** Wolf-Brief sagt `disney`. Migration `e5d8f1a36b40`
  registriert nur `disneystudios`, `disneyanimation`, `disneyplus` für
  US-Disney. Falls `disney` nicht in der Production-DB existiert, surfaced
  `aggregate_pair` das in `notes` (`DE-Channel @disney (TikTok) wurde
  nicht in der DB gefunden — Onboarding/Whitelist-Eintrag prüfen.`) — der
  Report erscheint dann mit leerer US-Datenbasis. Wolf kann den Handle
  bei Bedarf in einer Folge-PR auf `disneystudios` korrigieren ohne
  weiteres Test-Refactoring.
- **Paramount US-Handle:** Korrekter Handle ist `paramountpics`, nicht
  `paramountpictures` (per Migration + Wolf-Brief).
- Die `_find_channel`-Lookup ist case-insensitive und filter nicht nach
  Markt — wenn ein Handle mehrfach existiert (z.B. `netflix` für US
  UND DE), nimmt der Lookup den ersten Treffer. Sprint-3-Backlog falls
  beobachtet.

---

## Optional (nicht in diesem Sprint)

- **Pair-Übersicht `/insights/weekly` ohne `:pair`-Param** — Briefing-§4
  als „nur bei Aufwand-Reserve". Aufwand-Reserve war heute durch die
  Test-Setup-Friktion (Test-DB-Init, fehlende Dev-Deps) verbraucht;
  Pair-Übersicht bleibt Sprint-3-Item.

---

## Backlog-Items, die durch diesen Sprint sichtbarer werden

1. **Per-Channel-Error-Isolation in Cron** (Repo-Audit Backlog #1) —
   sobald die 6 neuen Pairs Coverage haben, wird ein Apify-Hänger auf
   einem Channel den ganzen Cron-Run reißen. Stark empfohlen vor dem
   nächsten Channel-Erweiterungs-Sprint.
2. **`match_key.py`-Bug ("unknown" landet in DB)** (Repo-Audit Backlog
   #2) — der Read-Filter aus PR #74 maskiert das, aber je mehr Pairs
   produziert werden, desto mehr akkumuliert sich die DB-Drift.
3. **`_find_channel` ohne Markt-Filter** — siehe Hinweise oben. Wird
   zum Problem, sobald ein Handle in mehreren Märkten existiert.
4. **Cron-TT-Phase-Regression** (PR #76 Diagnose) — orthogonal zu
   diesem Sprint, aber jeder Pair-Report braucht Daten, die der Cron
   liefert. Cron-Fix ist Voraussetzung für die Quality-Gate-Smoke-Tests
   nach Deploy.
