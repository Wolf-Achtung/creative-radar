# B1 Phase 1 — Diagnose-Only-Befund

**Datum**: 2026-05-11 EOD
**Sprint**: UK-Markt-Integration B1 (Schema + Aggregations-Erweiterung)
**Status nach dieser Session**: Diagnose komplett, Phase 2 (Implementation)
ist für 2026-05-12 ohne weiteren Wolf-Ping freigegeben.

**Diese Datei ist der Implementations-Guide für morgen.** Sie enthält:

- Pre-Mapping-Verifikation (1 Diskrepanz: `universalpictures` ist
  `enabled=False`).
- Konkrete Zeilen-Verweise für jeden Code-Edit-Schritt.
- Aufwand-Re-Estimate.
- Reihenfolge-Vorschlag für morgen.

---

## 1. Pre-Mapping-Verifikation

### 1.1 PAIRS-Struktur (alle 7 Pairs, `insight_engine.py:84-317`)

| Pair (Zeile) | Status | Plattformen vorhanden |
|---|---|---|
| `warnerbros` (Z. 85) | enabled | TT/IG/YT |
| `sonypictures` (Z. 129) | enabled | TT/IG/YT |
| `primevideo` (Z. 163) | enabled | TT/IG/YT (YT US-only) |
| `disney` (Z. 192) | enabled | TT/IG/YT (YT US-only, multi-channel-pool) |
| `netflix` (Z. 251) | enabled | TT/IG/YT |
| `paramountpictures` (Z. 275) | enabled | TT/IG/YT (YT US-only) |
| `universalpictures` (Z. 302) | **disabled** | **kein `platforms`-Dict, nur Legacy `channels`-Mirror** |

### 1.2 ⚠️ DISKREPANZ: `universalpictures` ist disabled

`PAIRS["universalpictures"]` (Z. 302-316) hat:

- `"enabled": False` (Z. 311)
- `"reason"`: "US-Channel @universalpictures has 0 posts/30d, …" (Z. 312-315)
- **Kein `platforms`-Dict** — nur Legacy `"platform": "tiktok"` + `"channels"`-Mirror

Das Pre-Mapping listet UK für `universalpictures`:

```
universalpictures | universalpicturesuk | universalpicturesuk | UniversalPicturesUK
```

**Auswirkung**: Wenn morgen UK ohne Aktivierung addiert wird, läuft
`aggregate_pair` für diesen Pair gar nicht — die UK-Einträge werden
ignoriert. Mit Aktivierung (`enabled=True` + neuer `platforms`-Dict)
würde aber gleichzeitig das DE-Schwach-Datenbasis-Problem (Z. 312-315)
neu auflodern.

**Empfohlene Auflösung für B1**:

1. UK-Channels für `universalpictures` NICHT in B1 hinzufügen.
2. In B1-PR-Body als „out-of-scope, abhängig von universalpictures-
   Aktivierungs-Entscheidung" dokumentieren.
3. Separater Folge-Sprint: `universalpictures` aktivieren ODER weiter
   disabled lassen — orthogonale Fragestellung zur UK-Integration.

**Effekt**: 6 Pairs bekommen UK in B1, statt 7. Pre-Mapping bleibt
konsistent für: `warnerbros`, `sonypictures`, `primevideo`, `disney`,
`netflix`, `paramountpictures`.

### 1.3 ✅ `primevideo` hat KEIN `paramountplus`-Mention

`PAIRS["primevideo"]` (Z. 163-191): TT/IG = `amazonmgmstudios` +
`primevideode`, YT = `AmazonMGMStudios` (US-only). Korrekt — Pre-
Mapping-Entscheidung `paramountplusuk` als out-of-scope für B1 ist
gerechtfertigt.

### 1.4 ✅ Disney YT-Sub-Brand-Pattern verifiziert

`PAIRS["disney"]["platforms"]["youtube"]` (Z. 227-233):

```python
{"handle": "WaltDisneyStudios", "market": "US"},
{"handle": "marvel", "market": "US"},        # ← NICHT "marvelstudios"
{"handle": "pixar", "market": "US"},
{"handle": "StarWars", "market": "US"},      # ← Case-Mix
{"handle": "20thCenturyStudios", "market": "US"},
```

