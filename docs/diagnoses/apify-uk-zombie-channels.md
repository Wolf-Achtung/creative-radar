# Apify-UK-Zombie-Channels — Diagnose

**Sprint:** B2-α — Apify-UK-Diagnose
**Datum:** 2026-05-13
**Modus:** Diagnose-First — keine Code-Änderungen, kein Apify-Trigger ($0)
**Branch:** `claude/diagnose-apify-uk-channels-Pk4zE`

---

## TL;DR

Code-seitige Befunde decken den **Mechanismus** ab, warum 25 von 29 UK-Channels
in iso_week 20 als „Zombies" wirken (0 Posts in 14d). Plattform-/Apify-
Dashboard-Verifikation für die A/B/C-Kategorisierung **benötigt Wolf-
Handarbeit** — Claude Code hat in dieser Session weder Browser- noch
Apify-API-Zugang.

**Wichtigster strukturelle Befund:** Apify-Runs liefern ein **einzelnes
batched Dataset pro Plattform**. Channels ohne Items im Dataset hinterlassen
**keine Spur** in `failed_channels`, keinen `logger.warning`, keinen Counter
— sie verschwinden silent. Cost wird trotzdem fällig (Compute-Units pro Run,
nicht pro Item). → Sprint-Vorschlag *FU-1* unten.

Pricing-Tier ist **mit hoher Wahrscheinlichkeit nicht der Grund** ($13.41
Real-Verbrauch vs. $50 internes Soft-Warn-Limit; Concurrent-Runs-Limit greift
nicht, da Cron pro Lauf nur 2 Actor-Runs absetzt).

---

## Methodik & Scope-Begrenzung

Was Claude Code in dieser Session prüfen konnte:

- **Code-Pfad** für Channel-Selection, Apify-Calls, Item-Bucketing, Error-
  Handling (`apify_connector.py`, `monitor.py`, `cron.py`,
  `cron_channel_selection.py`, `youtube_connector.py`).
- **Migrations-Seed** für UK-Channels (`e5d8f1a36b40_whitelist_expansion_2026_05_05.py`).
- **Backlog-Doku** (`docs/backlog/UK_MARKET_INTEGRATION.md`,
  `docs/negative-findings.md`).

Was Claude Code **nicht** prüfen konnte (Stop-Bedingung 1 + 3 aus dem Brief):

- Apify-Dashboard (kein UI-Access, kein API-Token genutzt → Cost-Schutz).
- Plattform-Verifikation per Browser (kein HTTP-Browser im Tool-Set für
  Instagram/TikTok-Profilseiten — Anti-Bot-Wall, würde ohnehin nur HTML-
  Skelett zurückliefern).
- DB-Stand der 25 Zombie-Channels (kein psql-Zugriff im Sandbox).

Q1 ist daher als **Verifikations-Liste an Wolf** formuliert (siehe unten),
nicht als Befund.

---

## Q1 — Existieren die 25 Zombie-Channels auf der Plattform?

**Status:** offen, Wolf-Verifikation nötig.

Was wir aus Repo-Doku schon wissen (`docs/negative-findings.md` +
`docs/backlog/UK_MARKET_INTEGRATION.md`):

| Channel-Hypothese | Status laut Repo-Doku |
|---|---|
| Marvel UK TikTok | **existiert nicht** (negative-findings, 12.05.) |
| Star Wars UK TikTok | **existiert nicht** (negative-findings, 12.05.) |
| Pixar UK (alle Plattformen) | **existiert nicht** (negative-findings, 12.05.) |
| Disney TT-UK | **„kein offizieller Channel im Apify-Inventar"** (Phase-A-Backlog, 11.05.) |
| Netflix UK (IG/TT/YT) | **„komplett fehlend"** laut Phase-A-Backlog vom 11.05. — aber cron-trigger 13.05. nennt `netflixuk` als IG-Zombie → wurde **zwischen 11.05. und 13.05. nachträglich angelegt**, Existenz nicht bestätigt |

Damit ist `Bucket A` (existiert nicht / non-Apify-inventar) für mindestens
`marvel_uk-TT`, `starwarsuk-TT` und alle Pixar-UK-Channels schon ohne weitere
Recherche befüllbar.

**Konkrete Verifikations-Aufgabe für Wolf** (5 prominente Zombies, Browser-
Check via `curl -sI` oder direktem Aufruf):

```
https://instagram.com/netflixuk          → 200 + Recent-Posts?
https://tiktok.com/@netflixuk            → 200 + Recent-Posts?
https://instagram.com/sonypictures.uk    → 200 + Recent-Posts?
https://instagram.com/warnerbrosuk       → 200 + Recent-Posts?
https://tiktok.com/@warnerbrosuk         → 200 + Recent-Posts?
https://instagram.com/paramountpicturesuk→ 200 + Recent-Posts?
https://tiktok.com/@paramountpicturesuk  → 200 + Recent-Posts?
```

