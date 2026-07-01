# Projekt-Audit: Creative Radar — 2026-07-01

Analyse-Modus: Code-Review am tatsächlichen Stand von `main`/`claude/new-session-nscarp` (Commit `7cab306`), kein Zugriff auf Live-DB oder Railway-ENV. Wo eine Aussage von der tatsächlichen Produktions-ENV-Konfiguration abhängt (die von hier aus nicht einsehbar ist), ist das explizit vermerkt.

**Kontext-Hinweis:** Das Repo enthält bereits mehrere frühere Audits (`CREATIVE_RADAR_DIAGNOSIS.md`, Stand 30.04., und `creative-radar-bughunt-premortem-2026-05-17.md`). Beide sind inzwischen teilweise überholt — z. B. ist der dort bemängelte tote `visual_analyzed`-Counter (`insights.py:116`) längst korrigiert, ebenso "keine Authentifizierung" (Bearer-Auth + Admin-Session existieren jetzt). Dieser Report verifiziert unabhängig am aktuellen Code, nicht an den alten Diagnosen.

---

## Hoch

### H1. CORS: `allow_credentials=True` + Default-Origin `"*"`
**Datei:** `backend/app/config.py:45` (`cors_origins: str = "*"`), `backend/app/main.py:34-52`, `.env.example:29`

Die CORS-Middleware ist mit `allow_credentials=True` konfiguriert (nötig für das HttpOnly-Admin-Session-Cookie über die Subdomain-Grenze `app.creative-radar.de` → `api.creative-radar.de`). Der Origin-Default ist `"*"`. Starlettes `CORSMiddleware` **spiegelt bei `allow_origins=["*"]` den tatsächlichen Request-Origin zurück statt `"*"` zu senden**, sobald `allow_credentials=True` gesetzt ist — das ist genau die im Audit-Auftrag beschriebene Lücke, nur mit literalem `"*"` statt einem Subdomain-Wildcard-Pattern. Ist `CORS_ORIGINS` in Railway nicht explizit auf `https://app.creative-radar.de` gesetzt, kann **jede beliebige Website** einen `fetch(..., {credentials: 'include'})`-Call gegen die API machen und das Admin-Session-Cookie wird mitgeschickt — effektiv Session-Riding auf alle Admin-Endpunkte.

Der Code-Kommentar in `main.py:37-48` zeigt, dass das Team sich des Risikos bewusst ist und für Production einen festen Origin erwartet — das ist aber eine **Betriebs-Annahme, keine Code-Garantie**. Ob Railway aktuell wirklich `CORS_ORIGINS=https://app.creative-radar.de` gesetzt hat, kann von hier aus nicht geprüft werden.

**Sofort zu tun:** In Railway das aktuelle `CORS_ORIGINS` prüfen. Falls `*` oder leer: sofort auf `https://app.creative-radar.de` setzen. Den Code-Default habe ich in diesem Audit bereits von `*` auf den festen Produktions-Origin geändert (siehe Fix-Sektion) — das ist wirkungslos, falls Railway die ENV explizit überschreibt, schließt die Lücke aber ab, falls die ENV nie gesetzt wurde.

### H2. Bekannte CVEs in Starlette 0.41.3 und python-multipart 0.0.20
**Datei:** `backend/requirements.txt` (fastapi 0.115.6 zieht starlette 0.41.3; `python-multipart==0.0.20`)

`pip-audit` meldet 16 bekannte Schwachstellen in 4 Paketen:

| Paket | Version | CVEs | Fix-Version |
|---|---|---|---|
| starlette | 0.41.3 | PYSEC-2026-161 (×2), PYSEC-2026-249, PYSEC-2026-248, CVE-2025-54121, CVE-2025-62727, CVE-2026-48818, CVE-2026-48817 | 1.0.1 / 1.1.0 / 1.3.x |
| python-multipart | 0.0.20 | CVE-2026-24486, CVE-2026-40347, CVE-2026-42561, CVE-2026-53538/39/40 | 0.0.22 – 0.0.31 |
| jinja2 | 3.1.5 | CVE-2025-27516 | 3.1.6 |
| python-dotenv | 1.0.1 | CVE-2026-28684 | 1.2.2 |

Starlette und python-multipart sitzen im Request-Parsing-Pfad **jedes** eingehenden Requests bzw. jedes Datei-Uploads (`POST /api/channels/import-excel` nutzt `UploadFile`, also multipart-Parsing) — das ist die breiteste Angriffsfläche im Projekt. Ein Upgrade auf eine aktuelle FastAPI-Version (die eine gefixte Starlette zieht) ist nötig, aber ein Versionssprung von FastAPI 0.115 kann Breaking Changes mit sich bringen — nicht blind machen, siehe Umsetzungsvorschlag unten.

