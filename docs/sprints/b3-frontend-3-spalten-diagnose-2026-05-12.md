# B3 Frontend 3-Spalten Code-Stil-Diagnose

**Datum:** 2026-05-12
**Operating-Mode:** Read-only Diagnose (kein Code-Edit)
**Frage:** Soll B3 Frontend-Sprint die UK-Spalte explizit hartcoden
(Option A, ~1.5h) oder generisch über einen markets-Loop refactorn
(Option B, ~2-3h)?
**Anlass:** B1 (PR #121) hat das Backend-Schema um `uk_channel`
erweitert; B3 zieht das Frontend nach.

---

## TL;DR — Empfehlung

**Option A (explizit hartcoded UK)** ist die richtige Wahl für B3.
Drei harte Gründe (Detail unten):

1. **Backend ist selbst hartcoded DE/US/UK** — `_only_titles`,
   `_assets_total`, `_engagement`, `_post_url` etc. existieren als
   Per-Markt-Felder, nicht als generisches `markets`-Dict. Ein
   generisches Frontend würde gegen ein nicht-generisches Backend
   arbeiten und müsste die Markt-Keys trotzdem hartcoden.
2. **Cross-Market-Matching ist DE↔US-only** (explizit B2-Scope). Eine
   generische `markets.map()`-Struktur hätte auf der
   Cross-Market-Seite nichts zu loopen — das wäre Schein-Generik.
3. **FR/IT/ES sind hypothetisch, B2 ist konkret.** Der Refactor zu
   `per_market: dict` wird sich im Backend bei UK-pairwise-Matching
   (B2) sowieso aufdrängen — dann mit vollem Kontext (Triple-
   Intersection, UK↔DE-Engagement, etc.). B3 jetzt generisch zu
   bauen heißt, ein Pattern zu erfinden, das B2 wieder umkrempelt.

Aufwand-Spread: A ~1.5h gegen B ~2-3h. Risiko-Spread: A ist additiv
(15 Edits + 1 CSS-Rule), B berührt 15+ Stellen plus Pattern-Drift in
zwei mid-abstracted Spots (`market === 'DE' ? ... : ...`-Ternaries
in `collectRankedPosts()`).

---

## Backend-Schema-Realität

Alle Findings nach PR #126/#127 auf `main` (Sprint UK-B1 bereits
gelandet).

### PairAggregation top-level (`backend/app/schemas/insights.py:204-227`)

```
de_channel: Optional[ChannelStats]            (l. 213)
us_channel: Optional[ChannelStats]            (l. 214)
uk_channel: Optional[ChannelStats] = None     (l. 218)  ← B1
per_platform: list[PlatformAggregation] = []  (l. 227)
```

**Per-Markt-Felder hartcoded.** UK-Default `None` für pre-B1-Briefs.

### PlatformAggregation (`backend/app/schemas/insights.py:174-202`)

```
de_channel: Optional[ChannelStats] = None  (l. 191)
us_channel: Optional[ChannelStats] = None  (l. 192)
uk_channel: Optional[ChannelStats] = None  (l. 198)
```

Identisches Pattern. UK-Default `None`.

### TitleCoverage (`backend/app/schemas/insights.py:155-172`)

```
titles_in_both_markets: list[str]       (l. 156)  ← DE∩US-Semantik
de_only_titles: list[str]               (l. 157)
us_only_titles: list[str]               (l. 158)
uk_only_titles: list[str] = []          (l. 168)  ← B1
de_assets_with_title: int               (l. 159)
de_assets_total: int                    (l. 160)
us_assets_with_title: int               (l. 161)
us_assets_total: int                    (l. 162)
uk_assets_with_title: int = 0           (l. 169)  ← B1
uk_assets_total: int = 0                (l. 170)  ← B1
overall_coverage_pct: float             (l. 171)
```

**Wichtig:** `titles_in_both_markets` ist explizit DE∩US-Semantik.
Triple-Intersection (DE∩UK / US∩UK / DE∩US∩UK) und UK-pairwise sind
**B2-Scope** (siehe Kommentar l. 163-167).

### CrossMarketMatch (`backend/app/schemas/insights.py:142-153`)

