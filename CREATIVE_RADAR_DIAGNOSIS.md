# Creative Radar — Diagnose-Report

- Stand: 2026-04-30
- Branch: `claude/creative-radar-master-plan-LRKjT`
- Letzter main-Commit: `1514088` (Sprint 8.2e Image-Proxy)
- Phase: 1 + 2 (Diagnose + End-to-End-Audit, read-only)

> **Live-DB-Zugang fehlt.** Keine `DATABASE_URL` im Environment, kein lokaler Dump. Alle DB-Aussagen in diesem Report stammen aus Code-Analyse (SQLModel-Modelle, Migrations-Logik, Tests). Sample-Row-Inspektion ist explizit blockiert und erfordert Wolfs Freigabe (Abschnitt 13).

---

## 1. Executive Summary

Creative Radar ist ein FastAPI-Backend (Railway, Python 3.12) plus React/Vite-Frontend (Netlify) für KI-gestütztes Creative-Monitoring von Film-/Serien-/Game-Marketing-Posts auf Instagram und TikTok. Die Datenbank ist Postgres, geteilt mit `ki-sicherheit.jetzt` (Quelle Wolfs Vorwissen, in der Codebasis nicht referenziert). Externe Quellen: Apify (Instagram + TikTok Scraper), TMDb (Title-Sync), OpenAI (Vision + Text), Perplexity (optional, Marktkontext).

Das System ist seit dem 27. April 2026 sehr aktiv entwickelt worden (86 Commits in vier Tagen, 24 Codex-Sprints, vier feat/sprint-8-2-Branches). Der Code-Stand ist konsistent, getestet auf den jüngsten Pfaden (Selector, Renderer, Image-Proxy, Whitelist-Matcher), und produkttechnisch nahe am MVP-Workflow „Quelle scannen → KI-Analyse → Review → Wochenreport".

**Es gibt jedoch zentrale Schwächen, die die Produkt-Vision aus Briefing Abschnitt 3 aktuell nicht tragen können:**

1. **Datenverlust bei jedem Deploy.** Screenshots werden nach `backend/storage/evidence/` geschrieben (Railway-Container-FS, ephemer). Es existiert kein persistentes Volume und kein S3/R2-Backend. `SECURE_STORAGE_ENABLED` ist per Default `False`, dadurch wird kein Asset jemals als „secure" klassifiziert. Folge: keine Asset-History, keine Cross-Sprint-Vergleiche, der `visual_kinetics`-Reporttyp ist faktisch verkrüppelt.
2. **Geteilte DB mit `ki-sicherheit.jetzt` ohne Schema-Schutz.** SQLModel ruft `create_all()` plus eigene `ALTER TABLE ADD COLUMN`-Migrations auf generischen Tabellennamen (`asset`, `title`, `channel`, `post`) ohne Prefix oder eigenes Schema. Wenn das andere Projekt jemals dieselben Namen verwendet, droht silent column drift oder Migrationskollision.
3. **OpenAI-Vision sieht oft nichts.** `analyze_asset_visual` schickt `image_url` an die Chat-Completion-API. Ist die URL ein interner `/storage/evidence/...`-Pfad, ist sie für OpenAI nicht erreichbar. Externe Instagram/TikTok-CDN-URLs blocken oft auf Referer/Region. Der Code fängt den Fehler ab und liefert dann `text_fallback` zurück — der Nutzer sieht „KI-Analyse" obwohl in Wahrheit nur Caption/OCR ausgewertet wurde.
4. **Stille Buchhaltungsfehler in `insights.py`.** Der Counter `visual_analyzed` zählt `{"analyzed", "text_only"}`. Beide Werte werden im aktuellen Code nirgends geschrieben (gesetzt werden `done`, `text_fallback`, `running`, `pending`, `no_source`, `fetch_failed`, `error`, `provider_error`). Folge: Insights-Dashboard zeigt vermutlich permanent 0 visuell analysierte Assets.
5. **Markdown-Report-Export hängt an Magic-String-Hack.** `WeeklyReport.trend_summary_de` wird zur Doppelnutzung als Container für `[report_type] asset_ids=...` missbraucht (`api/reports.py` Zeile 60–65). Sobald jemand `trend_summary_de` editiert, fällt die `download.md`-Route auseinander.
6. **Keine Authentifizierung, keine Quotas.** Alle Endpunkte sind public. `POST /api/posts/manual-import`, `POST /api/monitor/apify-instagram`, `POST /api/titles/sync/tmdb` können von beliebigen Hosts aufgerufen werden. Datenverschmutzungs- und Kostenrisiko (Apify- und OpenAI-Abrechnung pro Call).
7. **Apify-Scraping juristisch ungeklärt.** Die TikTok-/Instagram-ToS verbieten unautorisiertes Scraping. Apify schiebt das Risiko vertraglich an den Kunden. Dieses Risiko ist in keinem Doc dokumentiert; explizit in Wolfs Briefing als Pre-Commitment markiert.
8. **Stale-Branch-Friedhof.** 17 `codex/*`-Branches und 4 `feat/sprint-8-2*`-Branches existieren auf GitHub als nicht aufgeräumte Sprint-Reste. 4 Issues (`#3`, `#7`, `#10`, `#12`) sind „OPEN", obwohl die zugehörigen Sprints laut Commit-History abgeschlossen sind.

**Was funktioniert gut:**

- Saubere Schicht-Trennung (Modelle / Schemas / Services / API / Jobs).
- Konsistenter Whitelist-Matcher mit klarer Trennung „auto-match" vs. „candidate" und Sicherheitsschwelle `confidence ≥ 0.95`.
- TMDb-Sync mit Idempotenz (tmdb_id-Lookup) und korrektem Backoff über `TitleSyncRun`.
- Image-Proxy (Sprint 8.2e) mit Whitelist, Größencap, Suffix-Match-Tests (`test_proxy.py`).
- Evidence-Quality-Klassifikation (Sprint 8.2b) ist diszipliniert: nur wirklich gesicherte Bilder dürfen `secure` heißen, alle anderen werden korrekt mit Warnings markiert.
- Tests decken die jüngsten Pfade (Selector, Renderer, Whitelist-Matcher) sauber ab.

**Bottom Line.** Die technische Grundstruktur trägt für den MVP, aber für die in Briefing Abschnitt 3 beschriebene Produkt-Vision (Asset-Capture, Märkte-Vergleich, Ranking, Trend-Detection) fehlen drei Fundamente: persistentes Asset-Storage, eine Trennung von der KI-Sicherheit-DB und ein robuster Visual-Analyse-Pfad. Diese drei Themen sind P0 für die Roadmap.

---

## 2. Tech-Stack & Architektur

### Stack-Übersicht

| Layer | Technologie | Version | Quelle |
|-------|-------------|---------|--------|
| Backend Runtime | Python | 3.12-slim | `backend/Dockerfile:1` |
| Web-Framework | FastAPI | 0.115.6 | `backend/requirements.txt` |
| ORM | SQLModel (auf SQLAlchemy) | 0.0.22 | `backend/requirements.txt` |
| Postgres-Treiber | psycopg + psycopg2-binary | 3.2.4 / 2.9.10 | `backend/requirements.txt` |
| HTTP-Client | httpx | 0.28.1 | `backend/requirements.txt` |
| KI-SDK | openai | 1.59.7 | `backend/requirements.txt` |
| Settings | pydantic-settings | 2.7.1 | `backend/requirements.txt` |
| Templating | jinja2 | 3.1.5 | (Import vorhanden, keine Templates im Repo gefunden) |
| Excel-Import | openpyxl | 3.1.5 | `services/channel_importer.py` |
| HTML-Parser | beautifulsoup4 | 4.12.3 | `services/link_preview.py` |
| Frontend Runtime | Vite + React 18 (latest) | latest | `frontend/package.json` |
| Hosting Backend | Railway (Dockerfile) | n/a | `backend/railway.json`, `railway.toml` |
| Hosting Frontend | Netlify | n/a | `netlify.toml`, `frontend/netlify.toml` |
| Datenbank | Railway Postgres (geteilt mit ki-sicherheit.jetzt laut Wolfs Vorwissen) | n/a | nicht im Code referenziert |

`requirements.txt` ist gepinnt, aber kein `requirements.lock` und keine Hashes — bei einem schiefen `pip install` ohne Cache könnten transitive Deps abdriften. `frontend/package.json` setzt `"latest"` für react/vite/react-dom — das ist ein Reproduzierbarkeitsrisiko (siehe Abschnitt 11).

### Komponenten- und Datenfluss-Diagramm

```
                                ┌────────────────────────────┐
                                │   Externe Datenquellen     │
                                │                            │
                                │  TMDb API   Apify (IG+TT)  │
                                │  OpenAI     Perplexity     │
                                │  IG/TikTok CDN (Bilder)    │
                                └─────────────┬──────────────┘
                                              │
                                              │ httpx
                                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                  FastAPI Backend (Railway)                       │
│                                                                  │
│   API-Router                Services                Modelle      │
│  ─────────────             ──────────              ─────────      │
│  health.py                 apify_connector.py      Channel        │
│  channels.py               tmdb_client.py          Title          │
│  titles.py                 title_sync.py           TitleKeyword   │
│  posts.py                  whitelist_matcher.py    Post           │
│  assets.py                 title_candidates.py     Asset          │
│  monitor.py                title_rematch.py        TitleSyncRun   │
│  reports.py                creative_ai.py          TitleCandidate │
│  insights.py               visual_analysis.py      WeeklyReport   │
│  proxy.py (img)            screenshot_capture.py                  │
│                            link_preview.py                        │
│                            channel_importer.py                    │
│                            report_selector.py                     │
│                            report_renderer_v2.py (current)        │
│                            report_generator.py    (legacy v1)     │
│                            insights.py                            │
│                            market_context.py                      │
│                                                                  │
│   StaticFiles: /storage/* → backend/storage/                     │
│       ↳ Pfad ephemer auf Railway, Files vergehen bei Deploy      │
└────┬───────────────────────────────────────────────────┬─────────┘
     │ SQLModel/SQLAlchemy                               │ /api/*
     ▼                                                   │
┌──────────────────────┐                                 │
│ Postgres             │                                 │
│ (Railway Postgres-_  │                                 │
│  HAO, geteilt mit    │                                 │
│  ki-sicherheit.jetzt)│                                 │
└──────────────────────┘                                 │
                                                         ▼
                                          ┌─────────────────────────┐
                                          │  Frontend (Netlify)     │
                                          │  Vite/React-Dashboard   │
                                          │                         │
                                          │  api/client.js          │
                                          │  App.jsx (965 LoC, SPA) │
                                          │                         │
                                          │  /api/* → Railway       │
                                          │  proxyImageUrl() →      │
                                          │   /api/img?url=...      │
                                          └─────────────────────────┘
```

