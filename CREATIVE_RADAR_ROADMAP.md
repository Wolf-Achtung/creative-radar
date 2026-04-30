# Creative Radar — Master-Plan (Roadmap)

- Stand: 2026-04-30
- Branch: `claude/creative-radar-master-plan-LRKjT`
- Vorgängerdokument: `CREATIVE_RADAR_DIAGNOSIS.md` (Phase 1+2, read-only)
- Geltungsbereich: Phase 3 (Roadmap) — verbindlich erst nach Wolf-Freigabe der Go/No-Go-Punkte aus Abschnitt 7

> Diese Roadmap ist konsistent zur Diagnose und führt deren Befunde in eine umsetzbare Reihenfolge. Jede Empfehlung trägt einen Querverweis auf den Diagnose-Abschnitt, in dem der Befund verifiziert wurde. **Phase 4 (Umsetzung) startet erst nach expliziter Freigabe der Go/No-Go-Punkte aus Abschnitt 7.**

---

## 1. Produkt-Definition

### 1.1 Was Creative Radar ist (und was nicht)

**Ist:** ein internes Creative-Intelligence-Tool für Film-/Serien-/Game-Marketing, das öffentliche Social-Media-Kanäle (zunächst Instagram + TikTok) systematisch dokumentiert, KI-gestützt klassifiziert und zu wiederverwendbaren Erkenntnissen verdichtet — inklusive Wochenreport, DE/US-Vergleich und kuratierten Top-Funden.

**Ist nicht:** kein Performance-Tracking-Tool (keine echten Klicks, keine Conversion-Daten fremder Accounts), keine Social-Listening-Plattform (kein Sentiment-Mining im MVP-Zustand), kein Public-Tool (interne Nutzung, kein offener Self-Service), kein Substitut für menschliche Kuratierung — sondern Beschleuniger.

Die zentrale Leitplanke aus `docs/data_policy.md` bleibt: **„sichtbare Creative-Muster, keine Erfolgsbehauptungen für fremde Accounts"**. Das ist nicht nur eine UX-Disclosure, sondern ein juristischer und ethischer Schutz, der die Architektur durchprägen muss (siehe Abschnitt 6 Schutzregeln).

### 1.2 Zielgruppen (intern und Pilotkunden)

| Persona | Bedarf | Was sie aus Creative Radar ziehen |
|--------|-------|------------------------------------|
| **Marketingleitung Film/Serie** (DE-Verleih, internationaler Kontext) | Wochen­überblick „was läuft kreativ in DE/US?", Argumentations­hilfe gegenüber Geschäftsführung | Wochenreport, DE/US-Vergleich, Top-Funde |
| **Trailer-Producer / Kreativ-Lead** | Inspirations­quellen, Pattern-Erkennung über Zeit, schnelle Recherche pro Titel | Title-Tracking-Dashboard, Asset-History, Kinetics-/CTA-Pattern-Liste |
| **Distribution / Theatrical-Marketing** | Wann legt US los, wann muss DE folgen? Welche Kanäle laufen heiß? | Channel-Rankings, Release-Timeline-Vergleich |
| **Externe Agenturen / Freelancer** (perspektivisch) | API/Export, eigenes Reporting | API-Keys + JSON/CSV-Exporte (P2) |
| **Founder Wolf (Solo-Betrieb, MVP-Phase)** | System läuft autonom, jeder Sprint trägt sichtbar | klare Operational-Health-Sicht, geringe Manual-Steps, kalkulierbare Kosten |

Briefing Abschnitt 3 nennt diese Gruppen explizit. Die Roadmap priorisiert Marketingleitung + Trailer-Producer im MVP — sie sind die zwei Personas, die im UI heute schon adressiert werden (`Treffer prüfen`, `Report erstellen`).

### 1.3 Differenzierung gegenüber Standard-Werkzeugen

| Standardlösung | Was sie kann | Wo Creative Radar sich unterscheidet |
|---|---|---|
| Brandwatch / Talkwalker | breites Social-Listening, Mention-Tracking, Sentiment | Creative-Radar fokussiert **Visual-Creative-Muster** statt Volumen-Mention. Cuts, Claims, Title-Placement, Kinetics — was Listening-Tools strukturell nicht erfassen. |
| Tubular / Conviva | Video-Performance bei eigenen Channels | Creative Radar ist **Cross-Account-Inspiration**, nicht eigene Performance. |
| Iconosquare / Later | IG-Account-Analyse mit eigenen Insights | Creative Radar betrachtet **fremde Accounts mit öffentlichen Sichtbarkeits-Signalen**, nie mit Owner-Daten. |
| Manuelle Slack-Sammlung | bisheriger Industrie-Standard im DACH-Trailer-Marketing | Creative Radar ist die **strukturierte, reproduzierbare** Variante davon — mit DB, Whitelist und Wochen-Cadence. |

Die echte USP: **DE/US-Cross-Market-Vergleich auf Asset-Ebene mit kuratierter Whitelist + KI-Visual-Klassifikation**. Diese Kombination habe ich in keinem Standard-Tool gefunden, und sie ist nahe an Wolfs Markt-Erfahrung anschlussfähig.

### 1.4 Use Cases im MVP-Scope (Sprint 9–12)

**UC-1 „Wochenreport für die Geschäftsführung".** Wolf öffnet Montag das Dashboard, klickt „Vorschlag erstellen" für die letzten 7 Tage, prüft 5–10 vorgeschlagene Assets, gibt frei, lädt HTML/Markdown-Report herunter, mailt ihn raus. Dauer Soll: ≤30 Minuten.

**UC-2 „DE/US-Vergleich für einen anstehenden Release".** Wolf wählt Report-Typ `de_us_comparison`, Markt = alle, Limit = 20. Liest Pair-Group-View, identifiziert: „US betont Star-Power, DE betont Genre-Hook". Nutzt das als Briefing-Input.

**UC-3 „Title-Recherche on-demand"** (heute Lücke, Roadmap P1). Wolf gibt einen Titel ein, sieht alle Assets dieser Property quer über Channels und Märkte, mit KI-Annotationen.

**UC-4 „Quellen-Operations".** Wolf prüft einmal pro Woche: läuft der Apify-Lauf? Sind neue Title-Candidates aufgetaucht? Sind Channels deaktiviert/inaktiv geworden?

### 1.5 Was außerhalb des MVP-Scopes liegt (bewusst)

- **Kein Owner-View für die geprüften Accounts.** Wir kommunizieren nicht aktiv an A24, Constantin etc., dass sie überwacht werden — das ist legal innerhalb des „öffentlich sichtbare Inhalte"-Rahmens, juristisch aber sensibel. Roadmap rührt das nicht an.
- **Keine echten Klick-/Performance-Daten** für fremde Accounts. Visible-Likes/Views sind Annäherungen, kein Performance-Beweis.
- **Keine kommerzielle API für Externe** im MVP. Erst nach juristischer Klärung (Abschnitt 4).
- **Keine Echtzeit-Streams.** Wochen-Cadence ist gewollt (Editorial-Logik, Kostenkontrolle).

### 1.6 Erfolgs-Metriken (Definition of Done für MVP)