### H3. `npm audit`: 1 High-Finding in Vite 8.0.10 (Dev-Server, nicht Produktion)
**Datei:** `frontend/package.json`

`vite@8.0.10` hat einen gemeldeten High-Severity-Fund: NTLMv2-Hash-Leak über `launch-editor` (Windows-only, nur im Dev-Server relevant) und einen `server.fs.deny`-Bypass unter Windows. **Beide betreffen ausschließlich den lokalen Dev-Server (`vite dev`)**, nicht den über Netlify ausgelieferten statischen Build (`vite build` erzeugt reines HTML/JS/CSS ohne den Dev-Server-Code). Die *tatsächlich* live ausgelieferte Seite ist davon nicht betroffen — ich stufe das trotz "High" im npm-Advisory als **Niedrig** für dieses Projekt ein, siehe dort. `npm audit fix --force` würde auf `vite@8.1.2` heben (außerhalb des aktuell deklarierten Ranges) — sollte vor dem Fix kurz gegen den Dev-Workflow getestet werden.

---

## Mittel

### M1. Keine Security-Headers
**Datei:** kein Treffer in `backend/app` oder `frontend/` — es existiert keine Middleware und keine `_headers`-Datei für Netlify.

`X-Content-Type-Options`, `X-Frame-Options`/`frame-ancestors`, `Referrer-Policy` und eine CSP fehlen vollständig auf Backend- und Frontend-Antworten. Da das Admin-Login-Formular und die Report-HTML-Downloads (`/api/reports/latest/download.html`) öffentlich erreichbar sind, ist v. a. Clickjacking-Schutz (`X-Frame-Options: DENY` bzw. CSP `frame-ancestors 'none'`) sinnvoll.

### M2. Kein Rate-Limiting — weder auf teuren KI-/Scraper-Endpoints noch auf dem Admin-Login
**Dateien:** global (kein `slowapi`/Ratelimit-Import in `backend/app`), konkret: `backend/app/api/admin.py:965` (`/api/admin/login`), `backend/app/api/monitor.py` (Apify-Trigger), `backend/app/api/assets.py:204` (`analyze-visual-batch`)

Es gibt **keinerlei** Rate-Limiting im gesamten Backend. Zwei konkrete Konsequenzen:
- `/api/admin/login` prüft das Passwort zwar konstant-zeitig (`secrets.compare_digest`, `admin_session.py:116`), aber ohne Lockout/Throttling kann ein Angreifer beliebig viele Passwörter pro Sekunde durchprobieren.
- Kostenpflichtige Endpunkte (`/api/monitor/apify-instagram`, `/api/assets/analyze-visual-batch`, `/api/reports/generate-weekly`) haben nur die nachgelagerten Monats-Budget-Caps (`apify_monthly_budget_usd`, `anthropic_monthly_budget_usd`) als Bremse — die greifen erst, wenn das Monatsbudget real aufgebraucht ist, verhindern aber keinen kurzfristigen Burst (z. B. 500 Requests in einer Minute, falls der Bearer-Token durchsickert).

Aktuell relevant nur, falls `AUTH_ENABLED`/`ADMIN_AUTH_ENABLED` aktiv sind (beide Defaults `false`) — sonst sind die Endpunkte ohnehin offen. Falls Auth schon produktiv scharf ist, ist das Fehlen von Rate-Limiting die nächstliegende Lücke.

### M3. Image-Proxy folgt Redirects ohne erneute Host-Prüfung (potenzieller SSRF-Umweg)
**Datei:** `backend/app/api/proxy.py:44-56`

`_host_is_allowed()` prüft nur den **initialen** Hostnamen aus dem Query-Parameter `url` gegen die Allowlist (`cdninstagram.com`, `fbcdn.net`, `tiktokcdn*.com`). Der `httpx.AsyncClient` wird aber mit `follow_redirects=True` erstellt (Zeile 53) — ein 3xx-Redirect von einem erlaubten Host auf einen beliebigen anderen Host (auch internes Netz/Metadata-Endpoint) würde nicht erneut gegen die Allowlist geprüft. Praktisch begrenzt der Impact sich darauf, dass die drei erlaubten CDN-Hosts kein offenes Redirect anbieten dürften — aber das ist eine Annahme über Drittanbieter-Verhalten, keine Kontrolle im eigenen Code.

