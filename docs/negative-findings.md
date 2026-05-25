# Negative Findings — Geprüft und nicht vorhanden

Dieses Dokument hält recherchierte Channel-Hypothesen fest, die nach Prüfung **nicht als dedizierte Accounts existieren**. Zweck: vermeidet wiederholte Recherche in zukünftigen Channel-Inventory-Sprints, dokumentiert "warum kein X" für späteres Lesen.

---

## UK-Sub-Brand-Channels (Stand 12.05.2026, PR #127)

Während des Sprints "Disney IG UK Pool-Erweiterung" (PR #127, 12.05.2026) wurden folgende UK-Sub-Brand-Hypothesen geprüft und **negativ beschieden**:

### Marvel UK TikTok
- **Hypothese:** `@marveluk` oder `@marvel_uk` auf TikTok als UK-Schwester zum globalen `@marvel`-Account
- **Befund:** kein dedizierter UK-Account auf TikTok. Marvel-Content für UK läuft über den globalen `@marvel`-Account.
- **Vorhanden auf:** Instagram (`@marvel_uk`, aktiv, 7 Posts/30d Stand 12.05.)
- **Konsequenz für Channel-Pool:** Marvel UK nur via IG, kein TT-Channel im Pool.

### Star Wars UK TikTok
- **Hypothese:** `@starwarsuk` auf TikTok als UK-Schwester zum globalen `@starwars`-Account
- **Befund:** kein dedizierter UK-Account auf TikTok. UK-Content über globalen `@starwars`-Account.
- **Vorhanden auf:** Instagram (`@starwarsuk`, im Pool aufgenommen)
- **Konsequenz:** Star Wars UK nur via IG, kein TT-Channel im Pool.

### Pixar UK (alle Plattformen)
- **Hypothese:** dedizierte UK-Accounts für Pixar (IG, TT, YT)
- **Befund:** Pixar betreibt **keine** dedizierten UK-Accounts auf einer der drei Plattformen. UK-Content läuft komplett über die globalen Pixar-Hauptaccounts.
- **Konsequenz:** kein Pixar-UK-Channel im Channel-Pool, weder IG noch TT noch YT.

---

## Aktive UK-Channel-Realität (Stand 12.05.2026)

Posts in den letzten 30 Tagen, alle `mvp=true`:

| Channel | Platform | Posts 30d |
|---|---|---|
| marvel_uk | Instagram | 7 |
| lionsgateuk | Instagram | 6 |
| disneystudiosuk | Instagram | 4 |
| disneyuk | Instagram | 3 |
| Alle anderen UK-Channels (18) | TikTok / Instagram / YouTube | 0 |

**Interpretation:** UK-Aktivität konzentriert sich praktisch komplett auf Instagram-Master-Accounts der vier Major-Sub-Brands. TikTok-UK-Channels sind strukturell im Pool angelegt (Phase A PR #121), aber aktuell ohne reale Posts.

---

## Aktualisierung dieses Docs

Bei künftigen Channel-Inventory-Sprints: wenn eine Hypothese geprüft und negativ ausfällt, hier eintragen mit Datum + Sprint-Referenz + kurzer Begründung. Verhindert Doppel-Recherche.

## 2026-05-13 — Wolf Browser-Verifikation

- **`paramountpicturesuk` Instagram** — Profilseite nicht verfügbar. UK-Erweiterung Phase A hatte den Channel angelegt, aber das Konto existiert nicht auf der Plattform. Deactivated 2026-05-13.
- **`sonypictures.uk` Instagram** — Profilseite nicht verfügbar. Deactivated 2026-05-13.

Note: Die TikTok-Versionen beider Channels (`tiktok.com/@paramountpicturesuk`, `tiktok.com/@sonypictures.uk`) existieren — bleiben aktiv.

## 2026-05-17 — Post-Cron Sonntag (erster Cron nach DB-Migration)

### Cron-Stats
- **Status:** success
- **Run-ID:** `bd5d0a6a-896b-4c1f-8c09-b0fd1de00adc`
- **Laufzeit:** 36 Min (07:03–07:39 UTC = 09:03–09:39 CEST)
- **Erster regulärer Cron nach DB-Migration vom 06.05.** → DB-Pipeline stabil, keine Connection-Pool-Storms, Postgres-Checkpoints im gesunden Schwingungsmuster.

### Plattform-Yields
- **Instagram:** 60/61 processed, 153 Assets, **1 zero_yield**
- **TikTok:** 32/32 processed, 140 Assets, **0 zero_yield**
- **YouTube:** 31/34 processed, 128 Assets, **3 failed**

### FU-1 Surface-Verifikation
- `zero_yield_channels`-Surface funktioniert in Prod, Schema vollständig (alle 5 Keys: `channel_id`, `handle`, `platform`, `market`, `url`).
- Werte deutlich unter Erwartung (IG=1 statt 7–10, TT=0 statt 10–12).
- **Interpretation:** bessere Datenqualität als vermutet. Channel-Whitelist nach den Cleanups (Sprints 10a–10f) erfasst kaum noch tote Profile.

### Neuer Befund-Typ: "scrape-blocked" Channels
- **bad_robot (instagram, Market.INT)** — Profil existiert und postet aktiv (verifizierter Account, 689 Posts, 76.4k Follower, frische Posts zu "The End of Oak Street", "Presumed Innocent"). **Trotzdem: post-Tabelle enthält 0 Einträge** seit Channel-Aufnahme am 27.04.2026. Apify-Actor apify~instagram-api-scraper wirft keinen Fehler (steht nicht in failed_channels), liefert aber systematisch 0 Posts.
- **Hypothesen:**
  - Anti-Scraping-Maßnahme dieses spezifischen Accounts (verifizierte Marken-Accounts haben manchmal stärkere Restriktionen)
  - Apify-Actor-Issue spezifisch für diesen Handle
  - Falscher Handle/URL in DB (Channel-URL stimmt aber überein mit https://www.instagram.com/bad_robot)
- **Aktion:** **NICHT deaktivieren** — Channel ist real, aktiv, wertvoller Content (J.J. Abrams Productions). Stattdessen: eigenes Diagnose-Ticket für nächsten Sprint.
- **Eröffnet neue Kategorie:** "scrape-blocked" — Channel aktiv aber permanent unscrapebar. Unterscheidet sich von Bucket A (404), Bucket B (totes Profil), Bucket C (postet wenig).

### YouTube-Befunde (separat zu untersuchen)
- **NetflixUK Handle-Lookup-Failure** — API antwortete HTTP 200, aber YouTube fand keinen Channel unter dem Handle NetflixUK. Kontext (alt/neu, wann hinzugefügt) zur Klärung. Eigenes Diagnose-Ticket im nächsten Sprint.
- **marvel + paramountplus 500-backendErrors** — aufeinanderfolgend bei _list_recent_video_ids → playlistItems. Kein erfolgreicher Call dazwischen. Hypothese: Quota- oder Rate-Limit-Pattern, nicht zufällig (MarvelHQDE und MarvelUK davor liefen erfolgreich). Beim nächsten Cron beobachten, ob es exakt die gleichen Channels trifft.

### Cost-Datenpunkt 2 — ungültig
- **Anthropic: 0 Calls, $0.00** (vs. DP1 vom 13.05. mit $14.73 bei Pre-Cron-Trigger).
- F0.7-Anthropic-Cap-Entscheidung verschiebt sich auf nächsten Sonntag (24.05.) als echten DP2 mit Briefs.
- **Klärungsbedarf vor 24.05.:** Soll regulärer Sonntag-Cron Briefs generieren oder nur Assets? Wenn Brief-Generation by-design im Sonntag-Cron passieren soll → Bug, eigene Diagnose. Wenn nicht → DP2-Quelle korrigieren (nur Pre-Cron-Trigger zählen).

### Gesamtkosten
- OpenAI: $0.1074 (471 Calls)
- Apify: $1.03 (2 Calls)
- Anthropic: $0.00 (siehe oben)
- Vision: $0.75 (50/50 succeeded)
- **Total: ~$1.13**
- Budget: 0% von $200 Monatscap

## 2026-05-25 — FU-2 Bucket-Klassifizierung (post-Cron-Lauf)

Klassifizierungsbasis: Cron-Run-ID `49f96238-e474-4e42-8d23-1357e5274893`
(2026-05-25 07:30 Berlin, status=completed). IG zero-yield: 2, TT zero-yield: 0.

**Befund:** Keine neuen Bucket-A-Channels. Beide Zero-Yield-Channels sind
browser-verifiziert lebende, aktiv postende Accounts:
- `disneystudios` (US IG) — verifiziert, 6.947 Beiträge, aktuelle Posts
- `hbo` (US IG) — verifiziert, 9.761 Beiträge, aktuelle Posts

Beide → Kategorie scrape-blocked (Profil aktiv, Apify scrapt 0).
NICHT deaktiviert — Content ist real und wertvoll.

**Keine DB-Änderung in diesem Lauf.**

Hinweis: Das Skript scripts/fu2_bucket_classifier.sql war vor diesem Lauf
zweifach veraltet (siehe Eintrag unten) und musste erst repariert werden.

## 2026-05-25 — fu2_bucket_classifier.sql: zwei Schema-Drifts (PR #162)

Das FU-2-Klassifizierungs-Skript (vom 13.05.) lief am 25.05. nicht mehr —
zwei stille Schema-Drifts seit Erstellung:

1. **status-Wert:** Skript filterte `WHERE status = 'success'`. CronRun.status
   kennt aber nur running/completed/failed/budget_exceeded — 'success' wird
   nie geschrieben (geändert durch Cadence-Sprint PR #149). Folge: erste
   Query lieferte (0 rows). Fix: → 'completed'.
2. **json/jsonb-Mismatch:** Skript castete `'[]'::jsonb` + jsonb_array_elements.
   summary_json ist real Typ `json` (nicht jsonb) — SQLAlchemys Column(JSON)
   resolvet auf Postgres-json. Fix: → '[]'::json + json_array_elements.

Lehre: Side-Skripte in scripts/ werden von Schema-ändernden PRs nicht
automatisch mitgezogen und driften unbemerkt. Gefixt in PR #162.

## 2026-05-25 — scrape-blocked: disneystudios + hbo (Beobachtung, offen)

Zwei lebende US-IG-Major-Accounts liefern bei Apify 0 Posts (scrape-blocked).
Zero-Yield-Historie aus cron_run.summary_json (verlässlich erst ab 17.05.,
da FU-1 Zero-Yield-Surface erst mit PR #146 ~13.05. eingeführt):

- `disneystudios` (US IG, Disney-Pair-Pool, "Lead-Cinema-Master"):
  17.05. NICHT betroffen → 25.05. in BEIDEN Läufen Zero-Yield.
  Neu seit dieser Woche, ein betroffener Cadence-Zyklus.
- `hbo` (US IG, in KEINEM Pair-Pool): nur 25.05., nur ein Lauf.
- `bad_robot` (INT IG): durchgehend Zero-Yield seit 17.05. — bekannter
  Dauerfall, scrape-blocked-Präzedenz.

**Brief-Impact:** Disney-Pool federt ab — fällt disneystudios aus, liefern
4 weitere US-IG-Channels (marvelstudios, pixar, starwars, 20thcenturystudios).
Geschätzt ~20% Verlust im US-IG-Teil des Disney-Briefs; DE/UK unbetroffen.
hbo ist in keinem Pool → Brief-Impact null; offene Frage, ob hbo ein
bewusster Observation-Channel oder ein Artefakt ist.

**Status: offen, Beobachtung.** Nachprüfung nach dem 01.06.-Cadence-Lauf
via Zero-Yield-Query über cron_run.summary_json. Entscheidungsregel:
disneystudios am 01.06. erneut betroffen → bestätigtes Muster, Apify-
Actor-Fix-Sprint definieren. Nicht mehr betroffen → temporärer Throttle,
erledigt. Liste gewachsen → Eskalation.

Mögliche Ursache (Hypothese, unbestätigt): Anti-Scraping-Throttle für
große verifizierte Marken-Accounts; tagesabhängig/account-spezifisch,
da andere große verifizierte Accounts (marvelstudios etc.) am 25.05.
normal lieferten.

## 2026-05-25 — Klarstellung: Cron-Skip-Logik ist PK-Lookup, kein Zeitfenster

Korrektur einer im Handover verbreiteten Fehlannahme: Die Skip-Entscheidung
des Cadence-Crons (cron.py:477-484) ist ein reiner Composite-PK-Lookup auf
(pair_key, target_iso_year, target_iso_week). Es gibt KEIN "≤7d"-Zeitfenster
und keine generated_at-Vergleichslogik.

Ein Pair wird übersprungen, wenn für die ANSTEHENDE Ziel-ISO-Woche bereits
ein insight_report-Eintrag existiert — sonst generiert. Vorwochen-Briefs
lösen nie einen Skip aus.

Das "≤7d/8-14d/>14d"-Wording betrifft ausschließlich die Frontend-Stale-
Warnung (PR #148) — eine reine Anzeige-Logik, NICHT die Generierungs-Skip-
Entscheidung. Beides wurde im Handover fälschlich vermischt.

## 2026-05-25 — regenerate-Endpoint generiert nur die aktuelle ISO-Woche

POST /api/admin/insights/regenerate (admin.py) akzeptiert pair + replace +
window_days, aber KEINEN Zielwochen-Parameter. generate_and_persist_report
läuft mit now=None → aggregate_pair fällt auf datetime.now() zurück. Der
Endpoint generiert daher IMMER für die aktuelle ISO-Woche.

Praxisfolge (25.05.): Ein Versuch, Disney für KW 21 zu aktualisieren,
erzeugte stattdessen einen KW-22-Brief (heute = KW 22). Für vergangene
Wochen ist der Endpoint ungeeignet. Disney hat dadurch jetzt einen
KW-22-Brief vom 25.05.; der reguläre Cron am 01.06. (Zielwoche KW 23)
ist davon unberührt — andere PK-Row, kein Skip.

## 2026-05-25 — M2 JSON-Parse-Retry: (c)/(d)-Vorbehalt aufgelöst

Beim M2-Sprint (PR #157) blieb offen, ob der warnerbros-JSON-Parse-Fehler
vom 25.05. Fall (c) Truncation oder (d) transient-kaputtes-JSON war —
mangels abrufbarer PR-#154-Diagnostik nicht verifizierbar.

Auflösung: Der warnerbros-Re-Run nach M2-Merge war erfolgreich (KW-21-Brief
generiert, cost 453 ct = mit Retry-Aufschlag). Ein Re-Call hätte gegen
echte Truncation wieder abgeschnittenes JSON geliefert — der Brief entstand
aber. Also Fall (d), transient. KEIN Truncation-Problem, keine separate
max_tokens-Untersuchung nötig. M2-Retry hat in der Praxis funktioniert.

## 2026-05-25 — SQLite/Postgres-Test-Dialekt: Variante Z umgesetzt

Aus PR #143 bekannt: Backend-Suite läuft gegen SQLite, produktiv ist
PostgreSQL 18. Variante Z (PR #160) hat eine additive Postgres-Integration-
Suite (app/tests/integration/) ergänzt — echte Coverage für Advisory-Lock-
Semantik und Schema-Resolution.

Empirischer Befund: Die 8 Integration-Tests liefen direkt grün — KEINE
Bugs gefangen, die SQLite durchgelassen hätte. Heißt: Die produktiven
Postgres-Pfade sind dialekt-korrekt. Die breitere Migration der gesamten
Suite auf Postgres (Variante X) ist damit NICHT gerechtfertigt — die
Datenbasis spricht dagegen. Integration-Suite ist der gezielte
Erweiterungs-Ort, falls künftig ein Postgres-Incident auftaucht.