```
de_engagement, us_engagement
de_duration_seconds, us_duration_seconds
de_post_url, us_post_url
de_caption_excerpt, us_caption_excerpt
```

**Reines DE↔US-Tupel.** Keine UK-Felder. `insight_engine.py:1782-1783`
bestätigt: *"Cross-Market bleibt explizit 2-Markt (DE↔US). Triple-
bzw. UK-pairwise-Matching ist B2-Scope."*

### ChannelStats (`backend/app/schemas/insights.py:107-140`)

```
market: str  (l. 109)
```

**Einzige generische Stelle:** `market` ist Free-String — kein Enum.
Wird in `_channel_stats` (`insight_engine.py:1303-1315`) als "DE" |
"US" | "UK" gesetzt. Loop-tauglich.

### LLMReport / CrossMarketInsight (`backend/app/schemas/insights.py:357,443`)

```
cross_market_insight.de_vs_us: str
cross_market_insight.transfer_opportunity: str
```

**`de_vs_us` ist hartcoded** im LLM-Output-Schema. UK steht im
Markdown- und JSON-Annex (l. 2055-2073: 3× `_format_channel_section`-
Aufruf für DE/US/UK), aber nicht im strukturierten Scoring-Feld.

### Generische Helper

`_format_channel_section(market: str, stats, platform: str)`
(`insight_engine.py:1950`) ist bereits markt-agnostisch — nimmt
Market als String-Param. **Brauchbar als Vorbild für Frontend-Pattern
falls Option B gewählt wird.**

### Backwards-Compat (`insight_engine.py:2252-2261`)

Persistierte Briefe werden via `PairAggregation.model_validate(...)`
rehydratiert. Pre-B1-Briefs hydratisieren clean dank Defaults:

- `uk_channel = None` (`PairAggregation`, `PlatformAggregation`)
- `uk_only_titles = []`, `uk_assets_with_title = 0`,
  `uk_assets_total = 0` (`TitleCoverage`)
- `per_platform = []` (Pre-Sprint-4-Briefs)

**UK wurde via "Sprint UK-B1 (2026-05-12)" eingeführt** — kein
Migrations-Pfad nötig, weil Felder additiv mit Defaults.

---

## Frontend-Hardcoding-Karte

Hauptdatei: **`frontend/src/InsightWeekly.jsx`** (1047 Zeilen).
Keine TypeScript-Types/Zod-Schemas — direktes Konsumieren der
Pydantic-Antworten.

### Stellen mit `de_*` / `us_*` Field-Access

| File | Line | Kontext | Edit-Aufwand |
|------|------|---------|--------------|
| `InsightWeekly.jsx` | 233-234 | `DE: {formatNumber(m.de_engagement)}` / `US: ...` (CrossMarketCard) | trivial |
| `InsightWeekly.jsx` | 237-238 | `m.de_post_url && <a>DE-Post</a>` / `m.us_post_url` | trivial |
| `InsightWeekly.jsx` | 775-777 | `<ChannelStatsCard stats={aggregation.de_channel} />` / `us_channel` (Legacy) | trivial |
| `InsightWeekly.jsx` | 787 | `const hasData = p.de_channel \|\| p.us_channel` | trivial |
| `InsightWeekly.jsx` | 795-797 | `{p.de_channel && ...} / {p.us_channel && ...}` (Multi-Plattform) | trivial |
| `InsightWeekly.jsx` | 820 | `const channel = market === 'DE' ? plat.de_channel : plat.us_channel` (ternary) | mittel |
| `InsightWeekly.jsx` | 826 | `const channel = market === 'DE' ? aggregation.de_channel : aggregation.us_channel` (ternary) | mittel |
| `InsightWeekly.jsx` | 904, 912 | `<h4>DE</h4>` / `<h4>US</h4>` (TopRankingSection-Header) | trivial |

**Summe: 8 Code-Stellen, ~15 Field-Accesses.** Davon 2 sind Ternaries,
die bei Option A auf if/elseif aufgebläht würden — der eigentliche
Mehrwert eines markets-Loops wäre genau hier.

### Hardcoded Markt-Strings