Bei `sonypictures.uk` zusätzlich prüfen, ob der Dot im Handle für die Apify-
TikTok-Actor-Eingabe (`profiles: [...]`) zulässig ist — der TT-Scraper macht
sonst Username-Resolution auf Wort-Boundary und der Dot kann das brechen
(siehe Q3 unten).

---

## Q2 — Sind die Channels in der Apify-Whitelist?

**Befund:** **Es gibt keine separate Apify-Whitelist.** Die Cron-Selection
hängt rein an drei DB-Flags der `channel`-Tabelle:

```python
# app/services/cron_channel_selection.py:60-69
select(Channel).where(
    Channel.mvp == True,
    Channel.active == True,
    Channel.platform == platform,
).order_by(Channel.handle)
```

Per Cron-Trigger-Kontext (13.05.) sind alle 29 UK-Channels `mvp=t active=t
monitoring_enabled=t` → alle passieren den Filter, alle landen in den per-
Plattform-Pool.

A-Class (`assets_30d ≥ 1`) wird jeden Lauf gesynct, B-Class wird in **Dritteln
über `run_index ∈ {0,1,2}`** rotiert (`cron_channel_selection.py:95-101`). Bei
`CRON_SYNC_INTERVAL_DAYS=3` (default) durchläuft die Rotation alle drei
Drittel **in 9 Tagen**. Über die 14-Tage-Fenstergrenze hinaus wurde also
jeder UK-Zombie mindestens 1× — typischerweise 1-2× — gequeued.

**→ Selection / Whitelist ist nicht die Bottleneck-Stelle.** Die Channels
*werden* an Apify übergeben. Was Apify damit macht (oder eben nicht),
verlieren wir aufgrund des Q3-Befunds aus dem Sichtfeld.

---

## Q3 — Schlagen die Apify-Calls fehl?

**Befund: Calls schlagen nicht offen fehl — sie liefern für Zombie-Channels
silent 0 Items.** Das ist eine **Beobachtbarkeits-Lücke**, nicht (zwingend)
ein Apify-Fehler.

Die kritische Code-Stelle:

```python
# app/api/monitor.py:315-334
def _group_items_by_channel(raw_items, channels, normalize):
    by_channel: dict[UUID, list[dict]] = defaultdict(list)
    for index, raw_item in enumerate(raw_items):
        item = normalize(raw_item)
        channel = _match_channel(channels, item.get("owner_username"), index)
        by_channel[channel.id].append(item)
        channel_lookup[channel.id] = channel
    return [(channel_lookup[cid], items) for cid, items in by_channel.items()]
```

Apify schickt **ein** Dataset für **alle** Channels eines Plattform-Runs
zurück (`run_public_channel_monitor` für IG, `run_tiktok_profile_monitor` für
TT). `_group_items_by_channel` iteriert nur über zurückgekommene Items.
Channels, die im Dataset **gar nicht vorkommen**, werden nie ge-bucketed,
nie als `failed_channels`-Entry geloggt, nie counter-erhöht. Sie sind
**unsichtbar** in `cron_run.summary_json`.

Demgegenüber hat **YouTube** (per-Channel-Loop in `cron.py:230-289`)
ordentliches Per-Channel-Error-Reporting: `YouTubeNotFoundError`,
`YouTubeAuthError`, `YouTubeQuotaExceededError`, `YouTubeAPIError` werden
alle in `failed_channels` mit `error_class` + `error_message` festgehalten.
Wenn YT-UK-Channels also fehlschlagen, **müssten** sie in
`cron_run.summary_json.platforms.youtube.failed_channels` auftauchen — bitte
Wolf prüfen.

**Mechanismus-Kandidaten für „silent 0 Items"** auf Apify-Seite (Hypothesen,
nicht code-verifizierbar ohne Apify-Dashboard):

1. **Falscher / kaputter `channel.url`** (besonders verdächtig für Wolf-SQL-
   Phase-A-Inserts vom 11.05., die _nicht_ über die Migration laufen).
   `run_public_channel_monitor` macht nur `url.rstrip("/")` — kein Validate,
   keine Existenz-Prüfung. Apify-IG-Scraper retourniert für unauflösbare
   URLs typischerweise leere Items ohne Error.
2. **Account hat im Apify-Lookback-Fenster keine Posts.** Der IG-Scraper
   liefert per `resultsLimit=5` die letzten 5 Posts; gibt es weniger oder
   keine in der Lookback-Heuristik des Actors, kommt eine leere Liste
   zurück.
