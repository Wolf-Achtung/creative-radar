# Creative Radar — Steckbrief

Kurzprofil für Wartung und Wiedereinstieg. Was hier steht, ist am
genannten Datum am Code oder an der Anbieter-Doku geprüft worden — nicht
aus dem Gedächtnis geschrieben. Wo eine Quelle nicht erreichbar war,
steht das da.

**Letzter Wartungsdurchgang: 19.08.2026**

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

### Offene Frage zum Denkverhalten

`app/tests/test_model_defaults_drift.py` führt `claude-opus-4-8` mit
`denkt_per_default: False`. Die Anbieter-Doku sagt am 19.08.2026:
Adaptive Thinking **Yes**, und „The API default is `high`" für den
`effort`-Parameter. Kein Opus-Aufrufort im Projekt übergibt `effort`.
Details und Folgen stehen im Wartungsbericht (PR vom 19.08.) — hier
bewusst nicht entschieden.

### Preise (pro 1k Token, USD)

| Modell | Config | Anbieter-Doku 19.08.2026 | |
|---|---|---|---|
| Haiku 4.5 | 0.001 / 0.005 | $1 / $5 pro MTok | stimmt |
| Sonnet 5 | 0.003 / 0.015 | $2 / $10 pro MTok | Config liegt **höher** |
| Opus 4.8 | 0.005 / 0.025 | $5 / $25 pro MTok | stimmt |

Der Sonnet-Kommentar in `config.py` begründet die Abweichung mit einer
Preiserhöhung zum 01.09.2026. Auf der Preisseite steht dazu am
19.08.2026 nichts; die Cost-Logs überschätzen den Sonnet-Anteil solange
um 50 %.

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
`REMATCH_STAGE_TIMEOUT_SECONDS`, `ENABLE_TITLE_SYNC_IN_CRON`,
`ENABLE_BRIEF_GEN_IN_CRON`, `ENABLE_INTERNAL_CRON_SCHEDULER`,
`FEATURE_SEGMENT_ROUNDUPS_ENABLED`, `FEATURE_CUTTER_WEEKLY_ENABLED`,
`FEATURE_DESIGNER_WEEKLY_ENABLED`, `PG_STATEMENT_TIMEOUT_MS`,
`CRON_SYNC_INTERVAL_DAYS`, `CRON_A_CLASS_THRESHOLD`, `CRON_A_CLASS_MAX`,
`SEED_DEV_ON_DEPLOY`, `SEED_DEV_PAIRS` (Deploy-Bootstrap),
`CR_DB_URL` (nur lokale Diagnose-Skripte in `scripts/`).

Frontend (Netlify, Build-Zeit): `VITE_API_BASE`, `VITE_API_TOKEN`,
`VITE_ENVIRONMENT`. Stehen in keiner `.env.example` — das Beispiel deckt
nur das Backend ab.

**Was von hier aus nicht prüfbar ist:** welche Variablen in Railway und
Netlify tatsächlich gesetzt sind. Die Gegenüberstellung oben vergleicht
Code gegen `.env.example`, nicht Code gegen Deployment. Verwaiste
Variablen im Railway-Projekt — etwa `PERPLEXITY_API_KEY` und
`PERPLEXITY_MODEL`, im Kosten-Audit 01.08.2026 aus dem Code entfernt —
fallen nur bei einem Blick in die Railway-Oberfläche auf.

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
| `backend-tests.yml` | PR + push main | pytest gegen echtes Postgres 18 |

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

## Testlauf lokal

```sh
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
ALLOW_SQLITE_FALLBACK=true .venv/bin/python -m pytest app/tests -q
```

Stand 19.08.2026: **1518 bestanden, 10 übersprungen**, ~2 Minuten.