Pre-Mapping für UK: `DisneyUK + MarvelUK + StarWarsUK`. **Pattern
konsistent**: Master + 2 Sub-Brands (Marvel, Star Wars). Kein
Pixar-UK / 20thCenturyUK in Phase A vorgesehen — akzeptabel, da
diese Sub-Brands UK-seitig weniger aktiv sind. Nachträgliche Ergänzung
in separatem UK-Recherche-Sprint möglich.

### 1.5 Pre-Mapping-Zusammenfassung nach Diagnose

| Pair | UK IG | UK TT | UK YT |
|---|---|---|---|
| `disney` | `disneyuk` + `disneystudiosuk` + `marvel_uk` | `disneyuk` | `DisneyUK` + `MarvelUK` + `StarWarsUK` |
| `warnerbros` | `warnerbrosuk` | `warnerbrosuk` | `WarnerBrosUK` |
| `sonypictures` | `sonypictures.uk` | `sonypictures.uk` | `SonyPicsUK` |
| `paramountpictures` | `paramountpicturesuk` | `paramountpicturesuk` | `ParamountPicturesUK` |
| `primevideo` | `primevideouk` | `primevideouk` | (kein YT-UK) |
| `netflix` | `netflixuk` | `netflixuk` | `NetflixUK` |
| ~~`universalpictures`~~ | OUT-OF-SCOPE B1 | OUT-OF-SCOPE B1 | OUT-OF-SCOPE B1 |

Out-of-scope (durch Wolf bereits festgelegt): `paramountplusuk`,
`lionsgateuk`. Plus diese Diagnose-Session: `universalpictures` (alle
Plattformen).

---

## 2. Konkrete Zeilen-Verweise für Phase 2 (morgen)

### 2.1 PAIRS-Erweiterungen — `insight_engine.py`

Pro Pair drei Edits (TT, IG, YT) plus Legacy `channels`-Mirror.
**`channels`-Mirror muss `platforms["tiktok"]` 1:1 spiegeln** (sonst
bricht `test_pairs_backwards_compat_mirror_first_platform`).

**`warnerbros`** (Z. 95-125):
- TT (Z. 96-106): nach `{"handle": "warnerbrosdeutschland", "market": "DE"}` → `{"handle": "warnerbrosuk", "market": "UK"}`
- IG (Z. 107-112): nach `{"handle": "warnerbrosde", "market": "DE"}` → `{"handle": "warnerbrosuk", "market": "UK"}`
- YT (Z. 113-117): nach `{"handle": "WarnerBrosDE", "market": "DE"}` → `{"handle": "WarnerBrosUK", "market": "UK"}`
- `channels`-Mirror (Z. 121-125): UK-Entry analog TT mit-aufnehmen

**`sonypictures`** (Z. 131-159):
- TT (Z. 132-140), IG (Z. 141-147), YT (Z. 148-152): UK-Eintrag analog
- `channels`-Mirror (Z. 155-159): UK-Entry analog TT

**`primevideo`** (Z. 165-188):
- TT (Z. 166-172): `{"handle": "primevideouk", "market": "UK"}`
- IG (Z. 173-176): `{"handle": "primevideouk", "market": "UK"}`
- YT (Z. 180-182): **KEIN UK-Entry** (Phase A hat kein YT-UK angelegt
  für Prime Video)
- `channels`-Mirror (Z. 185-188): UK-Entry analog TT

**`disney`** (Z. 194-247):
- TT (Z. 195-207): nach DE-Entry → `{"handle": "disneyuk", "market": "UK"}`
- IG (Z. 208-218): nach DE-Entry → 3× UK-Entries (`disneyuk`, `disneystudiosuk`, `marvel_uk`)
- YT (Z. 227-233): UK-Pool (`DisneyUK`, `MarvelUK`, `StarWarsUK`)
- `channels`-Mirror (Z. 240-247): UK-Entry analog TT (single `disneyuk`)

