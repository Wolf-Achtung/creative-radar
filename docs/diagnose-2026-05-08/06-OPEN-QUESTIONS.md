# 06 — Wolf-Decisions vor Implementierungsstart

Sieben Entscheidungen, die vor dem Ranking-Sprint geklärt sein sollten.
Jede mit Empfehlung (Option A) und realistischen Alternativen.

---

## Frage 1 — Aktivierungs-Rate-Formel

Wie definieren wir "Aktivierungs-Rate" für das Ranking, gegeben dass
TikTok/Instagram alle fünf Engagement-Metriken liefern, YouTube aber
keine Shares/Saves hat?

- **A (Empfehlung) — Branchen-Standard mit YT-Sonderpfad:**
  `(likes + comments + saves) / views` für TT/IG;
  `(likes + comments) / views` für YT. Branchenüblich, GF kennt die
  Größenordnung, YT-Pfad transparent dokumentiert.
- **B — Engste Definition:** `likes / views` über alle Plattformen. Maximale
  Vergleichbarkeit, ignoriert aber Comments und Saves — die im Trailer-
  Kontext stark sind.
- **C — Code-Konsistenz:** `_engagement_sum / views` (alle vier Metriken
  + Shares). Konsistent mit bestehendem `_engagement_sum`-Helper, aber
  Shares-Definitionen unterscheiden sich plattform-übergreifend.

→ Decision triggert auch Frage: zeigen wir die **Rate** als primäres
Sortier-Signal oder den **absoluten Engagement-Sum** wie heute?

---

## Frage 2 — Disney-US-Handle in PAIRS

`PAIRS["disney"]` erwartet US-Handle exakt `disney`. Whitelist-Migration
`e5d8f1a36b40` registriert nur `disneystudios` und `disneyanimation` für
Disney US. Live-Bestätigung ausstehend.

- **A (Empfehlung) — PAIRS auf `disneystudios` umstellen:** entspricht der
  Whitelist-Realität, fängt Animation getrennt mit ein. Konsistent mit
  `paramountpics` (kein `paramountpictures`).
- **B — Channel `disney` US/TT manuell anlegen:** falls dieser Handle
  intentionell der Master sein sollte (und in Production existiert).
- **C — Pair `disney` in zwei splitten:** `disney_studios` (Live-Action)
  und `disney_animation` — entspricht der Whitelist exakt, bringt aber
  zusätzliche Frontend-Komplexität.

→ Live-Verifizierung des aktuellen Stands per
`SELECT handle FROM creative_radar.channel WHERE handle ILIKE '%disney%' AND platform='tiktok' AND market='US'`
nötig, bevor entschieden wird.

---

## Frage 3 — Persistenz für Briefings

Der neue Insight-Engine-Pfad schreibt nichts in die DB. Jeder
Frontend-Open generiert einen frischen LLM-Call (~$0.40/Brief). Vor
Cadence-Sprint muss das geklärt sein.

- **A (Empfehlung) — Neue Tabelle `insight_report`:** primary_key
  `(pair_key, iso_year, iso_week)`, JSON-Spalten für `aggregation` und
  `llm_output`. Sauberer Schnitt, keine Legacy-Lasten.
- **B — `weeklyreport`-Legacy-Tabelle reaktivieren:** mit `pair_key`-Spalte
  erweitert. Spart eine Tabelle, vermischt aber Phase-4-Reports und
  neuen Pfad in einer Tabelle.
- **C — Anthropic-prompt-caching aktivieren** (System-Prompt cachen,
  User-Prompt frisch). Reduziert Kosten ~70 %, löst aber das
  GF-Wartezeit-Problem nicht (Modell muss trotzdem rechnen).

→ A + C kombinieren ist möglich (Persistenz + Caching für Re-Generation).

---

## Frage 4 — `weeklyreport`-Tabelle: aktiv halten oder formal deprecated?

Alte Phase-4-Tabelle, vom neuen Insight-Engine-Pfad nicht beschrieben.

- **A (Empfehlung) — Formal deprecated, im Schema lassen:** Migration zur
  Entfernung erst, wenn Frage 3 (Persistenz) entschieden ist und niemand
  die alten Drafts mehr braucht.
- **B — Sofort entfernen:** Migration `drop table weeklyreport`. Risiko:
  alte HTML-Drafts im Storage werden von keinem Code mehr referenziert,
  aber falls jemand auf die DB-Spalten verlinkt, geht das verloren.
- **C — Reaktivieren** als Persistenz-Tabelle für neuen Pfad — siehe
  Frage 3-B.