### Deploy-Topologie

- **Backend.** Ein einziger Railway-Service, gebaut aus `backend/Dockerfile`. `start.sh` ruft `uvicorn app.main:app` auf einem `$PORT`. Es existieren **drei** konkurrierende Quellen für den Startbefehl: `Dockerfile` `CMD ["./start.sh"]`, `railway.json` `startCommand` (uvicorn direkt), `railway.toml` `startCommand` (`./start.sh`). In der Praxis hat Railway-JSON Vorrang über TOML, und die deployStartCommand schlägt das Dockerfile-CMD. Funktional identisch, aber Drift-anfällig.
- **Frontend.** Netlify baut `frontend/` mit `npm run build`. Es existieren **zwei** `netlify.toml`: eine im Repo-Root, eine in `frontend/`. Beide sind identisch. Drift-Risiko.
- **API-Routing.** `netlify.toml` setzt `/api/*` als 200-Proxy auf `https://creative-radar-production.up.railway.app/api/:splat`. Diese URL ist hartkodiert. Ein Wechsel der Railway-Domain bricht den Proxy.
- **Storage.** `app.mount("/storage", StaticFiles(directory=storage_path))` mit `storage_path = next(...)`-Fallback-Logik. Auf Railway endet das in `/app/storage` (Container-FS, **nicht persistent**). Sprint-8.2b-Code (`SECURE_STORAGE_ENABLED=False`) trägt diesem Umstand bereits Rechnung.

### Konfiguration

`Settings` in `backend/app/config.py` hält folgende ENV:

- DB: `DATABASE_URL` / `DATABASE_PRIVATE_URL` / `DATABASE_PUBLIC_URL` / `PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD`/`PGDATABASE` / `ALLOW_SQLITE_FALLBACK`
- KI: `OPENAI_API_KEY`, `OPENAI_MODEL` (Default `gpt-4o-mini`), `PERPLEXITY_API_KEY`, `PERPLEXITY_MODEL` (Default `sonar-pro`)
- TMDb: `TMDB_API_KEY`, `TMDB_READ_ACCESS_TOKEN`
- Apify: `APIFY_API_TOKEN`, `APIFY_INSTAGRAM_ACTOR_ID` (Default `apify~instagram-scraper`), `APIFY_TIKTOK_ACTOR_ID` (Default `clockworks~tiktok-scraper`), `APIFY_RESULTS_LIMIT_PER_CHANNEL=5`, `APIFY_WAIT_SECONDS=60`
- CORS: `CORS_ORIGINS=*` (Default), `FRONTEND_URL=*`, `BACKEND_URL=""`
- Image-Proxy: `IMAGE_PROXY_ALLOWED_HOSTS=cdninstagram.com,fbcdn.net,tiktokcdn.com,tiktokcdn-us.com,tiktokcdn-eu.com`, `IMAGE_PROXY_TIMEOUT_SECONDS=8`, `IMAGE_PROXY_MAX_BYTES=8 MiB`
- Storage: `SECURE_STORAGE_ENABLED=False`
- Sonst: `APP_ENV=production`, `APP_NAME=creative-radar`, `REPORT_TIMEZONE=Europe/Berlin`

Es gibt keine `.env.example`-Eintragung für Apify-, TMDb- oder Image-Proxy-Variablen — nur OpenAI/Perplexity sind dort erwähnt. `.env.example` ist asynchron zur tatsächlichen Settings-Klasse (Risiko, neue Mitarbeiter setzen halbleere ENV).

---

## 3. Repo-Inventar

### Top-Level

```
creative-radar/
  backend/                FastAPI service (Python)
  frontend/               Vite + React SPA
  docs/                   4 Markdown docs (Setup, Datenpolitik, MVP-Auswahl, Steps)
  .env.example            unvollständig (siehe oben)
  .gitignore              python+node, ok
  README.md               kurz, MVP-orientiert
  netlify.toml            Root-Variante, identisch zu frontend/netlify.toml
```

### Backend (`backend/app`)

| Bereich | Dateien | Zeilen ca. | Zustand |
|---------|---------|------------|---------|
| API-Router | `api/health.py`, `channels.py`, `titles.py`, `posts.py`, `assets.py`, `monitor.py`, `reports.py`, `insights.py`, `proxy.py` | 9 Dateien, ~600 LoC | aktiv |
| API `__init__.py` | leer (1 Zeile) | 1 | ok |
| Modelle | `models/entities.py` | 240 | aktiv, ein einziger File |
| Schemas | `schemas/dto.py` | 170 | aktiv |
| Services (aktiv) | `apify_connector.py`, `tmdb_client.py`, `title_sync.py`, `title_candidates.py`, `title_rematch.py`, `whitelist_matcher.py`, `creative_ai.py`, `visual_analysis.py`, `screenshot_capture.py`, `link_preview.py`, `channel_importer.py`, `report_selector.py`, `report_renderer_v2.py`, `insights.py`, `market_context.py`, `seeds.py` | 16 Dateien | aktiv |
| Services (legacy) | `report_generator.py` | 107 | nur über `/api/reports/generate-weekly` erreichbar; v2 ist primär. Doppel-Implementierung. |
| Services (placeholder) | `ai_asset_analyzer.py` (`create_placeholder_ai_summary`) | 23 | wird im manuellen Import-Pfad genutzt |
| Jobs | `jobs/collect_posts_job.py`, `analyze_assets_job.py`, `generate_weekly_report_job.py` | je <10 | **Platzhalter, kein realer Cron** |
| Prompts | `prompts/asset_classification_de_v1.md`, `weekly_report_de_v1.md` | 35 / 16 | **werden vom Code nicht geladen.** Inline-Prompts in `creative_ai.py`/`visual_analysis.py` sind die echten. → toter Code-Pfad |
| Daten | `data/channels_seed.json` (201 Zeilen, ca. 28 Channels) | 201 | **wird nicht gelesen.** `seeds.py` benutzt eine hartkodierte 20er-Liste `MVP_CHANNELS`. → toter Datenpfad |
| Tests | `tests/test_proxy.py`, `test_report_renderer_v2.py`, `test_report_selector.py`, `test_title_rematch.py`, `test_whitelist_matcher.py` | 5 Dateien, ~270 Tests-LoC | gut für aktuelle Sprints, low coverage insgesamt |
| Konfig | `Dockerfile`, `start.sh`, `railway.json`, `railway.toml`, `pytest.ini`, `requirements.txt` | — | drei Quellen für startCommand (siehe 2.) |

### Frontend (`frontend/`)

| Datei | Zeilen | Funktion |
|-------|--------|----------|
| `src/App.jsx` | 965 | komplette SPA: Home, Treffer prüfen, Report, Quellen, ImagePreview mit Candidate-Iteration |
| `src/api/client.js` | 113 | API-Wrapper, Endpoint-Map, `proxyImageUrl()` |
| `src/styles.css` | 294 | inline CSS |
| `index.html` | 13 | trivial Vite-Mount |
| `package.json` | 19 | **alle Deps `"latest"`** |
| `netlify.toml` | 14 | Build + API-Proxy + SPA-Fallback |

Es existiert **keine Komponentenstruktur** (keine `components/` Ordner). Alles ist in `App.jsx`. Bei 965 Zeilen wird das Wartung mittelfristig zur Bremse.

### Git-Aktivität

