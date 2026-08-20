# Staging-Abnahme — Klick-Checkliste

> **Für den aktuellen Durchgang (20.08.2026) ist
> [`STAGING_JETZT_TUN.md`](./STAGING_JETZT_TUN.md) die richtige Liste.** Environment,
> Domains, Netlify-Site und Banner stehen bereits; dort stehen nur die vier
> verbliebenen Schritte, ausgeschrieben. Dieses Dokument beschreibt den Aufbau
> **von null** — Referenz, falls Staging je neu gebaut werden muss.

Reine Handanweisung, ohne Begründungen. Warum das alles so gebaut ist, steht in
[`STAGING_SETUP.md`](./STAGING_SETUP.md) — hier geht es nur ums Abarbeiten.

> **Modell-Wechsel 20.08.2026:** kein eigener `staging`-Branch mehr. Beide
> Umgebungen deployen **`main`**; Neues liegt hinter Feature-Flags
> (`FEATURE_TRAILER_INTELLIGENCE_ENABLED` usw.) und ist nur dort an, wo die
> Flag-Variable gesetzt ist. Und statt synthetischer Seed-Daten bekommt
> Staging eine **Kopie der Produktionsdaten** (`scripts/staging_refresh.py`,
> Block 7). Die betroffenen Stellen unten sind angepasst; ein evtl. noch
> existierender `staging`-Branch kann gelöscht werden.

**Zeitbedarf:** 30–45 Minuten, davon ~10 Minuten Warten auf DNS und Deploys.
**Voraussetzung:** Browser; nur Block 7 (Daten-Refresh) braucht das Terminal.

Du kannst nach jedem Block aufhören und später weitermachen. Die Blöcke bauen
aufeinander auf, aber jeder ist für sich abgeschlossen.

---

## Block 1 — entfällt (Modell-Wechsel 20.08.2026)

Hier stand: `staging`-Branch per Vorwärts-Merge auf `main` heben. Ohne
Branch-Modell gibt es nichts zu heben — weiter mit Block 2.

---

## Block 2 — Railway: Environment `staging` anlegen (5 Min)

- [ ] Railway → Projekt **overflowing-unity** → oben auf **production** klicken →
      **New Environment**
- [ ] Name: `staging`, Quelle: **Duplicate from production**
- [ ] Warten, bis die Services erscheinen (Postgres + Backend)

**Prüfen:** Im `staging`-Environment muss eine **eigene** `postgres-creative-radar`
stehen. Wenn ja: gut, das ist eine leere Instanz mit eigenem Volume. Falls keine da
ist: **+ New → Database → PostgreSQL**.

- [ ] Backend-Service (staging) → **Settings → Source → Branch**: muss auf
      **`main`** stehen (Modell-Wechsel 20.08.2026 — steht dort noch
      `staging`, umstellen)

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
| `FEATURE_TRAILER_INTELLIGENCE_ENABLED` | `true` — **nur hier**, nie in production (Modell-Wechsel 20.08.2026: neue Features laufen auf main hinter diesem Flag) |

> `SEED_DEV_ON_DEPLOY` **nicht** setzen (Modell-Wechsel 20.08.2026): die
> Daten kommen per Refresh aus der Produktion (Block 7), nicht aus dem
> synthetischen Seed. Steht die Variable noch, löschen — sonst setzt jeder
> Deploy die kopierten Daten wieder auf den Seed zurück.

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
- [ ] **Branch to deploy: `main`** (Modell-Wechsel 20.08.2026 — dieselbe
      Quelle wie Prod, die Unterschiede kommen aus den Site-Variablen)
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
- [ ] **Daten sind drin:** Kanäle und Posts sichtbar — die kommen aus dem
      Refresh (Block 7). Vor dem ersten Refresh ist die Liste leer; das ist
      dann kein Fehler, sondern die Reihenfolge.

---

## Block 6 — Flag-Übung (5 Min)

Der letzte Punkt der Abnahme im main+Flag-Modell: beweisen, dass ein Flag
nur die Umgebung ändert, in der es gesetzt ist.

- [ ] Railway (staging) → Variables: `FEATURE_TRAILER_INTELLIGENCE_ENABLED`
      steht auf `true` (Block 3c)
- [ ] `https://api-staging.creative-radar.de/api/health` öffnen →
      `"trailer_intelligence": true` ✅
- [ ] `https://api.creative-radar.de/api/health` öffnen →
      `"trailer_intelligence": false` ✅ (die Variable ist in production
      **nicht** gesetzt — und das bleibt so, bis zur Abnahme)
- [ ] Zur Probe das Flag in staging auf `false` stellen → health kippt nach
      dem Service-Restart auf `false` → wieder auf `true` stellen

**Fertig.** Ab hier gilt: alles deployt `main`, neue Features liegen hinter
Flags und sind zuerst nur in Staging an.

---

## Block 7 — Daten-Refresh: Prod-Kopie nach Staging (10 Min, Terminal)

Staging arbeitet auf einer **Kopie** der Produktionsdaten — nie live auf der
Prod-DB. Der Refresh ist wiederholbar; jeder Lauf setzt Staging auf den
aktuellen Prod-Stand zurück.

Einmalig vorbereiten:

- [ ] `brew install libpq && brew link --force libpq` (bringt `pg_dump`/`pg_restore`)
- [ ] Railway → Postgres-Service (**staging**) → **Variables** →
      `DATABASE_PUBLIC_URL` kopieren
- [ ] `echo 'export CR_STAGING_DB_URL="<kopierte URL>"' >> ~/.creative-radar/staging.env`

Bei jedem Refresh (auch beim ersten):

```sh
cd ~/creative-radar
source ~/.creative-radar/db.env          # CR_DB_URL = Produktion (hast du schon)
source ~/.creative-radar/staging.env
python3 scripts/staging_refresh.py --ziel-host <staging-db-host>          # Trockenübung
python3 scripts/staging_refresh.py --ziel-host <staging-db-host> --ausfuehren
```

`<staging-db-host>` ist der Host aus der Staging-URL (z. B.
`xyz.proxy.rlwy.net`). Das Skript bricht ab, wenn dieser Host nicht
eindeutig die Staging-Seite bezeichnet — Quelle und Ziel lassen sich damit
nicht verwechseln, auch nicht mit vertauschten env-Dateien.

- [ ] Trockenübung zeigt die drei Schritte, kein Fehler
- [ ] Echter Lauf endet mit „Fertig."
- [ ] `staging.creative-radar.de` zeigt nach einem Reload die Prod-Kanäle
      und -Briefs (gelber Banner bleibt)

> Login-User werden mitkopiert — du meldest dich auf Staging mit derselben
> E-Mail an; der Code steht wegen `DISABLE_EMAILS=true` im Railway-Log.

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