- **Funktional:** mindestens 3 Wochenreports in Folge ohne manuelle SQL-Eingriffe erstellt.
- **Technisch:** P0-Blocker aus Diagnose-Abschnitt 11 beseitigt (Storage, DB-Trennung, Visual-Pipeline, Auth).
- **Kosten:** monatliche externe Kosten transparent, ≤ Budget-Cap, das Wolf in Abschnitt 7 freigibt.
- **Qualität:** mindestens 70 % der Apify-Treffer erhalten eine erfolgreiche Visual-Analyse mit Status `done` (nicht `text_fallback`).
- **Compliance:** dokumentierte ToS-Klärung (Apify, IG, TT) und DSGVO-Konzept im Repo.

---

## 2. Feature-Backlog (priorisiert P0/P1/P2)

> Aufwandsschätzung in **Personentagen Solo-Founder** (PT-S). 1 PT-S = 6 fokussierte Stunden. Schätzungen sind **Komplexitäts-Schätzung, kein Zeitplan** — siehe Abschnitt 5 für die zeitliche Reihung.

### Legende

- **P0** — blockierend für Produktvision oder hat Datenverlust-/Sicherheits-/Rechts-Risiko (siehe Diagnose Abschnitt 11). Muss vor neuen Features angefasst werden.
- **P1** — kritischer Mehrwert für die MVP-Personas oder schließt Kette von Folgeproblemen.
- **P2** — Nice-to-have, Hygiene, oder Skalierungs-Vorbereitung.

### 2.1 P0 — Fundamente (Datenverlust, Recht, Sicherheit)

| ID | Feature / Maßnahme | Diagnose-Querverweis | Aufwand PT-S | Abhängigkeiten |
|---|---|---|---|---|
| **F0.1** | **Persistentes Asset-Storage** (S3 oder Railway-Volume) für `screenshot_capture` + Migration der `/storage/evidence/`-Logik. Inklusive `SECURE_STORAGE_ENABLED=True` aktivieren | Diagnose §1 Punkt 1, §2 Storage, §11 R1 | 3–4 | Wolf-Entscheidung Provider (Abschnitt 4) |
| **F0.2** | **DB-Trennung** von `ki-sicherheit.jetzt` (eigenes Schema `creative_radar` ODER eigene Postgres-Instanz) plus Alembic-Initialisierung mit Baseline-Migration der existierenden 8 Tabellen | Diagnose §4, §9, §11 R2 | 3 | Wolf-Entscheidung Schema vs. eigene DB (Abschnitt 7) |
| **F0.3** | **Auth-Layer** (Bearer-Token aus ENV gegen alle nicht-GET-Endpoints, plus optional GET-Schutz für `/api/insights`, `/api/reports/*`). Frontend liest `VITE_API_TOKEN` | Diagnose §11 R7, §12 S-1 | 1 | — |
| **F0.4** | **Visual-Pipeline reparieren**: KI-Vision bekommt eine **öffentlich erreichbare URL** (entweder das ge­cap­tur­te Bild aus persistentem Storage, oder die externe CDN-URL ohne Internal-Path-Pollution). Status-Setzung sauber: `done` nur wenn KI das Bild wirklich gesehen hat | Diagnose §1 Punkt 3, §5.5, §6.2, §11 R4 | 2 | F0.1 |
| **F0.5** | **Apify-/Instagram-/TikTok-ToS-Klärung** + Dokumentation als `docs/legal_review.md`. Bis Klärung: Apify-Endpoints hinter Feature-Flag `APIFY_MONITOR_ENABLED` | Diagnose §11 R3, §12 S-4 | 0,5 (Doku) + extern (juristisch) | Wolf-Klärung mit Anwalt |
| **F0.6** | **Rate-Limit + Cost-Cap** für `/api/monitor/*` und `/api/titles/sync/*` (z.B. SlowAPI), plus Apify-Run-Cost-Logging | Diagnose §5.3, §11 R7, R14 | 1 | F0.3 |
| **F0.7** | **DSGVO-Lösch-/Aufbewahrungskonzept** als `docs/data_protection.md`, plus minimaler Lösch­-Endpoint (`POST /api/posts/{id}/delete-personal-data`) der Captions/Handles maskt | Diagnose §11 R18, §12 S-5 | 1,5 | — |

**P0-Summe:** ~12 PT-S + externe juristische Klärung (Aufwand nicht eigenständig schätzbar).

### 2.2 P1 — Mehrwert für die MVP-Personas

| ID | Feature / Maßnahme | Diagnose-Querverweis | Aufwand PT-S | Abhängigkeiten |
|---|---|---|---|---|
| **F1.1** | **Title-Tracking-Dashboard** (UC-3): pro Filmtitel Asset-Timeline, Channel-Distribution, KI-Annotationen-Übersicht, Cross-Market-View | §10 Lücke „Title-Tracking" | 3 | F0.4 (saubere Visual-Daten) |
| **F1.2** | **Channel-Rankings + Insights-UI verdrahten** (`/api/insights/overview` ist heute toter Endpoint) | §5.8, §8, §10 Lücke „Ranking" | 1,5 | F1.4 (Counter-Fix) |
| **F1.3** | **Asset-Export als JSON/CSV** für `report_selector`-Output und freigegebene Assets | §10 Lücke „API/Export" | 1 | F0.3 |
| **F1.4** | **Insights-Counter-Bug fixen** (`{"analyzed", "text_only"}` → `{"done", "text_fallback"}`) plus Tests | §1 Punkt 4, §11 R5, §12 QW-1 | 0,3 | — |
| **F1.5** | **Magic-String-Hack auf `trend_summary_de` ablösen**: `WeeklyReport.report_meta JSONB` (oder eigene Tabelle `report_assets`), Migration, Endpunkte umverdrahten | §1 Punkt 5, §6.1 Schritt 13, §11 R6, §12 QW | 1,5 | F0.2 (Alembic) |
| **F1.6** | **Background-Job für Apify-Monitor** (kein 120-Sekunden-Block im API-Worker). Variante: Railway-Cron-Service, oder einfacher BackgroundTasks/asyncio.Queue | §5.2, §11 R10 | 2 | F0.3 |
| **F1.7** | **TMDb-Sync für TV** (`/discover/tv`) ergänzen, plus konfigurierbare Page-Cap (heute 3 hardcoded → max. 60 Filme/Markt/Sync) | §5.4, §10 Lücke „Serien" | 1 | — |
| **F1.8** | **N+1-Fix in `find_best_title_match`**: Keywords mit `selectinload` in einem Query laden | §5.6, §11 R11 | 0,5 | — |
| **F1.9** | **Carousel-Iteration im Apify-Pfad**: alle Bilder pro IG-Carousel als separate Assets oder Asset-Children erfassen | §6.2, §7 Audit-Tabelle „Carousel-Bilder" | 2 | F0.1 |
| **F1.10** | **`Asset.de_us_match_key` deterministisch** (Slug-Erzeugung statt KI-non-determ.) | §7 Audit-Tabelle | 0,5 | — |
| **F1.11** | **Frontend-Komponenten-Refactor**: `App.jsx` (965 LoC) auf `pages/`, `components/` aufteilen — minimal, aber wartungsfähig | §3, §8 | 2 | — |
| **F1.12** | **Frontend-Deps pinnen** (`react`, `vite`, `react-dom`) plus `package-lock.json` committen | §11 R8, §12 QW-2 | 0,3 | — |
| **F1.13** | **Image-Proxy-Hostlist als Single-Source-of-Truth** (Backend liefert `/api/img/allowed-hosts`, Frontend zieht beim Mount, kein Hardcode) | §7 Audit-Tabelle, §11 R12 | 1 | F0.3 |
| **F1.14** | **Alembic-Migration aller `_ensure_columns`-DDL** in versionierte Revisions, inkl. Rollback-Plan | §4, §11 R2 (Hygiene) | 2 | F0.2 |
| **F1.15** | **Status-Werte als Enum konsolidieren** (kein freier String mehr in `visual_analysis_status`) | §11 R13 | 0,5 | F1.4 |
| **F1.16** | **Backup-Konzept dokumentieren + Restore-Test einplanen** | §11 R15 | 1 | F0.2 |
| **F1.17** | **Strukturierte Logs** (JSON-Logger, Sentry/Logflare optional). Mindestens: Apify-Run-Outcome, Vision-Outcome, Title-Sync-Outcome | §11 R5, R10, R14 | 1,5 | — |
| **F1.18** | **Tote API-Endpunkte aufräumen** (`/api/insights/overview` UI-verdrahten oder löschen, `/api/posts/analyze-instagram-link` ebenso, `/api/reports/generate-weekly` v1 deprecaten/löschen) | §3, §5.8, §8, §12 H-5 | 0,5 | F1.2, F1.5 |
| **F1.19** | **`Post.external_id` UNIQUE + Migration** + `(channel_id, external_id)` Composite-Index | §4, §7, §11 (Stille Duplikate) | 0,5 | F1.14 |
| **F1.20** | **Externer-Bild-DOM-Audit** (E2E-Test, kein direkter `cdninstagram.com` ohne `/api/img/`-Wrapper) | §11 R12, §13 Stille Fehler | 0,5 | F1.13 |

