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

