# UK-Markt-Integration Backlog

**Status 11.05.2026 EOD**: Phase A komplett, Phase B in 3 Sub-Sprints
aufgeteilt nach Diagnose-Befund.

## Phase A — Channel-Inventar  ✅ DONE 2026-05-11

24 UK-Channels in DB, alle `active=true mvp=true`:

- **10 Instagram** (9 Priority-A + 1 Priority-B-Reclassify `lionsgateuk`
  von `market='INT'` → `market='UK'`)
- **7 TikTok** (5 Priority-A + 2 Priority-B)
- **7 YouTube** (alle Priority-A, komplett neu)

UK-Channels rotieren ab So 17.05.2026 im Apify-Cron mit (`mvp=true`).
Apify wird Posts einsammeln, Daten landen in DB. Brief-Sichtbarkeit
fehlt bis Phase B durch ist.

### Inventar-Lücken (Phase-A-Backlog)

- **Disney TT-UK**: kein offizieller Channel im Apify-Inventar
- **Netflix UK**: komplett fehlend (kein IG/TT/YT-UK angelegt)

Diese werden in einem separaten UK-Recherche-Sprint adressiert
(~30-45 Min, kann parallel zu B-Sub-Sprints laufen).

## Phase B — Brief-Integration  ⏳ PENDING

Diagnose zeigt: DE/US ist auf **7 Schichten verdrahtet**.
Sprint-Brief schätzte 1.5-2h, Diagnose zeigt **10-13h realistisch**.
Aufgeteilt in 3 inkrementelle Sub-Sprints, jeder deploybar, jeder
mit sichtbarem Nutzen.

### Diagnose-Befund-Schichten (Hardcoding-Stellen)

| # | Schicht | Stelle | Hardcoding |
|---|---|---|---|
| 1 | PAIRS-Config | `insight_engine.py:84-317` | offen erweiterbar ✓ |
| 2 | `_aggregate_platform` | `insight_engine.py:1542-1547` | 2-bucket `de_specs`/`us_specs` if/else |
| 3 | `PlatformAggregation`-Schema | `schemas/insights.py:166-181` | benamte Felder `de_channel`/`us_channel` |
| 4 | `CrossMarketMatch`-Schema | `schemas/insights.py:142-152` | 10 Felder `de_*`/`us_*` |
| 5 | `TitleCoverage`-Schema | `schemas/insights.py:155-163` | `de_only_titles`/`us_only_titles` |
| 6 | `asset.de_us_match_key` DB-Spalte | `models/entities.py` | binärer Join-Key |
| 7 | Frontend `InsightWeekly.jsx` | `frontend/src/InsightWeekly.jsx:750-932` | 2-Spalten-Grid + Ternär |

### B1 — Schema + Aggregations-Erweiterung  ⏳ PENDING

Aufwand: **~3-4h**. 1 PR.

Scope:
- `PlatformAggregation.uk_channel: Optional[ChannelStats] = None`
- `TitleCoverage` um `uk_*`-Felder erweitern
- `_aggregate_platform` um 3. Bucket (`de_specs`, `us_specs`, `uk_specs`)
- PAIRS-Einträge für alle 7 Pairs um UK-Channels aus Phase A erweitern
- `test_pairs_registry_schema`-Invariant lockern:
  `markets ⊆ {DE,US,UK}` statt `markets == {DE,US}`
- Brief-Markdown-Section per `_format_channel_section("UK", ...)`
  ausspielen

Wert: Brief-LLM sieht UK-Posts und behandelt sie im Prosa-Output mit.
Frontend rendert sie noch nicht visuell, aber der Brief-Text erwähnt
sie.

Lücken-Behandlung: Pairs ohne UK-Channel (`netflix`, Disney TT-Lücke)
crashen nicht — `Optional[ChannelStats] = None`.

### B2 — Cross-Market 3-Wege-Matching  ⏳ PENDING