**`netflix`** (Z. 253-271):
- TT (Z. 254-257): `{"handle": "netflixuk", "market": "UK"}`
- IG (Z. 258-261): `{"handle": "netflixuk", "market": "UK"}`
- YT (Z. 262-265): `{"handle": "NetflixUK", "market": "UK"}`
- `channels`-Mirror (Z. 268-271): UK-Entry analog TT

**`paramountpictures`** (Z. 277-298):
- TT (Z. 278-283), IG (Z. 284-288), YT (Z. 290-292): UK-Eintrag analog
- `channels`-Mirror (Z. 295-298): UK-Entry analog TT

### 2.2 `_aggregate_platform` — `insight_engine.py:1515-1614`

Generischer 3-Markt-Pattern. Sequenz der Edits:

| Z. | Bestand (2-Markt) | Erweiterung (3-Markt) |
|---|---|---|
| 1542-1547 | `de_specs`/`us_specs` + Lookups | + `uk_specs = [c for c in channel_specs if c["market"] == "UK"]` + `uk_handles`/`uk_channels` |
| 1550-1551 | `de_resolved`/`us_resolved` | + `uk_resolved = {c.handle.lower() for c in uk_channels}` |
| 1553-1565 | `notes`-Loops für DE/US (Z. 1554, 1560) | + `for spec in uk_specs:` Loop analog |
| 1571-1572 | `de_display_handle`/`us_display_handle` | + `uk_display_handle = uk_specs[0]["handle"] if uk_specs else ""` |
| 1574-1587 | `de_stats`/`us_stats` über `_channel_stats(...)` | + `uk_stats = _channel_stats(session, uk_channels, uk_display_handle, "UK", window_start, window_end, platform=platform) if uk_specs else None` |
| 1588 | `_cross_market_matches(de, us, ...)` | **B2-Scope** — bleibt 2-Markt in B1. Optional: `if uk_channels: notes.append("UK-Pool vorhanden, Cross-Market-Matches mit UK folgen in B2")` |
| 1589 | `_title_coverage(de_stats, us_stats, session, de_channels, us_channels, ...)` | Signatur erweitern: `_title_coverage(de_stats, us_stats, uk_stats, session, de_channels, us_channels, uk_channels, ...)` (siehe 2.4) |
| 1591-1600 | Datenbasis-schwach-Notes für DE/US | + UK-Pendant: `if uk_stats and uk_stats.posts_count < 5: notes.append("Datenbasis UK schwach ({label}): nur {uk_stats.posts_count} Posts in den letzten {window_days} Tagen.")` |
| 1607-1614 | `return PlatformAggregation(..., de_channel=de_stats, us_channel=us_stats, ...)` | + `uk_channel=uk_stats` |

### 2.3 `PlatformAggregation` + `PairAggregation` — `schemas/insights.py:166-203`

**`PlatformAggregation`** (Z. 166-181): nach `us_channel`-Feld (Z. 178)
neues additives Feld einfügen:

```python
us_channel: Optional[ChannelStats] = None
uk_channel: Optional[ChannelStats] = None  # ← NEU
```

Back-Compat: Default `None`, alte persistierte Briefe parsen via
`model_validate` weiterhin.

**`PairAggregation`** (Z. 184-203): nach `us_channel` (Z. 194)
analog:

```python
de_channel: Optional[ChannelStats]
us_channel: Optional[ChannelStats]
uk_channel: Optional[ChannelStats] = None  # ← NEU, Default für legacy
```

Mirror-Befüllung erfolgt automatisch via `per_platform[0].uk_channel`
in der bestehenden Mirror-Logik (vermutlich in `aggregate_pair` —
falls morgen ein Test bricht, hier nochmal nachsehen).

### 2.4 `TitleCoverage` — `schemas/insights.py:155-163`

Nach den `us_*`-Feldern (Z. 161-162) drei additive Felder mit
Defaults:

```python
us_assets_with_title: int
us_assets_total: int
uk_only_titles: list[str] = []          # ← NEU
uk_assets_with_title: int = 0           # ← NEU
uk_assets_total: int = 0                # ← NEU
overall_coverage_pct: float
```

