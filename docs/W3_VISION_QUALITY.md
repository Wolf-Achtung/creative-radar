# W3 Vision-Pipeline-Output-Qualität: Diagnose

**Erstellt:** 2026-05-01 / Phase 4 W3 Task 3.4
**Datenbasis:** 10 Production-Samples gezogen via temporärem Admin-Endpoint
`/api/admin/sample-vision-outputs?limit=10`. Stichprobe diversifiziert über
`visual_analysis_status` (ORDER BY status, id).
**Charakter:** Snapshot-Diagnose. Wird gemeinsam mit `W3_STATUS_DIAGNOSE.md`
nach Phase-4-Ende archiviert.

## 1. Sample-Inventur

| # | Asset-ID (kurz) | Channel | Status | Quelle | OCR | Bewertung |
|---|---|---|---|---|---|---|
| 1 | `1a40c696` | @warnerbros (TikTok) | `analyzed` | TikTok-CDN screenshot/thumbnail | `B-MODE INDIA PG-13 © 2026 WBEI.` | gut |
| 2 | `52b78e3c` | @warnerbros (TikTok) | `analyzed` | TikTok-CDN | `Digger` | sehr gut |
| 3 | `675513c4` | @warnerbros (TikTok) | `analyzed` | TikTok-CDN | `DIGGER` | gut |
| 4 | `c3802f0d` | @warnerbros (TikTok) | `analyzed` | TikTok-CDN | `MORTAL KOMBAT` | gut |
| 5 | `601f1023` | A24 (Instagram) | `error` | IG-CDN | `Goin' to the chapel.` | **gut — aber Status widerspricht Inhalt** |
| 6 | `9f6ab3da` | Akkord Film (IG) | `no_source` | – | – | sehr gut (ehrlich, „keine Analyse möglich") |
| 7 | `fcec9935` | 24 Bilder (IG) | `no_source` | – | – | **leicht halluzinierend (behauptet „internationaler Kontext")** |
| 8 | `0e681100` | 20th Century Studios (IG) | `text_fallback` | `/storage/evidence/...jpg` (Legacy) + IG-CDN | `The moment is here. Get tickets…` | sehr gut — Status passt nicht zur Qualität |
| 9 | `11447c90` | 20th Century Studios (IG) | `text_fallback` | Legacy + IG-CDN | `Tour Diaries 001: CDMX…` | gut |
| 10 | `358b3c8d` | 20th Century Studios (IG) | `text_fallback` | Legacy + IG-CDN | `A category inspired by The Devil Wears Prada 2…` | gut |

## 2. Kritische Befunde

### 2.1 Post-F0.1 Object-Keys nirgends sichtbar

Kein einziges Asset hat einen Object-Key (`evidence/<asset_id>_<uuid>.jpg`).
Alle `visual_evidence_url`-Werte sind entweder `null` oder `/storage/evidence/...`
(Legacy). F0.1-Code ist deployed, aber **noch nicht produktiv ausgelöst** für
diese Assets — `analyze_asset_visual` müsste neu für sie laufen, damit der
Capture-Pfad einen R2-Key erzeugt.

**Konsequenz für Task 3.5 / W4:** der Backfill-Lauf
(`scripts/backfill_evidence.py`) ist Wolf-Aktion. Bis er gefahren ist, bleiben
alle Bestandsassets im Legacy-Zustand. Selector klassifiziert sie als secure
(F0.4 erkennt Legacy-Substring), aber die Backing-Files sind nach jedem
Railway-Redeploy weg.

### 2.2 `language`-Feld halluziniert ganze Sätze

Werte im Sample:

- `"Unknown"` (4×)
- `"English"` (1×, Output-Text aber deutsch)
- `"en"` (1×, Output-Text deutsch)
- `"Unknown (ohne sichtbaren Post-Text/Caption nicht verifizierbar)"` (1×)
- `"English (caption); likely mixed with Spanish context due to CDMX"` (1×)

Das Modell befüllt das Feld entweder mit ISO-artigen Codes, vagen Wörtern oder
ganzen Beschreibungssätzen. **Schemafehler im Prompt-Output.** Das Feld ist
nicht für Konsumenten geeignet — weder als Filter noch als Anzeige.

Nach `_as_text` landet der String ungeprüft in `Asset.language`. DB-Spalte ist
ein freier `VARCHAR`, kein Constraint zwingt das Modell zur Disziplin.

### 2.3 Status `error` mit vollständiger, kohärenter Analyse

Sample 5 (`601f1023`, A24 Euphoria): `visual_analysis_status = "error"`. Im
selben Datensatz steht ein 470-Zeichen-`ai_summary_de`, klares OCR
(`Goin' to the chapel. @euphoria Episode 3 tonight on @hbo.`), Sprache
korrekt erkannt (English). **Der Status widerspricht dem Inhalt.**

Das passt zum bekannten W3-Diagnose-Punkt: der Production-Erfolgs-Status
heißt **`analyzed`**, nicht `done` (mein Code in `services/visual_analysis.py`
setzt `done`, nirgends im aktuellen Code wird `analyzed` aktiv gesetzt). Es
muss einen weiteren Status-Pfad geben, den Task 3.1 nicht erfasst hat —
wahrscheinlich Apify-Receiver oder ein älteres Script. Die `error`-
Klassifikation hier könnte dieser Pfad sein, der bei Sub-Step-Failure den
Status auf `error` schaltet, obwohl die Analyse selbst erfolgreich war.

**Konsequenz für Counter-Korrektur:** `services/insights.py:116` muss
`{"done", "analyzed", "text_fallback"}` zählen, nicht nur `{"done",
"text_fallback"}`. 4 von 20 Production-Assets sind `analyzed` — die werden
heute NICHT als `visual_analyzed` gezählt, der Counter zeigt 0 statt 4
(plus die `text_fallback`).

### 2.4 No-Source-Halluzination (eines von zwei Samples)

Beide `no_source`-Samples haben `image_url=None`, `caption=` (leer oder
fehlend), keine OCR — also wirklich nichts zum Analysieren.

- Sample 6 (Akkord Film): **ehrlich.** Antwort beginnt mit „Der Link verweist
  auf den Instagram-Kanal 'Akkord Film', jedoch ohne konkreten Post" und
  listet konkret auf, was fehlt.
- Sample 7 (24 Bilder): **halluziniert leicht.** Behauptet Kontext-Information
  („Instagram-Post auf dem Kanal '24 Bilder' im internationalen Kontext")
  und schließt mit „Franchise nicht spezifiziert", ohne darauf hinzuweisen,
  dass NICHTS konkret bewertbar war.

Das ist Halluzinations-Risiko Stufe 1 (mild), aber die Production-Anzeige
würde 24 Bilder als „im internationalen Kontext" framen — eine inhaltlich
ungestützte Aussage.

### 2.5 `text_fallback` mit überraschend hoher Output-Qualität

Alle drei `text_fallback`-Samples haben präzises OCR und kohärente,
strukturierte Vision-Analysen. Code-Pfad-Erwartung wäre: bei text_fallback
hat das OpenAI-Vision-Call versagt und die Heuristik aus Caption übernommen.
Tatsächlich sehen die Outputs aus, als hätte das Modell das Bild gelesen.

**Hypothese:** Der `text_fallback`-Pfad in `_heuristic_analysis` läuft heute
gar nicht produktiv — entweder ist der Vision-Call durchgelaufen und hat
einen Status-Flag gesetzt, oder der Status `text_fallback` wird von einem
anderen Code-Pfad gesetzt, dessen Existenz ich noch nicht erfasst habe.

Konsistent mit 2.3: es gibt mindestens einen Code-Pfad in der Pipeline, den
Task-3.1-Diagnose nicht gefunden hat. Möglicherweise wird `analyzed` und
`text_fallback` von externem Code gesetzt (Apify-Receiver, Cron-Job,
Legacy-Modul). **Folge-Diagnose nötig — vermutlich in W4.**

### 2.6 OCR-Qualität durchweg verlässlich

`MORTAL KOMBAT`, `DIGGER`, `B-MODE INDIA PG-13`, `Goin' to the chapel`,
`The moment is here. Get tickets…` — OCR ist robust, kein Bug-Hebel.

## 3. Verbesserungs-Hebel

### Hebel A — Sprach-Feld-Whitelist (NIEDRIGER AUFWAND, HOHER IMPACT)

**Problem:** `language` enthält Halluzinationssätze statt ISO-Codes.

**Fix:** Post-Processing in `services/visual_analysis.py` nach
`_safe_json(raw)`. Whitelist `{"de", "en", "es", "fr", "it", "pt", "ja",
"ko", "zh", "unknown"}`. Wenn Modell-Output nicht matcht: case-insensitive
Substring-Suche; wenn auch das fehlschlägt: `"unknown"`.

Plus: Prompt-Ergänzung „`language` muss einer von: de, en, es, fr, it, pt,
ja, ko, zh, unknown sein."

**Test:** mock-Vision-Output mit halluziniertem Sprache-Wert → Asset bekommt
`unknown`. Mock mit korrektem ISO → Asset bekommt den Wert.

**Kosten-Impact:** keiner. Pure Post-Processing.

### Hebel B — No-Source-Halluzination unterbinden (NIEDRIGER AUFWAND, MITTLERER IMPACT)

**Problem:** Bei `evidence.status == "no_source"` UND leerer Caption sollte
das Modell explizit „keine Analyse möglich" liefern, statt zu raten.

**Fix:** Im `_heuristic_analysis`-Pfad, wenn caption leer ist, einen
expliziten String setzen: „Keine Inhaltsanalyse möglich — weder Bild noch
Caption-Text vorhanden." statt der heuristisch-vagen Beschreibung. Das ist
der einzige Pfad, der bei `no_source` ankommt (siehe `analyze_asset_visual`
Zeile 134-135 in `services/visual_analysis.py`).

**Test:** Asset ohne image_url + ohne caption → ai_summary_de == genau die
explizite „nicht möglich"-Phrase.

**Kosten-Impact:** keiner. Pure Codepfad-Anpassung.

### Hebel C — Prompt-Anti-Halluzination (MITTLERER AUFWAND, MITTLERER IMPACT)

**Nicht in W3 umsetzen.** Im Prompt eine Anti-Halluzinations-Klausel
einfügen („Wenn du nicht sicher bist, schreibe `nicht erkennbar`"). Das
würde alle Vision-Outputs leicht konservativer machen — Kosten/Nutzen
unklar bei dieser Stichprobengröße. **Backlog für W4+.**

### Hebel D — Status-Diagnose-Erweiterung (KEIN VISION-HEBEL — folgt aus 2.3 + 2.5)

**Nicht in W3 als Vision-Hebel.** Aber: Counter-Korrektur in
`services/insights.py:116` ist klar identifiziert (Variante B-Erweiterung
um `analyzed`). **Wird im selben Commit wie Hebel A/B umgesetzt** — der
Counter-Fix ist eine Konsequenz aus W3-Diagnose, nicht aus Task 3.5
spezifisch.

## 4. Empfehlung für Task 3.5

**Umsetzen: Hebel A + Hebel B + Counter-Fix** (drei kleine, niedrig-Risiko
Änderungen in einem Commit).

- A: Schemafehler im Output beheben
- B: Halluzinations-Risiko in der einzigen Pfadgruppe, wo es real ist
- Counter-Fix: `analyzed` als Erfolgs-Status zählen (Production-Realität)

**Backlog für W4+:**
- Hebel C (Prompt-Anti-Halluzination) — braucht größere Stichprobe
- Diagnose des unbekannten Code-Pfads, der `analyzed` und `error` setzt
  (vermutlich Apify-Receiver oder externes Script)
- F0.1-Re-Capture für Bestandsassets via Backfill-Skript (Wolf-Aktion)

## 5. Korrektur zu W3_STATUS_DIAGNOSE.md

Die Status-Diagnose aus Task 3.1 (Commit `8ea5aaa`) markierte `analyzed` als
„tot — nur historische Daten". **Falsch.** Production-Aggregat zeigt:
13× `text_fallback`, **4× `analyzed`**, 2× `no_source`, 1× `error` (von 20
Assets). `analyzed` ist der häufigste Erfolgs-Status nach `text_fallback`.

Mein Code in `services/visual_analysis.py` setzt aber `done` als Erfolgs-
Status, nirgends `analyzed`. Es muss einen anderen, mir unbekannten
Code-Pfad geben, der `analyzed` setzt. Stichprobe legt nahe, dass Outputs
aus diesem Pfad qualitativ vollwertig sind.

**Korrektur in derselben Berührung wie Hebel A/B:**
- `services/insights.py:116`: `{"done", "text_fallback"}` →
  `{"done", "analyzed", "text_fallback"}`. Damit zählen die 4 Production-
  Assets korrekt als `visual_analyzed`.
- Diese Doku verweist auf `W3_STATUS_DIAGNOSE.md` mit Korrektur-Hinweis.
