# Creative Radar — Steckbrief

Kurzprofil für Wartung und Wiedereinstieg. Was hier steht, ist am
genannten Datum am Code oder an der Anbieter-Doku geprüft worden — nicht
aus dem Gedächtnis geschrieben. Wo eine Quelle nicht erreichbar war,
steht das da.

**Letzter Wartungsdurchgang: 19.08.2026** · letzte Korrektur: 25.08.2026
(siehe „Katalog-Zuordnung — vier Befunde")

---

## Laufzeit

| Was | Wert | Quelle im Repo |
|---|---|---|
| Python (Prod) | 3.12 (`python:3.12-slim`) | `backend/Dockerfile:1` |
| Python (CI) | 3.12 | `.github/workflows/backend-tests.yml` |
| Postgres (Prod/Staging) | 18 | `docker-compose.yml`, CI-Service `postgres:18` |
| Node (Netlify-Build) | 22 (Active LTS) | `netlify.toml` → `NODE_VERSION` |
| Node (`engines`) | `^20.19.0 \|\| >=22.12.0` | `frontend/package.json` |
| Backend-Start | `uvicorn app.main:app` | `backend/start.sh` |
| Deploy-Vorlauf | `python -m scripts.db_bootstrap` | `backend/railway.json` |

Node 20 ist seit April 2026 aus dem Support. Die `engines`-Angabe lässt
ihn noch zu, gebaut wird aber mit dem Pin aus `netlify.toml` (22) — die
Untergrenze wird von Netlify **nicht** als Versionswahl gelesen.

## Modell-IDs

Alle über `Settings` per ENV überschreibbar. Der Opus-Wert läuft über
`insight_engine.OPUS_MODEL_ALIAS` und trägt jeden teuren Pfad:
Wochen-Brief, Segment-Roundup, Cutter-/Designer-Briefing, Titel-Brief,
ER-Forecast.

| Einstellung | Wert | Status laut Anbieter | Frühestes Abschaltdatum |
|---|---|---|---|
| `ANTHROPIC_OPUS_MODEL` | `claude-opus-4-8` | Active (in der Übersicht unter „Legacy" geführt) | **nicht vor 28.05.2027** |
| `ANTHROPIC_SONNET_MODEL` | `claude-sonnet-5` | Active | nicht vor 30.06.2027 |
| `ANTHROPIC_HAIKU_MODEL` | `claude-haiku-4-5-20251001` | Active | **nicht vor 15.10.2026** ← nächster Horizont |
| `OPENAI_MODEL` | `gpt-5.4-mini` | **ungeprüft** (s.u.) | unbekannt |

Quelle Anthropic: `platform.claude.com/docs/en/about-claude/models/overview`
und `.../model-deprecations`, abgerufen 19.08.2026. Beide Seiten waren aus
der Wartungsumgebung **erreichbar** — ältere Kommentare im Code, die das
Gegenteil annehmen, sind überholt.

Quelle OpenAI: **nicht erreichbar.** `platform.openai.com` und
`openai.com` werden vom Egress-Proxy dieser Umgebung blockiert
(`EGRESS_BLOCKED`, kein 403 am Ziel). Modell-Existenz und Preise für
`gpt-5.4-mini` sind damit weiterhin unbestätigt — auch die
`OPENAI_*_PER_1K_USD`-Werte, die in jede Kostenauswertung eingehen.

### Denkverhalten — geprüft, Tabelle stimmt

`app/tests/test_model_defaults_drift.py` führt `claude-opus-4-8` mit
`denkt_per_default: False`. **Das ist richtig.** Zwei Angaben werden
leicht verwechselt:

- „Adaptive Thinking: Yes" in der Modell-Übersicht heißt *das Modell
  kann es*, nicht *es tut es ungefragt*.
- Opus 4.8 und 4.7 denken **nur**, wenn der Aufruf
  `thinking={"type": "adaptive"}` mitschickt. Ohne den Parameter läuft
  kein Denken. Kein Aufrufort im Projekt schickt ihn — also denkt hier
  heute kein Opus-Pfad.
- Opus 5 und Fable 5 verhalten sich **anders**: die denken ohne jede
  Angabe. Ein Wechsel dorthin ändert Kosten und Abschneide-Risiko
  schlagartig.

`effort` ist davon unabhängig und steht per API-Vorgabe auf `high`
(gleichbedeutend mit „Parameter weggelassen"). Das ist ein Kosten-Hebel,
kein Fehler: `medium` oder `low` auf den strukturierten Brief-Pfaden
spart Token, kostet aber Qualität. Bisher nicht gemessen.

### Preise (pro 1k Token, USD) — Stand 20.08.2026

| Modell | Config | Anbieter-Liste | |
|---|---|---|---|
| Haiku 4.5 | 0.001 / 0.005 | $1 / $5 pro MTok | stimmt |
| Sonnet 5 | 0.002 / 0.010 | $2 / $10 pro MTok | stimmt (am 20.08. korrigiert) |
| Opus 4.8 | 0.005 / 0.025 | $5 / $25 pro MTok | stimmt |

Der Sonnet-Wert stand bis zum 20.08.2026 auf 0.003/0.015 — der
erwarteten Standard-Rate ab 01.09.2026. **Diese Erhöhung findet nicht
statt.** Die Preisseite sagt: „The $2/$10 … pricing for Claude Sonnet 5,
announced at launch as introductory pricing through August 31, 2026, is
now the standard price. The previously scheduled increase to $3/$15 …
will not occur." Bis zur Korrektur haben die Cost-Logs den Sonnet-Anteil
um 50 % überschätzt; der Anthropic-Monatsdeckel hat dadurch zu früh
gebremst, nicht zu spät.

Lehre für den nächsten Durchgang: die Modell-Referenz im `claude-api`-
Skill ist zwischengespeichert und war hier zwei Tage veraltet. Bei
Preisfragen die Preisseite selbst abrufen.

## Abhängigkeiten

Stand 19.08.2026: `pip-audit` gegen `backend/requirements.txt` und
`npm audit` im `frontend/` melden **je 0 bekannte Schwachstellen**.

Alle Python-SDKs sind gepinnt (`==`), einzige Ausnahme ist
`boto3>=1.34.0,<2.0.0`. Der tägliche Workflow `model-drift.yml` hält
`anthropic`, `openai`, `fastapi`, `sqlmodel` und `alembic` rein
berichtend gegen PyPI.

## ENV-Vertrag

`.env.example` ist die vollständige Liste und wird seit 19.08.2026
maschinell gegen `config.py` gehalten:

- `test_jede_einstellung_steht_in_env_example` — gelesen, aber nicht
  dokumentiert (läuft still auf dem Default).
- `test_env_example_erfindet_keine_einstellungen` — dokumentiert, aber
  von niemandem gelesen (wirkungslose Konfiguration).

Beide in `app/tests/test_wartung_2026_08_19.py`. Stand heute: 130
Variablen im Code, alle dokumentiert.

Nicht über `Settings`, sondern direkt per `os.environ` gelesen — deshalb
von der ersten Prüfung nicht erfasst:

`CRON_RUN_TIMEOUT_MINUTES`, `CRON_TOTAL_RUN_TIMEOUT_SECONDS`,
`CRON_POST_ANALYSIS_STAGE_TIMEOUT_SECONDS`, `TITLE_SYNC_STAGE_TIMEOUT_SECONDS`,
`REMATCH_STAGE_TIMEOUT_SECONDS`, `VISION_STAGE_TIMEOUT_SECONDS`,
`ENABLE_TITLE_SYNC_IN_CRON`,
`ENABLE_BRIEF_GEN_IN_CRON`, `ENABLE_INTERNAL_CRON_SCHEDULER`,
`FEATURE_SEGMENT_ROUNDUPS_ENABLED`, `FEATURE_CUTTER_WEEKLY_ENABLED`,
`FEATURE_DESIGNER_WEEKLY_ENABLED`, `FEATURE_TRAILER_INTELLIGENCE_ENABLED`,
`PG_STATEMENT_TIMEOUT_MS`,
`CRON_SYNC_INTERVAL_DAYS`, `CRON_A_CLASS_THRESHOLD`, `CRON_A_CLASS_MAX`,
`SEED_DEV_ON_DEPLOY`, `SEED_DEV_PAIRS` (Deploy-Bootstrap),
`CR_DB_URL` (nur lokale Diagnose-Skripte in `scripts/`),
`CR_STAGING_DB_URL` (nur `scripts/staging_refresh.py`).

### Staging (Modell-Wechsel 20.08.2026)

Beide Railway-Umgebungen deployen `main`; Neues liegt hinter
`FEATURE_*`-Flags (`app/core/feature_flags.py`), das Frontend liest den
Zustand aus `GET /api/health → features`. Staging-Daten sind eine
**Kopie** der Prod-DB (`scripts/staging_refresh.py`, Richtungs-Sicherung
mutations-geprüft) — nie eine Live-Verbindung. Setup und Klick-Liste:
`docs/STAGING_SETUP.md` + `docs/STAGING_ABNAHME_CHECKLISTE.md`.

Frontend (Netlify, Build-Zeit): `VITE_API_BASE`, `VITE_API_TOKEN`,
`VITE_ENVIRONMENT`. Stehen in keiner `.env.example` — das Beispiel deckt
nur das Backend ab.

### Railway-Abgleich (ENV-Listen 22.08.2026, beide Umgebungen)

Production: 44 Variablen gesetzt. Am 22.08. bereinigt — acht Variablen
entfernt, die entweder exakt dem Code-Default entsprachen
(`ANTHROPIC_MONTHLY_BUDGET_USD`, `APIFY_MONTHLY_BUDGET_USD`,
`APIFY_RESULTS_LIMIT_PER_CHANNEL`, `APIFY_WAIT_SECONDS`) oder
funktionslos waren: `BACKEND_URL` (Settings-Feld, das keine Codezeile
liest), `CRON_A_CLASS_THRESHOLD`/`_MAX` und `CRON_SYNC_INTERVAL_DAYS`
(seit Wegfall der B-Klassen-Rotation synct jeder Lauf ohnehin alle
aktiven Kanäle; `run_index` ist laut `cron_channel_selection.py`
ausdrücklich vestigial). Alle drei Kosten-Deckel laufen damit einheitlich
auf den Code-Defaults ($50/$100/$50) — die frühere Asymmetrie ist
aufgelöst.

Geändert am selben Tag: `FRONTEND_URL` von der Zweit-Domain
(`creative-radar.ki-sicherheit.jetzt`) auf `https://app.creative-radar.de`
— der Wert steht im Fußtext jeder Login-Mail und in den Dashboard-Links
der Playbook-Mail. `CRON_TOTAL_RUN_TIMEOUT_SECONDS` 12000 → **14400**
und `CRON_RUN_TIMEOUT_MINUTES` 210 → **250** (Sicherheitsreserve für die
neuen Cron-Stages der Woche vom 18.08.; der Reaper muss über dem harten
Timeout bleiben, nach gemessenem Lauf wieder senkbar).

Staging: 39 Variablen, unverändert und regelkonform — keine API-Keys,
kein S3, `MOCK_EXTERNAL_APIS=true` (deckt Apify + YouTube),
`DISABLE_EMAILS=true` (Login-Codes ins Log), TI-Flag nur hier. Bekannte
Einschränkung: Evidence-Keys aus der Prod-DB-Kopie sind ohne S3-Zugang
nicht auflösbar — `/api/thumbnails` 302t auf `/storage/…` ins Leere, die
Karten fallen aufs CDN-Bild zurück.

Bewusst **nicht** in Production gesetzt (laufen auf dem Code-Default):

| Variable | Wirkung ohne Setzung |
|---|---|
| `ANTHROPIC_OPUS_MODEL` / `_SONNET_` / `_HAIKU_` | die eingecheckten Defaults laufen — der Modell-Wächter prüft damit heute wirklich das, was in Produktion läuft |
| `ENABLE_INTERNAL_CRON_SCHEDULER` | `is_scheduler_enabled()` fällt auf `app_env == "production"` zurück → Montags-Cron **an** (`cron_scheduler.py:45`) |
| `DISABLE_EMAILS`, `DOCS_PUBLIC`, `MOCK_EXTERNAL_APIS`, `STAGING_EXPECTED_DB_HOST` | nur in `staging` gesetzt — in Production korrekt aus |
| `ALLOW_AUTH_DISABLED_IN_PRODUCTION` | `False` → Boot-Abbruch bei abgeschalteter Auth greift |
| `APIFY_MONTHLY_BUDGET_USD`, `ANTHROPIC_MONTHLY_BUDGET_USD`, `OPENAI_MONTHLY_BUDGET_USD` | $50 / $100 / $50 aus dem Code — seit 22.08. alle drei einheitlich auf Default |
| `IMAGE_PROXY_ALLOWED_HOSTS` | Code-Default gilt — inkl. `ytimg.com,ggpht.com` seit dem Bild-Fix vom 22.08. (#410) |
| `INSIGHT_CITATION_STRICT_ENFORCE` | `False`, Soft-Mode. Am 20.08.2026 gemessen: 0,19 % Falsch-Zitat-Rate, Kriterium (< 2 %) erfüllt — der Flip ist durch die Daten gedeckt (s.u.) |
| alle `*_PER_1K_USD`, `USD_TO_EUR_RATE` | Code-Werte gelten — die Preistabelle oben beschreibt also die echte Kostenrechnung |
| `FEATURE_TRAILER_INTELLIGENCE_ENABLED` | in Production aus bis zur Staging-Abnahme (Montags-Entscheidung bei Wolf) |
| `SMTP_HOST` / `_USER` / `_PASSWORD` | **nirgends gesetzt.** Fällt Resend aus, greift der SMTP-Rückfallweg ins Leere: `MailerError` → 503, Login komplett tot. Es gibt keinen zweiten Weg |

Merkposten für den nächsten Wartungs-PR: das tote Feld `backend_url`
aus `config.py` und `.env.example` entfernen.

**Weiterhin nicht prüfbar:** die Netlify-Variablen (`VITE_API_BASE`,
`VITE_API_TOKEN`, `VITE_ENVIRONMENT`).

## Citation-Cutover — gemessen am 20.08.2026

`INSIGHT_CITATION_STRICT_ENFORCE` steht auf `False`. Das Kriterium für
den Umstieg auf Strikt steht in `config.py`: Falsch-Zitat-Rate < 2 %.

**Gemessen: 0,19 %.** 120 Briefs, 4.186 zitierte IDs, 8 nicht belegt,
verteilt auf 6 Briefe. Zeitraum 2026-W22 (Start der Funktion) bis W33 —
zwölf Wochen statt der geforderten zwei bis drei. Das Kriterium ist mit
zehnfachem Abstand erfüllt.

Wo es hakt, wenn es hakt:

| Sektion | Zitate | falsch | Rate |
|---|---|---|---|
| `cross_market_insight` | 512 | 6 | 1,17 % |
| `ganz_konkret[6]` | 192 | 2 | 1,04 % |
| alle übrigen 24 Sektionen | 3.482 | 0 | 0,00 % |

`cross_market_insight` ist der Ausreißer — dort zitiert das Modell
`match_key`-Werte statt Post-URLs, einen anderen ID-Raum. Die letzten
drei Wochen (W31–W33) sind fehlerfrei.

### Die Auswertung wiederholen

Sie braucht **keine Logs**. `_validate_citations` ist eine reine Funktion
von `(LLMReport, PairAggregation)`, und beide liegen als JSON-Spalten
(`llm_output`, `aggregation`) in jeder `insight_report`-Zeile.
`scripts/diag_citation_rate.py` baut sie zurück und lässt denselben
Validator noch einmal laufen — read-only, kein LLM-Aufruf, jederzeit
wiederholbar, unabhängig von der Railway-Log-Aufbewahrung:

```sh
source ~/.creative-radar/db.env && \
    python -m scripts.diag_citation_rate --pro-sektion
```

Zwei Fallen, beide beim ersten Produktionslauf getreten und behoben:

- **Validation-Context.** `LLMReport` prüft `cross_market_insight`
  bedingt; die Signale (`has_de_data`, `has_cross_market`) stehen in der
  Aggregation und kommen über `info.context`. Ohne Context gilt
  „Pflicht AN" — das verwarf jeden lionsgate-Brief (kein DE-Channel, von
  der Pflicht ausgenommen). Erst Aggregation validieren, daraus den
  Context bauen, dann den Bericht.
- **Kein `$.**`-SQL als Ersatz.** Eine jsonpath-Abfrage über
  `cited_post_ids` zählt im lax mode doppelt (Array-Unwrapping) und
  überschätzt die Menge um rund das Doppelte.

### Bekannte Altzeilen

Sechs Briefe aus W19–W22 lassen sich nicht mehr validieren:
`ganz_konkret` hat 9–10 Einträge, das Schema erlaubt heute höchstens 8.
Die Begrenzung kam nach diesen Briefen; sie tragen ohnehin null Zitate.
Kein Wartungsfall.

## Arbeitsregel Feature-Flags — Wolfs Freigabe-Modell (23.08.2026)

**Diese Regel ist verbindlich und überlebt jede Session.** Anlass: am
Wochenende 22.08. wurden vier Features ungeflaggt gemergt und waren
damit sofort in Production aktiv — Wolfs ausdrückliches Modell
(„Neues zuerst in Staging ausprobieren, ohne den laufenden Stand zu
beeinflussen") war verletzt. Die Merge-Autorisierung deckt das Mergen
ab, NICHT das Überspringen der Staging-Erprobung.

1. **Jedes neue Feature** bekommt beim Bau ein Flag nach dem
   bestehenden Schema `FEATURE_<NAME>_ENABLED` (Helper in
   `app/core/feature_flags.py`, Default **aus**, gelesen per
   `os.environ`). Sichtbarkeit im Frontend ausschließlich über
   `GET /api/health → features`; Endpoints gaten server-seitig mit
   503 + Env-Var-Name (Muster Trailer Intelligence).
2. **Rollout:** Flag in Staging auf `true` → Wolf probiert aus → nach
   seiner Freigabe Flag in Production setzen. Danach beim nächsten
   Wartungsdurchgang Flag-Cleanup erwägen (etabliertes Feature =
   Flag raus).
3. **Ausgenommen:** Bugfixes, Wartung, Sicherheit und unsichtbare
   Infrastruktur ohne UI-/Verhaltensänderung (z. B. der
   Empfehlungs-Snapshot-Cron). Im Zweifel: flaggen.

Flag-Inventar (Stand 25.08.2026; Wächter
`test_wartung_2026_08_23_flags.py` hält health-Parität und
Default-aus). Der Wächter **entdeckt die Flags seit dem 25.08. selbst**
per Introspektion über `feature_flags` — die Vorversion trug eine
Handliste, und das erste Flag nach ihrer Entstehung stand prompt nicht
darin (eine Mutation des Defaults auf `true` überlebte unbemerkt). Ein
Flag ohne health-Schlüssel muss jetzt ausdrücklich in `_NUR_CRON`
stehen; Vergessen fällt auf. Diese Tabelle ist damit Dokumentation,
nicht mehr die Prüfgrundlage:

| Flag | Feature | Staging | Production |
|---|---|---|---|
| `FEATURE_TRAILER_INTELLIGENCE_ENABLED` | Muster-Panel (TI Stufe 1) | an | aus, Abnahme Mo 25.08. |
| `FEATURE_SEGMENT_ROUNDUPS_ENABLED` | Segment-Roundups (Cron+Endpoint) | an | an |
| `FEATURE_CUTTER_WEEKLY_ENABLED` | Cutter-Wochenbriefing (Cron) | an | an |
| `FEATURE_DESIGNER_WEEKLY_ENABLED` | Designer-Wochenbriefing (Cron) | an | an |
| `FEATURE_WIR_PROJEKTE_ENABLED` | Titel-Markierung „Unser Projekt" (#412) | zu setzen | aus bis Freigabe |
| `FEATURE_PROJEKT_START_BRIEF_ENABLED` | Start-Brief je Wir-Projekt (#414) | zu setzen | aus bis Freigabe |
| `FEATURE_KAMPAGNEN_TIMING_ENABLED` | Kampagnen-Timing im Monitoring (#415) | zu setzen | aus bis Freigabe |
| `FEATURE_SOUND_TRENDS_ENABLED` | Sound-Trends im Monitoring (#416) | zu setzen | aus bis Freigabe |
| `FEATURE_KATALOG_NACHLADEN_ENABLED` | Fehlende Titel aus TMDb nachladen (#429) | an | **an** seit 25.08., Vorschau statt Staging-Abnahme |

## Katalog-Zuordnung — vier Befunde vom 25.08.2026

Ein Tag, der als „Katalog-Lücke schließen" begann und drei Fehler
aufdeckte, die vorher niemand sah. Der Reihe nach, weil die
Ursachenkette zusammenhängt.

### 1. `POST /api/titles` antwortete seit jeher `{}`

Der Commit für die Keywords lief **nach** `session.refresh`; ein
Commit macht die Instanz stale, FastAPI serialisierte danach ein
leeres `__dict__`. Auf `main` reproduziert, nicht vermutet.

Die Folge war kein Schönheitsfehler. `createTitleFromCandidate` liest
`title.id` aus der Antwort, `undefined` fällt bei `JSON.stringify`
heraus — das Asset bekam **keinen Titel**, während der Kandidat auf
`resolved` ging und der Toast „neu angelegt und zugeordnet" meldete.

So entstanden 74 geschlossene Kandidaten mit titellosem Asset. Behoben
in #436 (Refresh nach allen Commits, plus ein Wurf im Frontend, wenn
die Antwort keine ID trägt). **Merkposten:** Endpoints, deren Antwort
ein anderer Codepfad ausliest, brauchen einen Test auf die Antwort —
nicht nur auf den Statuscode.

### 2. Ein mehrdeutiger Katalog-Name schaltet die ganze Automatik ab

`_build_exact_title_lookup` kennt **drei** Zustände, und die
Unterscheidung ist sicherheitsrelevant:

| Zustand | Bedeutung |
|---|---|
| Schlüssel fehlt | nicht im Katalog |
| Schlüssel da, Wert da | eindeutig |
| Schlüssel da, Wert `None` | **mehrdeutig — Menschensache** |

Ein `lookup.get(name)` wirft die letzten beiden zusammen. Im
KI-Assist ist das harmlos (kein eindeutiger Treffer → nicht zuordnen,
der Fehler fällt auf die sichere Seite). Im Katalog-Nachladen kippte
es ins Gegenteil: der mehrdeutige Fall landete im TMDb-Pfad und legte
**noch** eine Zeile gleichen Namens an — jede Runde eine mehr (#435).

Wichtiger als der Bug ist die Folge des Zustands selbst: Autopilot,
KI-Assist **und** Nachladen lassen einen mehrdeutigen Namen alle
liegen. Ein Doppelklick auf „Titel anlegen" schaltete die Automatik
für diesen Namen also dauerhaft ab. Seit #436 gibt `create_title` den
vorhandenen Titel zurück, statt blind anzulegen; ein bereits
mehrdeutiger Name wird mit 409 abgewiesen.

### 3. Staging kann LLM-getriebene Features nicht erproben

Das Katalog-Nachladen liest den Marker `(nicht im Katalog)`. Den setzt
ausschließlich die KI-Prüfung — und die braucht den Anthropic-Key, den
Staging bewusst nicht hat. **Staging kann diesen Marker nie
erzeugen.** Wolfs Freigabe-Modell („Neues zuerst in Staging") läuft
bei jedem Feature ins Leere, dessen Eingabe aus einem LLM-Pfad stammt.

Ersatz seit #432: eine **Vorschau**. `anwenden=False` ist die Vorgabe;
derselbe Code läuft vollständig durch und wird am Ende zurückgerollt
statt festgeschrieben. Genau ein Ausdruck unterscheidet die Fälle —
ein zweiter Codepfad, der aufzählt was passieren *sollte*, wäre in dem
Moment wertlos, in dem er vom echten abweicht.

Die Vorschau hat sich am selben Tag zweimal bezahlt gemacht: sie hat
die Doubletten-Schleife sichtbar gemacht und den Fehler unter Punkt 4.

### 4. „Kein Asset" heißt nicht „Schutt"

Die erste Fassung von `titel_doubletten_aufraeumen.py` wollte jede
aktive Zeile ohne Asset stilllegen. Die Vorschau gegen Production
meldete **639 Zeilen**: „The Mummy", „Cape Fear", „Little Women",
„It", „Ocean's Eleven" — echte, verschiedene Werke gleichen Namens.
Sie haben null Assets, weil ihnen noch kein Post zugeordnet wurde; bei
19.000 Titeln trifft das auf fast alle zu.

Seit #437 gilt als Schutt nur, was **ohne `tmdb_id`** und ohne Asset
ist. Eine TMDb-Zeile wird nie angefasst. Namensgleichheit im Katalog
ist ein Zustand, kein Fehler.

### Das Ergebnis

47 verlorene Kandidaten wieder geöffnet (`luecken_wieder_oeffnen.py`,
mit Reparatur des Vorschlags aus der KI-Notiz — bei acht stand dort
noch der Matcher-Fehlgriff, `'partners'` statt
`'Steckerlfisch Fiasko'`). Davon **25 zugeordnet, null neue Titel
nötig**.

Es gab also fast keine Katalog-Lücke. Die 47 Posts waren Folge des
stillen Fehlers aus Punkt 1, nicht eines zu engen Katalogs. Das
Feature, das die Lücke schließen sollte, hat vor allem bewiesen, dass
es kaum eine gab — und den Weg zum eigentlichen Fehler gebahnt.

### Werkzeuge aus diesem Tag (Vorschau bzw. read-only per Vorgabe)

| Skript | Frage |
|---|---|
| `diag_geschlossene_luecken.py` | Welche geschlossenen Kandidaten haben ein Asset ohne Titel? |
| `diag_titel_doubletten.py` | Welche aktiven Titel teilen sich einen Normalnamen? |
| `luecken_wieder_oeffnen.py` | Zurück in die Queue, mit Namen aus der KI-Notiz |
| `titel_doubletten_aufraeumen.py` | Handgemachten Schutt stilllegen, Gruppen zusammenlegen |

### Offen

- **`minions & monsters`**: zwei aktive TMDb-Zeilen (878357 mit 43
  Assets, 1315772 mit 1). Altbestand, nicht aus diesem Tag. Ob
  dasselbe Werk unter zwei IDs oder zwei Formate — ungeklärt.
- **`the beauty of ballroom`**: eine Manual-Zeile mit einem Asset
  neben der TMDb-Zeile `10s Across the Borders`, deren **Lokaltitel**
  derselbe ist. Zusammenlegen erst nach einem Blick auf den Post.
- **27 geschlossene Kandidaten ohne Lücken-Marker** — titellos, ohne
  KI-Urteil. Kann richtig sein (Post bewirbt kein Werk) oder derselbe
  stille Fehler ohne KI-Notiz. Nicht geprüft.

## Auth-Schichten

Drei, unabhängig schaltbar:

1. **Bearer-Token** (`AUTH_ENABLED` + `API_TOKEN`) — „richtiges
   Frontend?". Liegt im öffentlichen Frontend-Bundle.
2. **Admin-Session** (`ADMIN_AUTH_ENABLED` + `ADMIN_PASSWORD` +
   `ADMIN_SESSION_SECRET`) — „richtiger Mensch?".
3. **User-Login per E-Mail-Code** (`USER_AUTH_ENABLED` +
   `USER_SESSION_SECRET`, Versand über Resend oder SMTP).

`ALLOW_AUTH_DISABLED_IN_PRODUCTION` bricht den Boot ab, wenn 1 oder 2 in
Production aus sind. `/api/admin/cron/sync-all` akzeptiert
ausschließlich `CRON_API_TOKEN` oder eine Admin-Session — nie das
allgemeine `API_TOKEN`.

## Wächter

| Datei | Läuft | Prüft |
|---|---|---|
| `test_model_defaults_drift.py` | täglich 05:20 UTC + PR | Modell-Tausch ohne Denk-Entscheidung |
| `test_wartung_2026_08_19.py` | Testlauf | ENV-Vertrag, Klartext-Codes im Log, stille Leer-Antworten, SMTP-Rückfallweg |
| `backend-tests.yml` | PR + push main | pytest gegen echtes Postgres 18 — **nur bei `backend/**`-Änderungen** |
| `vertrag-tests.yml` | PR + push main, `backend/**` UND `frontend/**` | `pytest -m vertrag`: Tests, die über die Backend/Frontend-Grenze lesen. Ohne Postgres, unter einer Minute |
| `test_wartung_vertrag_marker.py` | Testlauf | AST-Wächter: jeder Test, der (auch über Helfer) `frontend/`-Dateien liest, trägt den `vertrag`-Marker; der Workflow existiert, filtert per Marker und triggert auf beide Seiten |

Der Modell-Wächter liest die **Vorgabewerte aus `config.py`**, nicht die
Belegung in Railway. Ein per ENV gesetztes Modell sieht er nicht — er
belegt „der eingecheckte Default ist ein bekanntes Modell", nicht „das
Modell in Produktion ist bekannt".

## Kosten-Deckel

Monatsbudget je Anbieter, Pre-Flight im Cron bricht den ganzen Lauf mit
`status='budget_exceeded'` ab. Jeweils per ENV abschaltbar.

| Anbieter | Deckel | Warnschwelle |
|---|---|---|
| Apify | $50 | 80 % |
| Anthropic | $100 | 80 % |
| OpenAI | $50 | 80 % |

## Vision-Durchsatz — korrigiert am 20.08.2026

Die beiden Vision-Deckel im Cron waren an einem Preis aus der
gpt-4o-mini-Zeit kalibriert und nie nachgezogen worden.
`_VISION_COST_USD_PER_CALL` stand auf `0.015` mit dem Kommentar
„gpt-4o-mini Vision pricing ballpark"; gemessen am costlog kostet ein
Call auf `gpt-5.4-mini` **$0,0027** (1.409 Calls, 379,73 Cent) — Faktor
5,6. Diese Zahl stand in den Kommentaren, mit denen beide Deckel
begründet wurden.

Die Folge war kein Kostenfehler, sondern stiller Datenverlust. Bei rund
680 neuen Assets pro Woche ließen 50 frische + 200 Backlog etwa zwei
Drittel liegen, und Instagram-CDN-Links verfallen nach 24–48 h — was ein
Lauf nicht anfasst, ist danach oft nicht mehr analysierbar. Gemessen
über 90 Tage: 3.228 `pending`, 3.023 `analyzed`, 2.338 `fetch_failed`.

| | vorher | jetzt |
|---|---|---|
| `CRON_VISION_MAX_ASSETS_PER_RUN` | 50 | 800 |
| `CRON_VISION_BACKLOG_MAX_ASSETS_PER_RUN` | 200 | 800 |
| `_VISION_COST_USD_PER_CALL` | 0.015 | 0.0027 |
| Zeitgrenze | keine | `VISION_STAGE_TIMEOUT_SECONDS`, Default 1800 s |

Das Anheben der Deckel wäre für sich genommen fahrlässig — ein Deckel in
*Stück* sagt nichts über Laufzeit, genau der Fehler aus dem Vorfall
10.08.2026 in der Post-Analyse. Deshalb begrenzt jetzt die **Zeit**:
beide Vision-Stages teilen sich *einen* `time.monotonic()`-Zeitpunkt
(nicht je einen, sonst wäre die Summe doppelt so groß), geprüft **vor**
jedem Asset. Was nicht mehr hineinpasst, erscheint als `skipped_budget`
im Cron-Summary statt still zu verschwinden. `0` stellt das alte
Verhalten „nur Stückzahl" ohne Code-Deploy wieder her.

800 · $0,0027 ≈ **$2,20 pro Lauf** gegen einen OpenAI-Monatsdeckel von
$50. Der Altbestand von ~3.200 `pending` braucht damit rund vier Wochen.

Geprüft in `app/tests/test_wartung_2026_08_20_vision.py` (14 Tests,
sechs Mutationen verifiziert).

## Testlauf lokal

```sh
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
ALLOW_SQLITE_FALLBACK=true .venv/bin/python -m pytest app/tests -q
```

Stand 20.08.2026: **1565 bestanden, 10 übersprungen**, ~2 Minuten.
