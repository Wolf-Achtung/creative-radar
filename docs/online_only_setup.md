# Creative Radar v1 – Online-only Setup für GitHub, Railway und Netlify

## Entscheidung

Es wird **ein einziges GitHub-Repository** genutzt:

```text
creative-radar
```

Darin liegen Backend und Frontend gemeinsam:

```text
creative-radar/
  backend/
  frontend/
  docs/
```

Das ist für den MVP richtig, weil Frontend und Backend fachlich zusammengehören und Änderungen sauber gemeinsam versioniert werden können.

---

## A. Was vor dem Start gebraucht wird

Benötigt:

- GitHub Account
- Railway Account
- Netlify Account
- dieses Starterpaket als ZIP

Noch nicht nötig:

- OpenAI API Key
- eigene Domain
- Cloudflare R2/S3
- Instagram API
- lokales Terminal

---

## B. GitHub: Repo anlegen und Dateien hochladen

1. GitHub öffnen.
2. `New repository` klicken.
3. Name:

```text
creative-radar
```

4. Sichtbarkeit:

```text
Private
```

5. Repository erstellen.
6. ZIP lokal entpacken.
7. In GitHub `Add file` → `Upload files` wählen.
8. Den Inhalt des entpackten Ordners `creative-radar-v1` hochladen.
9. Commit:

```text
Initial Creative Radar v1 online scaffold
```

Wichtig: Im Repo müssen danach direkt diese Ordner sichtbar sein:

```text
backend
frontend
docs
```

Nicht versehentlich so hochladen:

```text
creative-radar-v1/backend
creative-radar-v1/frontend
```

Falls das passiert: nicht dramatisch, aber Railway/Netlify Root Directory müssten dann entsprechend anders gesetzt werden. Besser: `backend`, `frontend`, `docs` direkt auf Repo-Ebene.

---

## C. Railway: Backend deployen

1. Railway öffnen.
2. `New Project`.
3. `Deploy from GitHub repo`.
4. Repo `creative-radar` auswählen.
5. Wenn Railway fragt, welches Verzeichnis genutzt werden soll:

```text
backend
```

6. Falls Railway es nicht automatisch erkennt: Service Settings → Source → Root Directory:

```text
backend
```

7. Deploy starten.

Railway nutzt:

```text
backend/Dockerfile
backend/railway.json
```

---

## D. Railway: Postgres hinzufügen

1. Im Railway-Projekt `New` klicken.
2. `Database` wählen.
3. `PostgreSQL` hinzufügen.
4. Prüfen, ob beim Backend-Service die Variable `DATABASE_URL` vorhanden ist.

Ohne `DATABASE_URL` startet das Backend zwar eventuell mit SQLite, aber das wäre auf Railway nicht stabil für den MVP. Für den Online-Betrieb bitte Postgres verwenden.

---

## E. Railway: Variables setzen

Im Backend-Service unter `Variables` setzen:

```env
APP_ENV=production
APP_NAME=creative-radar
CORS_ORIGINS=*
REPORT_TIMEZONE=Europe/Berlin
```

Optional schon setzen:

```env
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.1
```

`OPENAI_API_KEY` bleibt für v1 zunächst leer. Das System nutzt dann Platzhalter-Zusammenfassungen.

---

## F. Railway: Public URL prüfen

1. Backend-Service öffnen.
2. Settings → Networking.
3. Public URL erzeugen.
4. URL im Browser testen:

```text
https://DEINE-RAILWAY-URL.up.railway.app/api/health
```

Erwartetes Ergebnis:

```json
{
  "status": "ok",
  "service": "creative-radar",
  "version": "1.0.0"
}
```

Diese Railway-URL wird gleich für Netlify gebraucht.

---

## G. Netlify: Frontend deployen

1. Netlify öffnen.
2. `Add new site`.
3. `Import an existing project`.
4. GitHub Repo `creative-radar` auswählen.
5. Build settings:

```text
Base directory: frontend
Build command: npm run build
Publish directory: dist
```

6. Environment variable setzen:

```env
VITE_API_BASE=https://DEINE-RAILWAY-URL.up.railway.app
```

7. Deploy starten.

---

## H. CORS nachziehen

Nach dem Netlify-Deploy hast du eine Netlify-URL, z. B.:

```text
https://creative-radar.netlify.app
```

Dann in Railway beim Backend-Service setzen:

```env
CORS_ORIGINS=https://creative-radar.netlify.app
FRONTEND_URL=https://creative-radar.netlify.app
BACKEND_URL=https://DEINE-RAILWAY-URL.up.railway.app
```

Danach Railway Backend neu deployen/restarten.

Für den allerersten Test darf `CORS_ORIGINS=*` bleiben.

---

## I. Erste Bedienung im Dashboard

1. Netlify-URL öffnen.
2. Prüfen: Systemstatus `ok`.
3. Button klicken:

```text
MVP-Daten anlegen
```

4. Danach sollten angezeigt werden:

```text
Channels: 20
Whitelist-Titel: 10
```

5. Im Bereich `Manueller Treffer-Import` einen echten Instagram-Link einfügen.
6. Asset speichern.
7. Im Review-Bereich `Approve` oder `Highlight` klicken.
8. Report-Entwurf erzeugen.

---

## J. Erste Testdaten

Zum Testen reicht ein realer Instagram-Link plus Caption. Screenshot-URL kann leer bleiben.

Beispiel:

```text
Channel: A24
Titel: Mission: Impossible oder anderer Whitelist-Titel
Post-Link: echter Instagram-Link
Asset-Typ: Trailer / Teaser / Poster / Kinetic
Caption: Text aus dem Post
Sichtbarer Text: optional
```

Wenn kein Titel ausgewählt wird, versucht das Backend über Caption/OCR die Whitelist zu matchen.

---

## K. Häufige Fehler

### Frontend zeigt „nicht verbunden“

Prüfen:

- Netlify ENV `VITE_API_BASE` gesetzt?
- Railway Backend Public URL korrekt?
- `/api/health` im Browser erreichbar?
- CORS in Railway zu streng?

### Channels bleiben 0

Button `MVP-Daten anlegen` klicken. Wenn Fehler erscheint, Railway Logs prüfen.

### Post-Import scheitert mit 409

Der Post-Link wurde bereits importiert. Für denselben Link wird kein zweites Asset angelegt.

### Report ist leer

Mindestens ein Asset muss `approved` oder `highlight` sein und `include_in_report=true` haben. Das erledigen die Buttons `Approve` und `Highlight` automatisch.