- 86 Commits insgesamt, ältester `5761948` vom 2026-04-27 (Visual placement analysis service), neuester `1514088` vom 2026-04-30 (Sprint 8.2e Image-Proxy).
- 22 Branches auf GitHub: `main`, der aktuelle Claude-Branch, **17 codex/\*** und **4 feat/sprint-8-2\***. Die codex/\*-Branches sind alle gemergt, aber nie gelöscht.
- 4 OPEN-Issues (#3, #7, #10, #12), die aus dem Commit-Verlauf erkennbar abgearbeitet sind, aber nicht geschlossen wurden.
- 0 OPEN PRs.
- Keine GitHub Actions, keine `.github/workflows/`. CI/CD läuft ausschließlich über Railway/Netlify-Auto-Deploys auf Push.

### Tote Pfade (verifiziert)

1. `backend/app/jobs/*` — alle drei Jobs sind Platzhalter, nirgendwo importiert oder als Cron registriert. Railway hat keinen separaten Cron-Service angelegt (nicht im Repo dokumentiert).
2. `backend/app/prompts/*.md` — Inhalt wird nirgends per `open()` oder `Path.read_text()` geladen. Reine Doku.
3. `backend/app/data/channels_seed.json` — keine Referenz im Code. `seeds.MVP_CHANNELS` ist die echte Quelle.
4. `frontend/netlify.toml` und Root-`netlify.toml` sind identisch — eine ist redundant (siehe Issue Liste in Abschnitt 12).

### Build & Deploy-Status

- Letzter Deploy: nicht im Repo nachweisbar (kein Deployments-Log lokal). Aus Commit-Aktivität: vermutlich `1514088` ist live, da Image-Proxy-Endpoint bereits Tests hat.
- `pytest.ini` setzt nur `pythonpath = .` — keine Coverage-Konfiguration, kein Pre-Commit-Hook.
- Frontend `npm run build`: nicht in CI verifiziert; nur Netlify führt den Build aus.

---

## 4. Datenbank-Inventar

> Alle Aussagen aus `app/models/entities.py` und `app/database.py`. **Live-Inspektion nicht möglich** (siehe Disclaimer am Anfang).

### Tabellen (SQLModel)

| Tabelle | PK | Kerne Spalten | Constraints |
|---------|----|---------------|-------------|
| `channel` | `id UUID` | `name`, `platform=instagram`, `url`, `handle`, `market`, `channel_type`, `priority`, `active`, `mvp`, `notes`, timestamps | keine Unique außer PK |
| `title` | `id UUID` | `tmdb_id` (indexed), `title_original`, `title_local`, `franchise`, `content_type=Film`, `market_relevance=MIXED`, `release_date_de`, `release_date_us`, `source=Manual`, `aliases JSON`, `priority`, `active`, `notes`, timestamps | `tmdb_id` indexed, **nicht UNIQUE**. Die Sync-Logik nutzt `tmdb_id` als faktischen Lookup-Key — bei Race-Condition möglich Duplikat. |
| `titlekeyword` | `id UUID` | `title_id FK`, `keyword`, `keyword_type=keyword`, `active` | keine Unique auf (title_id, keyword) — Duplikat-Risiko |
| `post` | `id UUID` | `channel_id FK`, `platform=instagram`, `post_url UNIQUE+INDEX`, `external_id`, `published_at`, `detected_at`, `caption`, `raw_payload JSON`, `visible_likes/comments/views/shares/bookmarks`, `duration_seconds`, `media_type`, `status=new`, timestamps | `post_url` unique. **`external_id` nicht unique** — bei zwei IG-Pfaden für denselben Post (Reel-URL vs. p-URL) entstehen Duplikate. |
| `asset` | `id UUID` | `post_id FK`, `title_id FK?`, `asset_type` (Enum AssetType), `language=Unknown`, `screenshot_url`, `thumbnail_url`, `ocr_text`, `detected_keywords JSON`, `ai_summary_de/en`, `ai_trend_notes`, `confidence_score`, `review_status` (Enum), `curator_note`, `include_in_report`, `is_highlight`, **18 visual_*-Felder** (siehe unten), timestamps | keine Unique. Mehrere Assets pro Post sind möglich (Carousel, mehrere Frames). |
| `titlesyncrun` | `id UUID` | `source=tmdb`, `markets JSON`, `date_from/to`, `fetched_count`, `upserted_count`, `deduped_count`, `status=success`, `error_message`, `created_at` | reine Audit-Tabelle |
| `titlecandidate` | `id UUID` | `asset_id FK INDEX`, `suggested_title`, `suggested_franchise`, `source` (Enum), `confidence`, `status=open` (Enum), timestamps | keine Unique. `resolve_open_candidates_for_asset` löst alle OPEN auf RESOLVED — atomarer Zustand-Wechsel. |
| `weeklyreport` | `id UUID` | `week_start/end`, `generated_at`, `status=draft` (Enum), `executive_summary_de/en`, `trend_summary_de`, `html_url`, `pdf_url`, `html_content`, timestamps | **`trend_summary_de` wird zweckentfremdet** als Magic-String-Container für Report-Type + Asset-IDs (siehe Abschnitt 6 + 7) |

### Visual-Pack-Felder auf `asset` (Sprint 8.2)

`visual_analysis_status`, `visual_source_url`, `visual_notes`, `placement_title_text`, `placement_position`, `placement_strength`, `has_title_placement BOOL`, `has_kinetic BOOL`, `kinetic_type`, `kinetic_text`, `de_us_match_key`, `visual_confidence_score FLOAT`, `visual_evidence_url`, `visual_crop_title_url`, `visual_crop_cta_url`, `visual_crop_kinetic_url`, `visual_evidence_status`, `visual_evidence_pack JSON`.

`database.py` führt diese Spalten in `_ensure_columns("asset", ASSET_COLUMNS)` per `ALTER TABLE ADD COLUMN` ein — eine **eigenbau Migration** ohne Alembic, ohne Versions-Tabelle, ohne Rollback. Auf der geteilten DB ist das ein Hochrisiko-Vorgehen (siehe Abschnitt 11).

### AssetType-Enum-Drift

`AssetType` in Python (`entities.py`) hat 19 Werte (Trailer, Trailer Drop, Teaser, Poster, Key Art, Story, Kinetic, Character Card, Cast Post, Review Quote, CTA Post, Ticket CTA, Release Reminder, Behind the Scenes, Event/Festival, Series Episode Push, Franchise/Brand Post, Discovery, Unknown). `database.py` führt `_ensure_pg_enum_values("assettype", ASSETTYPE_ENUM_VALUES)` aus — d.h. Code stellt sicher, dass das Postgres-Enum alle 19 Werte kennt. Sauber. Aber: Wenn ki-sicherheit.jetzt einen Postgres-Enum mit demselben Namen `assettype` hätte, gäbe es eine Kollision. **Naming-Hygiene-Problem** (siehe Abschnitt 9).

### Indizes

Nur explizit gesetzt:
- `title.tmdb_id` indexed
- `post.post_url` indexed (durch UNIQUE)
- `titlecandidate.asset_id` indexed

**Fehlend für Performance:**
- `post.detected_at` (alle Reports filtern darauf)
- `post.channel_id` (Joins in Reports/Insights)
- `asset.post_id` (impliziter FK-Index, je nach Postgres-Version vorhanden)
- `asset.title_id` (Joins für Vergleiche)
- `asset.review_status` (Filter im Frontend)
- `asset.visual_analysis_status` (Batch-Selector)

Bei wachsendem Datensatz wird das spürbar.

### Geteilte-DB-Risiko

Wolf gibt im Briefing an, dass die DB mit `ki-sicherheit.jetzt` geteilt ist. In der Code-Basis gibt es **keine Spur** von ki-sicherheit-Tabellen oder -Modellen. Daher kann ich nicht beweisen, ob es Schema-Konflikte gibt. Aber:

- Tabellennamen wie `asset`, `title`, `channel`, `post`, `weeklyreport` sind sehr generisch.
- `SQLModel.metadata.create_all(engine)` legt jede fehlende Tabelle stillschweigend an. Bei einer nicht passenden bestehenden Tabelle (z.B. ki-sicherheit hat ein anderes `post`) bricht es nicht — SQLAlchemy ergänzt nur fehlende Tabellen, **nicht fehlende Spalten**.
- `_ensure_columns()` ergänzt fehlende Spalten per `ALTER TABLE`. Das modifiziert eine fremde Tabelle, falls Namensraum geteilt wird. **Hochrisiko.**

**Empfehlung**: Trennung der DBs (P0). Begründung im Detail in Abschnitt 9.

---

## 5. Pipeline-Inventar

### 5.1 Quellen und Ingest-Pfade

| Quelle | Endpoint im Repo | Trigger | Status |
|--------|------------------|---------|--------|
| Manueller Post-Import | `POST /api/posts/manual-import` | UI-Form (nur in MVP-Doku, nicht im aktuellen Frontend) | aktiv |
| Public-Link-Analyse (IG) | `POST /api/posts/analyze-instagram-link` | im Frontend nicht aufgerufen, nur Endpoint-Map | aktiv aber unbenutzt im UI |
| Apify Instagram Scraper | `POST /api/monitor/apify-instagram` | UI-Button „Instagram prüfen" | aktiv (wenn `APIFY_API_TOKEN` gesetzt) |
| Apify TikTok Scraper | `POST /api/monitor/apify-tiktok` | UI-Button „TikTok prüfen" | aktiv |
| TMDb Title-Sync | `POST /api/titles/sync/tmdb` | UI-Button „Titelquellen aktualisieren" | aktiv |
| Excel-Channel-Import | `POST /api/channels/import-excel` | UI-Form (`details`-collapsed) | aktiv |
| Seed-Daten (MVP) | `POST /api/channels/seed-mvp`, `/api/titles/seed-mvp` | nicht im Frontend, nur per direktem Aufruf | aktiv |

**YouTube/Facebook/X**: gar nicht implementiert. Briefing Abschnitt 3 fordert „weitere Plattformen" — derzeit nur IG + TikTok.

### 5.2 Cron / Scheduling

**Kein automatischer Cron.** Die drei `app/jobs/*.py`-Dateien sind tot (siehe 3.). Es gibt:

- keinen Railway-Cron-Service in den Configs
- kein APScheduler/celery
- keinen separaten Worker-Prozess

Alle Pipelines laufen synchron im Request-Lifecycle eines API-Calls. `/api/monitor/apify-instagram` ist eine `async`-Route, die `_run_actor()` aufruft mit `apify_wait_seconds=60` plus 60 Sekunden HTTP-Timeout — d.h. **bis zu 120 Sekunden blockiert** der Browser. Bei Netlify-Proxy-Default 30 Sekunden ist das ein Timeout-Risiko (siehe 11).

### 5.3 Apify-Integration

`services/apify_connector.py`:

- `is_apify_configured()`, `is_tiktok_configured()` prüfen Token + Actor-ID
- `_run_actor()`: POST auf `/v2/acts/{actor_id}/runs?waitForFinish=N`, dann GET der `defaultDatasetId`-Items
- IG-Actor-Default: `apify~instagram-scraper`. Input-Schema: `directUrls`, `resultsLimit`, `resultsType=posts`, `addParentData=true`
- TikTok-Actor-Default: `clockworks~tiktok-scraper`. Versucht zwei Input-Varianten (`profiles`/`usernames`) als Fallback
- `normalize_public_item()` und `normalize_tiktok_item()` extrahieren `post_url`, `caption`, `image_url`, `published_at`, `owner_username`, Engagement-Metriken, `duration_seconds`, `external_id`, plus `raw` (komplettes Apify-Payload)
- `_image_from_item()`: rekursiver Bild-Extractor, prüft 10+ Felder (`displayUrl`, `imageUrl`, `thumbnailUrl`, `coverUrl`, `images`, `childPosts`, `videoMeta`, `authorMeta`)

**Stärken**:
- Idempotenz: `_create_asset_from_item()` skippt vorhandene `post_url` (Status `existing`)
- `raw_payload` wird gespeichert (Replay-fähig)

**Schwächen**:
- **Kosten + ToS-Risiko nicht abgesichert.** Keine Quotas, keine Logging der Apify-Run-Kosten, keine Fehlermeldung bei 402/429. Apify schiebt Scraping-Risiko ans Konto.
- **Keine Retry-Logik.** Wenn Apify einen 5xx oder Timeout liefert, geht der Run verloren ohne Replay.
- **Kein Backfill.** Es gibt keinen Endpoint, um vergangene Posts (>30 Tage) zu importieren.
- **TikTok ohne `external_id` UNIQUE.** Wenn ein Apify-Run einen Post unter `webVideoUrl` und ein anderer unter `shareUrl` liefert, entstehen Duplikate.

### 5.4 TMDb-Integration

`services/tmdb_client.py` + `services/title_sync.py`:

- Auth: entweder `TMDB_API_KEY` (Query-Param `api_key`) oder `TMDB_READ_ACCESS_TOKEN` (Bearer-Header). Sauber.
- `discover_movies(region, language, date_from, date_to)`: `/discover/movie` mit `with_release_type=2|3`, harte Page-Cap auf 3 (`while page <= 3`).
- `sync_titles_from_tmdb`: läuft DE+US default, lookback 8 Wochen, lookahead 24 Wochen. Erzeugt `TitleSyncRun`.
- Lookup: `Title.tmdb_id` als primärer Key, plus Fallback auf `(title_original, release_year)` falls vor TMDb-Sync angelegt.
- Aliases werden als TitleKeyword(`keyword_type="alias"`) angelegt.

**Schwächen**:
- Page-Cap 3 → maximal 60 Filme pro Markt pro Sync. Bei dichtem Release-Kalender kann das Filme verlieren.
- `discover_movies` filtert nicht auf TV/Streaming. Briefing fordert auch Serien/Games — TV-Sync fehlt komplett (`/discover/tv`).
- Keine Abdeckung von Game-Titeln. TMDb hat keine Games-API; das ist eine architektonische Lücke.

### 5.5 KI-Pipelines

#### Text-Analyse (`creative_ai.py:analyze_creative_text`)

- Modell: `settings.openai_model` (Default `gpt-4o-mini`)
- Trigger: Apify-Monitor + Public-Link-Analyse (nicht der manuelle Import — der nutzt `create_placeholder_ai_summary`)
- Input: post_url, channel_name, market, title_name, caption, ocr_text, asset_type_hint
- Output: `asset_type` (Enum), `language`, `ai_summary_de/en` (3/2 Sätze), `ai_trend_notes` (2 Sätze), `confidence_score` (0–1), `review_status=NEW`
- Fallback ohne Key: hartkodierte Stub-Summary, Confidence 0.2

#### Visual-Analyse (`visual_analysis.py:analyze_asset_visual`)

- Modell: `settings.openai_model` (default `gpt-4o-mini`) — **gpt-4o-mini ist multimodal-fähig**, aber nur für **öffentlich erreichbare URLs**.
- Schritt 1: `capture_asset_screenshot()` lädt Bild von `screenshot_url`/`thumbnail_url`/`visual_source_url`, speichert nach `backend/storage/evidence/{uuid}.jpg`. Status `captured` / `no_source` / `fetch_failed`.
- Schritt 2: `image_url = visual_evidence_url or thumbnail_url or screenshot_url` — wenn `evidence_url` ein **interner Pfad** wie `/storage/evidence/xyz.jpg` ist, wird genau dieser an OpenAI geschickt → **OpenAI kann den Pfad nicht abrufen**.
- Schritt 3: OpenAI Vision gibt JSON zurück mit OCR, Title-Placement-Struktur, Kinetics, etc. Bei jeglichem Exception-Fall: Fallback auf `_heuristic_analysis()` mit Status `text_fallback`.
- Schritt 4: `visual_analysis_status` wird gesetzt nach Evidence-Status: `no_source` > `fetch_failed`+`done` → `fetch_failed` > sonst übernehme JSON-Status (default `text_fallback`).

**Stiller Fehler #1.** Wenn das Capture vor dem OpenAI-Call erfolgreich war (`evidence.status="captured"`), wird `image_url` zu `/storage/evidence/...` gesetzt. OpenAI kriegt diese URL und gibt einen Fehler oder leeres JSON. Der `except`-Zweig setzt dann `text_fallback` — der User sieht „nur Textanalyse" obwohl das Bild eigentlich gecaptured war. **Das gecapturte Bild wird nicht für die KI verwendet.**

**Stiller Fehler #2.** `_evidence_quality()` klassifiziert `/storage/evidence/...` mit `SECURE_STORAGE_ENABLED=False` als `source_only` — aber die Datei existiert real auf dem Container-FS, ist nur nicht mountet (kein StaticFiles-Public-Mount? Doch: `app.mount("/storage", StaticFiles(...))` macht es zugänglich, aber Sprint 8.2b deaktiviert die `secure`-Klassifikation absichtlich, weil das Volume ephemer ist). Effekt: das Frontend zeigt das Bild, aber der Selector cappt die Suitability auf „mittel" — eine korrekte und disziplinierte Vorsichtsmaßnahme.

#### Perplexity (`market_context.py`)

- `fetch_weekly_market_context(assets_count, titles)` ruft `https://api.perplexity.ai/chat/completions` mit `sonar-pro`
- Triggert nicht im Code — die Funktion wird **nirgendwo aufgerufen**. Toter Pfad.

### 5.6 Whitelist-Matching

`services/whitelist_matcher.py`:

- `find_best_title_match(session, text, fields)` lädt **alle aktiven Titles** + alle Keywords pro Title (N+1: ein Query pro Title für Keywords).
- Normalisierung: NFKC, casefold, Sonderzeichen → Space, deutsche/engl. Anführungszeichen.
- Hashtag-Splitting: `#MissionImpossible` → `mission impossible`.
- Match-Klassen: `exact`, `exact_local`, `exact_alias`, `hashtag`, `unique_text`, `fuzzy` (SequenceMatcher > 0.72).
- `is_safe_auto_match`: `confidence ≥ 0.95` und Source in den safe-Kategorien.
- Bei mehrdeutigem Match → `source="ambiguous"`, kein auto-match.

**Performance.** Bei 1.000 Assets × 200 Titles und Rematch-Run: ~200 Queries für Keywords (ein N+1 pro Title). Bei wachsender DB wird das spürbar (siehe 11).

### 5.7 Screenshot-Capture

`services/screenshot_capture.py`:

- Kein Headless-Browser, kein Playwright. Nur `httpx.Client.get()` direkt auf die URL.
- Filter: nur `content-type: image/*`, mindestens 1024 Bytes, max 4 Versuche aus den drei Source-Feldern.
- Speichert nach `backend/storage/evidence/{asset_id}_{uuid}.jpg|png|webp|gif`.

**Schwäche**: kein „echter" Screenshot von Posts (Frame-Extraction von Videos, Carousel-Iteration). Ein TikTok-Video hat z.B. nur ein Cover-Image — alles andere geht verloren.

### 5.8 Report-Pipelines

| Endpoint | Service | Modus |
|----------|---------|-------|
| `POST /api/reports/generate-weekly` | `report_generator.py` (v1) | wählt Assets aus DB anhand `Post.detected_at` Range, `include_only_reviewed=True`, generiert HTML inline |
| `POST /api/reports/suggest` | `report_selector.py` | kein DB-Write, liefert Vorschläge mit Score, Tags, Warnings, Suitability |
| `POST /api/reports/generate` | `report_renderer_v2.py` | nimmt vorausgewählte `asset_ids`, generiert HTML mit Report-Type-spezifischer Struktur |

**Doppel-Implementierung.** v1 und v2 koexistieren. v1 wird vom Frontend gar nicht mehr aufgerufen (`endpoints.generateReport` zeigt auf v1, ist aber im UI-Flow nicht mehr verlinkt — der UI-Pfad ist suggest → generate). v1 ist toter Code-Pfad, der noch eine Public-API-Surface bildet.

---

## 6. End-to-End-Datenfluss-Tracing

Vollständiges Tracing der Pipeline **Apify-Instagram-Monitor → Asset → Report-Output**.

### 6.1 Schritt-für-Schritt

**Trigger.** UI-Klick auf „Instagram prüfen" in `App.jsx:runApifyMonitor` mit `max_channels=5, results_limit_per_channel=5, only_whitelist_matches=true`.

**Schritt 1 — `POST /api/monitor/apify-instagram`** (`api/monitor.py:apify_instagram_monitor`).
- Prüft `is_apify_configured()` → 400 wenn Token fehlt.
- Lädt Channels: `Channel.active=True AND mvp=True AND platform="instagram"`, optional gefiltert auf `channel_ids`. Nimmt erste `max_channels`.
- Sammelt `channel_urls = [c.url for c in channels]` und ruft `run_public_channel_monitor()`.

**Schritt 2 — Apify-Aufruf** (`apify_connector.py:_run_actor`).
- POST `https://api.apify.com/v2/acts/{actor_id}/runs?token=X&waitForFinish=60` mit JSON `{directUrls, resultsLimit, resultsType:"posts", addParentData:true}`.
- Wartet bis zu 60 Sekunden auf Run-Completion. HTTP-Timeout auf 120 Sekunden.
- GET `/datasets/{dataset_id}/items?clean=true&format=json` → `list[dict]`.

**Schritt 3 — Normalisierung** (`apify_connector.py:normalize_public_item`).
- Extrahiert `post_url` (mit Fallback `https://www.instagram.com/p/{shortcode}/`), `caption`, `image_url` (rekursive Suche), `published_at` (ISO mit TZ-Aware-Parsing), `owner_username`, Engagement, `duration_seconds`, plus `raw=item`.

**Schritt 4 — Channel-Match** (`monitor.py:_match_channel`).
- Sucht den Channel mit passendem Handle. Fallback: erster Channel der Liste (Index-basiert).

**Schritt 5 — Duplikatcheck** (`monitor.py:_create_asset_from_item`).
- `select Post where post_url = item.post_url` — wenn vorhanden, return `("existing")`.

**Schritt 6 — Whitelist-Match** (`whitelist_matcher.py:find_best_title_match`).
- Caption + ocr_text + detected_keywords + suggested_title werden gegen alle aktiven Titles + Keywords gematcht.
- Wenn `is_safe_auto_match` (confidence ≥ 0.95 in safe source) → Title automatisch zugeordnet.
- Sonst: bei `only_whitelist_matches=True` → return `("no_match")`.

**Schritt 7 — Post + Asset anlegen.**
- `Post(channel_id, post_url, external_id, caption, raw_payload, visible_*, …)` insert+commit.
- KI-Aufruf `analyze_creative_text()` für `asset_type, summary, trend_notes, confidence_score, review_status`.
- `Asset(post_id, title_id, asset_type, screenshot_url=item.image_url, thumbnail_url=item.image_url, …)` insert+commit.

**Schritt 8 — Title-Recovery (wenn nicht gematcht).**
- Erneuter Match-Versuch mit `ai_summary_de/en, visual_notes, placement_title_text, detected_keywords` als zusätzliche Felder.
- Bei Erfolg: Title zuordnen. Sonst: `create_candidate_from_asset` (nur wenn `confidence < 0.95`).

**Schritt 9 — Visual-Analyse (separat triggerable).**
- UI-Klick auf „KI-Bildanalyse starten" → `POST /api/assets/{id}/analyze-visual` oder Batch.
- `screenshot_capture.capture_asset_screenshot()` lädt Image, schreibt nach `/storage/evidence/`.
- `visual_analysis.analyze_asset_visual()` ruft OpenAI Vision mit der URL (siehe Stille Fehler oben).
- Felder: `ocr_text`, `visual_notes`, `placement_*`, `kinetic_*`, `de_us_match_key`, `visual_confidence_score`, `visual_evidence_*`, `visual_evidence_pack`.

**Schritt 10 — Review** (`api/assets.py:update_asset_review`).
- UI-Buttons setzen `review_status`, `include_in_report`, `is_highlight`, optional `title_id`, `curator_note`.

**Schritt 11 — Report-Suggest** (`api/reports.py:suggest_report` → `report_selector.select_assets_for_report`).
- Lädt Assets+Posts+Channels im Datums-Range.
- Filtert nach Channel/Market.
- Berechnet Baseline (Median Engagement-Signal).
- Pro Asset: Score (8 Kriterien), Tags, Warnings.
- Cap-Logik (5 Caps für Suitability).
- Kein DB-Write.

**Schritt 12 — Report-Generate** (`api/reports.py:generate_from_suggestion` → `report_renderer_v2.generate_report_html`).
- Holt Assets, setzt `include_in_report=True`.
- Rendert HTML pro Report-Type (`weekly_overview`, `de_us_comparison`, `visual_kinetics`).
- **Hack**: `trend_summary_de = f"[{report_type}] asset_ids={','.join(...)}"` — ein technischer Identifier wird in einem Inhalts-Feld versteckt.

**Schritt 13 — Markdown-Export** (`api/reports.py:latest_report_download_markdown`).
- Liest `trend_summary_de`, parst die Magic-String wieder zurück, lädt Assets, generiert Markdown.
- **Bricht**, sobald `trend_summary_de` editiert oder regulär befüllt wird.

### 6.2 Wo verlässt Information die Pipeline?

| Punkt | Was geht verloren |
|-------|-------------------|
| Apify→DB | Mehrere Bilder pro Carousel: nur das erste wird als `image_url` extrahiert; die Reste landen nur in `raw_payload` ohne strukturierten Zugriff |
| Apify→DB | TikTok Music (`musicMeta`): wird nur in `raw_payload` als `_creative_radar_music` gehängt, kein eigener DB-Field |
| Apify→DB | Comments-Daten: nicht extrahiert |
| KI-Text→Asset | `language` ist auf 64 chars gecappt — ok, aber nicht konsistent mit dem Modell-Feld (kein Constraint im DB-Schema) |
| OpenAI-Vision | Wenn OpenAI das Bild nicht erreichen kann (interner Pfad oder gesperrtes CDN), erkennt der Code das nicht als Stille Fehlanalyse, sondern macht heuristic Fallback und setzt status `text_fallback` als wäre das eine Designentscheidung |
| Screenshot-Capture | Bei jedem Railway-Deploy verschwinden alle gecapturten Bilder |
| Asset→Report | `visual_evidence_pack` wird befüllt aber nicht im Report angezeigt |
| Report→Markdown | Magic-String-Parsing bricht silent bei Editing; assets-Liste leer ohne Fehlermeldung |

### 6.3 Race Conditions

- `manual_import` und `analyze-instagram-link` machen jeweils zwei aufeinanderfolgende Match-Versuche mit Commits dazwischen. Bei parallelen Requests an dieselbe `post_url` würde der zweite mit `409` wegen Unique-Constraint ablehnen — sauber.
- `_run_actor()` und der nachgelagerte Bulk-Insert sind nicht in einer Transaktion. Bei Crash mitten im Loop verlieren wir alle bis dahin nicht-committeten Items (jeder `_create_asset_from_item` committet selbst) — nur der aktuelle Item-Insert wäre rollback-fähig, frühere bereits committete Inserts bleiben.
- `_ensure_columns` läuft beim App-Startup. Mehrere Replicas parallel könnten dieselbe Migration zweimal versuchen — Postgres-DDL ist pro Statement transaktional, aber `ADD COLUMN` ohne `IF NOT EXISTS` würde bei zweitem Replica werfen. Der Code prüft vorher per `inspector.get_columns(table_name)`. Mehrere Replicas in einer Race können trotzdem beide das `not in existing` true sehen → Crash. Aktuell vermutlich nur ein Replica.

### 6.4 Encoding / Zeitzonen

- `datetime.fromisoformat(str(value).replace("Z", "+00:00"))` macht Apify-Timestamps tz-aware → ok.
- `entities.utc_now()` setzt `datetime.now(timezone.utc)` → tz-aware → ok.
- `report_selector._to_datetime_bounds(date_from, date_to)` macht `datetime.combine(date, time.min)` → **naiv**. Vergleich `Post.detected_at >= start` mit naivem `start` und tz-aware-Spalte ist Postgres-seitig riskant: Postgres rechnet UTC-Annahme, aber die DB-Spalten haben in SQLAlchemy keine `tzinfo`-Annotation. SQLModel mappt `datetime` ohne tz auf `TIMESTAMP WITHOUT TIME ZONE` — das Vergleichsfeld matcht textuell. **Funktional unauffällig, aber fragil**.
- Unicode-Titles werden in `_normalize_text()` per NFKC behandelt. Korrekt.
- Emojis im OCR-Text gehen unverändert durch und landen im JSON-Feld — saubere Behandlung.

---

## 7. Mapping-Audit-Tabelle

> Format wie im Briefing Abschnitt 6 vorgegeben. Die Tabelle ist nicht erschöpfend, deckt aber alle Datenströme der heutigen MVP-Pipeline ab.

| Feld/Datenstrom | Quelle | In DB? | Im API-Layer? | Im Frontend? | Vollständig? | Befund | Ursache | Risiko | Prio |
|---|---|---|---|---|---|---|---|---|---|
| Post `external_id` | Apify (TikTok `id`/`videoId`, IG aus shortCode-Parse) | ja (`post.external_id`) | nein (Endpoints liefern es nicht aktiv aus, nur via volles Post-Objekt) | nein | teilweise | ID gespeichert, aber kein Index, nicht UNIQUE | Migration vergessen | Duplikate bei URL-Form-Wechsel | P1 |
| Post `raw_payload` | Apify | ja (JSON) | ja (volles Post-Objekt) | nein (Frontend zeigt es nicht) | ja | Replay-fähig, aber DB wächst stark | by design | DB-Größe | P2 |
| Post `visible_shares`, `visible_bookmarks`, `duration_seconds` | Apify | ja (per `_ensure_columns`) | ja | ja (`MetricStrip`) | ja | sauber | — | — | — |
| Asset `screenshot_url` | Apify `image_url` | ja | ja | ja | meist | externe IG/TT-CDN-URLs sind kurzlebig | externe Quelle | Bild lädt nicht mehr | P1 |
| Asset `thumbnail_url` | Apify `image_url` (gleich wie screenshot_url!) | ja | ja | ja | redundant | beide Felder bekommen denselben Wert | doppelte Zuweisung in `_create_asset_from_item` | unnötiger Speicher, Verwirrung | P2 |
| Asset `visual_evidence_url` | `screenshot_capture` lokal | ja | ja | nein (Frontend zeigt es als Bildquelle, aber Pfad ist auf Storage relativ) | bedingt | bei Deploy weg | kein persistentes Volume | massive Datenverlust | **P0** |
| Asset `visual_evidence_pack` | `analyze_asset_visual` | ja (JSON) | ja | nein | nein | Pack befüllt, aber nirgends ausgewertet | Feature halb-fertig | Doppelarbeit | P2 |
| Asset `visual_crop_title_url`, `visual_crop_cta_url`, `visual_crop_kinetic_url` | nirgends gesetzt | ja (Spalten existieren) | ja | nein | nein | Spalten leer | Feature nie implementiert | toter DB-Speicher | P2 |
| Asset `ocr_text` | OpenAI Vision JSON | ja | ja | ja | bedingt | wenn OpenAI das Bild nicht erreicht: leer | Visual-Pfad bricht | Stille Datenlücke | **P0** |
| Asset `de_us_match_key` | OpenAI Vision JSON oder `_slug(asset.placement_title_text)` | ja | ja | ja (`insights.placement_comparison`) | bedingt | Slug-Erzeugung kann je Asset variieren (nicht-deterministisch wenn KI mal anders spuckt) | KI-nicht-determ. | False-negative bei DE/US-Pairing | P1 |
| Asset `visual_analysis_status` | `visual_analysis.py` setzt einen von 7 Werten | ja | ja | ja (Status-Label) | ja | aber `insights.visual_analyzed` filtert auf `{analyzed, text_only}` — beide existieren nicht | Drift zwischen Status-Definition und Counter | Insights-Counter bleibt 0 | **P0** |
| Asset `confidence_score` vs. `visual_confidence_score` | zwei separate Felder, der erste aus Text-KI, der zweite aus Visual-KI | ja | ja | nur visual sichtbar | mittel | unklar wer welches anzeigt | semantische Doppelung | Verwirrung | P2 |
| Title `aliases` | TMDb `title` + `original_title`, plus manueller `aliases` | ja (JSON) | ja | nein (UI zeigt nur Title) | ja | TMDb-Sync schreibt Aliases auch als TitleKeyword(`alias`) → Doppelhaltung | doppelte Spur | inkonsistent updatebar | P2 |
| TitleKeyword `keyword_type` | `keyword`, `alias` aus Sync, kein anderer Wert benutzt | ja | ja | nein | ja | Wertraum unklar definiert | kein Enum | Drift | P2 |
| WeeklyReport `trend_summary_de` | a) v1: echte Trend-Summary, b) v2: `[type] asset_ids=...` Magic-String | ja | ja | inkonsistent (UI zeigt Inhaltstext, der dann der Magic-String ist) | nein | Field zweckentfremdet | Hack zur Daten-Persistenz für Markdown-Export | bricht bei jeder UI-Änderung | **P0** |
| WeeklyReport `html_url`, `pdf_url` | nirgends gesetzt | ja | ja | nein | nein | tote Spalten | Feature nie gebaut | kein direkter Schaden | P2 |
| Channel `notes` | manuell oder Auto-Import | ja | ja | nein | ja | Feld nicht im UI | Feature ausgeblendet | Datenverlust bei Auto-Import nicht reproduzierbar | P2 |
| Channel `mvp` | seed setzt `True`, Excel-Import auch `True` | ja | ja | nein | ja | Filter `mvp=True` ist die einzige Quelle für Apify-Selektion. Wenn Excel-Import alle als `mvp=True` flagged → Apify monitort alles | by design | unkontrollierte Quotas | P1 |
| Channel handle `auto_import_instagram` | Auto-Anlage in `_get_or_create_auto_channel` | ja | ja | ja | ja | Magic-Handle, kein Schutz vor Kollision | by design | Verwirrung | P2 |
| TitleSyncRun `error_message` | bei Sync-Failure | ja | ja (`/api/titles/sync/runs`) | nein | ja | UI zeigt nur das Datum | low-prio Diagnostik fehlt | Logs statt UI | P2 |
| Insights `visual_status_breakdown` | `insights.py` Counter | nein (computed) | ja | nein (UI nutzt Endpoint nicht) | nein | gesamter `/api/insights/overview` wird vom UI nicht aufgerufen | toter API-Endpoint | Wartungsschuld | P1 |
| Comments-Stream | nicht extrahiert | nein | nein | nein | nein | Sentiment-Mining fehlt | nicht implementiert | Feature-Lücke | P2 |
| Audio/Music (TikTok) | nur in `raw_payload._creative_radar_music` | teilweise | nein (kein Endpoint) | nein | nein | Feature-Lücke | nicht implementiert | Feature-Lücke | P2 |
| Carousel-Bilder (IG) | im `raw_payload`, aber nur das erste extrahiert | teilweise | nein | nein | nein | Verlust an Kreativ-Variation | by design | Datenverlust | P1 |
| Image-Proxy-Hostlist | hartkodiert in `config.py` und `api/client.js` | n/a | n/a | n/a | nein | zwei Wahrheits-Quellen | kein Sync-Mechanismus | Drift bei Erweiterung | P1 |