**P1-Summe:** ~22 PT-S.

### 2.3 P2 — Skalierung, Erweiterung, Hygiene

| ID | Feature / Maßnahme | Diagnose-Querverweis | Aufwand PT-S | Abhängigkeiten |
|---|---|---|---|---|
| **F2.1** | **YouTube-Connector** (Trailer-Channels, offizielle Studio-Kanäle) — YouTube Data API v3, kein Scraping nötig | §10 Lücke „weitere Plattformen" | 3 | F0.1, F0.4 |
| **F2.2** | **Trend-Detection mit Wochen-Delta**: KI-Aggregation von `kinetic_type`, `placement_strength`, `creative_mechanic` über Zeit | §10 Lücke „Trend-Detection" | 4 | F0.4, F1.1 |
| **F2.3** | **Audio-/Music-Strukturierung** (TikTok `musicMeta` aus `raw_payload._creative_radar_music` in eigene Tabelle `post_music`) | §10 Lücke „Audio" | 2 | F0.2 |
| **F2.4** | **Sentiment / Comment-Mining** (Apify Comments-Scraper, OpenAI-Klassifikation) | §10 Lücke „Sentiment" | 4 | F0.5, F0.6 |
| **F2.5** | **Alerts** (Push/E-Mail bei `visible_views > Threshold` o.ä.) | §10 Lücke „Alerts" | 2 | F1.17 |
| **F2.6** | **Genre-Cluster**: Genre-Feld auf `Title` + Aggregation in Insights | §10 Lücke „Genre" | 1,5 | F1.7 |
| **F2.7** | **Audit-Log-Tabelle** für Curator-Aktionen (review_status, title_id-Änderung) | §11 R17 | 1 | F0.2 |
| **F2.8** | **Game-Titel-Quelle** (IGDB oder manueller Pflege-Modus) | §10 Lücke „Games" | 2 | — |
| **F2.9** | **Frame-Sampling von Videos** (kein Single-Cover, sondern 3–5 Frames pro Video via headless ffmpeg/Playwright) | §5.7, §10 „Asset-Capture Cuts" | 4 | F0.1 |
| **F2.10** | **Backfill-Endpoint** für historische Apify-Posts (>30 Tage, paginiert) | §5.3 Schwächen | 1,5 | F0.6 |
| **F2.11** | **Linter (ruff/black)** Backend + ESLint/Prettier Frontend, GitHub-Actions-CI | §3 Build & Deploy, §12 H | 1,5 | — |
| **F2.12** | **Coverage-Gate** (`pytest --cov`, Mindestschwelle 60 %) | §3, §12 H-3 | 0,5 | F2.11 |
| **F2.13** | **Docker-Build im CI** (Image bauen, nicht nur deployen) | §3 | 0,5 | F2.11 |
| **F2.14** | **Aufräumen toter Code-Pfade**: `app/jobs/*.py`, `app/prompts/*.md` (oder im Code laden), `app/data/channels_seed.json` (oder löschen), `report_generator.py` v1 | §3 Tote Pfade, §12 QW-7, H-5 | 0,5 | F1.18 |
| **F2.15** | **Issues #3, #7, #10, #12 schließen** + Stale-Branches löschen | §3 Git-Aktivität, §12 QW-5/QW-6 | 0,3 | — |
| **F2.16** | **Doppel-`netlify.toml` + drei startCommand-Quellen entwirren** | §2 Deploy-Topologie, §11 R9, §12 QW-3/QW-4 | 0,3 | — |
| **F2.17** | **`.env.example` synchron zur `Settings`-Klasse** (Apify, TMDb, Image-Proxy ergänzen) | §2 Konfiguration, §12 QW-8 | 0,2 | — |
| **F2.18** | **Performance-Indizes** (`post.detected_at`, `post.channel_id`, `asset.title_id`, `asset.review_status`, `asset.visual_analysis_status`) | §4 Indizes, §12 QW-9 | 0,5 | F1.14 |
| **F2.19** | **PDF-Export** für Wochenreport (jinja2 + weasyprint oder browserless) | §4 `weeklyreport.pdf_url` (toter Field) | 1,5 | F1.5 |
| **F2.20** | **Webhook-Output** (POST an externe URL bei `report.status=final`) | §10 Lücke „Webhook" | 1 | F1.17 |
| **F2.21** | **Markt-Erweiterung UK/FR/JP** (TMDb-Sync, Channel-Markt-Werte) | §10 „Welcher Markt-Scope" | 1 (pro Markt) | F1.7 |

**P2-Summe:** ~33 PT-S.

### 2.4 Cumulative Aufwand-Übersicht

| Bucket | Items | Aufwand PT-S | Wochen Solo (5 PT-S/Woche) |
|---|---|---|---|
| P0 | 7 | ~12 + extern juristisch | ~2,5 Wochen + Wartezeit für Anwalt |
| P1 | 20 | ~22 | ~4,5 Wochen |
| P2 | 21 | ~33 | ~6,5 Wochen |
| **Total** | **48** | **~67 PT-S** | **~13,5 Wochen** |

