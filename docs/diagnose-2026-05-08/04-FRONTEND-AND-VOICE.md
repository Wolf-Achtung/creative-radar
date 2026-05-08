# 04 — Frontend & aktueller Voice-Stand

> **Hinweis:** Live-Beispiel-Outputs (vollständige Brief-Texte für Netflix +
> ein zweites Studio im Original-Wortlaut) konnten nicht gezogen werden —
> API erreichbar nur mit Bearer-Token + Sandbox-Allowlist. Die
> Render-Sektionen sind aus dem JSX-Code rekonstruiert; die LLM-Voice
> ist aus dem Few-Shot-Beispiel im SYSTEM_PROMPT (`02`-Audit). Sektionen,
> die echte Beispieltexte brauchen, sind als **needs verification** markiert.

## Was wurde geprüft

- `frontend/src/InsightWeekly.jsx` (528 LOC, vollständig).
- `frontend/src/App.jsx` (1026 LOC, Router + Hauptseite + Hero).
- `frontend/src/styles.css` (369 LOC).
- `grep` nach `localStorage`, `sessionStorage`, `cookie`, `@media`.

## Befunde

### Frontend-Repo-Struktur

Vier Source-Files. Sehr flach, kein Routing-Framework, kein State-Manager:

```
frontend/src/
├── App.jsx                # 1026 LOC — Router + Hauptseite + alle Werkzeuge
├── InsightWeekly.jsx      # 528 LOC  — Studio-Brief-Seite
├── styles.css             # 369 LOC  — globale Styles
└── api/
    ├── client.js          # 115 LOC  — Fetch-Wrapper, Endpoint-Map
    ├── imageUrl.js        # 59 LOC   — Image-Proxy-Helfer
    └── imageUrl.test.mjs  # 88 LOC
```

**Routing:** `App.jsx:11-18` matcht `/insights/weekly/<pair>` per Regex und
rendert `<InsightWeekly pair=...>`, sonst Hauptseite (`<App>`). Kein
React-Router. Kein 404-Catch.

### Hauptseite (`App.jsx`)

**Hero** (`App.jsx:899-905`): Trailerhaus-grünes Banner, Eyebrow
"CREATIVE INTELLIGENCE WORKSPACE", h1 "Creative Radar", Untertitel
"Diese Woche neu bei den Majors und Streamern — was funktioniert, was
nicht und wie wir's nutzen."

**Studio-Briefings-Block** (`App.jsx:907-944`): Sechs feste Cards in einem
auto-fit-Grid (`minmax(180px, 1fr)`). Reihenfolge fest verdrahtet:

1. Warner Bros (`/insights/weekly/warnerbros`)
2. Sony Pictures
3. Prime Video
4. Disney
5. Netflix
6. Paramount

Universal Pictures ist **nicht** auf der Hauptseite (im PAIRS aber als
disabled-Placeholder). Konsistenz: Frontend-Liste vs. Backend-PAIRS muss
manuell synchron gehalten werden — laut Kommentar in `InsightWeekly.jsx:6-9`.

**Tabs** (`App.jsx:957-961`): "Report erstellen" / "Treffer prüfen" /
"DE/US Vergleich" / "Quellen". Alle als sekundäre Werkzeuge unter dem
Hero, hinter dem Eyebrow "Weitere Werkzeuge / noch nicht final".

### Studio-Brief-Seite (`InsightWeekly.jsx`)

Header: Eyebrow "STUDIO-REVIEW", h1 mit Pair-Label, Untertitel "Was diese
Woche funktioniert, was nicht und wie wir's nutzen."

**Render-Reihenfolge der Sektionen** (`InsightWeekly.jsx:497-516`):

1. `aggregation.notes` (falls vorhanden) — graue Notes-Card.
2. `LLMOutput`:
   - **Headline-Card** (`output.headline` als h2, `output.tldr` darunter).
   - **"Worum geht's diese Woche"** (Render von `aktuell_im_fokus`,
     Titel verlinkt wenn `post_url` vorhanden, Markt + Format-Typ +
     Release-Datum + Verdict in grauem Kleintext, Kennzahl als
     `insight-evidence`-Block).
   - **"Diese Woche: was funktioniert gut, was nicht"** (`ganz_konkret`-Liste,
     je Eintrag: `bezug`-Tag in Caps, dann `pattern` als Strong, dann
     "Lern-Take: …", dann optional "Frage: …" kursiv).
   - **Trends + Actions** (zweispaltig).
   - **"Cross-Market Insight"** (`de_vs_us` + `transfer_opportunity`).
   - **"Was machen die anderen?"** (`konkurrenz.was_alle_machen,
     format_trend, genre_beobachtung, neu_seit_letzten_wochen`).
   - **"Für den Cutter"** (Schnitt-Pace, Hook-Strategie, Empfohlene Längen,
     Must-Show-Liste, No-Go-Liste).
   - **"Für den Motion-Designer"**.
   - **"Für den Creative Producer"**.
   - **"Tonalität"**.
   - **"Watch-Outs"**.
   - **"Vergleichbare Posts"** (jede Liste mit Handle + KPI + Relevanzgrund;
     `post_id` wird als Link gerendert wenn er mit `http` beginnt).
   - **Risks + Data Caveats** (gemeinsam in einer Card unten).
