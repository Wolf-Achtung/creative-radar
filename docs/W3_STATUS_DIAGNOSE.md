# W3 Status-Diagnose: Visual Analysis Pipeline

**Erstellt:** 2026-05-01 / Phase 4 W3 Task 3.1
**Zweck:** Inventur aller Stellen, an denen `Asset.visual_analysis_status` gesetzt wird,
plus Edge-Case-Liste für Task 3.2.
**Charakter:** Snapshot-Diagnose, nicht Living-Doc. Nach Phase-4-Ende kann sie
gemeinsam mit `W3_VISION_QUALITY.md` archiviert werden.

## 1. Status-Setz-Stellen (`Asset.visual_analysis_status`)

Reihenfolge: vom Default in der Datenbank bis zum Endzustand nach `analyze_asset_visual`.

| # | Datei | Zeile | Wert | Trigger / Bedingung | Beobachtung / Risiko |
|---|---|---|---|---|---|
| 1 | `app/models/entities.py` | 176 | `"pending"` | SQLModel-Default bei Insert | OK — Standardzustand vor analyze. |
| 2 | `app/database.py` | 75 | `'pending'` | DDL-Default für neue Spalte (Postgres) | OK — gleicher Wert wie SQLModel. |
| 3 | `app/services/visual_analysis.py` | 114 | `running` | Erste Anweisung in `analyze_asset_visual` | OK. |
| 4 | `app/services/visual_analysis.py` | 138 | `text_fallback` | `image_url` vorhanden, aber `settings.openai_api_key` leer | OK — kein API-Key, fällt auf Heuristik. |
| 5 | `app/services/visual_analysis.py` | 91 (`_heuristic_analysis`) | `text_fallback` | Heuristische Caption-Analyse, wenn kein image_url ODER OpenAI-Exception | OK — bewusster Fallback. |
| 6 | `app/services/visual_analysis.py` | 184 | `done` | OpenAI-Call wirft keine Exception | **BUG: auch wenn `raw == "{}"` oder Output strukturell leer ist. Fix in Task 3.2 → `vision_empty`.** |
| 7 | `app/services/visual_analysis.py` | 187 | `text_fallback` | OpenAI-Call wirft Exception (Timeout, 5xx, Connection-Error, etc.) | **BUG: Timeout vs. Provider-5xx vs. nicht-ladbares Bild werden alle in einen Topf geworfen. Fix in Task 3.2 → `vision_timeout` / `image_unreachable`.** |
| 8 | `app/services/visual_analysis.py` | 195 | `no_source` | `evidence.status == "no_source"` (capture hat keine Source-URL) | OK. |
| 9 | `app/services/visual_analysis.py` | 197 | `fetch_failed` | `evidence.status == "fetch_failed"` UND `data["visual_analysis_status"] == "done"` | **Edge-Case-Inkonsistenz: nur wenn Vision *trotzdem* "done" sagt, wird auf fetch_failed degradiert. Wenn Vision Exception → text_fallback (nicht fetch_failed), obwohl Capture failed. Race-Condition zwischen den beiden Pipeline-Stages. Fix in Task 3.2: `evidence.status == "fetch_failed"` soll generell zu `fetch_failed` führen.** |
| 10 | `app/services/visual_analysis.py` | 199 | aus `data.get("visual_analysis_status")` mit Default `text_fallback` | Fallback-Branch | **BUG: Vision-Modell könnte selbst einen Status halluzinieren (z.B. `"broken"` oder `"unknown"`), der dann unvalidiert in die DB landet. Fix in Task 3.2: Whitelist gegen `ALLOWED_TERMINAL_STATES` prüfen.** |

## 2. Lese-Stellen (Konsumenten dieser Status)

