# Executive Summary — Diagnose-Sprint 2026-05-08

> **Lese-Hinweis:** Diese Diagnose ist **code-only** durchgezogen. Der
> Sandbox-Host konnte weder die Production-API (`api.creative-radar.de`)
> noch die DB direkt erreichen, und es lagen weder `API_TOKEN` noch
> `DATABASE_URL` im Env. Alle Mengen-/Frische-Aussagen, die Live-Daten
> brauchen, sind in den Detail-Dokumenten als *needs live verification*
> markiert. Wolf-Ping zur Token-/Allowlist-Frage am Ende des Tagesberichts.

## Top-5-Findings

1. **Insight-Engine ist konfigurations-getrieben, nicht TikTok-codegebunden.**
   Die einzige Plattform-Hardcodierung sitzt im PAIRS-Dict
   (`insight_engine.py:78-162` — sieben Mal `"platform": "tiktok"`). Keine
   `if platform == "tiktok"`-Verzweigung im Code. Multi-Plattform V2a ist
   eine PAIRS-Erweiterung + Aggregations-Refactor (zweite Achse Plattform×Markt),
   nicht ein neuer Service.

2. **Briefings werden nicht persistiert.** Der `/api/insights/weekly`-Endpoint
   ruft Opus 4.7 bei jedem Frontend-Open frisch auf (~$0.40/Brief). Die
   Legacy-`weeklyreport`-Tabelle wird vom neuen Pfad **nicht** beschrieben.
   Ohne Persistenz kann Wochen-Cadence keinen Wert liefern (Brief existiert
   erst, wenn GF ihn anfragt) und die Cost-Linie ist an Reload-Verhalten
   gekoppelt, nicht an Brief-Anzahl.

3. **YouTube ist im Cron nicht aktiv.** Cron-Sync läuft alle 3 Tage über
   IG + TikTok via Apify (`api/cron.py`). YouTube-Connector existiert,
   ist getestet und nur per `POST /api/admin/youtube/sync/{channel_id}`
   manuell aufrufbar. ENUM `acquisition_strategy` hat Default `apify` —
   YT-Channels ohne expliziten Update sind potenziell auf der falschen
   Strategy. Vor YT-Aktivierung: ein-Statement-Backfill.

4. **Disney-US-Pair zeigt latenten Bug.** `PAIRS["disney"]` erwartet
   US-Handle exakt `disney`. Whitelist-Migration `e5d8f1a36b40` registriert
   nur `disneystudios` und `disneyanimation`. `aggregate_pair` schreibt das
   Fehlen in `notes` und crasht nicht — das US-Panel zeigt "Channel nicht
   in DB" statt Daten. Hat Wolf bisher übersehen.

5. **Frontend hat einen Key-Mismatch zwischen Prompt und JSX.** Der
   SYSTEM_PROMPT verlangt JSON-Keys mit Umlauten (`für_cutter`,
   `tonalität`, `begründung`). Das Frontend liest `output.fuer_cutter`,
   `output.tonalitaet`, `t.begruendung`. Wenn Opus seine Umlaut-Regel
   strikt umsetzt, blendet das Frontend ganze Sektionen aus. Live-Verifikation
   eines echten Outputs nötig — vermutlich produziert Opus aktuell ASCII-Keys
   trotz Umlaut-Regel, aber das ist kein verlässlicher Zustand.

## Top-3-Risiken

1. **Cost-Drift durch fehlende Persistenz** — siehe Finding 2. Bei
   Wochen-Cadence × 6 Pairs × N-Reloads/GF/Tag wächst der Cost linear
   mit Engagement statt mit Inhalt.

2. **Ranking-Sortierung benachteiligt YouTube und Reichweiten-Posts.** Der
   bestehende `_engagement_sum` ist eine vier-Metrik-Summe ohne Views;
   YT hat nur zwei davon (`likes + comments`). Ohne Aktivierungs-Rate-Decision
   produziert Ranking-V1 strukturell schiefe Top-Listen.

3. **Voice-/Frontend-Drift durch Schemata-Doppelt-Pflege.** PAIRS im
   Backend (`insight_engine.py:78`) und FALLBACK_LABEL im Frontend
   (`InsightWeekly.jsx:10`) müssen synchron gehalten werden — nur per
   Code-Kommentar gesichert. Die Hauptseite (`App.jsx:918-924`) hat
   einen dritten Slug-Block, hartkodiert.

## Empfohlene Sprint-Reihenfolge

Die geplante Reihenfolge **Ranking → Voice → Multi-Plattform V2a →
YouTube → Cadence → GF-Briefing** ist im Wesentlichen tragfähig, mit
einer Verschiebung:

> **Briefing-Persistenz vor Cadence einschieben** (kleiner Mini-Sprint
> nach Multi-Plattform V2a, bevor YouTube an die Reihe kommt). Ohne
> Persistenz hat Cadence keinen Output; Persistenz allein ist 1-2 Tage
> Backend-Arbeit (neue `insight_report`-Tabelle + Cron-Auto-Trigger).

Empfohlene Vor-Sprint-Tasks (alle Read-only-Diagnose hat sie nicht selbst
gelöst, sind aber blockierend für Ranking-Sprint):

- **Wolf-Decision Aktivierungs-Rate** (`06-OPEN-QUESTIONS.md` Frage 1).
- **Wolf-Decision Disney-Handle** (Frage 2) — unblock vor erstem Disney-Brief-Test.
- **Live-Channel-Inventur** mit Token: bestätigen, dass alle 6 aktiven
  Pairs DE+US-Channels in A-Class haben (sonst 3-9 Tage Daten-Drift).
- **Follower-Count-Backfill** als One-Off-Skript-Lauf (für Aktivierungs-
  Rate-Normalisierung — heute nur in `raw_payload` zugänglich).

## Was nicht zu tun ist

- `weeklyreport`-Tabelle nicht löschen, bevor Persistenz-Decision steht.
- Disney-Handle nicht "schnell fixen" — Wolf-Decision in `06-OPEN-QUESTIONS`.
- Few-Shot-Pseudo-Umlaute im Prompt nicht entfernen, bevor Frontend-Keys
  geklärt sind (siehe Finding 5).

Detail-Dokumente: `01`-DB · `02`-Insight-Engine · `03`-Cron/Sync ·
`04`-Frontend/Voice · `05`-Ranking · `06`-Wolf-Decisions.
