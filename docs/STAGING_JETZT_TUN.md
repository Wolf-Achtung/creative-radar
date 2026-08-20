# Staging fertigstellen — Schritt für Schritt

Stand: 20.08.2026. Diese Liste ersetzt für den aktuellen Durchgang die
[Abnahme-Checkliste](./STAGING_ABNAHME_CHECKLISTE.md) — die beschreibt den Aufbau
von null, du bist aber schon deutlich weiter.

## Was bereits steht ✅

| | |
|---|---|
| Railway-Environment `staging` | ✅ da, mit eigenem Backend + eigener Postgres + eigenem Volume |
| `api-staging.creative-radar.de` | ✅ online, DNS steht |
| `staging.creative-radar.de` | ✅ lädt, gelber STAGING-Banner erscheint |
| Netlify-Site `venerable-cendol-2a4874` | ✅ da, deployt die Staging-Domain |
| Login auf Staging | ✅ funktioniert (`wolf@trailerhaus.de`) |

## Was noch fehlt ❗

Deine Staging-Umgebung zeigt Briefs mit dem Präfix **`[SEED]`** und dem Stand
**06.08.** Das sind erfundene Testdaten, und sie sind zwei Wochen alt. Grund: der
Branch, auf den Railway und Netlify zeigen, heißt `staging` — **und dieser Branch
existiert nicht mehr.** Seither wurde nichts Neues deployt.

Vier Schritte, ca. 25 Minuten. Nach jedem kannst du aufhören.

---

## Schritt 1 — Railway: Branch auf `main` (3 Min)

> Warum: Ohne existierenden Branch deployt Railway gar nicht mehr. Ab jetzt gilt
> „beide Umgebungen fahren `main`, die Unterschiede kommen aus den Variablen".

1. Railway öffnen → Projekt **overflowing-unity**
2. Oben auf **production** klicken → auf **staging** wechseln
3. Auf den Service **creative-radar** klicken (der mit `api-staging.creative-radar.de`)
4. **Settings** → Abschnitt **Source**
5. Bei **Branch** steht vermutlich `staging` → auf **`main`** ändern
6. Railway startet automatisch einen neuen Deploy

**Kontrolle:** Der Deploy wird grün. Falls nicht: Log öffnen, Fehlermeldung
notieren, hier melden.

---

## Schritt 2 — Railway: drei Variablen (5 Min)

Immer noch im **staging**-Environment, Service **creative-radar** → Reiter
**Variables**.

### 2a — Diese Variable LÖSCHEN

- [ ] **`SEED_DEV_ON_DEPLOY`** löschen (Mülleimer-Symbol)

> Warum das wichtig ist: Diese Variable füllt die Datenbank bei **jedem** Deploy
> mit den erfundenen `[SEED]`-Daten und **löscht dabei alles andere**. Bleibt sie
> stehen, macht sie dir in Schritt 4 die kopierten Echtdaten sofort wieder kaputt.

- [ ] Falls vorhanden, auch **`SEED_DEV_PAIRS`** löschen

### 2b — Diese Variable NEU ANLEGEN

**+ New Variable**:

| Name | Wert |
|---|---|
| `FEATURE_TRAILER_INTELLIGENCE_ENABLED` | `true` |

> Warum: Der Schalter für die neue Trailer-Intelligence-Auswertung. Er steht nur
> hier auf `true`. In **production** wird er **nicht** gesetzt — dort bleibt alles
> unverändert, bis du die Ansicht abgenommen hast.

### 2c — Diese Variable NACHSCHLAGEN und notieren

Du brauchst sie in Schritt 4.

- [ ] Im **staging**-Environment auf den Service **postgres-creative-radar** klicken
- [ ] Reiter **Variables**
- [ ] Wert von **`DATABASE_PUBLIC_URL`** kopieren (fängt mit `postgresql://` an)
- [ ] Irgendwo zwischenspeichern — Notizen, Passwortmanager. **Nicht** ins Repo.

> Es muss die **PUBLIC**-URL sein, nicht `DATABASE_URL`. Die interne Adresse
> (`*.railway.internal`) ist nur innerhalb von Railway erreichbar, dein Mac kommt
> da nicht hin.

---

## Schritt 3 — Netlify: Branch auf `main` (3 Min)

1. Netlify öffnen → Site **creative-radar-staging** (die mit
   `venerable-cendol-2a4874`)
2. **Site configuration** → **Build & deploy** → **Continuous deployment**
3. Bei **Branch to deploy** steht vermutlich `staging` → auf **`main`** ändern →
   **Save**
4. Reiter **Deploys** → **Trigger deploy** → **Deploy site**

**Kontrolle:** Nach ~2 Minuten `https://staging.creative-radar.de` neu laden. Der
gelbe Banner muss weiter da sein.

### Zwischenprüfung: greift der Schalter?

Diese zwei Links im Browser öffnen:

| Link | Erwartung |
|---|---|
| `https://api-staging.creative-radar.de/api/health` | `"trailer_intelligence": true` |
| `https://api.creative-radar.de/api/health` | `"trailer_intelligence": false` |

Stimmen beide, ist das Fundament fertig: **derselbe Code, unterschiedliches
Verhalten, gesteuert allein über eine Variable.**

---

## Schritt 4 — Echte Daten nach Staging kopieren (10 Min, Terminal)

Der einzige Schritt mit Terminal. Beim ersten Mal einmalig vorbereiten, danach
sind es zwei Zeilen.