### M4. `.env.example` ist stark veraltet — 33 tatsächlich gelesene Settings fehlen
**Datei:** `.env.example` vs. `backend/app/config.py`

Ein Abgleich aller `Settings`-Felder gegen `.env.example` zeigt u. a. folgende **fehlenden** Einträge: `ANTHROPIC_API_KEY`, `ANTHROPIC_HAIKU_MODEL`, `ANTHROPIC_SONNET_MODEL`, `ANTHROPIC_OPUS_MODEL` (+ alle zugehörigen Preis-Konstanten), `YOUTUBE_API_KEY`, `ADMIN_PASSWORD`, `ADMIN_SESSION_SECRET`, `ADMIN_AUTH_ENABLED`, `CRON_API_TOKEN`, alle `APIFY_*_BUDGET_*`/`ANTHROPIC_*_BUDGET_*`-Caps, die Cutter-Weekly-Schwellenwerte u. a. — insgesamt 33 Felder (vollständige Liste in der PR-Beschreibung). Zusätzlich liest der Code an 6 Stellen direkt `os.environ.get(...)` **außerhalb** der pydantic-`Settings`-Klasse (`CRON_A_CLASS_THRESHOLD`, `CRON_A_CLASS_MAX`, `CRON_SYNC_INTERVAL_DAYS`, `CRON_RUN_TIMEOUT_MINUTES`, `TITLE_SYNC_STAGE_TIMEOUT_SECONDS`, `PG_STATEMENT_TIMEOUT_MS`) — auch diese fehlen in `.env.example`.

Wer eine neue Umgebung nach `.env.example` aufsetzt, übersieht damit z. B. die komplette Anthropic-Integration (Vision/Brief-Gen liefe dann mit leerem API-Key ins Leere) und den Admin-Login (fail-closed → 503, also kein offenes Scheunentor, aber ein verwirrender Ausfall ohne ersichtlichen Grund).

### M5. KI-Modell-Defaults sind eine Generation hinter dem aktuellen Stand
**Datei:** `backend/app/config.py:79-90`

`anthropic_sonnet_model: str = "claude-sonnet-4-6"` und `anthropic_opus_model: str = "claude-opus-4-7"` sind laut aktueller Anthropic-Modellübersicht nicht mehr die neuesten Versionen (aktuell: Sonnet 5 / Opus 4.8) — nicht akut kaputt (keine Deprecation dieser konkreten IDs gefunden, nur der ältere `claude-sonnet-4-0`/`claude-opus-4-0` wird zum 15.06.2026 abgeschaltet, davon ist dieses Projekt nicht betroffen), aber der Code-Kommentar in `config.py:74-76` ("die Alias-Form ... trackt automatisch das neueste 4.5/4.6-Release") ist selbst schon veraltet. `openai_model: str = "gpt-4o-mini"` ist über die OpenAI-API (nicht Azure) nach aktueller Recherche weiterhin verfügbar, aber Teil der älteren GPT-4o-Generation, die OpenAI in ChatGPT/Azure bereits zurückzieht — Update in absehbarer Zeit einplanen. Beide Modell-IDs sind bereits ENV-konfigurierbar (`config.py`), die im Audit-Auftrag geforderte Absicherung ("per ENV-Variable konfigurierbar machen") ist also **schon vorhanden** — hier fehlt nur das Nachziehen der Default-Werte, was ich nicht blind gemacht habe, weil `.env.example`/Railway die Modelle vermutlich nicht explizit überschreiben (siehe M4) und ein Sprung auf ein neueres Modell die tatsächlichen Kosten/Antwortqualität ändert — das ist eine Produktentscheidung, keine Nebenläufigkeits-Korrektur.

---

## Niedrig

### N1. Kein Node-Version-Pin für den Netlify-Build
**Datei:** `netlify.toml`, `frontend/package.json` (kein `engines`-Feld)

Weder `netlify.toml` noch `package.json` legen eine Node-Version fest. Netlifys Default-Node-Version kann sich ändern und den Build unbemerkt brechen, insbesondere weil `vite@8`/`react@19` eine relativ neue Node-Version voraussetzen. Empfehlung: `NODE_VERSION` in `netlify.toml` oder `engines.node` in `package.json` explizit auf die aktuell funktionierende Version pinnen — ich habe das nicht selbst gesetzt, weil ich die tatsächlich von Netlify aktuell verwendete Node-Version nicht verifizieren kann und ein falscher Pin den Live-Build bricht (das fällt unter "potenzieller Breaking-Change für die Live-Seite").