3. `ChannelStatsCard` × 2 (DE + US, in Grid `insight-grid-two`).
4. `CrossMarketCard` — Match-Liste mit DE-/US-Engagement-Vergleich.
5. Footer mit "← zurück zum Hauptdashboard" + `generated_at`-Zeitstempel.

### Headline / TLDR im Frontend

Beide kommen direkt aus `report.llm_output.headline` und `.tldr`. Kein
zusätzliches Frontend-Mapping, keine Truncation, keine Validierung.

### Plattform-Tags pro Asset

**Werden nicht angezeigt.** Im LLM-Output gibt es keinen Plattform-Tag pro
Asset (alle Pairs sind aktuell TikTok-only, das wäre redundant). Im
`top_posts`-Render (`InsightWeekly.jsx:106-120`) sind `post_url`,
`engagement_sum`, `duration_seconds`, `caption_excerpt` sichtbar — keine
Plattform-Pille. Multi-Plattform V2a wird hier eine `platform`-Pille
einführen müssen.

### Mobile-/Responsive-Stand

`styles.css` hat zwei Media-Queries:

- `@media (max-width: 980px)` (`styles.css:284`)
- `@media (max-width: 800px)` (`styles.css:327, 336`) — `.insight-grid-two`
  und `.insight-meta` werden auf einspaltig gesetzt.

`main { max-width: 1200px; }` zentriert. Hero hat `max-width: 700px` für
den Untertitel. Cards stapeln per Default; das `pair-briefs-grid` ist
inline-styled und auto-fit (kein Media-Query nötig).

→ Für Smartphones (Breakpoints < 480px) gibt es **keinen expliziten
Test/Style**. Sollte auf den meistgenutzten Geräten (iPhone, Pixel)
funktionieren, weil Cards auto-stacken — **needs verification mit echtem
Browser**, `Trust but verify` (vgl. Pre-Commitment 6).

### Beispiel-Briefe — Voice im Few-Shot

Echte Live-Outputs **needs verification**. Der Few-Shot im SYSTEM_PROMPT
liefert die intendierte Voice. Auszug aus `insight_engine.py:378-380`:

> **Headline:** "Warner US läuft mit 22s, DE hängt bei 44s zu lang"
>
> **TLDR:** "Der US-Kanal liegt bei 15.146 Reaktionen im Schnitt, DE bei
> 341 — Faktor 44, das ist nicht nur Marktgröße. Der US-Cut sitzt bei
> 33s mit elf Posts unter 30s, DE liegt bei 44s mit fünf Posts über 30s.
> Wir sollten den DE-Cut auf 22-25s straffen und auf einen klaren
> Cold-Open ziehen, dann kommt mehr Beat in den Feed."

**Voice-Stand**:

- Konkrete Zahlen, keine Engagement-Rates → entspricht der
  Anti-Pattern-Regel.
- Cutter-Vokabular ("der Cut sitzt", "läuft zu lang", "zerläuft im Feed",
  "die Hook hält durch") ist im Prompt explizit erlaubt.
- Trailerhaus-Adressat-Modus (Lern-Modus, v3.0) korrekt im Few-Shot
  umgesetzt: `frage`-Felder formulieren, was Trailerhaus daraus für eigene
  Pitches lernt — keine "schneide den MK2-Cut auf 22s"-Anweisungen.

### Begriffe-Inventar — was würde der GF nicht verstehen?

Aus dem Frontend-Render + Few-Shot extrahiert:

| Begriff | Fundstelle (Frontend-Label) | Erklärungsbedarf |
|---|---|---|
| Coverage / Title-Coverage | `CoverageBanner`, mehrfach | Hoch — was bedeutet "Title-Coverage 60%"? |
| Cross-Market Match | `CrossMarketCard` | Hoch — Aggregations-Begriff |
| `de_us_match_key` | `notes`-Eintrag wenn 0 Matches | Hoch — Tech-Slang |
| Engagement-Sum | Top-Posts-Render | Mittel — sumof reactions |
| Duration-Buckets `<15s/15-30s/...` | `ChannelStatsCard.insight-buckets` | Mittel |
| Längen-Bucket | im Few-Shot, `ganz_konkret` | Mittel |
| Discovery-Cut, Catalog-Mid, Pace-Bruch | im Anti-Pattern verboten | sollte nicht mehr auftauchen |
| Cold-Open, BTS, L3, GSA | im Glossar erlaubt | Niedrig — branchenüblich |
| Hook (deutsch: Anfang/Einstieg) | Glossar erlaubt beides | Niedrig |
| Texted/Textless | Glossar | Mittel — Marketing-Slang |
| Tonalität-Adjektive (cinematisch, sophisticated, mysterious) | Pool im Prompt | Niedrig |
| `aggregation.notes` mit Strings wie "Datenbasis DE schwach: nur X Posts" | sichtbar im Frontend | Mittel — eher Audit-Text |