3. **TikTok-Handle-Normalisierung-Issue mit `sonypictures.uk`** — der Dot
   in dem Handle wird in `run_tiktok_profile_monitor:147-155` zwar nicht
   ausdrücklich gestrippt, aber der TT-Actor (`clockworks~tiktok-scraper`)
   ist intern dafür bekannt, Handles am ersten `.` zu splitten und nur
   `sonypictures` zu queryen → 404 silent.
4. **TT-Actor-Input-Schema-Mismatch** — der Actor wird mit zwei Varianten
   versucht (`profiles: [...]` zuerst, dann fallback `usernames: [...]`,
   siehe `apify_connector.py:160-187`). Wenn beide leere Datasets liefern,
   wird **keine Exception** geraised (`if items: return items` lässt
   leere Datasets durch). Das ist genau das beobachtete Muster.
5. **`excludePinnedPosts: True`** auf dem TT-Actor — Brand-Channels
   pinnen häufig Trailer-Posts. Wenn der Channel sonst keine Posts ≥14d
   alt hat, fällt er silent durch.

**Cost-Folge:** `record_apify_run` (cost_log.py) wird einmal pro Actor-Run
aufgerufen, **nicht pro Channel** — d.h. auch für 0-Item-Channels fließt
anteilig Compute-Unit-Cost an. Bei aktuell 25 Zombies × 0.4 USD pro CU pro
Run × ~9 Tage Rotation ist die Größenordnung aber irrelevant (~$1-2 pro Monat
verbrannt für Channels mit 0 Yield).

---

## Q4 — Pricing-Tier-Grenze?

**Befund: Pricing-Tier ist sehr wahrscheinlich nicht der Grund.** Indizien:

- Wolf hat im Cron-Trigger-Kontext **$13.41 Real-Verbrauch / $29 Plan-Limit**
  (46 %) gemeldet. Das interne Soft-Warn-Limit (`apify_soft_warn_pct=0.80`)
  bei `apify_monthly_budget_usd=50.0` aus `config.py:124-126` liegt bei
  $40 — wir sind weit drunter.
- **Concurrent-Runs-Limit (32)**: pro Cron-Tick werden genau **2** Apify-
  Runs abgesetzt (1× IG, 1× TT — YT ist Google-API). Auch bei zwei
  parallelen Cron-Triggers (vom Lock geblockt → 409) wären es nicht ≥32.
- **Datacenter-IPs (30)**: relevant für Proxy-Pools, nicht für unsere
  Actor-Runs. Apify-Hosted Actors poolen IPs intern — Tier-Limit greift,
  wenn ein einzelner Run >30 Proxy-IPs braucht (sehr untypisch).
- Wäre das Tier die Quelle, sähen wir entweder **systematische
  `apify run […] ended with status=FAILED`** Exceptions im Cron-Summary
  (sind die da, Wolf?) oder **`stale_run_timeout`** durch `_reap_stale_runs`.
  Der Cron-Trigger-Kontext deutet keines an.

**→ Pricing kann übersprungen werden, sofern Wolf im Apify-Dashboard nicht
gezielt einen einzelnen `Run rejected: quota exceeded`-Eintrag findet.**

---

## Kategorisierung der 25 Zombie-Channels

Mit den Code-Befunden kann die Kategorisierung **teilweise** vorhergesagt,
aber **nicht abschließend bestätigt** werden. Bestätigung braucht Wolf-Browser
+ Apify-Dashboard.

### A — Existiert nicht (zu negative-findings.md → Channel-Deactivate)

Schon dokumentiert oder stark indikativ:

- **Marvel UK TikTok** — bestätigt (`negative-findings.md`)
- **Star Wars UK TikTok** — bestätigt (`negative-findings.md`)
- **Pixar UK (IG, TT, YT)** — bestätigt (`negative-findings.md`)
- **Disney TT-UK** — Wolf-Befund Phase-A-Backlog: „kein offizieller Channel"

→ erwartete A-Bucket-Größe nach Verifikation: **4-6 Channels**.

### B — Existiert, aber Apify scrapt nicht (Action: Apify-Config-Fix)

Hypothesen-Liste (Q3-Mechanismen 1, 3, 4, 5):

- **`sonypictures.uk` TT** — Dot-im-Handle-Risk → TT-Actor-Input prüfen.
- **TT-UK-Cluster mit `excludePinnedPosts: True`** — alle TT-UK, deren
  Recent-Posts ausschließlich gepinnte Trailer sind, fallen silent durch.
- **IG-UK mit falscher / leerer `channel.url`** aus dem Wolf-SQL-Phase-A —
  müsste per `SELECT handle, url FROM channel WHERE market='UK'` validiert
  werden.