### 4a — Einmalig: Postgres-Werkzeuge installieren

Terminal öffnen, einfügen, Enter:

```sh
brew install libpq && brew link --force libpq
```

Prüfen, dass es geklappt hat:

```sh
pg_dump --version
```

Kommt eine Versionsnummer, ist alles gut. Kommt `command not found`, Terminal
einmal schließen und neu öffnen.

### 4b — Einmalig: Staging-Zugangsdaten hinterlegen

Ersetze `HIER_DIE_URL_EINFÜGEN` durch die URL aus Schritt 2c — **die
Anführungszeichen bleiben stehen**:

```sh
mkdir -p ~/.creative-radar
echo 'export CR_STAGING_DB_URL="HIER_DIE_URL_EINFÜGEN"' >> ~/.creative-radar/staging.env
```

### 4c — Den Staging-Host herausfinden

```sh
source ~/.creative-radar/staging.env
echo "$CR_STAGING_DB_URL" | sed -E 's|.*@([^:/]+).*|\1|'
```

Das gibt eine Zeile aus, etwa `interchange.proxy.rlwy.net`. **Diese Zeile
kopieren** — sie ist gleich der Wert für `--ziel-host`.

> Wozu das gut ist: Das Skript verlangt, dass du den Staging-Host ausdrücklich
> nennst. Es prüft dann, ob dieser Host wirklich zur Staging-URL gehört und **nicht**
> zur Produktions-URL. Damit kann man die beiden Datenbanken nicht verwechseln —
> auch nicht bei vertauschten Dateien.

### 4d — Trockenübung (verändert nichts)

Im Repo-Verzeichnis. `<HOST>` durch die Zeile aus 4c ersetzen:

```sh
cd ~/creative-radar
source ~/.creative-radar/db.env
source ~/.creative-radar/staging.env
python3 scripts/staging_refresh.py --ziel-host <HOST>
```

Erwartete Ausgabe:

```
Trockenuebung — es passiert nichts. Der echte Lauf wuerde:
  1. pg_dump  --schema=creative_radar aus der Quelle (CR_DB_URL)
  2. auf dem Ziel: DROP SCHEMA creative_radar CASCADE
  3. pg_restore in das Ziel (CR_STAGING_DB_URL)
Zum Ausfuehren: --ausfuehren anhaengen.
```

Kommt stattdessen `ABBRUCH: …`, **nicht weitermachen** — die Meldung sagt, was
nicht stimmt. Häufigster Fall: falscher oder zu unspezifischer Host.

### 4e — Echter Lauf

Dieselbe Zeile, `--ausfuehren` angehängt:

```sh
python3 scripts/staging_refresh.py --ziel-host <HOST> --ausfuehren
```

Dauert je nach Datenmenge 1–3 Minuten. Am Ende steht:

```
Fertig. Staging traegt jetzt den Prod-Stand von gerade eben.
```

### 4f — Kontrolle

`https://staging.creative-radar.de` neu laden:

- [ ] Der gelbe STAGING-Banner ist da
- [ ] Die Studio-Kacheln zeigen **echte** Briefs — **kein** `[SEED]` mehr
- [ ] Die Zahlen entsprechen dem, was auch `app.creative-radar.de` zeigt

**Fertig.** Ab jetzt kannst du Schritt 4d+4e jederzeit wiederholen, wenn du
frische Daten willst.

---

## Was danach gilt

- **Produktion ist unberührt.** Staging arbeitet auf einer *Kopie*. Egal was du
  dort anklickst, löschst oder ausprobierst: `app.creative-radar.de` merkt nichts.
- **Ein Refresh ist ein Reset.** Was du auf Staging von Hand angelegt hast, ist
  danach weg. Das ist Absicht.
- **Neue Features** entstehen auf `main` hinter `FEATURE_*`-Schaltern und sind
  zuerst nur in Staging an. Freigabe in Produktion = die Variable dort setzen.
  Zurücknehmen = Variable löschen. Kein Deploy nötig, ~10 Sekunden.
- **Der Montags-Cron läuft in Staging nicht.** Er ist an `app_env == "production"`
  gebunden. Staging scrapt nichts und verursacht keine Apify-/LLM-Kosten.

## Wenn etwas klemmt

| Symptom | Ursache | Fix |
|---|---|---|
| Railway-Deploy startet nicht | Branch zeigt noch auf `staging` | Schritt 1 |
| Nach einem Deploy sind wieder `[SEED]`-Daten da | `SEED_DEV_ON_DEPLOY` steht noch | Schritt 2a, dann Schritt 4 erneut |
| `health` zeigt `trailer_intelligence: false` in Staging | Variable fehlt oder Service noch nicht neu gestartet | Schritt 2b, dann in Railway **Redeploy** |
| `ABBRUCH: --ziel-host … kommt AUCH in CR_DB_URL vor` | Host zu unspezifisch (z. B. nur `rlwy.net`) | Vollständigen Host aus 4c nehmen |
| `Boot verweigert` im Railway-Log | `STAGING_EXPECTED_DB_HOST` passt nicht zur DB | Wert aus **postgres-creative-radar → Variables → `RAILWAY_PRIVATE_DOMAIN`** neu setzen |
| `pg_dump: command not found` | libpq nicht verlinkt | `brew link --force libpq`, Terminal neu öffnen |
| `permission denied` / `could not connect` in 4e | interne statt öffentlicher URL | In 2c `DATABASE_PUBLIC_URL` nehmen, nicht `DATABASE_URL` |
