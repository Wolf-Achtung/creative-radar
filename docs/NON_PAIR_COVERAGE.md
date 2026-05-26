# NON_PAIR_COVERAGE.md — Segment-Roundups für Non-Pair-Channels

**Status:** Konzept · **Stand:** 25.05.2026 · **Autor:** Wolf
**Vorbild:** `UK_MARKET_INTEGRATION.md` (Konzept-vor-Sprint-Logik)
**Verwandte Stubs:** `is_independents_enabled()`, `is_single_market_schema_enabled()` (PR #155, aktuell ohne Konsumenten)

---

## 1. Problem / Diagnose

Creative Radar scrapt auf **Channel-Ebene**, erzeugt Output aber ausschließlich auf
**Pair-Ebene**. Jeder Brief ist an genau ein Pair gekoppelt.

Folge: Channels, die in keinem Pair-Pool stehen, werden zwar gescrapt und ihre Posts
landen in der DB — es entsteht aber kein Output. Konkreter Auslöser: `hbo` (US IG)
wird aktiv gescrapt, hängt in keinem Pair, hat keinen Brief.

`hbo` ist kein Einzelfall, sondern legt eine strukturelle Lücke offen: Über die 9
Pairs hinaus existieren zahlreiche relevante Majors und Independents in US, UK und DE,
deren Output wöchentlich abgebildet werden soll.

**Kern der Diagnose:** Die Datenbeschaffung ist bereits gelöst (der Scraper verkraftet
beliebige zusätzliche Channels — `hbo` beweist es). Was fehlt, ist **ein Brief-Typ,
der nicht pair-gebunden ist.**

---

## 2. Getroffene Entscheidungen

Zwei Architektur-Entscheidungen sind vorab geklärt (Wolf, 25.05.):

**(a) Output-Einheit: Segment-Roundup.**
Nicht ein Brief pro Channel (skaliert nicht — 50+ Channels = 50+ Briefe/Woche,
sprengt das Budget), sondern **ein Brief pro Segment**. Ein Roundup aggregiert das
Top-Material aller Channels eines Segments zu einem wöchentlichen Radar-Sweep der
Kategorie.

**(b) Roundup rein deskriptiv.**
Der Roundup beschreibt, was im Segment in der Woche lief — **ohne Markt-Vergleich,
ohne Matching.** Damit teilt der Roundup kein Schema mit dem Pair-Brief und es gibt
keinen gemeinsamen Code-Pfad, über den ein Roundup-Bug einen Pair-Brief erreichen
könnte.

**Konsequenz beider Entscheidungen:** Der Roundup braucht **keine Matching-Logik** und
umgeht damit die fundamentale Independents-Frage (Single-Market-Pair vs.
Cross-Distribution-Pair, Aufwandsfaktor 2-5) vollständig. A24 ist im Roundup schlicht
ein Channel im Segment `us_independent` — kein Matching-Problem. Vergleichende
Independents-Briefs bleiben eine separate, spätere Entscheidung; der Roundup wartet
nicht darauf.

---

## 3. Segment-Taxonomie

Jeder klassifizierte Channel bekommt genau ein **Segment** (Markt × Tier) — sechs
Segmente:

| Segment            | Markt | Tier        | Beispiel-Channels        |
|--------------------|-------|-------------|--------------------------|
| `us_major`         | US    | Major       | hbo, disneyplus, …       |
| `us_independent`   | US    | Independent | a24, neonrated, …        |
| `uk_major`         | UK    | Major       | …                        |
| `uk_independent`   | UK    | Independent | …                        |
| `de_verleih`       | DE    | Verleih     | constantinfilm, …        |
| `de_independent`   | DE    | Independent | xverleih, …              |

**Datenmodell:** ein neues Feld `segment` auf der Channel-Tabelle, als Postgres-ENUM
`creative_radar.channel_segment` (umgesetzt in Migration `e1c93a4d7f08`, nullable ohne
Default).

### Klassifizierungsregel (verbindlich)

Maßgeblich ist die **Content-Positionierung**, nicht der Konzern-Eigentümer. Ein
Major-eigenes Arthouse-Label wird nach seinem Output klassifiziert, nicht nach seiner
Muttergesellschaft.

1. **Tier:** Mainstream / Wide-Release → `_major`. Arthouse / Specialty / Prestige →
   `_independent`. Gilt unabhängig vom Eigentümer — Focus Features (Universal),
   Searchlight (Disney), Sony Pictures Classics zählen als `_independent`.
2. **Streamer** zählen als Major-Distributor (`_major` bzw. `de_verleih`).
3. **DE-Markt:** `de_verleih` = DE-Pendant zu `_major` (marktführende Distributoren
   und Streamer-Dependancen, z. B. Constantin, LEONINE, HBO Max DE). `de_independent`
   = unabhängige Arthouse-Verleiher (z. B. X Verleih). Der Wertname `de_verleih` ist
   branchenüblich beibehalten, auch wenn er vom market×tier-Muster der übrigen fünf
   Werte abweicht.

### `segment = NULL` als explizite Restklasse

`NULL` ist kein „noch nicht klassifiziert", sondern eine **bewusste Kategorie**: alles,
was kein einem der drei Märkte zuordenbarer Content-Distributor ist, bleibt `NULL` und
damit außerhalb des Roundup-Scopes. Das umfasst:

- **Pair-Pool-Channels** — disjunkt zu den Roundups (siehe §2, Wolf-Entscheidung
  25.05.).
- **Trade-/Discovery-Press** (z. B. moviepilot, indiewire) — berichtet *über* Content,
  ist kein Distributor.
- **INT-Channels** (z. B. bad_robot, `market=INT`) — kein klares Markt-Präfix; bei
  künftiger Häufung ist die Taxonomie neu zu bewerten.

> **Schema-Vorbehalt erledigt:** Channel-Tabelle ist `creative_radar.channel`
> (verifiziert in Phase 0). Kein existierendes Feld bildet die Major/Independent-
> Distinktion ab — `channel_role` kennt sie nicht — daher das neue Feld.

Pair-Channels bleiben `segment = NULL` und sind von der Roundup-Logik **nicht
betroffen**. Der Roundup zieht ausschließlich Channels mit gesetztem `segment`, sofern
die Roundup-Generierung freigeschaltet ist (Flag, siehe §6).

**Befüllung:** einmaliger Klassifizierungs-Pass über die Whitelist (~168 Channels,
Stand vor Pass verifizieren). Hartkodierte Zuordnungsliste — kein Algorithmus. Das ist
eine Operations-/Migrations-Aufgabe, kein Code-Sprint.

---

## 4. Roundup-Brief-Schema (deskriptiv)

Eigenes Schema, **nicht** geteilt mit dem Pair-Brief. Inhalt pro Roundup:

- **Segment-Kopf:** Segment-Name, KW, Zeitfenster, Anzahl ausgewerteter Channels.
- **Pro Channel:** kurzer Aktivitäts-Abriss (Anzahl Posts im Fenster), Top-Posts mit
  den vorhandenen Engagement-Metriken.
- **Segment-Synthese:** ein vom LLM erzeugter deskriptiver Abschnitt — was lief diese
  Woche im Segment, ohne Vergleich zu anderen Märkten oder Segmenten.
- **Keine** Cross-Market-Matches, **keine** vergleichenden Aussagen.

**Fenster:** `window_days = 14` (Wolf-Entscheidung 25.05.). Anmerkung zur
Faktenlage: Die Pair-Pipeline nutzt real `window_days = 30` (verifiziert in Phase 0,
`aggregate_pair` / `generate_weekly_report`) — die frühere Annahme „identisch zur
Pair-Logik" war falsch. Der Roundup setzt 14d bewusst als eigenen, engeren Wert; das
Fenster ist als Parameter geführt, nicht hartkodiert, und später ohne Code-Änderung
anpassbar.

**Stub-Nutzung:** `is_single_market_schema_enabled()` (PR #155) ist genau für ein
nicht-vergleichendes Single-Segment-Schema angelegt und wird hier zum Konsumenten —
auch als Gate für den manuellen Pilot-Auslöser. Die endgültige Flag-Benennung ist erst
in Schritt 4 zu entscheiden (siehe §8); die Zuordnung im Pilot ist vorläufig.

---

## 5. Kostenmodell

**Bekannte Basis:** ~1,92 $/Pair (17,27 $ für 9 Pairs, Force-Run 17.05.).
Anthropic-Bestand aktuell ~64 $/Monat.

**Schätzung Roundup — ausdrücklich Schätzung, keine Messung:** Ein Roundup hat mehr
Input-Tokens als ein Pair-Brief (mehr Channels, mehr Posts je Lauf). Wie viel mehr
hängt am Top-N-Cutoff. Grobe Spanne für sechs Segmente: **+80–100 $/Monat**.

**Cap-Lage:** `ANTHROPIC_MONTHLY_BUDGET_USD = 200` (F0.7, PR #159), Soft-Warn bei 80 %
= 160 $. Rechnung: 64 $ Bestand + ~80–100 $ Roundup = **~144–164 $** — unter dem Cap,
kann aber den Soft-Warn auslösen. Kein hartes Kostenproblem mehr, aber der Wert ist
eine Schätzung und muss gemessen werden.

**Pilot-Pflicht:** Schritt 3 (siehe §7) wird mit **genau einem Segment** gefahren —
Vorschlag `de_verleih` oder `us_major` (datenreich). Ein Lauf, Ist-Kosten messen,
hochrechnen. Erst danach Roll-out der übrigen Segmente. Diagnose-First, auf Kosten
angewandt.

---

## 6. Cron-Integration

- Roundups laufen im bestehenden Montags-Cron (06:00 UTC), hinter dem Feature-Flag
  `is_independents_enabled()` (oder einem breiter benannten Flag). Default **aus** →
  Cadence verhält sich exakt wie heute, kein Roundup ohne explizit gesetztes Flag.
- **Reihenfolge zwingend: Pair-Briefs vor Roundups.** Die Pairs sind das Kernprodukt
  und bekommen das Budget zuerst. Greift während eines Laufs das F0.7-Cap
  (`_budget_enforced`, harter Lauf-Abbruch), trifft der Abbruch dann die noch nicht
  erzeugten Roundups — nicht die Pair-Briefs.
- Klarstellung zur Trennung: Die 9 bestehenden Pair-Briefings werden durch diese
  Erweiterung **nicht berührt** — eigener Brief-Typ, eigenes Schema, eigener
  Cron-Abschnitt. Ein Cap-Abbruch beschädigt keine bereits in der DB liegenden Briefe;
  er kann nur einen neuen Lauf abschneiden, und die Reihenfolge stellt sicher, dass
  dann zuerst die Roundups entfallen.
- `cron_run.summary_json` um einen Roundup-Abschnitt erweitern (Typ bleibt `json`,
  nicht `jsonb`). `status`-Werte unverändert: running/completed/failed/budget_exceeded.

---

## 7. Umsetzung — vier Schritte

| # | Schritt | Art | Status |
|---|---------|-----|--------|
| 1 | Dieses Konzept-Dokument | Konzept | erledigt |
| 2 | `segment`-Feld + Klassifizierungs-Pass + Markt-Sanierung (Lesart C) | Migration / Operations | **erledigt 25.05.2026, PR #164** |
| 3 | Roundup-Generator (Backend, `us_major`-Pilot) | Code-Sprint | **erledigt 25.05.2026, PR #165** |
| 4 | Cron-Integration + Roll-out 4 Segmente | Code + Operations | **erledigt 25.05.2026, PR #166** |
| 3b | Frontend-Anzeige der Roundups | Code-Sprint | **als nächstes** |

**Sprint-Reihenfolge:** 2 → 3 (Pilot) → 4 (Cron-Roll-out) → 3b (Frontend).

### Schritt 4 — Ergebnis (deployt auf Prod, 25.05.2026)

PR #166, fünf Commits: Flag-Rename `FEATURE_SINGLE_MARKET_SCHEMA` →
`FEATURE_SEGMENT_ROUNDUPS_ENABLED`; M2-JSON-Retry-Helper in `anthropic_client.py`
(additiv, Pair-Pfad unangetastet); Dedupe der Channel-Leerliste mit Platform-Suffix;
`cron_roundup_segments`-Setting (CSV-Env-Var, Default die 4 Segmente); Cron-Block
`_run_segment_roundups_after_briefs` nach den Pair-Briefs, mit **zweitem F0.7-Cap-Check**
(Run-Start-only-Cap genügt nicht — Roundup-Block re-computet den Spend und skippt bei
`hard_cap_exceeded`, Pair-Briefs bleiben unberührt). Testbasis 625 → 643.

**Roll-out: vier Segmente** — `us_major`, `us_independent`, `de_verleih`,
`de_independent`. UK ausgesetzt (`uk_major` 1 Channel, `uk_independent` 2 — zu dünn);
späteres Zuschalten = `CRON_ROUNDUP_SEGMENTS`-Env-Var-Change, kein Code.

**Prod-Status 25.05.:** deployt, `FEATURE_SEGMENT_ROUNDUPS_ENABLED=true` gesetzt, alte
Env-Var entfernt. Erster regulärer Cron-Lauf mit Roundups: Mo 01.06. (KW 23).
Manueller Endpoint `POST /api/admin/roundups/generate` jederzeit nutzbar (Gate greift).

### Schritt 3 — Pilot-Ergebnis (verifiziert auf Prod, 25.05.2026)

PR #165, fünf Commits: ORM-Mirror `channel.segment`, Migration-Smoketest-Fix
(`NEW_REVISION`-Pinning — Test-Infra-Korrektur), Tabelle `segment_roundup`,
Generator-Service + Schemas, Admin-Endpoint `POST /api/admin/roundups/generate` hinter
Bearer-Auth + `FEATURE_SINGLE_MARKET_SCHEMA`-Gate. Testbasis 614 → 625.

Realer Pilot-Lauf `us_major`, KW 22, 14d-Fenster, Top-5:

| Metrik | Schätzung | Ist |
|---|---|---|
| Channels evaluiert / mit Posts | 33 / — | 33 / **20** |
| Posts im Prompt | ~165 | **171** |
| Input-Tokens | ~25–35k | **9.726** |
| Cost/Lauf | $0,55–0,85 | **$0,26** |

**Kostenfrage entschärft.** Hochrechnung 6 Segmente × ~4,3 Wochen ≈ **~$6,70/Monat** —
weit unter Soft-Warn (160 $) und Cap (200 $). us_major ist das größte Segment, die
übrigen liegen eher darunter. Die Schätzung war ~3× zu hoch (kein voller JSON-Anhang,
nur 20 aktive Channels).

**Brief-Qualität tragfähig.** Synthese (`tldr`/`themes`/`what_ran`) verdichtet echt,
erkennt Muster statt nur zu listen. `channels_in_focus` hält die geforderte
Neutralität (Beobachtung, keine Wertung). Das LLM lieferte unaufgefordert
`data_caveats` mit.

**Datenbefunde aus dem Pilot (für Schritt 4 / separate Hygiene):**

- *Dedupe Channel-Leerliste:* Multi-Plattform-Handles (`netflixgeeked`, `disneyplus`,
  `hulu`) erscheinen mehrfach in der Leerliste — Generator zählt Channel-Rows, nicht
  unique Handles. In Schritt 4 Prompt klarstellen oder Leerliste nach Handle
  deduplizieren.
- *Falsche Account-Zuordnung:* deutschsprachige Verleih-Inhalte auf US-Major-Accounts —
  Scrape-/Quellen-Hygiene-Problem, **kein** Roundup-Bug. Separater Hygiene-Punkt.
- *0-Views-trotz-Engagement:* Carousels/Statics ohne View-Daten — strukturell bekannt,
  betrifft Engagement-Tiebreaker.

Zwei Alembic-Migrationen, PR #164: `e1c93a4d7f08` (Schema — ENUM-Feld `segment`) und
`b8f2a7c40e91` (Backfill + Markt-Korrektur). 97 Channels klassifiziert, 11 bewusste
NULL-Skips. **Lesart C umgesetzt:** der Backfill setzt `segment` *und* korrigiert
`market` — INT-Channels mit echtem DE/US/UK-Markt wurden mitkorrigiert, reversibel über
eine Helper-Tabelle (`_segment_backfill_rollback`, captured den alten `market`-Wert zur
Upgrade-Laufzeit). INT bleibt nur für Channels außerhalb DE/US/UK (das `market`-ENUM
kennt kein FR/AU/AT/IT).

Segment-Verteilung auf Prod:

| Segment | Channels |
|---|---|
| `us_major` | 33 |
| `us_independent` | 17 |
| `de_verleih` | 22 |
| `de_independent` | 22 |
| `uk_major` | 1 |
| `uk_independent` | 2 |
| `NULL` (Pair-Pool + Skips) | 114 |

---

## 8. Offene Punkte / Risiken

- **Kosten — erledigt.** Pilot-Messung (25.05.): $0,26/Lauf, Hochrechnung ~$6,70/Monat
  für 6 Segmente. Cap (200 $) nicht in Reichweite. Keine Nachjustierung am Top-N-Cutoff
  nötig.
- **UK-Segmente faktisch leer.** `uk_major` 1 Channel, `uk_independent` 2 — die
  UK-Master-Accounts liegen alle im Pair-Pool. Schritt 4 rollt nur die **vier
  substanziellen Segmente** aus (`us_major`, `us_independent`, `de_verleih`,
  `de_independent`); UK-Roundups bleiben ausgesetzt bis das UK-Inventar wächst
  (Anknüpfungspunkt: Channel-Erweiterungs-Strang).
- **M2-Retry fehlt im Roundup.** Der JSON-Parse-Retry der Pair-Pipeline wurde im Pilot
  bewusst nicht mitgezogen. Für den Cron-Betrieb zwingend: ohne Retry ist ein
  JSON-Parse-Fehler ein verlorener Brief. **In Schritt 4 mitziehen.**
- **Dedupe Channel-Leerliste** (Pilot-Befund) — in Schritt 4 beheben.
- **`segment` im ORM — erledigt** (PR #165, Commit 1).
- **`market`-Restbestand INT.** 9 Channels weiter `market=INT` / `segment=NULL`
  (AU/CA, Game-Publisher, scrape-blocked). Bewusste Restklasse. FR/AU/AT-Support wäre
  eigener Sprint, nur bei Bedarf.
- **Daten-Hygiene (kein Blocker).** Dublette-Rows `studiocanalde`/`studiocanal.de`,
  `sonypicturesde`/`sonypictures.de` — alte Row sollte `active=false`. Zusätzlich aus
  dem Pilot: falsche Account-Zuordnung (DE-Inhalte auf US-Accounts) — Scrape-/Quellen-
  Hygiene. Beides separater Operations-Punkt.
- **Flag-Benennung.** `is_single_market_schema_enabled()` /
  `FEATURE_SINGLE_MARKET_SCHEMA` heißt enger, als die Funktion ist (Roundups decken
  auch Majors ab). **In Schritt 4 final entscheiden:** bestehenden Stub weiterverwenden
  (Tech-Debt, Name lügt leicht) oder breiter benennen (z. B.
  `is_segment_roundups_enabled()` — Stub-Umbenennung, PR #155 betroffen).

---

## 9. Nächster Schritt

Freigabe dieses Dokuments durch Wolf → Schritt 2 (Migration + Klassifizierung) starten.
Schritt 3 wird nach Abschluss von Schritt 2 als eigener Master-Plan-Sprint definiert.