### N2. Kein Lint-Setup
Weder Backend (`ruff`/`flake8`/`pyproject.toml`) noch Frontend (`.eslintrc*`) haben eine Lint-Konfiguration oder ein `npm run lint`-Script. Kein Bug, aber eine fehlende Qualitätsschranke bei einem Projekt dieser Größe (67 Test-Dateien, > 100 Backend-Module).

### N3. API-Response-Formate uneinheitlich
**Beispiele:** `GET /api/health` → `{"status", "service", "version"}`; `GET /api/assets`, `/api/channels`, `/api/titles`, `/api/reports` → nackte Listen/Objekte ohne Hülle; `POST /api/reports/suggest` → Pydantic-Response-Model.

Eine echte Inkonsistenz, aber eine Vereinheitlichung (z. B. alle Listen in `{"items": [...], "success": true}` verpacken) würde laut Code-Review von `frontend/src/api/client.js` **direkt an mehreren Stellen brechen**, weil das Frontend z. B. `endpoints.assets()` als nacktes Array konsumiert. Ich habe daher **nur** eine rein additive, garantiert unschädliche Ergänzung vorgenommen: `GET /api/health` bekommt jetzt zusätzlich ein `timestamp`-Feld (reine Ergänzung, keine bestehende Konsumenten-Logik verändert sich). Eine vollständige Vereinheitlichung der Listen-Endpunkte sollte **nicht** ungefragt gemacht werden — das ist ein Format-Change, der Frontend und ggf. externe Konsumenten trifft.

### N4. Pagination funktioniert korrekt — keine Logikfehler gefunden
Stichprobenprüfung von `limit`/`offset` in `assets.py`, `admin.py`, `cron.py`: überall wird der Parameter tatsächlich auf die Query angewendet (`.limit()`/`.offset()`), keine still ignorierten Pagination-Parameter gefunden. `GET /api/posts` hat keine Pagination (liefert immer alles) — kein Bug, aber bei wachsender Datenmenge ein Performance-Risiko, keine Korrektheitslücke.

### N5. `trend_summary_de` als Doppel-Zweck-Feld (Metadaten-Overload)
**Datei:** `backend/app/api/reports.py:69-70, 176`

`WeeklyReport.trend_summary_de` wird zugleich als Klartext-Zusammenfassung *und* als Container für `[report_type] asset_ids=...`-Metadaten benutzt (Präfix-geparst beim Lesen). Funktioniert, ist aber fragil — sobald jemand das Feld direkt per SQL/Admin-UI editiert, kann der Download-Endpoint darauf brechen. Bereits in der alten Diagnose vom 30.04. angemerkt und seither nicht refactored; kein akuter Bug, reine Robustheitsschwäche.

### N6. Datei-Upload ohne explizites Größenlimit
**Datei:** `backend/app/api/channels.py:70-73` (`import_excel`)

`await file.read()` lädt die komplette Datei in den Speicher, ohne eigenes `Content-Length`-Limit (im Unterschied zum Image-Proxy, der `image_proxy_max_bytes` durchsetzt). Nur relevant, falls der Endpoint erreichbar ist (Bearer-Auth vorausgesetzt, falls aktiv) — dann aber ein einfacher Memory-DoS-Vektor über eine sehr große Datei.

### N7. Keine Secrets im Code oder in der bisherigen Git-Historie gefunden
Gezielte Suche nach typischen Mustern (`sk-...`, AWS-Keys, hartkodierte Tokens) in `git log -p --all` ergab keine Treffer. `.env`/`.env.local` sind in `.gitignore` erfasst.

---

## Was ich bereits geprüft und für unauffällig befunden habe (explizit negativ)