---

## 8. Frontend-Status

### Stack & Build

- Vite + React 18, single-page-application
- alle Deps `"latest"` in `package.json` — der nächste Netlify-Build kann inkompatible Major-Versionen ziehen
- kein Linting, kein TS, kein Storybook, keine Komponenten-Tests
- Bundle: nicht analysiert (kein `vite-bundle-analyzer`)

### Komponenten-Struktur

`App.jsx` (965 LoC) enthält **alles**:

- Top-Level App mit State + Effects (~250 Zeilen)
- `HomePanel`, `ReviewPanel`, `ComparisonPanel`, `ReportsPanel`, `SourcesPanel`
- Sub-Components: `Section`, `TodoCard`, `ImportantFinds`, `ReportStatus`, `ReviewGuide`, `AssetCard`, `MetricStrip`, `ImagePreview`
- Helper: `formatNumber`, `formatDate`, `clip`, `normalizeHandle`, `textPool`, `titleFromHashtag`, `inferTitleHint`, `getAssetDisplayTitle`

Keine Aufteilung in `components/`, kein Routing-Library — Tab-Wechsel über `useState`. Für ein MVP akzeptabel, mittelfristig Wartungsbremse.

### Datenfluss im Frontend

- `endpoints.*` ruft das Backend an, Antworten landen direkt im React-State
- `proxyImageUrl(url)` umhüllt CDN-URLs mit `${API_BASE}/api/img?url=...`
- `ImagePreview` erhält `sources=[]` (Array von Kandidaten); iteriert per `onError`-Handler. Sehr robuste Komponente, neu in Sprint 8.2d.

