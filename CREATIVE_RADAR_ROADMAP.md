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