- **Timing-safe Vergleiche:** Bereits durchgängig mit `hmac.compare_digest`/`secrets.compare_digest` umgesetzt (`app/auth.py:111`, `app/admin_session.py:96,116`) — nichts zu tun.
- **CSV-Formula-Injection:** Es gibt keinen CSV-Export mit nutzergesteuerten Daten im Code (nur CSV-*Import*-Skripte in `scripts/`, keine Export-Route) — nicht anwendbar.
- **Allowlist für Client-Parameter:** `asset_type`/`asset_type_hint` sind typisierte Pydantic-Enums (`AssetType`), FastAPI validiert automatisch gegen die Enum-Werte (422 bei Ungültigem) — kein Allowlist-Gap.
- **TLS/DB `rejectUnauthorized`-Äquivalent:** Kein hartcodiertes `sslmode=disable`/`verify=False` gefunden; die Postgres-Verbindung nutzt Standard-libpq-Verhalten plus explizite `connect_timeout`/Keepalives/`statement_timeout` (guter, bereits dokumentierter Trade-off in `database.py:66-113`).
- **Tote/dupliziert wirkende Dateien:** `insights.py` vs. `insight_engine.py` und `report_generator.py` vs. `report_renderer_v2.py` sehen wie Duplikate aus, sind aber beide aktiv importiert und bedienen unterschiedliche Pfade (`api/insights.py:55`, `api/reports.py:15-16`) — nicht tot.
- **Frontend-Dateien:** Alle `.jsx`-Dateien unter `frontend/src` sind transitiv von `App.jsx` erreichbar — keine verwaisten Dateien.
- **Tests:** `1120 passed, 10 skipped` (SQLite-Fallback-Modus, `pytest -q`), keine Fehlschläge.
- **Runtime-Version Backend:** `python:3.12-slim` (Dockerfile) ist eine aktuelle, unterstützte Python-Version — kein Handlungsbedarf.
- **Netlify-Redirect `/api/*` → `api.creative-radar.de`:** existiert in `netlify.toml`, wird vom Frontend aber nicht genutzt (`client.js` ruft die absolute Produktions-URL direkt auf, per Design wegen des Cross-Subdomain-Cookies — siehe Kommentar in `main.py:37-48`). Der Redirect ist funktional totes Config, aber bewusst so (keine Lösch-Empfehlung ohne Rückfrage, da Netlify-Config).

---

## Priorisierter Umsetzungsvorschlag

1. **Sofort, ohne Code-Änderung:** In Railway prüfen, ob `CORS_ORIGINS` tatsächlich auf `https://app.creative-radar.de` gesetzt ist (H1). Falls nicht: setzen.
2. **Kurzfristig:** FastAPI/Starlette/python-multipart/jinja2/python-dotenv gezielt anheben (H2) — mit Test-Lauf danach, nicht `pip install -U` über alles.
3. **Kurzfristig:** Security-Headers-Middleware ergänzen (M1) — geringer Aufwand, kein Breaking-Change-Risiko.
4. **Mittelfristig:** Rate-Limiting zumindest auf `/api/admin/login` und den teuren KI-/Scraper-Routen (M2) — Design-Entscheidung (in-memory reicht für Single-Instance-Railway-Deploy, aber bitte explizit gegenprüfen, ob mehrere Instanzen laufen).
5. **Mittelfristig:** Image-Proxy-Redirects erneut gegen die Allowlist prüfen oder `follow_redirects=False` setzen (M3).
6. **Jederzeit, risikofrei:** `.env.example` vervollständigen (M4) — bereits in diesem PR erledigt.
7. **Nach Rücksprache:** KI-Modell-Defaults auf die aktuellen Versionen heben (M5) — abhängig davon, ob Railway die Modelle schon per ENV überschreibt (bitte gegenprüfen) und ob die Preis-Konstanten mit-aktualisiert werden sollen.
8. **Nach Bedarf:** Node-Version-Pin (N1), Lint-Setup (N2), Upload-Größenlimit (N6).

---

## Bereits umgesetzt in diesem PR (risikoarm, siehe "Was du direkt umsetzen darfst")

- `backend/app/config.py`: `cors_origins`-Default von `"*"` auf `"https://app.creative-radar.de"` geändert (H1). Wirkungslos, falls Railway die ENV bereits explizit setzt; schließt andernfalls die Wildcard-Lücke.
- `.env.example`: alle 33 fehlenden Settings-Felder ergänzt (inkl. Anthropic/YouTube/Admin-Auth/Budget-Caps) sowie die 6 direkt per `os.environ` gelesenen Cron-/DB-Timeout-Variablen; MVP-Hinweis auf `CORS_ORIGINS=*` entfernt (M4).
- `backend/app/api/health.py`: `GET /api/health` liefert zusätzlich ein `timestamp`-Feld (rein additiv, N3).

Nicht angetastet: Dependency-Upgrades, Rate-Limiting, Security-Headers, Image-Proxy-Redirect-Verhalten, Modell-Default-Versionen, Node-Pin — diese benötigen entweder eine Produktentscheidung oder bergen ein reales Breaking-Change-Risiko und sind daher zur Bestätigung oben aufgeführt statt umgesetzt.
