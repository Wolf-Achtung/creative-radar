# Creative Radar v1

Online-first Monorepo für ein internes KI-gestütztes Creative Monitoring im Film-, Serien- und Game-Marketing.

## Projektlogik

Creative Radar v1 dokumentiert relevante Social-Media-Treffer zu einer gepflegten Titel-/Franchise-Whitelist. Der MVP startet bewusst mit manuellem Post-Link-Import statt automatischem Instagram-Scraping.

**Workflow v1:**

1. Channels und Whitelist im System anlegen.
2. Relevante Instagram-Post-Links manuell importieren.
3. KI-/Platzhalter-Zusammenfassung erzeugen lassen.
4. Mensch kuratiert: Approve / Highlight / Reject.
5. Weekly Report als HTML-Draft erzeugen.

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

## Scope v1

Enthalten:

- Instagram/Public-Post-Monitoring zunächst als manueller Import
- Whitelist-basierte Relevanzprüfung
- Asset-Typen: Trailer, Teaser, Poster, Story, Kinetic, Character Card, Review Quote, CTA Post, Unknown
- menschliche Freigabe vor Report
- Weekly Report als HTML
- Dashboard für Import, Review und Report-Draft

Nicht enthalten:

- echtes Klicktracking
- Performance-Ranking
- automatische Instagram-Sammlung
- TikTok/YouTube/Facebook
- automatische kreative Bewertung
- Versand ohne menschliche Freigabe

## Wichtige Leitplanke

Creative Radar v1 beschreibt sichtbare Creative-Muster. Es behauptet keine echten Klicks oder Performance-Erfolge fremder Accounts.