| File | Line | Snippet |
|------|------|---------|
| `InsightWeekly.jsx` | 11-17 | `FALLBACK_LABEL = { warnerbros: 'Warner Bros · DE + US', ... }` (alle Pairs) |
| `InsightWeekly.jsx` | 233, 234, 237, 238 | `"DE:"`, `"US:"`, `"DE-Post"`, `"US-Post"` (CrossMarketCard) |
| `InsightWeekly.jsx` | 904, 912 | `<h4>DE</h4>`, `<h4>US</h4>` |

**Bei Option A:** `FALLBACK_LABEL` muss pro Pair angepasst werden
(disney → 'Disney · DE + US + UK'; universalpictures dito; lionsgate
bleibt 'US + UK'). 7 Pairs × ~1 Edit.

### Kein `markets`-Loop vorhanden

Suche nach `[de_channel, us_channel].map(...)` oder
`MARKETS.map(...)`: **negative**. Die Ternaries (l. 820, 826) sind
das einzige Mid-Abstraction-Pattern — aber sie sind 2-Branch-only,
keine Loop-Foundation.

---

## CSS-Realität & Mobile-Responsive

### Aktuelle `.insight-grid-two` (`frontend/src/styles.css:328-332`)

```css
.insight-grid-two {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
```

Verwendet 3× in `InsightWeekly.jsx`: l. 331 (Trends/Actions),
l. 775 (Legacy DE/US Stats), l. 795 (Multi-Plattform Stats).

### Breakpoints (`frontend/src/styles.css`)

| Line | `@media` | Effekt |
|------|----------|--------|
| 284 | `max-width: 980px` | Generischer Tablet-Collapse (hero flex→col, grids 1fr) |
| 333-334 | `max-width: 800px` | **`.insight-grid-two` → 1fr (2→1 col)** |
| 342 | `max-width: 800px` | `.insight-meta` 2→1 |
| 675 | `max-width: 800px` | Ranking-Grid 2→1 |
| 700 | `max-width: 480px` | Schmale Phones (ranked-post-card) |
| 789 | `max-width: 800px` | Help-Tooltip |
| 839 | `max-width: 800px` | Multi-Plattform-Stats |

**Etablierte Brand-Breakpoints: 980px (Tablet-Collapse), 800px
(Mobile-Stack), 480px (schmale Phones).** Keine CSS-Variablen, keine
3-col-Variante existiert.

### Konsequenz bei 3 Spalten

- `.insight-grid-three`-Rule muss neu rein:
  `grid-template-columns: repeat(3, minmax(0, 1fr));`
- Bei `max-width: 980px` (Tablet) → 2 Spalten? oder direkt 1?
  Empfehlung: 980px → 2 col (1fr 1fr), 800px → 1 col (1fr). Das
  spiegelt das vorhandene Stack-Verhalten und macht UK-Spalte
  responsive sauber.
- 14px gap bleibt — bei 3 Spalten in 1200px-max-width-Container sind
  Spalten ~390px breit (statt ~590px), das passt für ChannelStatsCard
  (kein bekanntes min-width-Problem).

**CSS-Edit ist trivial unabhängig von Option A oder B** — gleiche
Rule wird gebraucht.

---

## Aufwand-Schätzung

### Option A — Explizit DE+US+UK hartcoded

| Punkt | Aufwand |
|-------|---------|
| `<ChannelStatsCard stats={aggregation.uk_channel} />` Spiegelung an 3 Stellen (l. 775-777, 795-797) | 15 min |
| `CrossMarketCard` UK-Felder ergänzen — **ABER:** `uk_engagement`, `uk_post_url` existieren im Schema **nicht** (CrossMarketMatch ist DE↔US-only, B2-Scope). Bei A: UK-Block in CrossMarketCard auslassen, Kommentar "B2-Scope" rein. | 10 min |
| Ternaries (l. 820, 826) auf if-elseif aufblähen: `market === 'DE' ? .de_channel : market === 'US' ? .us_channel : .uk_channel` | 10 min |
| `<h4>UK</h4>` neben DE/US (l. 904, 912) | 5 min |
| `FALLBACK_LABEL` pro Pair anpassen (7 Pairs) | 10 min |
| `.insight-grid-three` CSS-Rule + Media-Query | 15 min |
| `hasData`-Check (l. 787) um `\|\| p.uk_channel` erweitern | 5 min |
| Tests (Frontend hat keine, aber visuelle Smoke-Tests am Dev-Server) | 20 min |
| **Total** | **~1.5h** |

