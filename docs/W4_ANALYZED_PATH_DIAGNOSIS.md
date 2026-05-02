# W4 Mini-Run: `analyzed`-Setter-Diagnose + Konvergenz

**Erstellt:** 2026-05-02 / Phase 4 W4 Mini-Run nach Task 4.1d
**Auftrag:** identifizieren, wo Production-Assets den Status `visual_analysis_status = 'analyzed'` bekommen, und Variante (a)-Konvergenz umsetzen — `analyzed` als kanonischer Success-Status, 14d Toleranz für `done`.
**Charakter:** Snapshot-Diagnose. Wird gemeinsam mit anderen W3/W4-Snapshots in der Phase-Abschluss-Doku konsolidiert.

## 1. Methodik

`grep -rn '"analyzed"' backend/app --include="*.py"` plus `git log --all -S "..."` über alle bekannten Schreib-Patterns. Zusätzlich: alle Schreib-Stellen für `visual_analysis_status` aufgelistet, um zu prüfen, ob `analyzed` jemals als String-Literal an irgendeiner Stelle vorkommt.

## 2. Befunde — alle Vorkommen von `"analyzed"` im Repo

| Datei | Zeile | Art | Bemerkung |
|---|---|---|---|
| `services/visual_analysis.py` | 18 | Kommentar | (vor Konvergenz) erwähnte `analyzed` als legacy |
| `services/report_selector.py` | 28 | Konstante `ANALYSIS_DONE_STATES` | toleriert es als gültigen Done-Status (W3) |
| `services/insights.py` | 116 | Counter-Set | toleriert es als Success (W3-Fix) |
| `api/assets.py` | 171, 179 | Antwort-Feld-Name | im `analyze_visual_batch`-Response, NICHT als Status-Wert |
| `tests/test_insights.py` | 60, 108, 112, 121 | Test-Erwartung | prüft Counter-Toleranz |
| `tests/test_report_renderer_v2.py` | 41 | Test-Fixture | erstellt Asset mit Status `analyzed` |
| `tests/test_report_selector.py` | 130 | Test-Erwartung | prüft `_is_analysis_done` für `analyzed` |

**Schreib-Stellen für `visual_analysis_status` im aktuellen Code (vor Konvergenz):** alle setzen `"done"`, `"text_fallback"`, `"running"`, `"no_source"`, `"fetch_failed"`, `"vision_empty"`, `"vision_timeout"`, `"vision_error"`, `"image_unreachable"`, `"pending"`. **Niemand schreibt `"analyzed"`.**

Git-History-Audit:
```
git log --all --oneline -S 'visual_analysis_status = "analyzed"' -- backend/  → keine Treffer
git log --all --oneline -S "'analyzed'" -- backend/                            → 1 Treffer (W3-Doku)
```

**Kein einziger Code-Commit hat jemals `analyzed` als Status-Wert geschrieben.** Auch nicht in alten Branches (W2 hat alle gemergten Branches aufgeräumt; Git-History reflektiert den vollständigen Code-Snapshot des Repositories).

## 3. Schlussfolgerung — die Quelle des `analyzed`-Status liegt außerhalb des Repos

Production-Aggregat zeigt 4/20 Assets mit Status `analyzed`. Da der gesamte Repo-Code-Stand keinen Pfad enthält, der diesen Wert schreibt, müssen die Datensätze von einer **externen Quelle** kommen:

1. **Externes Script außerhalb des Repos** — z.B. ein Apify-Webhook, der auf einer separaten Apify-Aktor-Definition läuft und direkt in die Production-Postgres-DB schreibt
2. **Manuelle DB-Updates** durch Wolf via Postgres-Konsole (`UPDATE asset SET visual_analysis_status = 'analyzed' WHERE …`)
3. **Sehr alter Code-Stand** vor dem Repository-Beginn — heute nicht mehr im History, aber Daten persistent in der DB

Wahrscheinlichkeitsranking: (1) > (2) > (3). Wolfs W4-Briefing nannte Apify-Webhook explizit als wahrscheinlichsten Kandidaten.

