# Sprint 1 — Insight-Engine MVP (warnerbros DE+US)

Tagesbericht und Übergabe-Doku für den Wochenreport-Endpoint.

## Was wurde gebaut

### Backend
- `app/schemas/insights.py` — Pydantic-Modelle (`InsightReport`, `PairAggregation`, `LLMReport`, `ChannelStats`, `CrossMarketMatch`, `TitleCoverage`, `TopPost`, `HashtagFrequency`, …).
- `app/services/insight_engine.py` — deterministische Aggregation + Single-Shot Opus-4.7-Call.
  - Pair-Registry (`PAIRS`) hardcoded für `warnerbros` (DE-Handle `warnerbrosdeutschland`, US-Handle `warnerbros`, Plattform `tiktok`).
  - Hashtag-Extraktion aus `raw_payload.hashtags` (TikTok-Apify-Feld) mit Regex-Fallback auf `caption`.
  - Engagement-Sum = likes + comments + shares + bookmarks (TikTok-Mapping bereits in `apify_connector`).
  - Duration-Buckets `<15s / 15-30s / 30-60s / >60s / unknown`.
  - Cross-Market-Match-Logik über `Asset.de_us_match_key` (Inner-Join auf shared keys, sortiert nach Summe Engagement).
  - Coverage-% = `assets_with_title_id / total_assets`.
  - System-Prompt + Output-Schema als Modul-Konstanten; Codefence-Stripping defensiv eingebaut.
- `app/api/insights.py` — neuer Endpoint `GET /api/insights/weekly?pair=…&window_days=…&dry_run=…`. Bestehender `/overview`-Endpoint bleibt unangetastet.
- `app/tests/test_insight_engine.py` — 12 Tests (Aggregation, Hashtag-Counts, Buckets, Cross-Market-Match, Coverage, Window-Filter, Dry-Run, mock-LLM-Call, Codefence-Stripping, Parse-Failure-Fallback).
- 299 / 299 Backend-Tests grün (ohne die drei Postgres-Migration-Tests, die ohne Live-DB nicht laufen — vor meinen Änderungen identisches Verhalten).

### Frontend
- `frontend/src/InsightWeekly.jsx` — eigenständige React-Komponente: CoverageBanner, ChannelStatsCards (DE + US), CrossMarketCard, LLMOutput, KW-/Cost-Anzeige, Window-Slider, Dry-Run-Toggle.
- `frontend/src/App.jsx` — minimaler URL-Router (`window.location.pathname`-Match) am Ende der Datei. `/insights/weekly/<pair>` rendert die Insight-View, alles andere fällt auf `<App />` zurück. Begründung im Code: ein Route-Switch rechtfertigt keine `react-router`-Dependency; Sprint-2-Upgrade vorgemerkt.
- `frontend/src/api/client.js` — neuer `endpoints.insightsWeekly(pair, { windowDays, dryRun })`-Helper.
- `frontend/src/styles.css` — Insight-spezifische Klassen ergänzt (`.coverage-banner`, `.insight-grid-two`, `.insight-list`, `.insight-channel-card`, `.insight-tldr`, …). Nutzt vorhandene Design-Tokens (`.card`, `.eyebrow`, `.section-kicker`, `.pill`).
- `npm run build` läuft sauber durch (ein Vite-Bundle, 237 KB JS / 12 KB CSS, identische Größenordnung wie vorher).

## Diagnose-Befunde

1. **DB-Schema vollständig**: alle benötigten Felder vorhanden — `Post.caption`, `visible_likes/comments/shares/bookmarks/views`, `duration_seconds`, `published_at`, `detected_at`, `raw_payload (JSONB)`; `Asset.de_us_match_key`, `title_id`, `asset_type`, `placement_title_text`. Kein DB-Migrations-Schritt nötig.
2. **Kein dediziertes Hashtag-Feld**: TikTok-Posts speichern Hashtags entweder in `raw_payload['hashtags']` (Apify-Feld) oder gar nicht. Service liest beide Pfade (raw → Caption-Regex Fallback).
3. **Kein bestehendes Frontend-Routing**: Vanilla React 19 + Vite. Pragmatischer URL-Switch statt React-Router-Dependency.
4. **Anthropic-Wrapper** (`services/anthropic_client.py`) bereits da — bietet `messages_create_text` mit Retry/Backoff. Kein zweiter Wrapper nötig.
5. **Auth**: Endpoint nutzt das bestehende Bearer-Token-Middleware; keine Sonderbehandlung erforderlich.
6. **Channel-Lookup case-insensitive** auf `LOWER(handle)` + `platform="tiktok"` — robuster gegen Casing-Drift in Apify-Slugs.