### Option B — Generischer markets-Loop

| Punkt | Aufwand |
|-------|---------|
| `MARKETS = ['DE', 'US', 'UK']`-Konstante + Helper `getChannelForMarket(agg, market) => agg[\`${market.toLowerCase()}_channel\`]` | 20 min |
| Refactor aller 8 Code-Stellen auf `MARKETS.map(...)` | 45 min |
| `CrossMarketCard` — UK-Felder fehlen weiterhin im Schema. **Generischer Loop dort macht keinen Sinn, weil das Backend selbst DE↔US-only liefert.** Muss DE↔US-hartcoded bleiben → das Frontend ist "halb-generisch", was eine sinnvolle Code-Architektur unterminiert. | -- |
| Ternary-Refactor (l. 820, 826): `getChannelForMarket(...)` statt `market === 'DE' ? ... : ...` | 15 min |
| Markt-Labels auch dynamisch (`MARKETS.forEach`-Loop für `<h4>`) | 10 min |
| `FALLBACK_LABEL`: bleibt pair-spezifisch (Pairs haben unterschiedliche Markt-Sets — Lionsgate ist US+UK, etc.), also doch nicht trivial dynamisch | 15 min |
| `.insight-grid-three` + dynamisch via `style={{ gridTemplateColumns: \`repeat(${markets.length}, ...) \` }}` oder CSS-Var | 25 min |
| Tests | 20 min |
| **Total** | **~2.5h** |

**Delta:** ~1h Mehraufwand bei Option B, und das Cross-Market-
Argument für Generik fällt weg, weil das Backend dort nicht generisch
ist.

---

## Empfehlung

**Option A (explizit DE+US+UK hartcoded).**

### Gründe

1. **Backend-Frontend-Kohärenz:** Das Backend-Schema ist selbst
   hartcoded mit `de_*`/`us_*`/`uk_*`-Per-Markt-Feldern (außer
   `ChannelStats.market: str`). Ein generisches Frontend gegen ein
   hartcoded-Backend ist eine Abstraktions-Asymmetrie, die in Code-
   Reviews irritiert und bei Bugs Verwirrung stiftet.

2. **Cross-Market-Argument kippt:** Der einzige Stelle, wo ein
   generischer Loop wirklich Wert schaffen würde, ist
   `CrossMarketCard`. Dort ist das Backend aber explizit DE↔US-only
   bis B2. Eine generische Frontend-Schicht hätte dort nichts zu
   loopen — sie würde DE↔US trotzdem hartcoden und der "Generik"-
   Anspruch wäre kosmetisch.

3. **B2 wird die Architektur-Frage ohnehin neu stellen:** UK-pairwise-
   Matching, Triple-Intersection (`titles_in_all_three_markets`),
   evtl. ein generisches `per_market: dict` im
   PairAggregation/TitleCoverage-Schema — das ist B2-Scope. Wenn das
   Backend dort den Sprung zu `per_market` macht, ist der Frontend-
   Refactor in B3 (jetzt) verschwendet, weil das Pattern dann eh neu
   gebaut werden muss.

