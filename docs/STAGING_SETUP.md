# Staging-Umgebung: Setup-Anleitung

Stand: 2026-08-06 · Umsetzung des Staging-Briefings (lokal + Cloud-Staging).
Entschieden: Railway-**Environment** im bestehenden Projekt (kein separates
Projekt), Staging-Backend unter **api-staging.creative-radar.de**, Testdaten
**synthetisch** via `seed_dev.py`.

Der Code-Anteil (Mock-Modus, Boot-Check, Seed-Skript, docker-compose,
Staging-Banner) liegt im Repo. Dieses Dokument ist die Klick-Anleitung für
die Cloud-Seite plus die Abnahme-Checkliste.

**Abweichung vom Briefing, bewusst:** Creative Radar hat keine
Social-OAuth-Flows (Scraping via Apify-Token, YouTube via API-Key). Der
Briefing-Schritt „Test-App-Registrierungen / OAuth-Callback-URLs" entfällt
ersatzlos. Der Mock-Modus (`MOCK_EXTERNAL_APIS`) deckt stattdessen die
kostenpflichtigen Scrape-Quellen ab; OpenAI/Anthropic bleiben in Staging
schlicht ohne Key (die Call-Sites degradieren sauber, Briefs kommen aus dem
Seed).

---

## 1. Lokale Entwicklungsumgebung (Mac) — „drei Befehle"

Voraussetzungen: Docker Desktop, Node 22, das Repo.

```sh
# 1 — Postgres 16 + Backend (Hot-Reload, Mock-APIs, Migrationen laufen automatisch)
docker compose up -d

# 2 — Synthetische Testdaten (idempotent: zweiter Lauf resettet)
docker compose exec backend python -m scripts.seed_dev

# 3 — Frontend (nativ, HMR; frontend/.env.development zeigt auf localhost:8000)
cd frontend && npm install && npm run dev
```

Danach: Frontend auf http://localhost:5173, API auf http://localhost:8000,
DB-Einsicht via Adminer auf http://localhost:8080 (Server `db`, User/Pass
`cr`/`cr`). Login lokal: beliebige E-Mail als User anlegen? Nein — Dev-Login
läuft über `dev@example.com` (ist als `ADMIN_USER_EMAILS` gesetzt); da
`DISABLE_EMAILS=true`, steht der Login-Code im Backend-Log (außerhalb von
`APP_ENV=production` loggt der Mailer den Mail-Text mit):
`docker compose logs backend | grep mailer.disabled.body`. Vorher den User
einmalig anlegen (Adminer, Tabelle `creative_radar.app_user`, oder via
Admin-API).

Kein einziges echtes Secret nötig. Alles läuft offline.

**Postgres-Version:** compose nutzt `postgres:16-alpine`. Einmalig prüfen,
dass Railway dieselbe Major-Version fährt: Railway → Postgres → Data →
Query: `SELECT version();` — falls dort 17.x steht, in
`docker-compose.yml` auf `postgres:17-alpine` wechseln.

## 2. Branch-Modell

```
feature/*  →  staging (Auto-Deploy Cloud-Staging)  →  main (Auto-Deploy Prod)
```

Den `staging`-Branch einmalig von `main` erzeugen (GitHub → Branches → New
branch, Quelle `main`), **nachdem** dieser PR gemerged ist — sonst fehlen
dem Staging-Deploy Mock-Modus und Boot-Check.

## 3. Railway: Staging-Environment

1. Railway-Projekt → **Settings → Environments → New Environment** →
   Name `staging` (dupliziert die Service-Struktur von production).
2. Im `staging`-Environment eine **neue Postgres-Instanz** provisionieren
   (+ New → Database → PostgreSQL). Nicht die bestehende Instanz — die ist
   mit Prod (und ki-sicherheit.jetzt) geteilt. Schema/Tabellen entstehen
   automatisch beim ersten Deploy (`alembic upgrade head` läuft als
   preDeployCommand).
3. Backend-Service im `staging`-Environment: **Deploy-Trigger auf den
   Branch `staging`** stellen (Settings → Source → Branch).
4. Variablen setzen — Matrix unten. Startpunkt: Prod-Variablen kopieren,
   dann die Staging-Zeilen ÜBERSCHREIBEN und die „weglassen"-Zeilen
   LÖSCHEN.

### Variablen-Matrix (Backend, staging-Environment)

| Variable | Wert in Staging | Warum |
|---|---|---|
| `APP_ENV` | `staging` | aktiviert den Boot-Check |
| `STAGING_EXPECTED_DB_HOST` | Host der neuen Staging-Postgres (aus deren `DATABASE_URL`, z. B. `postgres-xyz.railway.internal`) | Boot verweigert sonst — Schutz gegen kopierte Prod-DB-URL |
| `DATABASE_URL` | Reference auf die **Staging**-Postgres | nie die Prod-DB |
| `MOCK_EXTERNAL_APIS` | `true` | Scrape läuft gegen Fixtures, 0 € |
| `ENABLE_INTERNAL_CRON_SCHEDULER` | weglassen oder `false` | ohne ENV ist der Scheduler außerhalb production automatisch aus; explizit `false` schadet nicht |
| `APIFY_API_TOKEN`, `YOUTUBE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `TMDB_API_KEY` | **löschen / leer** | kein Prod-Secret in Staging (DoD 3); Mock/Degradation übernimmt |
| `RESEND_API_KEY` | löschen, stattdessen `DISABLE_EMAILS=true` — oder eigener Resend-Key, falls Login-Mails in Staging echt sein sollen | Login-Codes stehen bei `DISABLE_EMAILS` im Deploy-Log |
| `CORS_ORIGINS` | `https://staging.creative-radar.de` | Staging-Frontend |
| `FRONTEND_URL` | `https://staging.creative-radar.de` | Links in Mails/Redirects |
| `BACKEND_URL` | `https://api-staging.creative-radar.de` | |
| `API_TOKEN` + Session-Secrets (`ADMIN_SESSION_SECRET`, `USER_SESSION_SECRET`) | **neu würfeln** (`openssl rand -hex 32`) | Staging-Tokens dürfen nie Prod-Zugriff geben |
| `ADMIN_USER_EMAILS` | wie Prod (wolf@trailerhaus.de) | |
| S3-Variablen | weglassen (`STORAGE_BACKEND=local`) oder eigener Staging-Bucket | keine geteilte Ressource |