### Auth / Session

**Keine.** Die App lädt sofort beim Mount alle Daten via `Promise.all`. Jeder, der die Netlify-URL kennt, hat vollen Zugriff.

### UX-Lücken (im Code sichtbar)

- `endpoints.analyzeInstagramLink` existiert in `client.js`, ist aber im UI nicht verlinkt. Toter Endpoint-Aufruf.
- `endpoints.insightsOverview` ebenso nicht aufgerufen (`/api/insights/overview` ist tot).
- `endpoints.manualImport` ebenso nicht im UI angeboten — der README-Workflow „Manueller Treffer-Import" ist nicht mehr im Frontend.
- `endpoints.generateReport` (= v1 `/api/reports/generate-weekly`) ebenso nicht im UI.
- Drei tote Endpunkte → API-Surface ist größer als nötig.

### Comparison-Panel

`ComparisonPanel` (Frontend) nutzt eine **eigene** Group-by-Title-Logik, die unabhängig von `report_renderer_v2._pair_group_key` ist. Beide gruppieren ähnlich, aber der Renderer hat 4 Fallback-Felder, das Frontend nur 2 (`title_name || placement_title_text`). **Drift-Risiko**: Frontend zeigt eine andere Zuordnung als der gerenderte Report.

### Integration mit `ki-sicherheit.jetzt`