**Endgültige Identifikation des dritten Code-Pfads ist Repository-extern und außerhalb des aktuellen Mini-Run-Auftrags.** Backlog-Punkt für Phase 5: Wolf-eigene Diagnose der Apify-Webhook-Konfiguration.

## 4. Variante (a)-Konvergenz — was umgesetzt wurde

Wolf-Empfehlung im W4-Briefing war: „im Code-Pfad `analyzed` schreiben (wie schon Production-Realität), in `services/visual_analysis.py` `done` durch `analyzed` ersetzen, Tests anpassen". Das gilt für den **Repo-internen Code-Schreib-Pfad**.

| Datei | Zeile | Vorher | Nachher |
|---|---|---|---|
| `services/visual_analysis.py` | 262 | `data["visual_analysis_status"] = "done"` | `data["visual_analysis_status"] = "analyzed"` |
| `services/visual_analysis.py` | 17–22 | `ALLOWED_TERMINAL_STATUS_FROM_DATA = {"done", "text_fallback"}` | `... = {"done", "analyzed", "text_fallback"}` (14d Toleranz) |
| `api/assets.py` | 152 | `if updated.visual_analysis_status == "done": done += 1` | `if updated.visual_analysis_status in {"done", "analyzed"}: done += 1` |

**Nicht geändert** (war bereits W3-tolerant):
- `services/report_selector.py:28` `ANALYSIS_DONE_STATES = {"done", "analyzed", "text_fallback"}` ✓
- `services/insights.py:116` Counter `{"done", "analyzed", "text_fallback"}` ✓

## 5. 14-Tage-Toleranz — Verhalten während des Übergangsfensters

| Datenherkunft | Status-Wert | Wird erkannt als | Wird gezählt als |
|---|---|---|---|
| Bestand vor 2026-05-02 (extern) | `analyzed` | done | success |
| Bestand vor W4 (in-repo) | `done` | done | success |
| Neue Vision-Analyse (post-W4) | `analyzed` | done | success |
| Externe Pipeline (post-W4) | `analyzed` | done | success |

**Beide Werte funktionieren identisch in Selector + Counter + Renderer.** Die Konvergenz schreibt nur den Code-internen Pfad neu; alle Konsumenten waren bereits beidseitig kompatibel (W3-Vorbereitung).

## 6. Phase-5-Backlog

- **Identifikation der externen `analyzed`-Setter-Quelle**: Wolf prüft Apify-Webhook-Konfiguration und etwaige Out-of-Repo-Scripts. Wenn gefunden und aktiv: dort denselben Status-Whitelist-Guard implementieren, sonst `done`-Werte als Bestand betrachten und nach 14d aus dem Toleranz-Fenster nehmen.
- **`done`-Bestandsdaten-Migration nach 14d**: einmalige `UPDATE asset SET visual_analysis_status = 'analyzed' WHERE visual_analysis_status = 'done'`. Nach Migration kann `done` aus `ALLOWED_TERMINAL_STATUS_FROM_DATA`, `ANALYSIS_DONE_STATES` und `insights.py:116` entfernt werden.
- **`error`/`provider_error`/`text_only`** sind weitere unklare Status-Werte aus alten Code-Snapshots oder externen Quellen — separater Diagnose-Auftrag in Phase 5 (im W3-Bericht als Backlog-Punkt 8 erfasst).

## 7. Tests-Stand

Vor Konvergenz: 128/128 grün. Nach Konvergenz: **130/130 grün** (2 neue Tests):

- `test_done_status_from_data_dict_is_still_accepted_for_compat` — verifiziert 14d-Toleranz: `done` aus `data`-Dict bleibt akzeptiert
- `test_analyzed_status_from_data_dict_is_accepted` — symmetrisch: `analyzed` aus `data`-Dict bleibt akzeptiert

Plus 1 angepasster Test:
- `test_done_when_vision_returns_useful_json` erwartet jetzt `analyzed` als Result (W4-Konvergenz dokumentiert im Test-Kommentar)
