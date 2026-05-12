# Landing-Page Pair-Sync — Diagnose

**Datum:** 2026-05-12
**Branch:** `claude/diagnose-landing-page-sync-loN8t`
**Operating-Mode:** Diagnose-First, KEINE Code-Änderungen
**Autor:** Wolf Hohl

---

## 1. Problem

Die Landing-Page (`app.creative-radar.de`) rendert nur **6** Studio-Briefing-Cards und labelt sie pauschal mit „DE + US, wöchentlich". Backend (`PAIRS`-Dict in `insight_engine.py`) führt aber **9** enabled Pairs mit korrekten Marktdaten — drei davon (`universalpictures`, `paramountplus`, `lionsgate`) erscheinen gar nicht, und die sechs sichtbaren tragen ein veraltetes Markt-Label: seit Phase A (UK-B1, kommentiert in `insight_engine.py:65-68`) sind alle sechs in Wahrheit **DE+US+UK**.

Das Out-of-Sync ist also zweidimensional: **fehlende Cards** und **falsche Markt-Strings auf den existierenden Cards**.

---

## 2. Phase-1-Befund: Frontend-Component + Datenfluss

### 2.1 Landing-Page-Component

- **Datei:** `frontend/src/App.jsx`
- **Section:** Z. 907-944 (`<section>` innerhalb der `hero`-Region, direkt unter „Studio-Briefings"-Headline)
- **Untertitel:** Z. 911 — hardcoded String `"Sechs Studio-Reviews pro Woche."` (deutsches Zahlwort, kein Count-Insert)
- **Card-Loop:** Z. 918-942 — inline-Array-Literal, kein State, kein Fetch, kein Import
- **Per-Card-Label:** Z. 940 — hardcoded `"DE + US, wöchentlich"` für **jede** Card (keine Pair-spezifische Formatierung)

### 2.2 Aktueller Datenfluss

**Keiner.** Die Pair-Liste ist ein inline-JSX-Array-Literal innerhalb der Render-Funktion:

```jsx
// frontend/src/App.jsx:918-925
{[
  { slug: 'warnerbros', label: 'Warner Bros' },
  { slug: 'sonypictures', label: 'Sony Pictures' },
  { slug: 'primevideo', label: 'Prime Video' },
  { slug: 'disney', label: 'Disney' },
  { slug: 'netflix', label: 'Netflix' },
  { slug: 'paramountpictures', label: 'Paramount' },
].map((pair) => (
  <a key={pair.slug} href={`/insights/weekly/${pair.slug}`} className="card" ...>
    <strong>{pair.label}</strong>
    <span className="muted small">DE + US, wöchentlich</span>
  </a>
))}
```

Kein API-Call, keine Konstante, keine Datei-Trennung. Pair-Liste, Display-Name und Markt-Label leben alle als String-Literale direkt im JSX.

### 2.3 Zweite Quelle der Wahrheit im Frontend (Drift-Risiko)

`frontend/src/InsightWeekly.jsx:10-20` definiert eine **zweite hardcodete** Pair-Map:

```jsx
const FALLBACK_LABEL = {
  warnerbros: 'Warner Bros · DE + US + UK',
  sonypictures: 'Sony Pictures · DE + US + UK',
  primevideo: 'Prime Video · DE + US + UK',
  disney: 'Disney · DE + US + UK',
  netflix: 'Netflix · DE + US + UK',
  paramountpictures: 'Paramount · DE + US + UK',
  universalpictures: 'Universal Pictures · DE + US + UK',
  paramountplus: 'Paramount+ · DE + US + UK',
  lionsgate: 'Lionsgate · US + UK',
};
```

Diese Map ist auf Sprint-2026-05-12-Stand **korrekt und vollständig** (alle 9 Pairs, richtige Markt-Strings). Sie wird in `InsightWeekly.jsx:994` als Fallback-Label genutzt, wenn der `/api/insights/weekly`-Response noch nicht da ist oder das `pair_label`-Feld leer ist.

→ **Konsequenz:** Es gibt bereits eine Pair-Registry im Frontend, die korrekter ist als die Landing-Page-Cards. Ein Refactor sollte beide Quellen vereinen.

---

## 3. Phase-2-Befund: Backend-PAIRS-Schema + Endpoint-Status

### 3.1 PAIRS-Dict-Struktur

- **Datei:** `backend/app/services/insight_engine.py`, Z. 87-456
- **9 Einträge**, alle `enabled=True`:
  1. `warnerbros` — TT/IG/YT × DE/US/UK
  2. `sonypictures` — TT/IG/YT × DE/US/UK
  3. `primevideo` — TT/IG × DE/US/UK; YT US-only
  4. `disney` — TT/IG/YT × DE/US/UK (UK-Channels existieren, TT-UK ist single-handle)
  5. `netflix` — TT/IG/YT × DE/US/UK
  6. `paramountpictures` — TT/IG × DE/US/UK; YT US+UK only
  7. `universalpictures` — TT/IG/YT × DE/US/UK
  8. `paramountplus` — TT/IG × DE/US/UK; YT US+DE only
  9. `lionsgate` — TT/IG × US/UK; YT US only — **kein DE**

### 3.2 Per-Pair-Felder (heute)

```python
PAIRS["warnerbros"] = {
    "label": "warnerbros DE+US",         # technisch, lowercase, VERALTET (sagt DE+US, ist real DE+US+UK)
    "platforms": {                       # source of truth ab Sprint-4
        "tiktok": [{"handle": ..., "market": "US"|"DE"|"UK"}, ...],
        "instagram": [...],
        "youtube": [...],
    },
    "platform": "tiktok",                # backwards-compat mirror
    "channels": [...],                   # backwards-compat mirror = platforms["tiktok"]
    "enabled": True,
    "reason": None,                      # str wenn enabled=False
}
```

**`enabled` ist sauber gepflegt** — `_enabled_pair_keys()` in `api/insights.py:23-24` filtert darüber, und die einzige Schreibstelle ist der PAIRS-Dict selbst.

**Was fehlt für Frontend-Konsum:**
- **`display_name`** — es gibt nur `label` ("warnerbros DE+US"), das technisch/lowercase ist und das Markt-Suffix bereits enthält (und es ist auf DE+US-Stand verbliebene Pre-Phase-A-Brief, also veraltet). Ein sauberes `display_name: "Warner Bros"` müsste neu eingeführt oder aus dem Key gemappt werden.
- **`markets` als Top-Level-Array** — heute nur derivierbar via `sorted({c["market"] for p in pair["platforms"].values() for c in p})`.
- **`frequency_label`** — global „wöchentlich"; nicht im Schema. Heute nur als hardcoded String im Frontend.

### 3.3 Endpoint-Status

**Es existiert KEIN `/api/pairs`-Endpoint.** Die einzigen `PAIRS`-Konsumenten im API-Layer sind:

| Datei | Zweck | Liefert PAIRS-Daten an Frontend? |
|---|---|---|
| `backend/app/api/insights.py:13-24` | `_enabled_pair_keys()` als internes Helper | nein, nur in 404-Detail-String |
| `backend/app/api/insights.py:36-104` | `GET /api/insights/weekly?pair=…` | indirekt — liefert einen Brief pro Pair, kein Discovery |
| `backend/app/api/admin.py:463-471` | Admin-Loop „alle enabled Pairs durchgenerieren" | intern, nicht für Frontend |

`GET /api/insights/overview` (`backend/app/services/insights.py:59-…`) aggregiert Asset-/Channel-Metriken — **keine Pair-Liste im Response-Shape**.

**Endpoint-Skizze (Variante B):**

```
GET /api/pairs
→ 200 OK, application/json
[
  {
    "pair_key": "warnerbros",
    "display_name": "Warner Bros",
    "markets": ["DE", "US", "UK"],
    "frequency_label": "wöchentlich",
    "enabled": true
  },
  ...
]
```

Nur enabled Pairs (default), oder via `?include_disabled=true` alle. Sortierung: stabile Reihenfolge nach PAIRS-Insertion-Order (Python-3.7+ Dict garantiert das) oder per `sort_order`-Feld im Dict-Eintrag.

---

## 4. Phase-3-Befund: Schema-Mapping Backend → Frontend

### 4.1 Heute (Frontend)

| Element | Quelle |
|---|---|
| Pair-Liste | Inline-Array in `App.jsx:918-924` |
| Card-Headline | `pair.label` (z.B. „Warner Bros") — handgepflegt |
| Card-Untertitel | Hardcoded String `"DE + US, wöchentlich"` — gleicher Text auf jeder Card |
| Section-Subline | Hardcoded `"Sechs Studio-Reviews pro Woche."` |

### 4.2 Soll (Backend-getrieben)

| Element | Berechnung |
|---|---|
| Pair-Liste | `GET /api/pairs` → enabled Pairs in stabiler Reihenfolge |
| Card-Headline | `pair.display_name` (Backend liefert „Warner Bros") |
| Card-Untertitel | `pair.markets.join(' + ') + ', ' + pair.frequency_label` → „DE + US + UK, wöchentlich" / „US + UK, wöchentlich" (Lionsgate) |
| Section-Subline | `${pairs.length} Studio-Reviews pro Woche` (Zahl numerisch — der bisherige Stil „Sechs" als deutsches Zahlwort wird damit aufgegeben; alternativ Number-to-Word-Map für 1-12) |

### 4.3 Frequency

Heute global „wöchentlich" — gilt für alle Pairs (`window_days=30` default am Endpoint, aber das ist Datenfenster, nicht Briefing-Rhythmus). Sprint-Crons in `api/admin.py` laufen ebenfalls pair-agnostisch. **Empfehlung:** `frequency_label` als globale Konstante im Backend (z.B. `INSIGHT_FREQUENCY_LABEL = "wöchentlich"`), pro-Pair-Override optional. Im Endpoint-Response trotzdem pro-Pair mitliefern, damit das Frontend nichts duplizieren muss.

---

## 5. Implementierungs-Varianten

### Variante A — Frontend-Konstante updaten (Quick-Fix)

**Was:**
1. `App.jsx:918-924`: Array auf 9 Einträge erweitern, pro Eintrag `markets: ['DE','US','UK']` (bzw. `['US','UK']` für Lionsgate).
2. `App.jsx:940`: Markt-String aus `pair.markets.join(' + ')` ableiten.
3. `App.jsx:911`: Untertitel an Array-Länge koppeln (numerisch oder Zahlwort-Map).
4. Optional: `FALLBACK_LABEL` aus `InsightWeekly.jsx:10-20` und das App.jsx-Array in eine gemeinsame Datei `frontend/src/pairs.js` ziehen, um die Doppel-Wahrheit im Frontend zu eliminieren.

**Aufwand:** ~10-15 Min (mit Frontend-Konstanten-Konsolidierung ~25 Min)

**Pro:**
- Kein Backend-Touch, kein neuer Endpoint, keine Migration.
- Sofort live nach Frontend-Deploy.
- Sehr kleines PR-Diff, einfach zu reviewen.

**Contra:**
- Drift kehrt zurück sobald Pair 10 dazukommt (Apple TV+, MGM, A24 sind in der Pipeline diskutiert).
- Tech-Debt: zwei Frontend-Quellen oder, nach Konsolidierung, weiterhin eine Frontend-Quelle parallel zum Backend-PAIRS-Dict.

### Variante B — Backend-API + Frontend-Refactor

**Was:**
1. Neuer Endpoint `GET /api/pairs` in `backend/app/api/insights.py` (oder neue `backend/app/api/pairs.py`-Datei, sauberer).
2. PAIRS-Dict-Schema-Ergänzung: `display_name` (z.B. „Warner Bros") als neues Feld pro Eintrag — alternativ aus Key abgeleitet (`SLUG_TO_DISPLAY = {"warnerbros": "Warner Bros", ...}`) in einer Mapping-Konstante, damit die PAIRS-Struktur stabil bleibt.
3. Endpoint berechnet `markets` aus `pair["platforms"]` (Union über alle Plattformen) und liefert `frequency_label` als globale Konstante.
4. `frontend/src/api/client.js`: `pairs()` Endpoint-Wrapper.
5. `frontend/src/App.jsx`: bei Mount `endpoints.pairs()` aufrufen, Loading-State + Error-Fallback.
6. `frontend/src/InsightWeekly.jsx:10`: `FALLBACK_LABEL` weiterhin als Initial-State, aber aus dem Pairs-Fetch hydratisiert.

**Aufwand:** ~45-75 Min (Endpoint ~15 Min, Pydantic-Response-Model ~10 Min, Test ~15 Min, Frontend-Fetch + Loading + Error ~20 Min, Konsolidierung mit InsightWeekly ~15 Min)

**Pro:**
- Single Source of Truth ist das Backend-PAIRS-Dict. Pair-Add wird zur 1-Datei-Änderung (`insight_engine.py`) + ggf. `display_name`-Map.
- Eliminiert beide Frontend-Hardcodes (Landing-Page + FALLBACK_LABEL).
- Sauberer Pfad für zukünftige Pair-Metadaten (Status-Badges „neu", „beta", Icons, Reihenfolge-Override, etc.).

**Contra:**
- Mehr Touch-Points: Backend (Endpoint + Schema), Frontend (Fetch-Logik + Loading-State), Tests.
- Loading-State auf Landing-Page (Cards „flacken" beim ersten Render) — kann mit Skeleton oder server-side rendering im Build-Step abgemildert werden, aber das Setup ist Vite/CSR-only.
- `display_name`-Felder müssen einmalig in den 9 Einträgen ergänzt werden (oder via Mapping-Konstante).

### Variante C — Hybrid: Backend-API + Build-Step-Bake

**Was:** Backend-Endpoint wie B, aber das Frontend zieht ihn nicht zur Laufzeit, sondern in einem `vite build`-Pre-Step (Netlify-Build-Command-Hook), serialisiert die Response in `frontend/src/pairs.generated.json` und baked sie als Konstante ein.

**Aufwand:** ~40-55 Min (Backend wie B, Build-Step + Netlify-Config + .gitignore-Entscheidung).

**Pro:** Cards sofort da, kein Loading-State.

**Contra:**
- Build-Step-Bake heißt: Pair-Add im Backend ist erst nach Frontend-Rebuild+Deploy sichtbar. Praktisch fast so manuell wie Variante A.
- Zusätzlicher Failure-Modus (Build kann Backend nicht erreichen → stale JSON wird eingebaked, ohne dass jemand es merkt).
- Vermischt Frontend-Build mit Backend-Verfügbarkeit — eigentlich ein Anti-Pattern.

**Bewertung:** Eher unsauber, würde ich nicht empfehlen.

---

## 6. Empfehlung + Wolf-Ping

### Empfehlung: **Variante B**

Begründung:
- Das Pair-Universum wächst sichtbar (UK Phase A ist gerade durch, B1 frisch dazu, Lionsgate als US+UK-Sonderform neu — 9 Pairs heute, Phase B könnte 12+ erreichen).
- Die ~45-75 Min Refactor amortisieren sich nach 1-2 weiteren Pair-Adds (jeder Pair-Add ohne B kostet zwei Frontend-Touches: `App.jsx` + `InsightWeekly.jsx`).
- B löst gleichzeitig den unsichtbaren Bug: aktuell zeigen die 6 vorhandenen Cards „DE + US, wöchentlich", obwohl alle 6 nach Phase A real DE+US+UK sind. Variante A müsste diesen Bug separat fixen; B macht ihn strukturell unmöglich.
- Loading-State-Risk auf Landing-Page ist gering — Anthropic-API-Call ist es nicht, das ist ein in-process `dict.items()`. p50 < 50 ms ist realistisch.

### Fallback: **Variante A**

Wenn heute **schnell** der Live-Stand gefixt werden soll und B als eigenständiger Sprint später kommt: Variante A mit Frontend-Konstanten-Konsolidierung (Landing-Page + FALLBACK_LABEL in eine `pairs.js`). Reduziert wenigstens auf eine Frontend-Quelle, halbiert den nächsten Drift-Schmerz.

### Wolf-Ping — Fragen vor Implementations-Sprint

1. **Welche Variante?** B (empfohlen, ~60 Min), A (Quick, ~15-25 Min), oder A-jetzt + B-später?
2. **Markt-Label-Format auf der Landing-Page:** soll jede Card ihr eigenes Markt-Set zeigen („DE + US + UK, wöchentlich" / „US + UK, wöchentlich") oder lieber ein generisches „Studio-Brief, wöchentlich" ohne Markt-Auflistung (weniger Drift-Anfällig, aber weniger Info-Dichte)?
3. **Section-Subline:** „Neun Studio-Reviews pro Woche" (Zahlwort-Map, deutsche Tradition) oder „9 Studio-Reviews pro Woche" (numerisch, automatisch aus Array-Länge)?
4. **`display_name`-Quelle (nur bei B/C relevant):** neues Feld im PAIRS-Dict pro Eintrag (sauber, aber 9 Edits) oder separate `SLUG_TO_DISPLAY_NAME`-Mapping-Konstante (PAIRS-Struktur bleibt unverändert)?
5. **Lionsgate-Markt-String:** „US + UK, wöchentlich" — bestätigt? (Pair hat kein DE-Profil; `insight_engine.py:426-455` dokumentiert das explizit.)
6. **Pair-Reihenfolge:** aktuell Insertion-Order im PAIRS-Dict (warnerbros zuerst, lionsgate zuletzt). Soll das die Landing-Page-Reihenfolge sein, oder gibt es ein Sort-Kriterium (alphabetisch, Major-Studios zuerst dann Streamer, etc.)?

---

## 7. Anhang — Berührte Dateien (Read-Only-Bestandsaufnahme)

| Datei | Zeilen | Rolle |
|---|---|---|
| `frontend/src/App.jsx` | 907-944 | Landing-Page-Card-Section (Subtitle + Card-Loop + Markt-Label) |
| `frontend/src/InsightWeekly.jsx` | 10-20, 994 | Sekundäre Pair-Map (FALLBACK_LABEL) + Verwendung |
| `frontend/src/api/client.js` | 101-104 | `insightsOverview`, `insightsWeekly` — kein `pairs()` heute |
| `backend/app/services/insight_engine.py` | 60-456 | PAIRS-Dict (9 Einträge), Schema, Reaktivierungs-Kommentare |
| `backend/app/api/insights.py` | 13-24, 70-88 | `_enabled_pair_keys`, 404/503-Pfade — kein Pair-Listing-Endpoint |
| `backend/app/api/admin.py` | 46, 463-471 | Admin-Loop-Pfad über enabled Pairs |
| `backend/app/services/insights.py` | 59-… | `build_overview` — Assets, keine Pair-Liste |

**Stop-Bedingung-Checks aus dem Briefing:**
- *Pair-Liste an mehreren Stellen redundant?* **Ja** — `App.jsx:918-924` (6 Pairs) + `InsightWeekly.jsx:10-20` (9 Pairs, korrekt). Refactor sollte beide konsolidieren (Bestandteil von B); für A separat zu entscheiden, ob auch FALLBACK_LABEL angefasst wird.
- *PAIRS-Schema hat kein `display_name`?* **Korrekt** — `label` ist technisch/veraltet, kein Frontend-tauglicher Display-Name. Variante B braucht entweder Schema-Erweiterung oder eine Slug→Name-Map.
- *Disney UK-Frage?* **Klärt sich von selbst** — `insight_engine.py:226-273` zeigt aktive UK-Channels auf TT/IG/YT (`disneyuk`, `disneystudiosuk`, `marvel_uk`, `starwarsuk`, `DisneyUK`, `MarvelUK`, `StarWarsUK`). Disney **ist** DE+US+UK im PAIRS-Dict. Die im Briefing erwähnte „UK formal nicht im Pair"-Notiz trifft nicht (mehr) zu.