**Im Repo nichts gefunden.** Keine geteilten Auth-Header, keine Cross-Domain-Cookies, keine gemeinsame Komponenten-Library, kein Cross-Origin-iFrame. Wenn es Integrationen gibt, sind sie über die geteilte DB (Postgres) und ggf. eine externe Plattform (Wolfs Vorwissen). Code-seitig: **kein Berührungspunkt**.

---

## 9. Integrationen mit ki-sicherheit.jetzt

### Was im Code ist

`config.py` und alle Service-Dateien zeigen keinen Hinweis auf ki-sicherheit.jetzt — keine ENV-Variable, kein gemeinsames Auth-Modul, kein Hostname, kein Modul-Import.

### Was Wolf laut Briefing weiß

- DB ist geteilt (`Postgres-_HAO`)
- Tabellen wie `asset`, `title`, `channel`, `post` sind angelegt — die existieren in beiden Codebasen, sehr wahrscheinlich mit unterschiedlicher Bedeutung

### Risikoanalyse

| Risiko | Eintritt | Auswirkung |
|--------|---------|------------|
| Tabellen-Namenskollision (z.B. ki-sicherheit hat ein `asset` mit anderer Schemastruktur) | **hoch** | Stilles Schreiben in fremde Tabelle, Datenverlust oder Fremd-Daten in unserer App |
| Postgres-Enum-Konflikt (z.B. `assettype`, `market`) | mittel | Migration bricht beim nächsten Deploy |
| Connection-Pool-Konkurrenz | mittel | Eine App kann der anderen Connections wegnehmen — Railway Postgres hat begrenztes max_connections |
| Backup-Komplexität | hoch | pg_dump erfasst beide Schemas; Restore betrifft beide Apps. Test-Restores sind riskant |
| Migration-Ownership unklar | hoch | Wenn ki-sicherheit `ALTER TABLE asset ...` macht, kann das unsere Spalten beeinflussen |
| DSGVO / Auditlog | mittel | Beide Apps loggen unterschiedlich; eine zentrale Datenschutzanfrage muss beide beachten |