Das ist der **maximalistische Umfang**. Der Sprint-Plan in Abschnitt 5 zeigt eine gestraffte 4-Wochen-Variante, die nur P0 + die wirkungsstärksten P1-Items umfasst.

### 2.5 Querschnitt: was wird in der Diagnose adressiert, was nicht?

| Diagnose-Befund | Roadmap-Item | Status |
|---|---|---|
| §1 Pkt. 1 (Storage ephemer) | F0.1 | adressiert |
| §1 Pkt. 2 (geteilte DB) | F0.2 | adressiert |
| §1 Pkt. 3 (Vision-Pipeline) | F0.4 | adressiert |
| §1 Pkt. 4 (Counter-Bug) | F1.4 | adressiert |
| §1 Pkt. 5 (Magic-String) | F1.5 | adressiert |
| §1 Pkt. 6 (keine Auth) | F0.3, F0.6 | adressiert |
| §1 Pkt. 7 (Apify-ToS) | F0.5 | adressiert |
| §1 Pkt. 8 (Stale Branches/Issues) | F2.15 | adressiert (P2, Hygiene) |
| §11 R1–R18 | komplett mappbar auf F0/F1/F2 | adressiert |
| §10 Lücken-Matrix | F1.1, F2.1–F2.21 | teilweise adressiert; P2 deckt den Rest |
| §13 Offene Fragen | Abschnitt 7 dieser Roadmap | als Go/No-Go geführt |

---

## 3. Architektur-Soll

### 3.1 Soll-Topologie (12-Monats-Sicht)

```
                 ┌─────────────────────────────────────────────┐
                 │  Externe Quellen                            │
                 │  Apify (IG, TT, optional Comments)          │
                 │  TMDb (Movies + TV) · IGDB (Games, P2)      │
                 │  YouTube Data API (P2) · OpenAI · Perplexity│
                 └────────────────────┬────────────────────────┘
                                      │
                                      ▼
   ┌─────────────────────────────────────────────────────────┐
   │  Backend (Railway, FastAPI)                             │
   │                                                         │
   │  API-Layer  ── Auth (Bearer-Token, ENV)                 │
   │             ── Rate-Limit + Cost-Cap                    │
   │             ── Public/internal endpoint split           │
   │                                                         │
   │  Worker-Pool (Railway Cron Service oder asyncio Queue)  │
   │   ↳ apify-monitor-job                                   │
   │   ↳ visual-analysis-batch-job                           │
   │   ↳ tmdb-sync-job                                       │
   │   ↳ weekly-report-job (optional, halbautomatisch)       │
   │                                                         │
   │  Service-Layer (Domänen-Services, unverändert)          │
   │  Modelle (versioniert via Alembic)                      │
   └────────┬─────────────────────────────────┬──────────────┘
            │                                 │
            │ SQLAlchemy                      │ httpx
            ▼                                 ▼
   ┌────────────────────┐         ┌─────────────────────────┐
   │ Postgres           │         │  Persistent Asset       │
   │ (eigenes Schema    │         │  Storage                │
   │  ODER eigene DB,   │         │  ↳ Railway Volume       │
   │  Alembic-versio-   │         │  ODER S3/R2/B2          │
   │  niert, isoliert   │         │  (Wolf-Entscheidung)    │
   │  von KI-Sicherheit)│         │                         │
   └────────────────────┘         └─────────────────────────┘
                                              ▲
                                              │ Public-URL
                                              │ (Signed oder via /api/img)
   ┌────────────────────────────────────────────────────────┐
   │  Frontend (Netlify, Vite/React)                        │
   │  components/   pages/   api/   hooks/                  │
   │  Auth-Header (Bearer aus VITE_API_TOKEN)               │
   │  Image-Proxy-Hostlist über /api/img/allowed-hosts      │
   └────────────────────────────────────────────────────────┘
```

### 3.2 Zentrale Architektur-Entscheidungen

#### 3.2.1 Storage-Strategie (F0.1)

**Empfehlung: S3-kompatibles Object-Storage** (z.B. Backblaze B2 oder Cloudflare R2). Begründung:

- **Reproduzierbar zwischen Deploys** (Railway Volume verschwindet bei Service-Recreate)
- **Multi-Region zugänglich** (für OpenAI Vision wichtig — F0.4)
- **Kostengünstig** für reine Bild-Daten (Größenordnung MB, nicht GB pro Woche)
- **Standard-Tooling** (boto3 kompatibel, gut dokumentiert)
- **Backup-fähig** (Versionierung, Lifecycle-Policies)

Alternative Railway-Volume: einfacher zu provisionieren (1 Klick), aber an Service gebunden, schwer zu migrieren, kein eingebautes Backup. Für MVP machbar, mittelfristig schwächer.

**Migration**: `screenshot_capture.capture_asset_screenshot()` schreibt in `Storage`-Service (Adapter-Pattern), das initial `LocalFileStorage` bleibt für Tests, in Production aber `S3Storage`. `visual_evidence_url` wird zur signed URL oder public bucket URL — damit löst sich das Vision-API-Problem (F0.4) automatisch.

#### 3.2.2 DB-Trennung (F0.2)

**Variante A — eigenes Schema in derselben Postgres-Instanz** (`creative_radar.asset`, `creative_radar.title`, …):

- Pro: minimaler Cost-Impact, eine Backup-Quelle
- Pro: keine zusätzliche Connection-Pool-Konkurrenz auf Cluster-Ebene
- Contra: weiterhin `max_connections` shared, ein Runaway-Query in einer App stört die andere
- Contra: pg_dump greift weiterhin beide Schemas (kann gewollt sein)
- Aufwand: 1–2 PT-S (Search-Path setzen, Models neu mappen, Alembic-Baseline)

**Variante B — eigene Postgres-Datenbank-Service** auf Railway:

- Pro: klare Trennung, eigene Connection-Pools, eigenes Backup
- Pro: Skaliert unabhängig — wenn Creative Radar wächst, betrifft das ki-sicherheit.jetzt nicht
- Contra: zusätzliche Postgres-Instanz kostet (Railway-Pricing TBD, je nach Plan)
- Aufwand: 2–3 PT-S (neuer Service, ENV-Migration, Daten-Export+Import, Alembic-Baseline)

**Empfehlung dieses Reports: Variante B** (eigene DB), weil sie die Trennung sauber zementiert. Die Mehrkosten sind vorhersehbar; das Risiko einer Schema-Kollision oder Migration-Race wiegt höher. Wenn Wolf das Cost-Argument bevorzugt: Variante A ist akzeptabel, sofern Tabellen ein Prefix `cr_` bekommen.

**Variante C — Status quo behalten**: ausdrücklich nicht empfohlen. Diagnose §4 und §11 R2 zeigen das Risiko.

→ Wolf-Frage 7.A.

#### 3.2.3 Migrations-Werkzeug (F1.14)

Heute: `_ensure_columns()` ist eigenbau, keine Versionierung, kein Rollback.

**Soll: Alembic** als Standard. Plan:

1. Alembic einrichten, Baseline-Migration aus aktuellen SQLModel-Modellen (`alembic stamp head` nach manueller Erst-Revision).
2. `_ensure_columns()` deprecaten, in Zukunft nur noch Alembic-Revisions.
3. Bei `create_db_and_tables()` startup-seitig: nur noch `alembic.command.upgrade(..., 'head')` aufrufen, kein `metadata.create_all` mehr.
4. Rollback-Plan pro Revision dokumentieren.

#### 3.2.4 Auth (F0.3)

**Variante A — Bearer-Token (gemeinsamer ENV-Wert)**:

- Pro: trivial, MVP-tauglich
- Pro: kompatibel mit Postman, curl, automatisierte Tests
- Contra: kein Multi-User, keine Audit-Trace pro Mensch
- Aufwand: 1 PT-S

**Variante B — Netlify Identity / Auth0 / Supabase Auth**:

- Pro: echte User, JWT, Multi-User-fähig
- Pro: skaliert auf Pilotkunden
- Contra: 1–2 PT-S Mehraufwand, externe Abhängigkeit
- Aufwand: 2 PT-S

**Empfehlung MVP: Variante A** (Bearer-Token). Wenn Pilotkunden dazukommen (Q3 oder später), umstellen auf Variante B.

#### 3.2.5 Background-Jobs (F1.6)

Heute: alles synchron im API-Worker. `/api/monitor/apify-instagram` blockiert bis 120 s.

**Variante A — Railway Cron Service** (separater Container, der `python -m app.jobs.apify_monitor` o.ä. ausführt):

- Pro: vom API entkoppelt, kein Browser-Timeout
- Pro: Standard-Pattern, einfach zu provisionieren
- Contra: zwei Container = doppelter Bare-Service-Cost
- Aufwand: 1,5 PT-S

**Variante B — `BackgroundTasks` von FastAPI** (Job läuft in derselben API-Worker, aber asynchron nach Response):

- Pro: kein separater Service, keine Mehrkosten
- Pro: Reuse von DB-Session/Engine
- Contra: läuft nur, solange der Worker steht; Restart killt In-Flight-Job
- Aufwand: 0,5 PT-S

**Variante C — Externe Queue (Redis + RQ / Celery)**:

- für MVP zu schwergewichtig, erst bei mehreren parallelen Jobs sinnvoll
- Aufwand: 4+ PT-S

**Empfehlung**: für den ersten Schritt **Variante B** (BackgroundTasks) als Stop-Gap, danach Übergang zu **Variante A** sobald ein zweiter Service ohnehin nötig ist (z.B. für nächtliche TMDb-Syncs). Wolf-Frage 7.E.

### 3.3 Komponenten-Soll im Backend

| Komponente | Zustand heute | Zustand Soll |
|---|---|---|
| API-Layer | 9 Router, public, ohne Auth | 9 Router, Bearer-Auth-geschützt, mit Rate-Limit |
| Service-Layer | gut getrennt, aber Doppelimplementierungen | v1-Pfade entfernt; Adapter-Patterns für Storage und KI |
| Modelle | SQLModel 8 Tabellen | + `report_assets` (Many-to-Many), + `post_music` (P2), + `audit_log` (P2), `Asset` mit Storage-FK |
| Migrations | `_ensure_columns()` eigenbau | Alembic-versioniert |
| Jobs | 3 Platzhalter-Files | echte Jobs als Cron oder BackgroundTask |
| Tests | 4 Files mit Coverage auf Sprint 8.2 | Coverage-Report ≥60 %, mindestens 1 Test pro Service |
| Config | `pydantic-settings`, `.env.example` veraltet | aktuell, plus `docs/env_reference.md` |

### 3.4 Komponenten-Soll im Frontend

| Komponente | Zustand heute | Zustand Soll |
|---|---|---|
| Bundle | `App.jsx` 965 LoC monolith | aufgeteilt: `pages/Home`, `pages/Review`, `pages/Reports`, `pages/Sources`, `pages/TitleTracking`, gemeinsame `components/` und `hooks/` |
| API-Client | hartkodierte Endpoint-Map | TypeScript-Codegen aus OpenAPI (Phase 2) — optional, nicht im MVP |
| Image-Proxy-Liste | hartkodiert | runtime aus `/api/img/allowed-hosts` |
| Auth | keine | Bearer-Token aus `VITE_API_TOKEN`, im Header |
| Build | `latest`-Versionen | gepinnt, `package-lock.json` committed |
| Linting | keins | ESLint + Prettier |

### 3.5 Datenfluss-Soll für die Visual-Pipeline (F0.4)

Aus Diagnose §5.5 ist der heutige Pfad fragil. Soll-Pfad:

1. Apify-Monitor erstellt `Asset` mit externer `screenshot_url`/`thumbnail_url`.
2. Background-Job `visual-analysis-job` nimmt das Asset, lädt das Bild via `screenshot_capture` aus dem CDN, **speichert es nach S3** (oder Railway-Volume), bekommt eine **öffentlich erreichbare URL** zurück.
3. Diese öffentliche URL wird in `visual_evidence_url` geschrieben und an OpenAI Vision übergeben.
4. OpenAI antwortet mit JSON, Status wird auf `done` gesetzt.
5. Wenn das Bild nicht ge­cap­tu­red werden kann (CDN-Block, 404), wird Status auf `fetch_failed` gesetzt — und eine Retry-Strategie greift (z.B. erneut versuchen mit anderer Source-URL nach Wartezeit).
6. Wenn Vision selbst fehlschlägt (Provider-Error, Quota), Status `provider_error`, kein Auto-Heuristik-Fallback ohne explizite Markierung.

**Vorteile**: KI sieht das Bild wirklich, `text_fallback` wird ehrlich (nur wenn auch wirklich kein Bild da war), `secure`-Klassifikation funktioniert, Cross-Sprint-Vergleich wird möglich.

### 3.6 Naming-Hygiene (F0.2-Begleit)

Wenn Variante A (gemeinsames Schema mit Prefix) gewählt wird: alle Tabellen `cr_channel`, `cr_title`, `cr_post`, `cr_asset` etc. Außerdem Postgres-Enum `cr_assettype` statt `assettype`. Migration: in Alembic-Baseline-Revision umbenennen.

Wenn Variante B (eigene DB): keine Prefix nötig, aber `Title.tmdb_id` als UNIQUE konstruieren (heute nur Index — Diagnose §4).

### 3.7 Was bewusst NICHT geändert wird

- **FastAPI** bleibt — läuft, ist passend skalierbar.
- **SQLModel** bleibt — Alembic-Migrations passen weiterhin auf die Modelle.
- **Vite/React** bleibt — Vue/Next/Svelte bringen keinen Mehrwert für den MVP-Scope.
- **Apify als Scraping-Layer** bleibt — selbst-gebaute Scraper sind teurer und juristisch nicht weniger riskant. Apify ist hier ein angemessener Trade-off (juristische Klärung trotzdem nötig, F0.5).
- **OpenAI als KI-Provider** bleibt — Multimodal-fähig, Vision-Pricing günstig (F0.4). Wechsel zu Anthropic Claude oder Google Gemini wäre denkbar, ist aber kein Diagnose-Befund.

---