`titles_in_both_markets` bleibt in B1 als DE∩US-Semantik. Triple-
Intersection (DE∩UK, US∩UK, DE∩US∩UK) ist B2-Scope.

### 2.5 `_title_coverage` Funktion — `insight_engine.py:1439-1509`

Sequenz:

| Z. | Bestand | Erweiterung |
|---|---|---|
| 1440-1442 | Signatur `de_stats, us_stats, session, de_channels, us_channels` | + `uk_stats`, + `uk_channels` |
| 1451-1456 | 2 sets + 4 counter | + `uk_titles: set[str] = set()` + `uk_with_title = 0` + `uk_total = 0` |
| 1458-1492 | Loop über `(de_channels, de_titles, "DE")` + `(us_channels, us_titles, "US")` | Tupel-Liste um UK-Tupel erweitern; if/else-Cascade (Z. 1481-1489) auf 3 Markets erweitern (oder klar mit dict-keyed-Counter refactoren — einfacher: dict-Pattern statt if-elif) |
| 1494-1496 | `de_only` / `us_only` | + `uk_only = sorted(uk_titles - de_titles - us_titles)` |
| 1497-1498 | `total_assets = de + us`, overall-Berechnung | + UK-Anteile addieren |
| 1500-1508 | `return TitleCoverage(...)` | + `uk_only_titles=uk_only` + `uk_assets_with_title=uk_with_title` + `uk_assets_total=uk_total` |

Plus zweite TitleCoverage-Instanz im Default-Pfad bei Z. 1692-1695:
identische uk_*-Defaults dort spiegeln.

### 2.6 `_build_user_prompt` Markdown-Generierung — `insight_engine.py:1840-1858`

Sequenz:

| Z. | Bestand (2-Markt) | Erweiterung (3-Markt) |
|---|---|---|
| 1840 | `cross_matches = platform_agg.cross_market_matches or []` | unverändert (B2-Scope) |
| 1842-1843 | `de_has_data` / `us_has_data` | + `uk = platform_agg.uk_channel` + `uk_has_data = bool(uk and uk.posts_count)` |
| 1844 | Skip-if-all-empty: `not de_has_data and not us_has_data and not cross_matches` | + `and not uk_has_data` |
| 1851-1854 | 2 `_format_channel_section`-Calls für DE/US | + `if uk_has_data: block.append(_format_channel_section("UK", uk, platform))` |

`_format_channel_section(market, stats, platform)` nimmt `market` bereits
als String-Parameter (Z. 1740) → Aufruf mit `"UK"` funktioniert ohne
Funktions-Edit. Markdown-Header `### UK: @<handle>` wird automatisch
erzeugt.

### 2.7 `test_pairs_registry_schema` — `test_insight_engine.py:277-297`

Eine Zeile lockern (Z. 293):

```diff
-    assert markets == {"DE", "US"}, "Beide Märkte müssen im channels-Mirror vertreten sein"
+    assert {"DE", "US"} <= markets <= {"DE", "US", "UK"}, (
+        "DE+US müssen vertreten sein; UK ist seit B1 erlaubt aber optional"
+    )
```

Docstring (Z. 278-283) entsprechend leicht anpassen — Hinweis dass UK
ab B1 (Sprint UK-Markt-Integration) zugelassen ist.

### 2.8 Weitere Tests, die wahrscheinlich keine Anpassung brauchen

- `test_pairs_backwards_compat_mirror_first_platform`: prüft
  `channels == platforms["tiktok"]` — bleibt grün, wenn beide
  konsistent erweitert werden.
- `test_aggregate_pair_handles_missing_channels_for_all_enabled_pairs`:
  prüft DE/US sind nicht-None — bleibt grün (UK ist optional, Test
  fragt nicht danach).
- `test_aggregate_pair_handles_single_market_youtube`: prüft Disney YT
  DE=None — bleibt grün.
- Disney/Warner/Sony Multi-Channel-Pool-Tests aus Sprints 10d/10h/10j:
  bleiben grün, da sie nur den US-Pool prüfen.

### 2.9 Neue Tests für B1 (mindestens 4)