### Empfehlung

**Trennen, mit klarer Migration:**

1. **Kurzfristig (P0, vor jeder weiteren Schemaänderung):** Eigenes Postgres-Schema `creative_radar` (`SET search_path TO creative_radar, public`) oder neues Postgres-Datenbank-Service auf Railway.
2. **Mittelfristig:** Alembic einführen, alle bestehenden `_ensure_columns`-Migrations zu Alembic-Revisions konvertieren, Rollback-fähig machen.
3. **Falls Wolf bewusst zusammen halten will:** Tabellen renamen mit `creative_radar_`-Prefix (`creative_radar_asset`, `creative_radar_title`, …) — minimal-invasiv, keine echte Trennung, aber Namensraum sauber.

**Begründung pro Trennung**: Die geteilte DB ist eine technische Schuld, die mit jeder weiteren Spalte tiefer wird. Trennung ist heute günstig (keine Live-Daten in größeren Mengen vermutlich), morgen teuer.

**Begründung kontra Trennung**: ein zusätzlicher Railway-Service kostet, ein zusätzlicher Backup-Job auch. Wenn ki-sicherheit.jetzt von Creative-Radar-Daten profitiert (gemeinsames Reporting?), bricht die Trennung diese Möglichkeit. Das müsste Wolf beantworten.

→ **Offene Frage 9.1 in Abschnitt 13.**

---

## 10. Lücken-Matrix gegen Produkt-Vision (Briefing Abschnitt 3)

| Vision-Anforderung | Was existiert | Was fehlt | Lücken-Score (0-3) |
|---|---|---|---|
| **Asset-Capture: Screenshots aller Filmtitel + Kinetics** | `screenshot_capture` lädt Erstes-Bild pro Asset | Kein Frame-Sampling von Videos, keine Carousel-Iteration, kein persistentes Storage | **3** (kritisch) |
| Asset-Capture inkl. Quell-Link, Kanal, Datum, Engagement, Region | Alles im `Post` und `Channel` | — | 0 (komplett) |
| **Märkte-Vergleich DE vs US — Visualisierung Cut/Tonalität/Frequenz/Reihenfolge/Story-Beats** | `de_us_comparison`-Reportmodus mit Pair-Group-Logik, Frontend `ComparisonPanel` | Keine Cut-/Schnittfrequenz-Analyse (Video erforderlich), keine Tonalitäts-Analyse (Audio erforderlich), keine Reihenfolge der Story-Beats | **3** (Kern fehlt) |
| **Ranking: Highlights + Rankings der meistgeklickten/-engagierten Assets total und je Kanal/Markt/Zeitraum** | `insights.build_overview` mit Score-Berechnung und Ranking | UI zeigt das gar nicht. Endpoint ist im Frontend nicht verdrahtet | **2** (Backend ja, Frontend nein) |
| Trend-Detection (Cut-Patterns, Hooks, Sound-Layer, Schnittfrequenzen) | KI-Analyse pro Asset gibt `ai_trend_notes`, `kinetic_type` | Keine Aggregation über Zeit, keine Wochen-Delta, keine Sound-Analyse | **2** (Halb) |
| Title-Tracking-Dashboard pro Filmtitel | Daten im Backend (Title-FK auf Assets) | UI nicht vorhanden | **2** (Daten ja, UI nein) |
| Creative-Briefing-Generator | Perplexity-Funktion da, aber tote | — | **3** (nicht aktiv) |
| Competitor-View (Studio-Performance) | `insights.channel_rankings` Backend-only | UI fehlt | **2** |
| Genre-Cluster | Genre nicht im Datenmodell | komplett | **3** |
| Audio-/Music-Analyse | TikTok-Music in `raw_payload` | Keine strukturierte Speicherung, keine Aggregation | **3** |
| Sentiment / Comment-Mining | nicht extrahiert | komplett | **3** |
| Alerts (Push wenn viral) | komplett | komplett | **3** |
| API/Export (CSV/JSON/Webhook) | `download.html`, `download.md` für Reports | kein Asset-Export, kein Webhook | **2** |

**Lücken-Total**: von 13 Vision-Komponenten sind **0 vollständig**, **2 halb**, **5 großteils fehlend**, **6 fehlen ganz**.

---

## 11. Risiken (Tech, Recht, Kosten, Skalierung, Compliance)

| # | Risiko | Eintrittswahrsch. | Auswirkung | Mitigation |
|---|--------|--------------------|-----------|------------|
| R1 | **Datenverlust beim Railway-Deploy** (ephemerer Storage für Evidence-Bilder) | sehr hoch (jeder Deploy) | hoch | S3/R2 oder Railway-Volume + Storage-Service-Refactor (P0) |
| R2 | **DB-Schema-Kollision mit ki-sicherheit.jetzt** auf generischen Tabellennamen | mittel | sehr hoch | Eigenes Schema oder Trennung (P0) |
| R3 | **Apify TikTok/IG Scraping verstößt gegen ToS** — juristisch | mittel | sehr hoch (Klagerisiko, App-Sperre) | Vertragslage prüfen (Apify EULA + IG/TT-ToS), defensive Logging, Rate-Limit, Opt-out auf Anfrage |
| R4 | **OpenAI Vision liefert oft text_fallback statt Bildanalyse** weil URL nicht erreichbar | hoch | hoch | Bilder zwingend an einen öffentlichen Storage hochladen vor Vision-Call, dann diese URL übergeben (P0) |
| R5 | **Insights `visual_analyzed`-Counter ist immer 0** wegen Status-Drift `text_only` vs. `text_fallback` | sehr hoch | mittel | Counter-Logik anpassen, Tests (P1, Quick-Win) |
| R6 | **Markdown-Report-Export bricht bei trend_summary_de-Edit** — Magic-String-Hack | mittel | mittel | Eigenes JSONB-Feld `report_meta` einführen, Migration (P1) |
| R7 | **Keine Auth — beliebige Kostenexplosion** (Apify, OpenAI) durch öffentlich aufrufbare Endpunkte | hoch | sehr hoch | Token-Auth (Bearer) oder Netlify Identity, Quoten pro IP (P0) |
| R8 | **Frontend-Deps `latest`** — nächster Netlify-Build kann brechen | mittel | mittel | Pin-Versions, Lockfile committen (P1, Quick-Win) |
| R9 | **Drei start-Command-Quellen (Dockerfile, start.sh, railway.json/toml)** | niedrig | mittel | Auf eine Quelle reduzieren (P2, Quick-Win) |
| R10 | **Apify-Calls blockieren API-Worker bis 120s** — Netlify-Proxy-Timeout 30s | hoch | mittel | Background-Job + Polling-Endpoint (P1) |
| R11 | **N+1 in `find_best_title_match`** — Skalierungsproblem ab ~500 Titles | mittel | mittel | Eager-Load Keywords mit `selectinload` (P1) |
| R12 | **Externe Bild-URLs werden im UI gerendert ohne Proxy bei `find-card`-Pfad** in `ImportantFinds` und Asset-Karten — die Karten rufen `proxyImageUrl` schon auf, aber andere Pfade möglicherweise nicht (Audit nötig) | mittel | niedrig | E2E-Test, der prüft, dass kein direkter cdninstagram.com-Link im DOM ist (P1) |
| R13 | **`text_only` vs `text_fallback` ist nur ein Beispiel von Drift** zwischen Code-Bereichen | hoch | mittel | Status-Werte als Enum, Konstanten zentral (P1) |
| R14 | **Apify-Run-Kosten unbekannt** — kein Counter, keine Limits | hoch | mittel | Abrechnungs-Endpoint von Apify pollen, Cost-Logging (P1) |
| R15 | **Keine Backup-Strategie dokumentiert** für die geteilte DB | hoch | sehr hoch (bei Tabellenkollision) | Wolf-Frage 9 + dokumentierter Backup-Plan (P0) |
| R16 | **Channel-Excel-Import setzt `mvp=True` für alle** | mittel | mittel | Default `mvp=False`, manuell selektieren (P2) |
| R17 | **Kein Audit-Log** — Änderungen am Asset (Curator-Notes, Review-Status) nicht historisiert | mittel | mittel | Append-Log-Tabelle, Trigger oder Service-Layer (P2) |
| R18 | **Compliance / DSGVO**: Caption-Texte und User-Handles gespeichert ohne Löschkonzept | mittel | hoch | Datenschutz-Konzept, Löschroutine, Aufbewahrungsfrist (P1) |

---

## 12. Sofort-Handlungsempfehlungen (Quick Wins, Hygiene, Security)

### Quick Wins (geringes Risiko, hoher Nutzen)