## 4. Externe Abhängigkeiten (APIs, Kosten, Verträge, juristische Klärung)

### 4.1 API-Inventar

| Anbieter | Wofür | Pflicht/Optional | Heute integriert | Vertragsstatus |
|---|---|---|---|---|
| **Railway** | Backend-Hosting + Postgres | Pflicht | ja | Standard-Vertrag mit Railway Inc. |
| **Netlify** | Frontend-Hosting + Build + API-Proxy | Pflicht | ja | Standard-Vertrag mit Netlify Inc. |
| **Apify** | Instagram + TikTok Scraping | Pflicht (für Auto-Modus); Manual-Import als Fallback | ja | Standard-Vertrag — **ToS-Implikationen für Drittseiten ungeklärt** (siehe 4.4) |
| **TMDb** | Film-/Serien-Whitelist-Quelle | Pflicht für Auto-Whitelist | ja (Movies); TV-Sync fehlt (F1.7) | TMDb-Terms verlangen Attribution + non-commercial Default — siehe 4.4 |
| **OpenAI** | Text-Klassifikation + Vision | Pflicht (sonst Stub-Mode) | ja | Standard-Anthropic/OpenAI-Terms |
| **Perplexity** | Wochen-Marktkontext | Optional, **heute toter Pfad** (Diagnose §5.5) | nicht aktiv | Standard-Terms |
| **S3 / R2 / B2** | Persistentes Storage (F0.1) | Pflicht ab P0 | nein | TBD (Wolf-Entscheidung) |
| **YouTube Data API** | Trailer-Channels (F2.1) | Optional (P2) | nein | Google API Terms |
| **IGDB** | Game-Titel (F2.8) | Optional (P2) | nein | Twitch/IGDB-Terms |
| **Sentry / Logflare** | Logs/Alerts (F1.17) | Optional | nein | Standard-Terms |

### 4.2 Kostenstruktur (Größenordnung, alles **TBD genau**)

> **Hinweis:** alle Preise unten sind Größenordnungs-Schätzungen für ein Solo-Founder-MVP mit moderater Last (~20 Channels, ~5 Posts/Channel/Woche, ~100 Assets/Woche). Verbindliche Zahlen muss Wolf vor Phase 4 aus den aktuellen Pricing-Pages ziehen — Preise ändern sich häufig, und ich vermeide bewusst veraltete Zahlen.

| Posten | Größenordnung pro Monat | Anmerkung |
|---|---|---|
| Railway Backend (Hobby/Starter) | TBD — siehe railway.app/pricing | Hobby-Plan reicht initial; bei separatem Cron-Service zusätzlicher Service-Plan |
| Railway Postgres | TBD — siehe railway.app/pricing | Variante A (gemeinsame DB) keine Mehrkosten; Variante B eigene Instanz |
| Netlify | 0 € (Free-Tier voraussichtlich ausreichend) | siehe netlify.com/pricing für Free-Tier-Limits |
| Apify Instagram Scraper | TBD — siehe apify.com/apify/instagram-scraper Pricing-Tab | Pay-per-result oder Pay-per-Compute-Unit (CU) |
| Apify TikTok Scraper (`clockworks~tiktok-scraper`) | TBD — siehe apify.com/clockworks/tiktok-scraper Pricing-Tab | gleiche Logik |
| OpenAI gpt-4o-mini Text + Vision | TBD — siehe openai.com/pricing | Vision-Calls werden nach Token-Volumen abgerechnet; pro Bild deutlich günstiger als gpt-4o |
| Perplexity sonar-pro | TBD — siehe perplexity.ai/pricing | optional |
| S3-kompatibles Storage (R2/B2) | TBD — siehe Anbieter-Pricing | Größenordnung ≪ andere Posten bei wenigen GB Bilder |
| Sentry/Logflare | 0 € im Free-Tier voraussichtlich ausreichend | optional |

**Empfehlung**: Wolf legt einen **monatlichen Hard-Cap** fest (z.B. 100 €/Monat oder 200 €/Monat). Cost-Cap (F0.6) im Code überwacht den Apify-Cost-Counter und stoppt Auto-Runs bei Überschreitung.

### 4.3 Vertragslage und Beschaffung

| Anbieter | Heute | Soll vor Phase 4 |
|---|---|---|
| Railway | abgeschlossen | unverändert |
| Netlify | abgeschlossen | unverändert |
| Apify | Standard-Account (Free oder Paid) | bezahlter Plan klar gewählt + ToS-Review (siehe 4.4) |
| TMDb | API-Key vorhanden (`TMDB_API_KEY` im Code) | Lizenz-Status klären (commercial vs. non-commercial) |
| OpenAI | API-Key | Usage-Tier klar; Hard-Cap aktivieren |
| S3/R2/B2 | nicht vorhanden | Account anlegen, Bucket-Policy schreiben (private mit signed URLs ODER public mit Random-UUID-Pfaden) |

### 4.4 Juristische Klärung (P0 Blocker)

#### 4.4.1 Instagram + TikTok ToS

Die ToS von Instagram (Meta Platforms Terms) und TikTok (Terms of Service) verbieten in der Standard-Lesart automatisiertes Scraping ohne ausdrückliche Genehmigung. Apify positioniert sich als Tool-Anbieter, der das Compliance-Risiko vertraglich an den Kunden weitergibt (typisch im Scraping-as-a-Service-Markt).

**Konkrete Risiken:**

- **Plattform-seitig**: Sperrung der eingesetzten Account-IDs / IP-Adressen, Cease-and-Desist gegen den Betreiber, im Extremfall Klage. Präzedenzfälle: hiQ Labs ./. LinkedIn (US); in der EU schwächere Eskalationspraxis, aber nicht risikolos.
- **DSGVO-seitig**: gescrapte Daten enthalten personenbezogene Inhalte (Caption mit User-Handles, Comment-Daten falls eingeschaltet). Die Verarbeitung benötigt eine Rechtsgrundlage (Art. 6 DSGVO) — typischerweise berechtigtes Interesse mit Interessenabwägung. Für interne Kreativ-Inspiration vertretbar, aber dokumentations­pflichtig.
- **Urheberrecht** an den gespeicherten Bildern (Trailer-Frames, Poster): Privilegierungen aus § 51 UrhG (Zitatrecht) oder die EU-Text-and-Data-Mining-Ausnahme (§ 44b UrhG, Art. 4 DSM-Richtlinie) können einschlägig sein, sind aber an Bedingungen geknüpft (interner Zweck, kein Public-Vertrieb). Pre-Commitment im Briefing: keine Implementierung neuer Scraper ohne Wolf-Freigabe.

**Empfehlung**:

1. Anwalts-Termin (Anwalt für IT-/Medienrecht) zur Klärung der drei Punkte oben. Aufwand intern: 0,5 PT-S Doku-Vorbereitung + extern Honorar (TBD).
2. Ergebnis als `docs/legal_review.md` im Repo, mit Stand-Datum und Wieder­vor­lage­frist.
3. Bis zum Ergebnis: Apify-Monitoring hinter Feature-Flag `APIFY_MONITOR_ENABLED=False`. Manueller Import bleibt aktiv, ist juristisch unkritisch (Mensch-im-Loop).