Aufwand: **~4-5h**. 1 PR. Voraussetzung: B1 gemerged.

Scope:
- `CrossMarketMatch` von 2-Markt zu Markt-keyed dict refactoren
  ODER zusätzliche Pairs-Variante (de↔uk, us↔uk, de↔us↔uk)
- `asset.de_us_match_key` → neue Spalte `cross_market_match_key`
  (Migration nötig) ODER Reuse mit erweiterter Logik (kein DB-Touch,
  aber Tech-Debt)
- `_cross_market_matches` 3-fach: DE∩US, DE∩UK, US∩UK

Wolf-Confirm in B2 nötig: Migration vs. Reuse von `de_us_match_key`.

Wert: Cross-Market-Sektion im Brief zeigt alle 3 Paarvergleiche.

### B3 — Frontend 3-Spalten-Layout  ⏳ PENDING

Aufwand: **~3-4h**. 1 PR. Voraussetzung: B1+B2 gemerged.

Scope:
- `MultiPlatformStats`, `TopRankingSection`, `CrossMarketCard` auf
  3-Markt-Grid umbauen
- `collectRankedPosts` von Ternär (`market === 'DE' ? ... : ...`)
  auf Loop
- Visual-Regression-Check: alte 2-Markt-Briefe rendern weiterhin
  sauber

Wert: UK voll sichtbar im UI mit eigenen Spalten.

## Alternative Pfade (verworfen)

### Pfad A — Generisches `markets: dict[str, ChannelStats]`

~4-6 Wochen Refactor für N-Markt-Support (FR/IT/ES später trivial).
Persistierte Briefe-Migration nötig. ~8-12 Tests rewrite.

**Verworfen 2026-05-11**: Aufwand zu hoch. B-Split liefert UK-Support
in 10-13h, FR/IT/ES kann später als separater A-Refactor folgen, wenn
nötig.

### Pfad C — Nur PAIRS-Config, kein Backend-Refactor

~1-2h. UK-Channels in PAIRS gelistet, aber keine Aggregation.

**Verworfen 2026-05-11**: Tote Konfig. UK-Daten würden zu Apify-Cost
führen, aber im Brief nirgendwo auftauchen. Nicht akzeptabel.

### Pfad C-prime — UK in US-Bucket merge

~2-3h. UK-Posts würden als pseudo-US im Brief mitlaufen.

**Verworfen 2026-05-11**: Daten irreführend (`US: warnerbros, dc,
warnerbrosuk` als ein Pool). Brief-Insight unzuverlässig.

## Reihenfolge-Empfehlung

1. **B1** zuerst (3-4h, eigener Tag) — Brief-LLM sieht UK
2. **B2** dann (4-5h, eigener Tag) — Cross-Market-Insights für UK
3. **B3** zuletzt (3-4h, eigener Tag) — UK visuell im Frontend

Ggf. parallel: UK-Inventar-Recherche-Sprint für Disney TT-UK +
Netflix UK-Cluster (separater Sprint, ~30-45 Min).

## Skip-/Done-Log

| Datum       | Sprint              | Outcome | Begründung |
|-------------|---------------------|---------|------------|
| 2026-05-11  | Phase A             | DONE    | 24 UK-Channels in DB, mvp=true |
| 2026-05-11  | Phase B (1-Sprint)  | SPLIT   | Hardcoding auf 7 Schichten — Brief-Schätzung 1.5-2h, real 10-13h |
| 2026-05-11  | Pfad A (Generisch)  | SKIPPED | 4-6 Wochen zu hoch, FR/IT/ES später als separater Refactor |
| 2026-05-11  | Pfad C (Stopgap)    | SKIPPED | Tote Konfig, Apify-Cost ohne Brief-Sichtbarkeit |
| 2026-05-11  | Pfad C-prime (Merge)| SKIPPED | UK als pseudo-US wäre irreführend |