→ **Voice-Sprint-Empfehlung:** Coverage-/Match-Begriffe brauchen entweder
deutsche Tooltips oder einen Erklär-Banner für nicht-technische Leser.

### User-Sortierung wird gespeichert?

`grep -n "localStorage\|sessionStorage\|cookie"` über `frontend/src/`:
**ein einziger Treffer** (Kommentar in `client.js:47`, kein Code). Die
Filter (`filters` in `App.jsx`) leben nur in React-State — bei Reload
verloren. Falls Wolf für Ranking-/Sortierungs-Persistenz "User-Sortierung
wird gespeichert" geplant hat, muss das Frontend-seitig neu eingebaut
werden (localStorage genügt).

## Inkonsistenzen / Lücken

1. **Universal Pictures als disabled-Placeholder**: Frontend-Hauptseite
   listet ihn nicht (App.jsx:918-924), `FALLBACK_LABEL` in
   `InsightWeekly.jsx:17` aber schon — wer den Pair-Slug direkt aufruft,
   bekommt 503. Konsistent mit dem Konzept "coming soon", verwirrt aber
   wenn jemand den Slug aus einer alten URL aufruft.
2. **Hero-Untertitel / Studio-Briefings-Headline doppeln sich:** Hero
   sagt "Was funktioniert, was nicht und wie wir's nutzen" — und der
   Brief-Header im Studio-Pair sagt fast wortgleich dasselbe. Konsistent,
   aber redundant.
3. **`CoverageBanner` ist im Render-Pfad nicht sichtbar** — die Komponente
   ist definiert (`InsightWeekly.jsx:53-68`), aber der Hauptrenderpfad
   ruft sie nicht. Commit `05d950e` ("ux: CoverageBanner aus Brief
   entfernt — Debug-Info, kein Trailerhaus-Use-Case") bestätigt: bewusst
   ausgebaut, Komponente ist toter Code.
4. **`output.fuer_cutter` vs. SYSTEM_PROMPT `für_cutter`**: Der Prompt
   schreibt Umlaute (`für_cutter`), das Frontend liest aber `fuer_cutter`
   (`InsightWeekly.jsx:290, 293, 296, 299, 321, 322, 336, 351, 358`). Das
   Pydantic-LLMReport-Modell muss zwischen den beiden Schreibweisen
   mappen oder das LLM produziert den ASCII-Key. **needs verification**:
   ist der LLM-Output im JSON tatsächlich `fuer_cutter` oder `für_cutter`?
   Ein Mismatch würde die Sektionen still ausblenden.
5. **`output.tonalitaet`** (`InsightWeekly.jsx:351`) — Prompt sagt
   `tonalität`. Gleicher Punkt wie 4.
6. **Plattform-Tags fehlen pro Asset** — vor Multi-Plattform V2a einplanen.
7. **Keine User-Sortier-Persistenz** — vor Ranking-Sprint einplanen.

## Risiken

- **Der `fuer_cutter`/`tonalitaet`-Key-Mismatch ist potenziell ein
  Production-Bug**, der je nach LLM-Output ganze Sektionen schluckt. Hat
  hohe Priorität, gehört aber zur Voice-Korrektur und nicht in eine eigene
  Sprint-Stelle. Code-only fest verifizierbar wäre eine LLMReport-Schema-Lesung,
  oder ein einzelner echter Brief-Output.
- **Frontend ist sehr breit** (App.jsx 1026 LOC) — jede neue Sektion in
  Studio-Brief erhöht das Risiko von Render-Konflikten. Multi-Plattform
  V2a sollte in `InsightWeekly.jsx` eine eigene `<PlatformBriefSection>`
  einführen, statt die `LLMOutput`-Komponente weiter aufzublähen.
- **Mobile-Stand ist nicht getestet**: ein GF, der Sonntag abend auf dem
  Smartphone reinschaut, könnte ein gestauchtes Layout sehen. Nicht
  blockierend, aber für GF-Briefing-Update relevant.

## Empfehlung für Sprint-Planung

1. **Voice-Sprint** (nach Ranking) muss als allerersten Mini-Schritt den
   `fuer_cutter`/`für_cutter` und `tonalitaet`/`tonalität`-Mismatch klären.
   Entweder Prompt auf ASCII-Keys (sauber für JSON-Parsing) oder Frontend
   auf Umlaut-Keys (passt zur Umlaut-Regel).
2. **Begriffe-Glossar oder Tooltips** als kleinen Frontend-Append: ein
   Aufklapp-Help-Block "Was bedeutet Coverage / Cross-Market Match" am
   Anfang des Studio-Briefs. Kein eigener Sprint, ein Tag Arbeit.
3. **Mobile-Test mit echtem Browser** als Akzeptanz-Kriterium am
   GF-Briefing-Update-Sprint.
4. **`CoverageBanner`-Komponente entfernen** — wenn ohnehin toter Code,
   beim nächsten Voice-Touch löschen (kleine Cleanup-Aufgabe, in dieser
   Diagnose nur dokumentiert, nicht entfernt).