#### 4.4.2 TMDb-Lizenz

TMDb-Terms unterscheiden non-commercial und commercial Use. Creative Radar wird intern für Marketing-Beratung genutzt — das kann commercial sein. **Klärung**: TMDb-API-Antrag mit kommerziellem Nutzungs-Use-Case prüfen.

#### 4.4.3 OpenAI-Datenfluss

Captions, OCR-Texte und Bilder werden an OpenAI gesendet. OpenAI verarbeitet API-Daten standardmäßig **nicht** für Modelltraining (laut aktuellen OpenAI-API-Terms — Stand prüfen). Trotzdem: relevant für Auftragsverarbeitungsvertrag (AVV/DPA), wenn personenbezogene Daten enthalten sind (z.B. User-Handles in Captions).

**Empfehlung**: OpenAI-DPA aktivieren (gibt es im Account-Settings) und im DSGVO-Konzept dokumentieren (F0.7).

#### 4.4.4 DSGVO im engeren Sinn (F0.7)

Pflicht für ein produktiv betriebenes System mit personenbezogenen Daten Dritter:

- **Verarbeitungsverzeichnis** (Art. 30 DSGVO) — pflegen
- **Rechtsgrundlage** je Datenkategorie (Art. 6 DSGVO) — dokumentieren
- **Aufbewahrungsdauer** definieren (z.B. 12 Monate für Captions, 24 Monate für Asset-Bilder, danach Löschung/Anonymisierung)
- **Lösch-Workflow** (Diagnose §11 R18): Endpoint `POST /api/posts/{id}/delete-personal-data`, der `caption`, `external_id`, `Channel.handle` maskt; der Asset-Datensatz für das aggregierte Reporting bleibt erhalten
- **Auskunftsanspruch** Dritter: erst relevant wenn Anfragen kommen — Prozess intern dokumentieren

### 4.5 SLA/Verfügbarkeit der externen Abhängigkeiten

| Anbieter | Typische Verfügbarkeit | Was passiert bei Ausfall? |
|---|---|---|
| Apify | hoch (>99 %), aber gelegentlich Actor-Schemata-Änderungen | Apify-Monitor-Job logged Error, Retry beim nächsten Cron-Run |
| TMDb | sehr hoch | Title-Sync skippt, nächste Woche neu |
| OpenAI | hoch, gelegentlich Rate-Limits | Visual-Analyse-Job markiert Asset als `provider_error`, Retry-Loop mit exponential backoff |
| S3/R2 | sehr hoch | hart kritisch — Asset-Capture failt, Status `fetch_failed` |
| Railway | hoch | Service-Restart auto, Cron-Job wartet |

Soll-Verhalten: alle externen Calls müssen `try/except` mit explizitem Status auf dem Asset/Job. Heute teilweise gegeben (Visual-Analyse hat das, Apify hat das nicht in jeder Variante).

### 4.6 Cost-Observability (F0.6 + F1.17)

Damit Wolf das Hard-Cap aus 4.2 durchsetzen kann:

- **Apify**: nach jedem Run `usage`-Daten aus Apify-API holen (Run-Cost in CU/USD) und in Tabelle `apify_run_log` speichern.
- **OpenAI**: Token-Counts pro Vision/Text-Call loggen, Multiplikation mit Pricing-Konstante (im Code als `OPENAI_PRICE_PER_1K_INPUT/OUTPUT`-ENV).
- **Wochen-Dashboard** (P1): Endpoint `/api/insights/cost-overview` zeigt Apify+OpenAI-Cost letzten 7 Tagen. Frontend rendert.
- **Hard-Cap**: bei Überschreiten setzt der Cost-Cap-Service `APIFY_MONITOR_ENABLED=False` runtime und alarmiert (Sentry/E-Mail).

---

## 5. Sprint-Vorschlag — Erste 4 Wochen

Der Plan deckt einen **Solo-Founder-Rhythmus von 3–4 PT-S/Woche** neben dem Bestandsbetrieb ab. Sprint 8.2 läuft parallel weiter (Image-Proxy, Display-Image-Candidates, Evidence-Selector); der 4-Wochen-Plan **fasst die Sprint-8.2-Pfade nicht an** (`api/proxy.py`, `report_selector.py`, `report_renderer_v2.py`, `ImagePreview` im Frontend) und arbeitet stattdessen an Stabilisierung, DB- und Storage-Fundament. Die Logik ist bewusst **Stabilisierung zuerst, Produkt-Bausteine danach** — kein Big-Bang. Woche 1 räumt auf, Woche 2 schließt Sicherheitslücken, Woche 3 löst die Visual-Pipeline, Woche 4 trennt die DB und stabilisiert die Report-Persistenz.

### Woche 1: Stabilisierung & Sicherheitsnetze

**Ziel der Woche.** Bestehende stille Fehler stilllegen, Build-Reproduzierbarkeit herstellen und Konfigurations-Drift entfernen — damit ab Woche 2 die schwereren P0-Themen (Auth, Storage, DB-Trennung) auf einer aufgeräumten Basis aufsetzen.

**Tasks.**

- **F1.4 Insights-Counter-Bug fixen** — Status-Set `{"analyzed","text_only"}` → `{"done","text_fallback"}` in `services/insights.py:117`, Test ergänzen (Diagnose §1 Pkt. 4, §11 R5). **0,3 PT-S**
- **F1.12 Frontend-Deps pinnen** — `react`, `react-dom`, `vite`, `@vitejs/plugin-react` auf konkrete Major.Minor.Patch, `package-lock.json` committen, `.gitignore`-Eintrag entfernen (Diagnose §11 R8, §12 QW-2). **0,3 PT-S**
- **QW-3 Doppel-`netlify.toml`** — Root-Variante behalten, `frontend/netlify.toml` löschen (Diagnose §2 Deploy-Topologie). **0,1 PT-S**
- **QW-4 Drei `startCommand`-Quellen reduzieren** — `Dockerfile CMD ["./start.sh"]` als alleinige Quelle behalten, `railway.json`/`railway.toml` `startCommand` entfernen (Diagnose §2, §11 R9). **0,1 PT-S**
- **F2.17 `.env.example` synchron zur `Settings`-Klasse** — Apify-, TMDb-, Image-Proxy-, Storage-Variablen ergänzen (Diagnose §2 Konfiguration, §12 QW-8). **0,2 PT-S**
- **F2.15 Hygiene-Aufräumen** — Issues `#3`, `#7`, `#10`, `#12` mit Verweis auf jeweiligen Merge-Commit schließen; 17 `codex/*`- und 4 `feat/sprint-8-2*`-Branches nach Bestätigung gemergt löschen (Diagnose §3 Git-Aktivität, §12 QW-5/QW-6). **0,3 PT-S**
- **F2.14 (Teil-Scope) tote Pfade entfernen** — `app/jobs/*.py` Platzhalter, `app/data/channels_seed.json` (verwaist) und `app/prompts/*.md` (nicht im Code geladen) löschen oder im Code referenzieren (Diagnose §3 Tote Pfade). **0,5 PT-S**
- **Vorbereitung F1.14 Alembic** — Alembic in `requirements.txt` aufnehmen, `alembic init`, Baseline-Revision aus aktuellen SQLModel-Modellen erzeugen, **noch nicht aktivieren** (Migration-Switch erst Woche 4). **1 PT-S**
- **Sprint-8.2-Begleitung** — keine Code-Änderung, nur Lesen + Tests laufen lassen, falls Wolf einen 8.2-PR aufmacht. **0,3 PT-S Puffer**