## LLM-Cost — Schätzung

- Modell-Alias: `claude-opus-4-7`.
- Prompt-Größe (warnerbros, 30d, ~5 Posts pro Markt + 1 Cross-Market-Match): ca. **2-5 k Input-Tokens**.
- Output-Limit: 2000 Tokens (~1,5 k typisch).
- Geschätzt **$0.10-0.30 pro Report** bei aktuellen Public-Rates ($15 input / $75 output je Mtok). Echte Cost wird im Response-Feld `cost_usd_estimate` zurückgegeben.

## Wolf-Ping — wie das Quality-Gate läuft

> **Inline-Quality-Gate vor Frontend-Polish:** Vor dem ersten echten Opus-Call den Endpoint mit `dry_run=true` rufen, JSON inspizieren, dann erst echt rufen.

```
# Dry-Run (zero cost) — Aggregation prüfen
GET /api/insights/weekly?pair=warnerbros&dry_run=true

# Echter Call (~$0.10-0.30)
GET /api/insights/weekly?pair=warnerbros
```

Frontend hat einen Dry-Run-Toggle in der Hero-Leiste — bequemer Pfad für Wolf zur Prompt-Iteration ohne Cost.

## Was ist morgen dran (Sprint 2 Voraussetzungen)

- Real-Run mit Live-DB auf Railway: ein erster echter Report-JSON für warnerbros, Wolf reviewt Quality im Frontend.
- Falls Quality nicht reicht: am `SYSTEM_PROMPT` in `services/insight_engine.py` iterieren — *nicht* am Code (siehe Pre-Commitment 1 im Briefing).
- Falls DE-Channel-Handle anders heißt als `warnerbrosdeutschland`: einmalig in `PAIRS["warnerbros"]["channels"]` korrigieren — der `notes`-Block im Report meldet das automatisch.
- Sprint-2: PDF-Export, Cron-Cache, weitere 6 Tier-A-Pairs nach 0d-Apify-Fix, IG-Coverage.

## Slack-Absatz für Wolf

> Sprint 1 / Insight-Engine MVP ist auf `claude/insight-engine-mvp-warnerbros-mDKMD` deploybar. Backend-Endpoint `/api/insights/weekly?pair=warnerbros` produziert den Pydantic-validierten `InsightReport`, Frontend-Route `/insights/weekly/warnerbros` rendert ihn (Headline → TL;DR → Trends → Actions → Cross-Market → Channel-KPIs → Caveats). Pair-Definition + System-Prompt sind hardcoded für warnerbros DE+US TikTok. **Quality-Gate-Step:** Endpoint mit `?dry_run=true` rufen oder Frontend-Toggle nutzen → Aggregation prüfen → echter Opus-4.7-Call (~$0.10-0.30/Report) → JSON-Quality reviewen → wenn nicht "morgen einem Producer auf den Tisch" reif: Prompt iterieren, nicht Code.

## DoD

- [x] Endpoint `GET /api/insights/weekly?pair=warnerbros` produziert validen `InsightReport`
- [ ] Wolf-Ping zur Quality des ersten Reports beantwortet (Live-DB-Step nach Deployment)
- [x] Frontend-Route `/insights/weekly/warnerbros` rendert den Report
- [ ] Smoke-Test: 3 aufeinanderfolgende Report-Generationen funktionieren (Live-DB-Step)
- [x] PR + Tagesbericht im Repo
- [x] Coverage-Caveat-Banner zeigt korrekt aktuelle title_id-Coverage