5. **Custom Domain:** Backend-Service (staging) → Settings → Networking →
   Custom Domain → `api-staging.creative-radar.de`. Railway zeigt den
   CNAME-Zielwert an.

## 4. DNS (united-domains)

Zwei CNAMEs, gleiche Übung wie damals bei `api.creative-radar.de`:

| Subdomain | Typ | Ziel |
|---|---|---|
| `api-staging` | CNAME | Wert aus Railway (Schritt 3.5) |
| `staging` | CNAME | Wert aus Netlify (Schritt 5.4) |

## 5. Netlify: Staging-Site

1. **Add new site → Import from Git** → dieses Repo → Site-Name
   `creative-radar-staging`. **Branch to deploy: `staging`.**
2. Build-Command in den Site-Settings überschreiben:
   `cd frontend && npm i && npm run build && npm run postbuild:staging`
   (Publish directory `frontend/dist`). Der Zusatzschritt schreibt
   `_headers` mit `X-Robots-Tag: noindex` + eine Disallow-`robots.txt` —
   nur für diese Site; die geteilte `netlify.toml` und Prod bleiben
   unberührt.
3. Environment variables (Site-Settings):
   - `VITE_API_BASE=https://api-staging.creative-radar.de`
   - `VITE_API_TOKEN=<der neue Staging-API_TOKEN aus Schritt 3>`
   - `VITE_ENVIRONMENT=staging` → gelber Banner „STAGING — Testdaten"
4. Domain `staging.creative-radar.de` hinzufügen (Domain management),
   CNAME siehe Schritt 4.
5. Zugriffsschutz: primär gilt der App-eigene E-Mail-Code-Login (Staging
   hat eine eigene `app_user`-Tabelle — nur dort angelegte Adressen kommen
   rein). Falls der Netlify-Plan Password Protection / Basic Auth hergibt:
   zusätzlich aktivieren. Banner + noindex + eigener User-Kreis sind die
   Grundsicherung.

## 6. Staging-DB befüllen

Einmalig nach dem ersten Deploy (Railway CLI, im staging-Environment):

```sh
railway run --environment staging python -m scripts.seed_dev
```

Alternativ einen Cron-freien One-off-Deploy nutzen. Das Skript verweigert
bei `APP_ENV=production` — versehentlich gegen Prod laufen geht nicht.

## 7. Abnahme-Checkliste (DoD aus dem Briefing)

- [ ] **0. Lokal:** `docker compose up` + Seed + `npm run dev` = lauffähig,
      offline, mit Testdaten und Mock-APIs (drei Befehle, Abschnitt 1).
- [ ] **1.** Push auf `staging` deployt Staging-Frontend + -Backend
      automatisch; Prod-Deploys hängen weiter nur an `main`.
- [ ] **2.** Staging-Frontend spricht mit Staging-Backend/-DB: gelber
      Banner sichtbar; Railway-Logs zeigen den Boot ohne
      `Boot verweigert`-Fehler (d. h. der Guard hat den Staging-DB-Host
      bestätigt). Gegenprobe: `STAGING_EXPECTED_DB_HOST` testweise
      verfälschen → Deploy muss beim Boot abbrechen.
- [ ] **3.** Variablen-Audit: kein Prod-Secret im staging-Environment
      (Matrix oben durchgehen, insbesondere API-Keys und Session-Secrets).
- [ ] **4.** Scrape gegen Mocks, per Log verifiziert: Cron-Lauf manuell
      auslösen (Admin-Button oder `POST /api/admin/cron/sync-all`) und im
      Log `apify-mock`/`youtube-mock` sehen — keine `api.apify.com`- oder
      `googleapis.com`-Zugriffe.
- [ ] **5.** `staging.creative-radar.de` antwortet mit
      `X-Robots-Tag: noindex` (`curl -sI https://staging.creative-radar.de | grep -i robots`)
      und Zugriff auf Inhalte erfordert Login.
- [ ] **6.** `seed_dev` zweimal ausführen → gleiche Zeilenzahlen, keine
      Duplikate (Post-Anzahl in Adminer/Railway-Query vergleichen).
- [ ] **7.** Rollback geprobt: absichtlich kaputter Commit auf `staging`
      (z. B. Syntaxfehler) → Staging-Deploy failt, Prod unberührt; Commit
      revertieren.

## 8. Grenzen, bewusst akzeptiert

- Lokal: keine Team-Erreichbarkeit, keine echten Domains/Header — dafür
  ist die Cloud-Staging da.
- Staging: LLM-Stufen (Briefs, Vision) laufen ohne Keys im Skip-Modus;
  Brief-Inhalte kommen aus dem Seed. Wenn ein Feature-Test echte
  LLM-Ausgaben braucht, temporär einen eigenen (nicht Prod-) Key setzen
  und die Budget-Caps (`ANTHROPIC_MONTHLY_BUDGET_USD` etc.) eng stellen.
- Die Fixtures sind deterministisch pro Handle, aber Timestamps sind
  relativ zu „jetzt" — Muster stabil, exakte Werte nicht byte-identisch
  über Tage.