→ erwartete B-Bucket-Größe: **8-15 Channels**, der eigentliche Action-
Brennpunkt.

### C — Existiert, Apify scrapt korrekt, aber Channel hat real keine Posts

- **YT-UK-Cluster (8 Channels)** — Studio/Verleih-Brand-YT-Channels posten
  oft <1×/Monat. Erwartete C-Bucket-Größe hier groß.
- IG-UK-Brands, die ihre UK-Versionen nur sporadisch bespielen (Wolf-
  Annahme: typisch für Regional-Sub-Brands).

→ erwartete C-Bucket-Größe: **5-10 Channels** (passiv, kein Action).

---

## Empfehlungen für Follow-up-Sprints

### FU-1 — Per-Channel-Apify-Yield-Reporting (M, ~4-6h)

**Wert:** schließt die Beobachtbarkeits-Lücke aus Q3. Macht „silent 0 Items"
in `cron_run.summary_json` sichtbar.

**Umfang:**

- `_run_apify_sync_for_platform_async` (monitor.py:337) um einen
  `zero_yield_channels: list[dict]` ergänzen: für jeden Channel aus
  `channels`, der **nicht** als Key in `_group_items_by_channel`-Output
  auftaucht, einen Entry `{handle, market, expected_yield: 0}` emittieren.
- `cron.py:152-157` + `:181-185` (IG / TT-Summary-Block) um diesen Counter
  erweitern.
- Test-Fall (`test_cron_sync.py` oder neu): Apify-Mock mit 2 Channels,
  Dataset enthält nur 1 → `zero_yield_channels` enthält den fehlenden.

Cost: rein Code-Refactor, **$0** Apify-Trigger.

### FU-2 — UK-Channel-Audit + negative-findings + Deactivate (S, ~1-2h)

Hängt an Q1-Wolf-Verifikation. Sobald Wolf die 5-7 Stichproben durch hat:

- Channels Bucket A → `mvp=false active=false` per SQL-Update + Eintrag in
  `negative-findings.md`.
- Channels Bucket C → bleiben aktiv, aber bekommen einen
  `notes`-Vermerk „low-frequency, kein Action".

Cost: $0.

### FU-3 — Apify-Actor-Input-Härten (M, ~3-4h)

Adressiert Q3-Mechanismen 3 + 4:

- TT-Actor: `excludePinnedPosts` per Channel-Override-Flag (für UK-Brands,
  die nur Pin-Content posten, deaktivieren).
- TT-Actor: explizite Behandlung von Dot-im-Handle (`sonypictures.uk` als
  Test-Case).
- Apify-Connector: bei leerer Items-Response in der Fallback-Schleife
  zwischen `profiles`- und `usernames`-Variante **nicht** silent
  weitermachen, sondern strukturierte Warnung loggen.

Cost: 1-2 Test-Trigger gegen `sonypictures.uk` + `netflixuk-TT`, ~$0.50.

### FU-4 — Apify-Run-Log-Sampling via Apify-API (L, ~6-8h)

**Wert:** automatisierte Tier-/Quota-Health-Checks, ohne Dashboard-Klick.

**Umfang:** kleiner Background-Job, der einmal pro Cron-Lauf die letzten 5
Apify-Run-Metadaten via `/v2/actor-runs` abfragt (Read-Only, kostenlos), die
Per-Channel-`stats.computeUnits`-Verteilung in `cron_run.summary_json.apify`
ablegt und in einen Admin-Endpoint hängt.

Cost: $0 (Run-Reads sind nicht abrechenbar).

---

## Stop-Bedingungen aus dem Brief — Status

| Bedingung | Ausgelöst? | Konsequenz |
|---|---|---|
| 1. Apify-Dashboard-Zugriff für Claude funktioniert nicht | **ja** | Q1 als Verifikations-Liste an Wolf eskaliert (Section Q1 oben) |
| 2. Befund eindeutig systematisch | **teil-ja** | Q3 ist klar systematisch (silent 0-Yield); Q1 bleibt heterogen |
| 3. Apify-Konfig in Repo nicht auffindbar | **nein** | Konfig liegt in `app/config.py:24-36, 124-131` + `.env.example:50-54` — Werte fehlen, aber Schema ist da |

---

## Aus Scope ausgeschlossen (Wolf-Briefe-Vorgabe)

- ✗ Apify-Config-Änderungen
- ✗ Channel-Deactivation (→ FU-2)
- ✗ `negative-findings.md` Erweiterung (→ FU-2)
- ✗ B2-β Aggregator-UK-Integration
- ✗ Code-Touches in `backend/`
