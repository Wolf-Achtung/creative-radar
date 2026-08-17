# Creative Radar

Online-first Monorepo für ein internes KI-gestütztes Creative Monitoring im Film-, Serien- und Game-Marketing.

**Stand August 2026** (README-Refresh im Audit 2026-08-17 — der alte Text beschrieb noch den v1-MVP mit manuellem Import):
Der wöchentliche Cron (Montag 03:00 UTC, in-process im Railway-Backend) sammelt via Apify und YouTube-API rund 500 Posts pro Woche über **Instagram, TikTok und YouTube**, analysiert Standbilder per Vision-Modell, klassifiziert Posts (Format/Ton/Zweck) und generiert Wochen-Briefs, Segment-Roundups und Cutter-/Designer-Briefings per LLM. Ein Mensch kuratiert weiterhin vor jedem Report. Drei Auth-Schichten (Bearer, Admin-Session, E-Mail-Login), Monats-Budget-Caps für alle Provider, Cost-Logging pro Call. Aktueller Arbeitsschwerpunkt: **Trailer Intelligence Stufe 5** (Schnitt-Rhythmus-Analyse, siehe `docs/TRAILER_INTELLIGENCE_STUFE5_PLAN_B.md`).

## Lokal starten in drei Befehlen

Docker Desktop + Node 22 vorausgesetzt — läuft komplett offline, kein API-Key nötig
(Mock-Modus, Details in [docs/STAGING_SETUP.md](docs/STAGING_SETUP.md)):

```sh
docker compose up -d                                    # Postgres 18 + Backend (Hot-Reload)
docker compose exec backend python -m scripts.seed_dev  # synthetische Testdaten (idempotent)
cd frontend && npm install && npm run dev               # Vite-Dev-Server auf :5173
```

API: http://localhost:8000 · Frontend: http://localhost:5173 · DB (Adminer): http://localhost:8080

## Projektlogik

Creative Radar dokumentiert relevante Social-Media-Treffer zu einer gepflegten Titel-/Franchise-Whitelist (TMDb-gestützt) und verdichtet sie zu evidenzbasierten Wochen-Briefings je Studio-Pair.

**Workflow (Produktion):**

1. Channels, Pairs und Titel-Whitelist im System pflegen.
2. Montags-Cron: Scrape (Apify/YouTube) → Vision-Analyse → Post-Klassifikation → Title-Sync → Rematch.
3. Brief-/Roundup-Generierung (LLM) mit Citation-Prüfung und Budget-Caps.
4. Mensch kuratiert: Approve / Highlight / Reject; manueller Instagram-Link-Import bleibt als Ergänzung.
5. Reports und Briefings im Dashboard; HTML-Export.

## Monorepo-Struktur

```text
creative-radar-v1/
  backend/    FastAPI API für Railway
  frontend/   React/Vite Dashboard für Netlify
  docs/       Online-Setup, Datenleitplanken, MVP-Auswahl
```

## Ziel-Deployment

- **Ein GitHub-Repository:** `creative-radar`
- **Backend:** Railway Service aus Ordner `backend`
- **Datenbank:** Railway Postgres
- **Frontend:** Netlify Site aus Ordner `frontend`
- **KI:** später OpenAI API, v1 funktioniert zunächst mit Platzhalter-Analyse

## Online-Setup in Kurzform

1. GitHub Repo `creative-radar` anlegen.
2. ZIP-Inhalt über GitHub Web UI hochladen.
3. Railway Projekt anlegen.
4. Railway Postgres hinzufügen.
5. Railway Backend-Service aus GitHub verbinden.
6. Railway Root Directory auf `backend` setzen.
7. Railway Service deployen und Public URL kopieren.
8. Netlify Site aus demselben GitHub Repo verbinden.
9. Netlify Base directory auf `frontend` setzen.
10. Netlify ENV `VITE_API_BASE=https://DEINE-RAILWAY-URL.up.railway.app` setzen.
11. Netlify deployen.
12. Im Dashboard auf „MVP-Daten anlegen“ klicken.
13. Ersten Post-Link manuell importieren.

Die ausführliche Schritt-für-Schritt-Anleitung steht in:

```text
docs/online_only_setup.md
```

## Scope

Enthalten (Stand August 2026):

- Automatische Sammlung: Instagram, TikTok (Apify), YouTube (Data API)
- Whitelist-basierte Relevanzprüfung, Titel-Matching gegen TMDb
- Vision-Analyse von Post-Standbildern; Post-Klassifikation (Format/Ton/Zweck/Lifecycle)
- LLM-Wochen-Briefs, Segment-Roundups, Cutter-/Designer-Briefings — mit menschlicher Freigabe
- Manuelle Trailer-Schnitt-Annotation (Tap-Along-Annotator, `tools/`) für Stufe 5

Nicht enthalten:

- echtes Klicktracking, Performance-Ranking fremder Accounts
- Video-Download von Drittplattformen (bewusste ToS-Entscheidung, siehe `docs/TRAILER_INTELLIGENCE_STUFE5_PLAN_B.md`)
- automatische Video-Frame-Analyse (P2-Backlog F2.9)
- Versand ohne menschliche Freigabe

## Wichtige Leitplanke

Creative Radar v1 beschreibt sichtbare Creative-Muster. Es behauptet keine echten Klicks oder Performance-Erfolge fremder Accounts.