| # | Maßnahme | Aufwand | Wirkung |
|---|----------|---------|---------|
| QW-1 | **Insights-Counter-Bug fixen**: in `services/insights.py:117` `{"analyzed", "text_only"}` durch `{"done", "text_fallback"}` ersetzen, plus Tests | 30 min | KPI-Dashboard wird wieder belastbar |
| QW-2 | **Frontend-Deps pinnen**: `react`, `vite`, `@vitejs/plugin-react`, `react-dom` auf konkrete Major.Minor.Patch + `package-lock.json` committen | 30 min | Build-Reproduzierbarkeit |
| QW-3 | **Doppel-`netlify.toml` entfernen**: nur eines (Repo-Root) behalten, Netlify-Doku referenzieren | 10 min | Konfigurations-Hygiene |
| QW-4 | **Drei start-Command-Quellen reduzieren**: nur `Dockerfile CMD ["./start.sh"]` behalten, `railway.json`/`railway.toml` startCommand entfernen | 10 min | Eindeutigkeit |
| QW-5 | **Stale Branches schließen**: 17 codex/* + 4 feat/sprint-8-2* nach Bestätigung gemergt sind, löschen | 15 min | Branch-Friedhof aufräumen |
| QW-6 | **Issues #3, #7, #10, #12 schließen** mit Hinweis auf den jeweiligen Merge-Commit | 10 min | Issue-Hygiene |
| QW-7 | **Tote Dateien löschen**: `app/data/channels_seed.json`, `app/prompts/*.md` (oder im Code laden), `app/jobs/*.py` (Platzhalter) | 30 min | Tote Pfade weg |
| QW-8 | **`.env.example` aktualisieren**: Apify/TMDb/Image-Proxy-Variablen ergänzen | 10 min | Onboarding |
| QW-9 | **Fehlende Indizes**: Migration für `post.detected_at`, `post.channel_id`, `asset.title_id`, `asset.review_status`, `asset.visual_analysis_status` | 1 h | Performance |
| QW-10 | **`thumbnail_url` und `screenshot_url` Doppel-Zuweisung entwirren**: nur einer wird gesetzt; `thumbnail_url` aus echten Thumbs (z.B. Video-Cover bei TikTok) | 1 h | Datenklarheit |
| QW-11 | **Inkonsistente Default `only_whitelist_matches` zwischen IG (True) und TikTok (False)** klären (vermutlich beide auf True für Kosten-Schutz) | 10 min | Kosten-Schutz |

### Hygiene

| # | Maßnahme | Aufwand |
|---|----------|---------|
| H-1 | Linter (ruff/black) im Backend einführen, CI-Hook | 2 h |
| H-2 | ESLint + Prettier im Frontend einführen | 2 h |
| H-3 | Coverage-Report (`pytest --cov`) als CI-Gate | 1 h |
| H-4 | Issue-Templates für Sprint-Briefings | 30 min |
| H-5 | `report_generator.py` (v1) entfernen, `/api/reports/generate-weekly` deprecaten und Frontend-Endpunkt löschen | 1 h |

### Security (Pflicht, P0)

| # | Maßnahme | Begründung |
|---|----------|-----------|
| S-1 | **Auth-Layer einführen**: einfachster Weg ist Bearer-Token aus ENV gegen alle `POST/PATCH/DELETE`-Endpunkte. Frontend liest `import.meta.env.VITE_API_TOKEN` | Schutz vor Kostenexplosion (Apify, OpenAI) und Datenverschmutzung |
| S-2 | **CORS auf Netlify-Domain einschränken** (`CORS_ORIGINS`) | Standard-Härtung |
| S-3 | **Rate-Limiting** auf `/api/monitor/*` und `/api/titles/sync/*` (z.B. SlowAPI) | Kostenschutz |
| S-4 | **Apify ToS-Klärung dokumentieren** als `docs/legal_review.md` mit Stand und nächster Prüfung | Compliance, im Briefing als Pre-Commitment markiert |
| S-5 | **DSGVO-Konzept**: Aufbewahrung, Löschroutine für Captions, User-Handles und Comments | Compliance |

---

## 13. Offene Fragen für Wolf

### 13.1 Hochpriorität (blockieren Phase 4)

1. **DB-Trennung von ki-sicherheit.jetzt — ja/nein/Schema-Trennung?** Empfehlung dieses Reports: trennen oder eigenes Schema. Das beeinflusst alle Migrations- und Backup-Pläne.
2. **Live-DB-Read für Phase-1-Validierung freigeben?** Brauche entweder: a) read-only DATABASE_URL als ENV (für Sample-SELECTs), b) `pg_dump` mit anonymisierten Daten, oder c) Ergebnis von 5–10 spezifischen SELECT-Queries, die ich vorgebe.
3. **Apify-ToS und juristische Lage**: Liegt eine Vereinbarung mit Apify vor? Wurde mit IG/TT-Legal geprüft? Briefing markiert das als Pre-Commitment — vor Phase 4 nötig.
4. **Persistenter Asset-Storage**: Railway Volume, S3, R2, Backblaze? Budget-Rahmen pro Monat? Zugriffskontrolle (öffentlich erreichbar oder signed URLs)?
5. **Auth-Modell**: Wer hat Zugriff? Single-User (Wolf), kleines Team, externe Kunden? Empfehlung: Bearer-Token in ENV reicht für MVP, danach Auth0/Netlify Identity.

### 13.2 Produkt-Vision

6. **Welche der 13 Lücken aus Abschnitt 10 sind P0 für die nächsten 4 Wochen?** Empfehlung: Asset-Capture-Persistenz, Märkte-Vergleich-UI, Title-Tracking-Dashboard.
7. **Game-Titel-Quelle**: TMDb hat keine Games. IGDB? RAWG? Manueller Pflege-Modus?
8. **Serien (TV)**: TMDb hat `/discover/tv` — soll der Sync das ergänzen?
9. **Welche Plattformen über IG/TikTok hinaus?** YouTube (Trailer-Channels), Letterboxd? Briefing erwähnt „weitere relevante Plattformen, die du als sinnvoll identifizierst" — meine Empfehlung: YouTube für Trailer-Verbreitung als nächste Priorität.
10. **Welcher Markt-Scope kurzfristig?** DE+US sind im Code, INT als Fallback. Soll UK, FR, JP folgen?

### 13.3 Operationelles

11. **Cron-/Background-Job-Plattform**: Railway Cron Service, GitHub-Actions-Schedule, oder externer Worker (sidekiq-Style)? Empfehlung Railway Cron für minimale Komplexität.
12. **Monitoring/Alerting**: Sentry, Logflare, Better Stack? Aktuell keine Beobachtbarkeit.
13. **Backup-Plan für Postgres**: Railway-Auto-Backup oder eigener `pg_dump`-Workflow? Wie oft? Test-Restore zuletzt wann?

### 13.4 Stille Fehler / blinde Flecken (mind. drei wie im Briefing gefordert)

1. **`visual_analyzed`-Counter zählt 0** wegen `text_only`-Drift — beobachtbar nur, wenn man Insights-Endpoint aufruft, der vom UI nicht aufgerufen wird. Doppelt unsichtbar.
2. **OpenAI-Vision-Calls scheitern silent in Heuristik-Fallback** — der Nutzer sieht „Analyse abgeschlossen", aber der Status `text_fallback` wird im UI als „nur Textanalyse" angezeigt. Aus Anwender-Sicht ist nicht klar, ob die KI das Bild jemals gesehen hat.
3. **Image-Proxy-Hostlist drift** — wenn ein neuer CDN dazu kommt (z.B. `*.akamaihd.net`), muss das Frontend UND das Backend simultan aktualisiert werden. Heute keine Tests, die das einfangen.
4. **`fastcdninstagram.com`-Spoofing-Test ist vorhanden** (`test_proxy.py`), aber es gibt keinen Pen-Test für andere Edge-Cases (URL-Encoding-Tricks, Path-Traversal nach `/api/img?url=...`).
5. **Stale Branches und offene Issues täuschen Aktivität vor**, die längst erledigt ist — bei der Sprint-Planung kann das zu Doppelarbeit führen.

---

## Appendix: Inventarisierte Dateien

```
backend/app/
  api/__init__.py                         (1 Zeile)
  api/health.py                           (15)
  api/channels.py                         (70)
  api/titles.py                           (158)
  api/posts.py                            (216)
  api/assets.py                           (178)
  api/monitor.py                          (259)
  api/reports.py                          (185)
  api/insights.py                         (19)
  api/proxy.py                            (93)
  config.py                               (57)
  database.py                             (183)
  main.py                                 (43)
  models/entities.py                      (242)
  schemas/dto.py                          (170)
  services/ai_asset_analyzer.py           (23, placeholder pfad)
  services/apify_connector.py             (211)
  services/channel_importer.py            (113)
  services/creative_ai.py                 (165)
  services/insights.py                    (187)
  services/link_preview.py                (68)
  services/market_context.py              (41, ungenutzt)
  services/report_generator.py            (107, legacy v1)
  services/report_renderer_v2.py          (182, aktiv)
  services/report_selector.py             (407)
  services/screenshot_capture.py          (70)
  services/seeds.py                       (90)
  services/title_candidates.py            (59)
  services/title_rematch.py               (71)
  services/title_sync.py                  (135)
  services/tmdb_client.py                 (116)
  services/visual_analysis.py             (220)
  services/whitelist_matcher.py           (205)
  jobs/collect_posts_job.py               (10, placeholder)
  jobs/analyze_assets_job.py              (6, placeholder)
  jobs/generate_weekly_report_job.py      (9, placeholder)
  prompts/asset_classification_de_v1.md   (35, ungenutzt)
  prompts/weekly_report_de_v1.md          (16, ungenutzt)
  data/channels_seed.json                 (201, ungenutzt)
  tests/test_proxy.py                     (113)
  tests/test_report_renderer_v2.py        (117)
  tests/test_report_selector.py           (143)
  tests/test_title_rematch.py             (69)
  tests/test_whitelist_matcher.py         (5, placeholder)

frontend/
  src/App.jsx                             (965)
  src/api/client.js                       (113)
  src/styles.css                          (294)
  index.html                              (13)
  package.json                            (19)
  netlify.toml                            (14)

docs/
  data_policy.md                          (9)
  implementation_steps.md                 (183)
  mvp_accounts_and_titles.md              (38)
  online_only_setup.md                    (286)
```

— Ende Diagnose-Report —