4. **FR/IT/ES sind nicht in der Roadmap:** Die Backlog-Doks
   (`docs/sprints/`) erwähnen UK explizit, aber keine weiteren
   Märkte. Bauen für hypothetische Anforderungen ist genau das
   Anti-Pattern aus CLAUDE.md ("Don't design for hypothetical future
   requirements").

5. **Aufwand-Risiko:** A ist 15 additive Edits + 1 CSS-Rule. B ist
   ein Refactor von 8 Stellen mit Ternary-Auflösung — Diff-Größe
   ~3-4× so hoch, Review-Aufwand ebenso. Bei einem 1.5h vs 2.5h
   Sprint kein Faktor, aber bei Bug-Surface vermutbar schon.

### Eine kleine Generik trotzdem mitnehmen

Die Ternaries in `InsightWeekly.jsx:820,826` (`market === 'DE' ?
plat.de_channel : plat.us_channel`) sollten zu

```js
const channel = plat[`${market.toLowerCase()}_channel`];
```

generalisiert werden. Das ist 1-Line-Wechsel pro Stelle, kein
Architektur-Refactor, und macht den UK-Fall automatisch tragbar ohne
einen `else if`-Block. Klassischer Pragmatik-Mittelweg.

### Was B3 NICHT macht

- Kein `MARKETS`-Konstanten-Export
- Kein generisches `per_market`-Dict im Backend (B2-Scope)
- Kein FR/IT/ES-Vorbau
- Kein CrossMarketCard-Refactor (DE↔US bleibt bis B2)
- Keine CSS-Variablen-Einführung (Stylesheet hat keine, kein Sprint
  dafür)

---

## Konkrete Code-Stellen für B3-Sprint (falls Option A gewählt)

### Backend

- Kein Edit. UK ist bereits in `PairAggregation`,
  `PlatformAggregation`, `TitleCoverage`, `_format_channel_section`
  ab Sprint UK-B1 vorhanden.

### Frontend — `frontend/src/InsightWeekly.jsx`

| Line | Edit |
|------|------|
| 11-17 | `FALLBACK_LABEL` pro Pair erweitern um " · UK" wo zutreffend (warnerbros/sonypictures/primevideo/disney/netflix/paramountpictures/universalpictures → DE+US+UK; paramountplus → DE+US+UK; lionsgate → US+UK; bestehende Labels prüfen, einige sind schon korrekt) |
| 233-238 | UK-Block in `CrossMarketCard` **NICHT** ergänzen — Schema-Feld fehlt (B2-Scope). Kommentar `{/* UK cross-market = B2-Scope */}` reicht. |
| 775-777 | `<ChannelStatsCard stats={aggregation.uk_channel} />` hinter US einfügen, in `.insight-grid-three` umbenennen |
| 787 | `const hasData = p.de_channel \|\| p.us_channel \|\| p.uk_channel;` |
| 795-797 | UK-Block analog DE/US, Grid auf `-three` umstellen |
| 820 | `const channel = plat[\`${market.toLowerCase()}_channel\`];` (Ternary auflösen) |
| 826 | `const channel = aggregation[\`${market.toLowerCase()}_channel\`];` |
| 904, 912 | UK-Header neben DE/US ergänzen — oder `MARKETS.filter(m => agg[\`${m.toLowerCase()}_channel\`]).map(m => <h4>{m}</h4>)` falls Pragmatik-Generik gewünscht |
| Außerdem: `collectRankedPosts(...)`-Aufrufer prüfen, falls die markets-Liste dort hartcoded ist (l. ~820-826 lokales Scope) | |

### Frontend — `frontend/src/styles.css`

| Line | Edit |
|------|------|
| 328-332 | `.insight-grid-three` neu hinzufügen: `grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px;` |
| 333-334 | Bestehende `max-width: 800px` `.insight-grid-two`-Regel auch auf `.insight-grid-three` anwenden (oder dedupliziert via Multi-Selektor) |
| neu (vor 333) | `@media (max-width: 980px) { .insight-grid-three { grid-template-columns: repeat(2, minmax(0, 1fr)); } }` (Tablet-Collapse 3→2) |

### Smoke-Test nach Deploy

- Dev-Server starten, `disney`-Pair-Brief öffnen → UK-Spalte sichtbar
  mit 4 IG-Pool-Channels (disneyuk + disneystudiosuk + marvel_uk +
  starwarsuk).
- Viewport-Test: 1200px → 3 col, 980px → 2 col, 800px → 1 col.
- `lionsgate`-Pair (kein DE) → DE-Spalte fehlt sauber, US+UK
  side-by-side ohne Lücke.
- `paramountpictures`-Pair YT-Slice (kein DE-YT) → entsprechende
  Spalten-Lücke wird sauber per `hasData`-Check ausgeblendet.

---

## Out-of-Scope (B2-Roadmap)

- UK-pairwise-Cross-Market-Matching (`CrossMarketMatch.uk_engagement` etc.)
- Triple-Intersection (`titles_in_all_three_markets`)
- LLM-Prompt-Schema: `cross_market_insight.de_vs_us` → `de_vs_us_vs_uk`
  oder generisch
- Backend-`PairAggregation` zu `per_market: dict` umbauen
- FR/IT/ES (nicht in der Roadmap)