**Aufwand gesamt:** ~3,1 PT-S — passt in die Wochen-Kapazität.

**Akzeptanzkriterien.**

- `pytest -q` läuft grün und enthält den neuen Test für `insights.visual_analyzed`.
- `npm run build` produziert ein Bundle mit gepinnten Versionen aus committedem `package-lock.json`.
- Repo enthält genau **eine** `netlify.toml` und genau **einen** `startCommand`-Pfad (Dockerfile-CMD).
- `.env.example` deckt alle in `app/config.py` definierten Settings ab; ein neuer Mitarbeiter kann ohne Rückfrage das Backend lokal starten.
- Issues `#3`, `#7`, `#10`, `#12` sind closed; Branchliste auf `main` + aktiver Branch + ggf. lebende Sprint-Branches reduziert.
- `app/jobs/`, `app/data/`, `app/prompts/` enthalten nur noch Code, der real geladen wird.
- Alembic ist installiert, Baseline existiert, aber `create_db_and_tables()` ist unverändert (kein Aktivieren in Woche 1).

**Risiken / Abhängigkeiten.**

- **Pinning kann Build-Brüche zeigen**, wenn die heute live gezogene `latest`-Version inkompatibel zu einer expliziten Version ist. Mitigation: Netlify-Preview-Deploy testen, bevor `main` gemergt.
- **Branch-Cleanup**: vor Löschen der `codex/*`-Branches Wolf-OK pro Sammel-Bestätigung einholen — siehe Schutzregel 6.3.
- **Alembic-Baseline auf der Live-DB**: nicht in Woche 1, aber vorzubereiten — Wolf-Entscheidung zu DB-Variante (A/B/C, Sektion 7) muss spätestens Mitte Woche 2 vorliegen, sonst rutscht F1.14/F0.2 in Woche 4.

### Woche 2: Asset-Capture-Persistenz (Fundament für Märkte-Vergleich)

**Ziel der Woche.** Asset-Bilder überleben Railway-Deploys und werden für die KI-Vision (und damit für DE/US-Vergleiche) verlässlich erreichbar — Voraussetzung dafür, dass Briefing-Abschnitt 3 (Asset-Capture, Märkte-Vergleich) überhaupt belastbar wird.

**Tasks.**

- **F0.1 Persistentes Asset-Storage** — `services/storage.py` als Adapter-Pattern einführen (`LocalFileStorage` für Tests, `S3Storage` für Production), `screenshot_capture.capture_asset_screenshot()` schreibt darüber. Bucket-Policy: privat mit Random-UUID-Pfad oder signed URL. Heutige `/storage/evidence/`-Dateien bleiben übergangsweise als Fallback lesbar. ENV-Variablen `STORAGE_BACKEND`, `S3_BUCKET`, `S3_REGION`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` ergänzen. (Diagnose §1 Pkt. 1, §2 Storage, §11 R1, Backlog F0.1.) **3 PT-S**
- **`SECURE_STORAGE_ENABLED` aktivieren — abgesichert** — erst wenn neue Captures erfolgreich auf Storage landen UND der Selector/Renderer-Test (`test_report_selector.py`) das `secure`-Klassifikationsverhalten dadurch nicht regressioniert. Sprint 8.2 `report_selector.py` und `report_renderer_v2.py` werden **nicht editiert**, nur via ENV gesetzt. (Diagnose §5.5, §11 R1.) **0,3 PT-S**
- **Backfill-Skript (einmalig, manuell ausgeführt)** — `scripts/backfill_evidence.py` durchläuft Assets mit existierender `screenshot_url`/`thumbnail_url`, lädt das Bild via Storage-Service, setzt `visual_evidence_url` auf die neue Storage-URL. Idempotent über `Asset.id` + Existenz-Check. **0,5 PT-S**
- **F2.18 Performance-Indizes als Alembic-Revision** — `post.detected_at`, `post.channel_id`, `asset.title_id`, `asset.review_status`, `asset.visual_analysis_status`. Revision in Woche 2 erstellt, **Migration-Run in Woche 4** mit der DB-Trennung. (Diagnose §4 Indizes, §12 QW-9.) **0,3 PT-S**
- **Sprint-8.2-Puffer** — wenn ein Sprint-8.2-Folge-PR aufgemacht wird, Reviewen ohne den Storage-Pfad anzufassen. **0,3 PT-S**

**Aufwand gesamt:** ~4,4 PT-S — am oberen Rand der Wochen-Kapazität, vertretbar weil F0.1 der zentrale Hebel ist.

**Akzeptanzkriterien.**

- `STORAGE_BACKEND=s3` in Production gesetzt; ein neuer Apify-Run produziert Assets, deren `visual_evidence_url` auf eine **vom Browser und von externen Diensten erreichbare URL** zeigt (HTTP 200 GET aus zwei Netzen geprüft).
- Nach einem Test-Redeploy auf Railway sind die in Woche 2 ge­cap­tu­red Bilder weiterhin abrufbar (kein 404).
- `pytest -q` deckt den Storage-Adapter ab: ein Test mit `LocalFileStorage` und ein Mock-Test mit `S3Storage` (Stubbed Client).
- Backfill-Skript wurde einmal gegen Production ausgeführt; Anzahl migrierter Assets im Skript-Output protokolliert.
- Alembic-Revision für Performance-Indizes existiert, **noch nicht angewendet** (markiert mit `# pending: deploy in Woche 4`).
- Sprint 8.2 ist unverändert: kein Diff in `api/proxy.py`, `report_selector.py`, `report_renderer_v2.py`, `frontend/src/App.jsx` (außer Bugfix-Hotfixes).

**Risiken / Abhängigkeiten.**

- **Wolf-Entscheidung zu Storage-Provider** (S3 / R2 / Backblaze B2 oder Railway-Volume) muss **vor Woche-2-Start** vorliegen — Go/No-Go-Punkt 7.D. Ohne Entscheidung blockiert F0.1.
- **Bestandsdaten-Größe unbekannt** (Diagnose §0 Disclaimer: keine Live-DB-Queries). Backfill-Aufwand kann höher liegen als 0,5 PT-S, falls bereits viele hundert Assets existieren — Skript dann in Chunks ausführen.
- **CDN-Hotlink-Block** der externen IG/TikTok-CDNs kann Backfill scheitern lassen; in dem Fall bleibt die heutige `screenshot_url` als Fallback im Frontend (Sprint 8.2-`display_image_candidates`-Logik trägt das bereits).
- **Visual-Pipeline (F0.4) ist noch nicht repariert** — die neue Storage-URL macht die Reparatur in Woche 3 erst möglich, ist aber für Woche 2 noch nicht wirksam. Status `done` zählt erst ab Woche 3.