| Datei | Zeile | Logik |
|---|---|---|
| `app/services/insights.py` | 116 | Counter `visual_analyzed += 1` für `{"done", "text_fallback"}` (W1-Fix). |
| `app/services/insights.py` | 107 | `visual_status_counter` zählt jeden Status-Wert für Breakdown-KPI. |
| `app/services/report_selector.py` | 27 | `ANALYSIS_DONE_STATES = {"done", "analyzed", "text_fallback"}` (`analyzed` ist Legacy). |
| `app/services/report_selector.py` | 28 | `ANALYSIS_FAILURE_STATES = {"error", "fetch_failed", "no_source"}` (`error` wird im Code nicht aktiv gesetzt — toter Status?). |
| `app/services/report_renderer_v2.py` | 122 | `data_gaps` umfasst `{"no_source", "fetch_failed", "error"}` (TABU — kein Edit). |
| `app/api/assets.py` | 152–165 | `analyze_visual_batch`-Counter: `done`, `no_source`, `fetch_failed`, `text_fallback`, `error`/`provider_error`. |

## 3. Status-Werte heute aktiv vs. nur abgefragt

| Status | aktiv gesetzt? | abgefragt? | Notiz |
|---|---|---|---|
| `pending` | ja (DB-Default + Model-Default) | ja | OK |
| `running` | ja | nein (rein transient) | OK |
| `done` | ja (Zeile 184) | ja (Counter, Selector) | **OVER-CLAIM: auch bei leerem Vision-Output** |
| `text_fallback` | ja (3 Stellen) | ja (Counter, Selector) | OK — bewusster Fallback |
| `no_source` | ja (Zeile 195) | ja | OK |
| `fetch_failed` | ja (Zeile 197) | ja | inkonsistent — siehe Edge-Case #9 |
| `analyzed` | nein (Legacy) | ja (in `ANALYSIS_DONE_STATES`) | tot — nur historische Daten |
| `error` | nein | ja (`ANALYSIS_FAILURE_STATES`, batch-counter) | tot |
| `provider_error` | nein | ja (batch-counter) | tot |

→ Drei abgefragte Status-Werte (`analyzed`, `error`, `provider_error`) werden **nirgends im aktiven Code gesetzt**. Sie sind Lese-Artefakte aus früheren Sprint-Phasen. Counter und Selector dürfen sie weiter tolerieren (Legacy-Daten), aber für neue Schreib-Wege sind sie irrelevant.

## 4. Edge-Cases identifiziert

Wolfs Vorgabe: mind. 3 Edge-Cases. Ich identifiziere **8** unklar oder falsch klassifizierte Zustände.