---

## Frage 5 — YouTube-Aktivierung vor oder nach Cadence?

Wolfs Roadmap setzt YouTube als Sprint 4 (nach Multi-Plattform V2a, vor
Cadence). Aus der Diagnose: ohne YT-Cron-Hook hat das Wochen-Cadence
keine YT-Daten. Manueller `/api/admin/youtube/sync/{channel_id}` reicht
nicht für ein automatisches Briefing.

- **A (Empfehlung) — YouTube vor Cadence (Roadmap-konform).** Cron-Hook
  in `_execute_platform_sync`, Strategy-Backfill (`UPDATE channel SET
  acquisition_strategy='youtube_api' WHERE platform='youtube'`),
  Quota-Überwachung im Code.
- **B — YouTube parallel zu Multi-Plattform V2a:** YT-Karten erscheinen
  in Studio-Briefen sobald der Connector im Cron-Hook ist. Kein eigener
  Sprint nötig.
- **C — YouTube nach Cadence:** Cadence wird mit TT+IG-only ausgerollt,
  YT kommt später als Erweiterung. Risiko: ein GF mit YT-Erwartung
  enttäuscht.

→ Wolfs Roadmap-Reihenfolge erscheint mir richtig — YT als 4 ist OK,
solange das Cadence-Sprint-Backlog explizit "ohne YT" sagt.

---

## Frage 6 — Multi-Plattform V2a: Pair-Definition

Aktuell hat jeder Pair ein einzelnes `platform`-Feld. Multi-Plattform V2a
soll TT + IG kombinieren in einem Studio-Brief. Strukturell:

- **A (Empfehlung) — `platforms: list[str]` im PAIRS-Dict, Aggregation
  per (Plattform × Markt):** ein Pair, zwei oder drei
  `ChannelStats`-Slices pro Markt, Frontend rendert eine Plattform-Pille
  pro Sektion. Saubere Erweiterung des bestehenden Schemas.
- **B — Mehrere Pair-Keys pro Studio:** `disney_tt`, `disney_ig`,
  `disney_yt` — Frontend kombiniert sie. Maximaler Decoupling, aber
  Frontend-Routing wird aufgeblähter.
- **C — Plattform als Tab innerhalb eines Pairs:** ein Brief pro Pair,
  Tab-Switch zwischen den Plattformen. Verschiebt die Aggregations-
  Komplexität ins UI.

→ A skaliert am saubersten zur YT-Erweiterung in Sprint 4.

---

## Frage 7 — Headline/TLDR: separater LLM-Call?

Heute sind `headline` und `tldr` Felder im großen JSON-Output (ein einziger
Opus-Call). Voice-Korrektur-Sprint könnte sie isolieren.

- **A (Empfehlung) — Im großen Call lassen:** spart einen LLM-Call pro
  Brief, behält Kontext-Konsistenz. Voice-Korrektur erfolgt durch
  schärfere Prompt-Regeln, nicht durch zweiten Call.
- **B — Separater Mini-Call** mit Haiku 4.5 für Headline + TLDR: $0.001
  statt $0.05 pro Brief. Spart Geld, doppelter Call ist mehr UI-Latenz.
- **C — Headline/TLDR Frontend-seitig generieren** aus `aggregation`-Daten
  + Template (kein LLM): null Cost, aber lebloser Text. Voice-Drift
  garantiert.

→ A ist konservativ und lässt sich zu B umbauen, sobald wir sehen, wo
der Cost wirklich auflandet.

---

## Optional: Frage 8 — `fuer_cutter` vs. `für_cutter` (Frontend-Mismatch)

Frontend-Code liest `output.fuer_cutter`, `tonalitaet`, etc. — der Prompt
beschreibt diese Felder mit Umlauten (`für_cutter`, `tonalität`). Siehe
`04-FRONTEND-AND-VOICE.md` Inkonsistenz #4.

- **A (Empfehlung) — Prompt auf ASCII-Keys umstellen** (`fuer_cutter`,
  `tonalitaet`, `fuer_motion_designer`, `fuer_creative_producer`,
  `begruendung`). JSON ohne Umlaut-Keys ist robuster (manche Parser/IDEs
  haben Probleme).
- **B — Frontend auf Umlaut-Keys umstellen.** Macht den Frontend-Code
  hässlicher, aber bleibt textseitig konsistent.

→ Schnell-Win-Decision: **A** vor dem nächsten Voice-Touch. Risiko
prüfen, ob ältere persistierte Reports (falls Frage 3 entschieden ist)
mit alten Keys lesbar bleiben.
