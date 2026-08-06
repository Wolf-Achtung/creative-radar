# Staging-Abnahme — Klick-Checkliste

Reine Handanweisung, ohne Begründungen. Warum das alles so gebaut ist, steht in
[`STAGING_SETUP.md`](./STAGING_SETUP.md) — hier geht es nur ums Abarbeiten.

**Zeitbedarf:** 30–45 Minuten, davon ~10 Minuten Warten auf DNS und Deploys.
**Voraussetzung:** nur der Browser. Kein Terminal, kein Docker.

Du kannst nach jedem Block aufhören und später weitermachen. Die Blöcke bauen
aufeinander auf, aber jeder ist für sich abgeschlossen.

---

## Block 1 — `staging`-Branch aktualisieren (2 Min)

Der Branch existiert schon, hängt aber **4 Commits hinter `main`**. Ohne Update
fehlen dem Staging-Deploy unter anderem die Post-Analyse-Stage und der
Trailer-Patterns-Endpunkt.

- [ ] GitHub → creative-radar → **Pull requests** → **New pull request**
- [ ] `base: staging` ← `compare: main`
- [ ] **Create pull request** → Titel egal (z. B. „staging auf main heben") → **Merge**

> Kein Review nötig, das ist ein reiner Vorwärts-Merge.

---

## Block 2 — Railway: Environment `staging` anlegen (5 Min)

- [ ] Railway → Projekt **overflowing-unity** → oben auf **production** klicken →
      **New Environment**
- [ ] Name: `staging`, Quelle: **Duplicate from production**
- [ ] Warten, bis die Services erscheinen (Postgres + Backend)

**Prüfen:** Im `staging`-Environment muss eine **eigene** `postgres-creative-radar`
stehen. Wenn ja: gut, das ist eine leere Instanz mit eigenem Volume. Falls keine da
ist: **+ New → Database → PostgreSQL**.

- [ ] Backend-Service (staging) → **Settings → Source → Branch** auf `staging` stellen

---

## Block 3 — Variablen setzen (10 Min, der wichtigste Block)

Backend-Service im **staging**-Environment → **Variables**.

### 3a — Diese sechs Variablen LÖSCHEN

Das Duplikat bringt die Produktions-Zugangsdaten für den S3-Bucket mit. Bleiben sie
stehen, schreibt Staging in den Produktions-Bucket.

- [ ] `S3_ACCESS_KEY_ID`
- [ ] `S3_SECRET_ACCESS_KEY`
- [ ] `S3_BUCKET`
- [ ] `S3_ENDPOINT_URL`
- [ ] `S3_REGION`
- [ ] `STORAGE_BACKEND`

### 3b — Diese API-Keys LÖSCHEN

- [ ] `APIFY_API_TOKEN`
- [ ] `YOUTUBE_API_KEY`
- [ ] `OPENAI_API_KEY`
- [ ] `ANTHROPIC_API_KEY`
- [ ] `TMDB_API_KEY`
- [ ] `TMDB_READ_ACCESS_TOKEN`
- [ ] `RESEND_API_KEY`
- [ ] `ENABLE_INTERNAL_CRON_SCHEDULER` (falls vorhanden)

### 3c — Diese Werte SETZEN oder ÜBERSCHREIBEN

| Variable | Wert |
|---|---|
| `APP_ENV` | `staging` |
| `MOCK_EXTERNAL_APIS` | `true` |
| `DISABLE_EMAILS` | `true` |
| `DOCS_PUBLIC` | `true` |
| `CORS_ORIGINS` | `https://staging.creative-radar.de` |
| `FRONTEND_URL` | `https://staging.creative-radar.de` |
| `BACKEND_URL` | `https://api-staging.creative-radar.de` |
| `SEED_DEV_ON_DEPLOY` | `true` |

### 3d — Diese zwei brauchen einen Blick

- [ ] `DATABASE_URL` → muss **`${{postgres-creative-radar.DATABASE_URL}}`** sein
      (eine Service-Referenz, kein getippter String)
- [ ] `STAGING_EXPECTED_DB_HOST` → Wert aus **Postgres-Service (staging) → Variables →
      `RAILWAY_PRIVATE_DOMAIN`**. Nur der Host, typisch
      `postgres-creative-radar.railway.internal`. Kein `postgresql://`, kein Port.

### 3e — Drei neue Geheimnisse

`API_TOKEN`, `ADMIN_SESSION_SECRET` und `USER_SESSION_SECRET` müssen **neu gewürfelt**
werden — Staging-Tokens dürfen nie Prod-Zugriff geben. Die Werte bekommst du separat
im Chat (nicht ins Repo).

- [ ] `API_TOKEN` überschreiben
- [ ] `ADMIN_SESSION_SECRET` überschreiben
- [ ] `USER_SESSION_SECRET` überschreiben
- [ ] `ADMIN_USER_EMAILS` → wie Prod lassen (wolf@trailerhaus.de)

### Kontrolle

Nach dem Deploy im **Deploy-Log** die erste Zeile suchen:

```
startup.resolved_config app_env=staging mock_external_apis=True cron_scheduler=off storage_backend=local …
```

- [ ] `app_env=staging` ✅
- [ ] `mock_external_apis=True` ✅
- [ ] `cron_scheduler=off` ✅
- [ ] `storage_backend=local` ✅ ← **wenn hier `s3` steht, Block 3a nochmal prüfen**
- [ ] Kein `Boot verweigert`-Fehler

---

## Block 4 — Domains (10 Min, davon 5 Warten)

### 4a — Railway

- [ ] Backend-Service (staging) → **Settings → Networking → Custom Domain** →
      `api-staging.creative-radar.de`
- [ ] Den angezeigten CNAME-Zielwert kopieren

### 4b — Netlify

- [ ] **Add new site → Import from Git** → creative-radar → Name
      `creative-radar-staging`
- [ ] **Branch to deploy: `staging`**
- [ ] Build-Command überschreiben:
      `cd frontend && npm i && npm run build && npm run postbuild:staging`
- [ ] Publish directory: `frontend/dist`
- [ ] Environment variables:
  - `VITE_API_BASE` = `https://api-staging.creative-radar.de`
  - `VITE_API_TOKEN` = der neue `API_TOKEN` aus Block 3e
  - `VITE_ENVIRONMENT` = `staging`
- [ ] Domain `staging.creative-radar.de` hinzufügen, CNAME-Ziel kopieren

### 4c — united-domains

- [ ] CNAME `api-staging` → Ziel aus 4a
- [ ] CNAME `staging` → Ziel aus 4b
- [ ] 5–10 Minuten warten

---

## Block 5 — Abnahme (10 Min)

Die eigentliche Prüfung. Jeder Punkt ist eine Ja/Nein-Frage.

- [ ] **Deploy-Kopplung:** `staging.creative-radar.de` lädt und zeigt den **gelben
      Banner** „STAGING — Testdaten"
- [ ] **Trennung:** Railway-Log zeigt den Boot ohne `Boot verweigert`
- [ ] **Kein Prod-Secret:** Variablen-Liste einmal durchgehen — keine der in 3a/3b
      genannten Variablen ist noch da
- [ ] **Mock greift:** Im Staging-Admin einen Cron-Lauf auslösen
      (`POST /api/admin/cron/sync-all`). Im Log müssen `apify-mock` / `youtube-mock`
      auftauchen — **keine** Zugriffe auf `api.apify.com` oder `googleapis.com`
- [ ] **Nicht indexierbar:** In einem neuen Tab
      `https://staging.creative-radar.de` öffnen, dann in den Netlify-Deploy-Details
      unter „Headers" prüfen, dass `X-Robots-Tag: noindex` gesetzt ist
- [ ] **Login funktioniert:** E-Mail eingeben, Code aus dem **Railway-Deploy-Log**
      holen (`DISABLE_EMAILS=true` → Code steht im Log, suche nach
      `mailer.disabled.body`)
- [ ] **Seed ist drin:** Kanäle und Posts sichtbar

> **Danach `SEED_DEV_ON_DEPLOY` wieder auf `false` setzen oder löschen.** Sonst wird
> die Staging-DB bei **jedem** Deploy auf den Seed-Stand zurückgesetzt — inklusive
> deiner von Hand angelegten Login-User.

---

## Block 6 — Rollback-Übung (5 Min)

Der letzte Punkt der Abnahme: beweisen, dass ein kaputter Commit auf `staging`
die Produktion nicht berührt.

- [ ] GitHub → Branch `staging` → eine beliebige Python-Datei im Backend öffnen →
      **Edit** → irgendwo `SYNTAXFEHLER(((` einfügen → **Commit directly to `staging`**
- [ ] Railway (staging): Deploy schlägt fehl ✅
- [ ] Railway (production): unberührt, läuft weiter ✅
- [ ] `app.creative-radar.de` funktioniert normal ✅
- [ ] GitHub → den Commit öffnen → **Revert** → mergen
- [ ] Staging-Deploy wird wieder grün

**Fertig.** Ab hier gilt das Branch-Modell:
`feature/* → staging → main (Prod)`.

---

## Wenn etwas klemmt

| Symptom | Ursache | Fix |
|---|---|---|
| `storage_backend=s3` im Log | Block 3a unvollständig | Die sechs S3-Variablen löschen |
| `Boot verweigert` | `STAGING_EXPECTED_DB_HOST` falsch | Wert aus dem Postgres-Service neu holen, nur den Host |
| Frontend zeigt keinen Banner | `VITE_ENVIRONMENT` fehlt | In Netlify setzen, **neu deployen** (Vite-Variablen wirken erst beim Build) |
| Login-Code kommt nicht an | so gewollt | Code steht im Railway-Log, `mailer.disabled.body` |
| Swagger-UI unter `/docs` leer | `DOCS_PUBLIC` fehlt | Auf `true` setzen |
| Staging-DB nach Deploy wieder leer/zurückgesetzt | `SEED_DEV_ON_DEPLOY` steht noch | Variable löschen |