| # | Beobachtung | Heutige Klassifikation | Vorschlag W3-Fix | Variante (A=zählt nicht / B=zählt) |
|---|---|---|---|---|
| 1 | OpenAI antwortet, aber `raw == "{}"` oder `_safe_json` liefert `{}` (leeres Dict) | `done` (zu optimistisch) | neuer Status `vision_empty` | A |
| 2 | OpenAI-Call wirft Exception (Timeout, Connection-Error, RateLimit, 5xx) | `text_fallback` (Sammeleimer) | aufspalten: `vision_timeout` (httpx.TimeoutException, openai.APITimeoutError), `vision_error` (alle anderen Provider-Errors) | A für beide |
| 3 | OpenAI Vision kann das Bild nicht laden (CDN-Hotlink-Block, 403/404 vom CDN aus, R2 403/404) | aktuell als generischer Exception → `text_fallback` | neuer Status `image_unreachable` (erkannt anhand der Exception-Message oder einer kurzen Probe-Anfrage) | A |
| 4 | Bild ist zu klein / korrupt / nicht-Bild (capture skipt es heute, geht zu nächstem Source) | `fetch_failed` wenn alle Sources skippen | OK auf Capture-Ebene; eigener Status `image_invalid` für „OpenAI lehnte Bild als ungültig ab" (z.B. unsupported format error) | A |
| 5 | `evidence.status == "fetch_failed"` UND OpenAI-Exception (Race) | `text_fallback` (Vision-Pfad gewinnt) | `fetch_failed` (Capture-Pfad sollte gewinnen, weil ohne Bild jede Vision-Aussage spekulativ ist) | A |
| 6 | OpenAI-Output ist Halluzination („eine Person spricht in die Kamera" für Trailer-Material) | `done` (Counter zählt mit) | nicht hier — gehört zu Task 3.4 (Vision-Qualität) und Task 3.5 (Re-Prompt-Hebel). Kein eigener Status. | – |
| 7 | Vision-Modell halluziniert sich selbst einen Status: `data["visual_analysis_status"] = "broken"` o.ä. | wird unvalidiert übernommen (Zeile 199) | Whitelist-Guard: nur `done` / `text_fallback` aus `data` akzeptieren, alles andere → `text_fallback` | – |
| 8 | Capture-Pfad: `storage.put()` raised (R2 transient down) | `fetch_failed` (W2-Fix) | OK, kein Eingriff nötig | – |

## 5. Designentscheidung W3 (Wolf-Empfehlung im Briefing übernommen)

- **Variante A** für die neuen Fehler-Status (`vision_empty`, `vision_timeout`, `vision_error`, `image_unreachable`, `image_invalid`): zählen **NICHT** als „capturierte/analysierte Assets" im `visual_analyzed`-Counter (`services/insights.py:116`). User sieht ehrliche Done-Quote.
- **Variante B** für `text_fallback`: bestehendes W1-Verhalten beibehalten — zählt mit, weil der Caption-Fallback ein erfolgreich abgeschlossener Pfad ist.

Konkret bedeutet das für Task 3.2:
- `services/insights.py:116` bleibt unverändert (`{"done", "text_fallback"}`).
- Neue Status-Werte werden NICHT ergänzt → automatisch Variante A.
- `services/report_selector.py` `ANALYSIS_FAILURE_STATES` wird um die neuen Status erweitert, sodass Selector sie sauber als „nicht für Report" filtert.

## 6. Was Task 3.2 konkret ändern muss

Aus dieser Diagnose abgeleitet, **vier neue Status-Werte** und drei Logik-Fixes:

1. **`vision_empty`** — Edge-Case #1. Set in `visual_analysis.py:184`-Branch nach Validierung des `data`-Dicts.
2. **`vision_timeout`** — Edge-Case #2. Set wenn Exception ist `openai.APITimeoutError` oder `httpx.TimeoutException` (oder allgemein Klasse mit `Timeout` im Namen).
3. **`image_unreachable`** — Edge-Case #2/#3. Set wenn OpenAI-Exception-Message eine bekannte „cannot load image"-Signatur enthält (z.B. `"download"` + `"failed"` + URL-Substring).
4. **`vision_error`** — Edge-Case #2 Sammeleimer für Provider-Errors, die nicht Timeout/Image sind.

Plus Logik-Fixes:
- **Edge-Case #5** (Capture-vs-Vision-Race): `if evidence.status == "fetch_failed"` ohne `and data.get(...) == "done"` — Capture-Failure dominiert immer.
- **Edge-Case #7** (Vision halluziniert Status): `_as_text(data.get("visual_analysis_status"), "text_fallback")` durch Whitelist-Guard ersetzen.
- **Counter-Erweiterung** `services/report_selector.py:28`: `ANALYSIS_FAILURE_STATES |= {"vision_empty", "vision_timeout", "vision_error", "image_unreachable", "image_invalid"}`.

## 7. Was NICHT Teil von Task 3.2 ist

- Edge-Case #6 (Vision-Output halluziniert Inhalte) — gehört zu Task 3.4 (Diagnose) und 3.5 (Hebel).
- Backfill von Legacy-`analyzed`-Werten — kein Briefing-Auftrag.
- DB-Migration für die neuen Status-Werte — kein Constraint nötig (`visual_analysis_status` ist `VARCHAR`).
