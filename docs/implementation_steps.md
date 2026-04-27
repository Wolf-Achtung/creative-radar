# Umsetzungsschritte Creative Radar v1 – Online-only

Dieses Projekt ist für einen Online-Workflow optimiert: GitHub Web UI, Railway, Netlify. Lokales Arbeiten ist nicht nötig.

## 1. GitHub Repository anlegen

1. GitHub öffnen.
2. Neues Repository erstellen.
3. Name: `creative-radar`.
4. Sichtbarkeit: `Private` empfohlen.
5. ZIP entpacken.
6. Den kompletten Inhalt des Ordners `creative-radar-v1` per GitHub Web UI hochladen.
7. Commit-Message: `Initial Creative Radar v1 online scaffold`.

Ergebnis: Ein Monorepo mit `backend/`, `frontend/`, `docs/`.

## 2. Railway Backend anlegen

1. Railway öffnen.
2. `New Project` wählen.
3. `Deploy from GitHub repo` wählen.
4. Repo `creative-radar` auswählen.
5. Service Settings öffnen.
6. Root Directory setzen auf:

```text
backend
```

7. Build/Deploy läuft über `backend/railway.json` und `backend/Dockerfile`.

## 3. Railway Postgres hinzufügen

1. Im selben Railway-Projekt `New` klicken.
2. `Database` → `PostgreSQL` hinzufügen.
3. PostgreSQL mit dem Backend-Service verbinden.
4. Prüfen, dass `DATABASE_URL` beim Backend-Service verfügbar ist.

Falls Railway die Variable nicht automatisch beim Backend-Service sichtbar macht: Variable `DATABASE_URL` aus der Postgres-Datenbank kopieren und beim Backend-Service eintragen.

## 4. Backend Environment Variables setzen

Im Railway Backend-Service unter Variables setzen:

```env
APP_ENV=production
APP_NAME=creative-radar
CORS_ORIGINS=*
REPORT_TIMEZONE=Europe/Berlin
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.1
```

Für den Start kann `CORS_ORIGINS=*` bleiben. Nach erfolgreichem Netlify-Deployment besser ersetzen durch:

```env
CORS_ORIGINS=https://DEINE-NETLIFY-URL.netlify.app
FRONTEND_URL=https://DEINE-NETLIFY-URL.netlify.app
BACKEND_URL=https://DEINE-RAILWAY-URL.up.railway.app
```

## 5. Railway Public URL aktivieren

1. Railway Backend-Service öffnen.
2. Settings → Networking.
3. Public Domain erzeugen.
4. URL kopieren, z. B.:

```text
https://creative-radar-backend-production.up.railway.app
```

Test im Browser:

```text
https://DEINE-RAILWAY-URL.up.railway.app/api/health
```

Erwartung:

```json
{"status":"ok","service":"creative-radar","version":"1.0.0"}
```

## 6. Netlify Frontend anlegen

1. Netlify öffnen.
2. `Add new site` → `Import an existing project`.
3. GitHub Repo `creative-radar` auswählen.
4. Build settings:

```text
Base directory: frontend
Build command: npm run build
Publish directory: dist
```

5. Environment Variable setzen:

```env
VITE_API_BASE=https://DEINE-RAILWAY-URL.up.railway.app
```

6. Deploy starten.

## 7. Dashboard öffnen

Netlify-URL öffnen.

Erwartung:

- Systemstatus zeigt `ok`.
- Channels: zunächst 0.
- Whitelist-Titel: zunächst 0.
- Assets: zunächst 0.

Falls `nicht verbunden` erscheint, stimmt meist `VITE_API_BASE` in Netlify oder `CORS_ORIGINS` in Railway nicht.

## 8. MVP-Daten anlegen

Im Dashboard auf folgenden Button klicken:

```text
MVP-Daten anlegen
```

Danach sollten erscheinen:

```text
Channels: 20
Whitelist-Titel: 10
```

## 9. Ersten Post importieren

Im Dashboard im Bereich `Manueller Treffer-Import` ausfüllen:

- Channel auswählen
- Titel/Franchise auswählen oder leer lassen
- Instagram-Post-Link einfügen
- Asset-Typ wählen
- Caption einfügen
- sichtbaren Text/OCR optional einfügen
- Screenshot-URL optional einfügen

Dann:

```text
Treffer importieren
```

Danach erscheint das Asset im Review-Bereich.

## 10. Asset kuratieren

Im Bereich `Asset Review`:

- `Approve` = kommt in den Report-Anhang
- `Highlight` = kommt in Summary/Highlights
- `Reject` = wird nicht verwendet

## 11. Report erzeugen

Im Bereich `Weekly Report`:

```text
Report-Entwurf erzeugen
```

Der Report nutzt die freigegebenen/highlighted Assets der letzten sieben Tage.

## 12. Nächste Ausbaustufe

Erst wenn dieser Ablauf funktioniert, sinnvoll erweitern:

1. echte OpenAI-Analyse statt Platzhalter-Analyse
2. Screenshot-Upload oder S3/R2 Storage
3. automatischer Screenshot-Service
4. Instagram/Public-Connector
5. Railway Cronjob für Donnerstag
6. PDF-Export
7. Login/Auth