1. **`test_aggregate_pair_includes_uk_channel_when_specced`** — seedet
   warnerbros mit `warnerbrosuk` als UK-Channel, prüft
   `agg.per_platform[0].uk_channel.posts_count > 0`.
2. **`test_aggregate_pair_uk_channel_none_when_pair_has_no_uk_specs`**
   — `universalpictures` (oder ein synthetic-Pair ohne UK-Spec) →
   `uk_channel is None`, kein Crash.
3. **`test_title_coverage_includes_uk_only_titles`** — 1 UK-Post mit
   Title-Match, kein DE/US-Post mit demselben Title → erscheint in
   `uk_only_titles`.
4. **`test_format_channel_section_renders_uk_header`** — unit-test für
   `_format_channel_section("UK", stats, "tiktok")` → Output enthält
   `"### UK:"`.

Plus möglicherweise:

5. **`test_pairs_registry_schema_accepts_uk_market`** — explizites
   Pendant zur Invariant-Lockerung (alle 6 erweiterten Pairs erscheinen
   mit UK im Mirror).

### 2.10 Brief-Renderer (Frontend) — explizit NICHT in B1

Frontend ist B3-Scope. B1 lässt das Frontend unverändert. Konsequenz:

- Brief-LLM-Input enthält UK-Sektion (Markdown).
- Brief-LLM-Output erwähnt UK-Posts in Prosa.
- Frontend rendert UK-Daten NICHT visuell (kein `uk_channel`-Card).

Akzeptierte Konsequenz: User sehen ab B1-Deploy UK-Erwähnungen im
Brief-Prosa-Text, aber keine UK-Spalte im UI. Korrektes Layout kommt
mit B3.

---

## 3. Aufwand-Re-Estimate

| Schritt | Zeit-Estimate |
|---|---|
| PAIRS-Erweiterungen für 6 Pairs (3 Plattformen × 6 Pairs, mit Disney UK-Pool für IG+YT) + Legacy-Mirror | ~30 Min |
| `_aggregate_platform` 3. Bucket-Pattern | ~30 Min |
| `_title_coverage` 3. Markt-Pattern + Schema-Felder | ~30 Min |
| `PlatformAggregation` + `PairAggregation` Schema-Felder | ~10 Min |
| `_build_user_prompt` 3. Markdown-Section | ~10 Min |
| `test_pairs_registry_schema` Invariant lockern | ~5 Min |
| 4-5 neue Tests (uk_channel populate, uk_channel None, title coverage UK, format-section UK, registry schema) | ~60 Min |
| Full-Suite-Run + ggf. Test-Repair (z. B. Disney-Tests, die hardcoded DE+US-Spec-Counts annehmen) | ~30 Min |
| Commit + Push + PR | ~10 Min |

**Total: ~3-3.5h.** Bleibt im B1-Range (3-4h aus Backlog #119). Keine
Überraschungen, die das in Richtung 4h+ schieben würden.

---

## 4. Reihenfolge-Vorschlag für Phase 2

1. **Schema-Erweiterung zuerst** (`schemas/insights.py`):
   `PlatformAggregation.uk_channel`, `PairAggregation.uk_channel`, 3
   `TitleCoverage.uk_*`-Felder. Lokale Tests laufen sofort weiter, da
   Defaults additiv.
2. **`_aggregate_platform` + `_title_coverage` erweitern**:
   Aggregations-Code für 3. Bucket. Tests gegen Schema bleiben grün
   (uk_channel ist None für alle existierenden Pairs).
3. **PAIRS-Erweiterung für 6 Pairs**: UK-Einträge addieren. Hier
   greift jetzt der Code aus Schritt 2 — UK-Posts werden aggregiert,
   UK-Coverage wird gerechnet.
4. **`test_pairs_registry_schema` Invariant lockern**: ein-zeiler.
5. **4-5 neue B1-Tests schreiben**.
6. **Full-Suite-Run**: Baseline 493 sollte auf 497-498 wachsen.
7. **`_build_user_prompt` Markdown-Section**: zuletzt, da pure Surface-
   Anpassung.
8. **Commit + Push + PR**.

Begründung: Schema-First minimiert Test-Breakage-Risiko während des
Refactors. PAIRS-Erweiterung zuletzt, weil die UK-Daten erst dann
sichtbar werden, wenn die Aggregations-Logik schon steht.

---

## 5. Pre-Phase-A-Channel-Existenz: NICHT verifizieren in dieser Session

Die Pre-Mapping-Handles (`warnerbrosuk`, `dcofficial`/`disneyuk` etc.)
existieren laut Wolf in der DB durch Phase A. Diese Session verifiziert
die DB-Existenz **nicht** — die Diagnose ist Read-Only auf
Repository-Source, nicht auf Datenbank.

**Risiko für morgen**: Falls einer der Pre-Mapping-Handles NICHT in der
DB existiert (z. B. weil Wolf bei Phase A einen Tippfehler in den 24
Inserts hatte), wird `aggregate_pair` für diese Plattform eine
`UK-Channel @<handle> wurde nicht in der DB gefunden`-Notiz schreiben,
aber nicht crashen. Tests müssen morgen unter dieser Annahme laufen.

Falls morgen ein solches Mismatch auftaucht: Korrektur ist trivial
(Wolf führt einzelne SQL-Updates aus). Kein Sprint-Blocker.

---

## 6. Out-of-Scope-Bestätigungen

Für B1:

- ❌ `universalpictures` UK-Channels (Pair ist disabled, neuer Scope nötig)
- ❌ `paramountplusuk` (paramount+ kein Pair)
- ❌ `lionsgateuk` (lionsgate kein Pair)
- ❌ Cross-Market-Matching DE↔UK / US↔UK (B2-Scope)
- ❌ Frontend 3-Spalten-Layout (B3-Scope)
- ❌ Disney TT-UK (Phase-A-Lücke, separater Recherche-Sprint)

**✅ Update 2026-05-11 EOD — Netflix-UK-Gap geschlossen**

Der ursprüngliche Diagnose-Stand zitierte Wolfs Brief vom 11.05.:
„Netflix UK: komplett fehlend (kein IG/TT/YT-UK angelegt)". Dieser
Stand ist überholt. Am 11.05. EOD wurde der Gap durch einen weiteren
SQL-Sprint (`sprint_uk_inventory_gap_2026_05_11`) geschlossen:

- `netflixuk` IG ✅ (5M Followers)
- `netflixuk` TT ✅ (1.5M Followers)
- `NetflixUK` YT ✅
- Plus `disneyuk` TT (4.9M Followers, Phase-A-Lücke ebenfalls geschlossen)

**DB-Stand jetzt**: 28 UK-Channels (10+1 IG, 7+2 TT, 8 YT), alle
`mvp=true`. Netflix UK ist damit voll in B1-Scope — alle 6 Pairs
(`warnerbros`, `sonypictures`, `primevideo`, `disney`, `netflix`,
`paramountpictures`) bekommen UK-Einträge in B1. Die ursprüngliche
DB-Query-Empfehlung entfällt.

---

## 7. Session-Output

| Akzeptanzkriterium | Status |
|---|---|
| Befund-Report unter `docs/sprints/b1-phase1-diagnose-2026-05-11.md` | ✅ diese Datei |
| Pre-Mapping-Bestätigung (oder Diskrepanz-Liste) | ✅ 1 Diskrepanz (`universalpictures`), 1 offen (Netflix-UK-DB-Existenz) |
| Konkrete Zeilen-Verweise für morgen | ✅ Abschnitte 2.1-2.6 |
| Aufwand-Re-Estimate für Phase 2 | ✅ ~3-3.5h (Abschnitt 3) |
| KEIN Code-Edit außer dem einen Doc-File | ✅ — diese Datei ist der einzige Write |
| KEIN Branch | ✅ — auf `main`, ungestagete Änderung |
| KEIN Commit, KEIN PR | ✅ |

Diagnose komplett. Phase 2 (Implementation) ist für morgen vorbereitet
mit konkreten Zeilen-Verweisen, einer abgehakten Pre-Mapping-Checkliste
und einer offenen Netflix-UK-DB-Frage, die Wolf vor Beginn der
Implementation kurz checken kann.
