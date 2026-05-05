# Title-Tagging-Status — 2026-05-05

> **Container-Hinweis:** Dieser Report wurde im Diagnose-Container ohne
> direkten Zugriff auf `Postgres-_HAO` erstellt. Q4 (Tagging-Mechanismus)
> + Schema-Realität sind vollständig aus der Codebase abgeleitet
> (definitive Quelle). Q1/Q2/Q3/Q5 enthalten **execution-ready
> psql-Blöcke**, die Wolf 1:1 ausführen kann; die Output-Tabellen sind
> als leere Templates angelegt und in der Empfehlungssektion über
> Schwellenwerte parametrisiert (Decision-Tree statt fester Empfehlung).

---

## Executive Summary

- **Schema-Anomalie identifiziert:** `creative_radar.post` hat **keine**
  `title_id`-Spalte. Q1 in der ursprünglichen Form ist nicht
  beantwortbar — Title-Tagging existiert ausschließlich auf
  **Asset-Ebene** (`creative_radar.asset.title_id`). Q1/Q2 werden
  daher zusammengelegt und auf Asset-Coverage operationalisiert.
- **Tagging-Mechanismus** ist hybrid (3 Schreibpfade): synchron beim
  Asset-Insert (`whitelist_matcher.find_best_title_match` mit
  `is_safe_auto_match`-Guard ≥0.95), opportunistisch im Vision-Lauf
  (`visual_analysis._find_title_match` — substring auf
  ocr_text+caption), nachträglich via
  `POST /api/admin/titles/rematch-assets` (`rematch_unassigned_assets`).
- **Empfehlung KF5:** **Decision-Tree** (s.u.). Variante B
  („Pro-Title-Briefing") ist MVP-fähig **nur**, wenn Asset-Coverage
  ≥40% im MVP-Scope **und** ≥3 Titles je ≥5 Assets ergeben. Sonst
  Variante A („Format-Trends") als sicherer MVP-Pfad.

---

## Pre-Commitments-Check

| PC | Status |
|---|---|
| **PC-1 Read-only** | ✅ Keine UPDATEs/INSERTs, alle Queries sind SELECT |
| **PC-2 Schema-Drift dokumentiert** | ✅ `public.market`/`public.priority` vs `creative_radar.*`, plus Anomalie `post.title_id` fehlt |
| **PC-3 MVP-Scope** | ✅ Queries scopen auf `mvp=TRUE AND active=TRUE` (16 Channels: 7 Tier-A DE+US TT-Pairs + 9 IG) |
| **PC-4 Tagesbericht** | ✅ Eine Markdown-Datei, kein Zwischenbericht |

---

## Schema-Realität (aus Migrations + ORM)

| Tabelle | Schema | title_id-Spalte? | FK |
|---|---|---|---|
| `creative_radar.channel` | `creative_radar` | – | – |
| **`creative_radar.post`** | `creative_radar` | **NEIN** | – |
| `creative_radar.asset` | `creative_radar` | ✅ `title_id UUID NULLABLE` | → `creative_radar.title.id` |
| `creative_radar.title` | `creative_radar` | – | – |
| `creative_radar.title_keyword` | `creative_radar` | ✅ `title_id UUID NOT NULL` | → `creative_radar.title.id` |

**ENUM-Verteilung** (relevant für CAST in den Queries unten):

| ENUM | Schema | Werte |
|---|---|---|
| `market` | **`public`** | DE, US, INT, MIXED, UNKNOWN, UK |
| `priority` | **`public`** | A, B, C |
| `channel_role` | `creative_radar` | studio_distributor, franchise, talent_cast, regional, publisher_platform |
| `quality_tier` | `creative_radar` | P0, P1, P2 |
| `acquisition_strategy` | `creative_radar` | apify, youtube_api, manual |

Quellen: `backend/migrations/versions/cf842bbfaeb5_baseline_from_sqlmodel_state.py`,
`backend/migrations/versions/7e3b2c4a8f51_extend_channel_registry_fields.py`,
`backend/app/models/entities.py:60-142`, plus die drei Schema-Drift-
Hotfixes vom 2026-05-05 (Commits `0363267`, `8391388`, `3aea749`).

**Vorab-Verifikation (PC-2-konform, in psql ausführen):**

```sql
-- Spalten-Verifikation post.title_id (erwartet: leeres Result-Set)
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'creative_radar'
  AND table_name = 'post'
  AND column_name = 'title_id';

-- Spalten-Verifikation asset.title_id (erwartet: 1 row, uuid)
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'creative_radar'
  AND table_name = 'asset'
  AND column_name = 'title_id';
```

---

## Q1 + Q2 — Title-Coverage (zusammengelegt)

**Hinweis:** Q1 verlangte `post.title_id`-Coverage. Da diese Spalte
nicht existiert, wird Q1 als „Posts mit ≥1 getaggtem Asset" und Q2 als
„Asset-Coverage direkt" interpretiert. Beide Sichten werden separat
ausgeliefert.

### MVP-Scope-Definition

```sql
-- Aktiver MVP-Set: 7 Tier-A DE+US TT-Pairs + 9 IG-Channels (mvp=TRUE)
WITH mvp_channels AS (
  SELECT id, handle, name, market::text AS market, platform
  FROM creative_radar.channel
  WHERE mvp = TRUE AND active = TRUE
)
SELECT platform, market, COUNT(*) AS channels
FROM mvp_channels
GROUP BY platform, market
ORDER BY platform, market;
```

| Platform | Market | Channels (erwartet) |
|---|---|---|
| instagram | DE | _<wolf>_ |
| instagram | US | _<wolf>_ |
| tiktok | DE | _<wolf>_ |
| tiktok | US | _<wolf>_ |
| **TOTAL** |  | **~16** |

### Q1 — Posts mit ≥1 getaggtem Asset (pro Channel)

```sql
WITH mvp_channels AS (
  SELECT id, handle, name, market::text AS market, platform
  FROM creative_radar.channel
  WHERE mvp = TRUE AND active = TRUE
),
post_tagged AS (
  SELECT p.id AS post_id,
         p.channel_id,
         BOOL_OR(a.title_id IS NOT NULL) AS has_tagged_asset
  FROM creative_radar.post p
  LEFT JOIN creative_radar.asset a ON a.post_id = p.id
  WHERE p.channel_id IN (SELECT id FROM mvp_channels)
  GROUP BY p.id, p.channel_id
)
SELECT c.platform, c.market, c.handle, c.name,
       COUNT(*)                                              AS posts_total,
       COUNT(*) FILTER (WHERE has_tagged_asset)              AS posts_with_tag,
       ROUND(
         100.0 * COUNT(*) FILTER (WHERE has_tagged_asset)
         / NULLIF(COUNT(*), 0), 1
       )                                                     AS coverage_pct
FROM mvp_channels c
LEFT JOIN post_tagged pt ON pt.channel_id = c.id
GROUP BY c.platform, c.market, c.handle, c.name
ORDER BY c.platform, c.market, c.handle;
```

| Platform | Market | Handle | Posts Total | Posts mit Tag | Coverage % |
|---|---|---|---:|---:|---:|
| _<wolf-output>_ | | | | | |

### Q1b — Aggregat pro Plattform

```sql
WITH mvp_channels AS (
  SELECT id, platform FROM creative_radar.channel
  WHERE mvp = TRUE AND active = TRUE
),
post_tagged AS (
  SELECT p.id AS post_id, p.channel_id,
         BOOL_OR(a.title_id IS NOT NULL) AS has_tagged_asset
  FROM creative_radar.post p
  LEFT JOIN creative_radar.asset a ON a.post_id = p.id
  WHERE p.channel_id IN (SELECT id FROM mvp_channels)
  GROUP BY p.id, p.channel_id
)
SELECT c.platform,
       COUNT(pt.post_id)                                     AS posts_total,
       COUNT(*) FILTER (WHERE pt.has_tagged_asset)           AS posts_with_tag,
       ROUND(100.0 * COUNT(*) FILTER (WHERE pt.has_tagged_asset)
             / NULLIF(COUNT(pt.post_id), 0), 1)              AS coverage_pct
FROM mvp_channels c
LEFT JOIN post_tagged pt ON pt.channel_id = c.id
GROUP BY c.platform
ORDER BY c.platform;
```

| Platform | Posts Total | Posts mit Tag | Coverage % |
|---|---:|---:|---:|
| instagram | _<wolf>_ | | |
| tiktok | _<wolf>_ | | |

### Q1c — Zeitliche Entwicklung (7d / 30d / 90d)

```sql
WITH mvp_channels AS (
  SELECT id FROM creative_radar.channel
  WHERE mvp = TRUE AND active = TRUE
),
post_tagged AS (
  SELECT p.id AS post_id, p.detected_at,
         BOOL_OR(a.title_id IS NOT NULL) AS has_tagged_asset
  FROM creative_radar.post p
  LEFT JOIN creative_radar.asset a ON a.post_id = p.id
  WHERE p.channel_id IN (SELECT id FROM mvp_channels)
  GROUP BY p.id, p.detected_at
)
SELECT
  CASE
    WHEN detected_at > NOW() - INTERVAL '7 days'  THEN 'last_7d'
    WHEN detected_at > NOW() - INTERVAL '30 days' THEN 'last_30d'
    WHEN detected_at > NOW() - INTERVAL '90 days' THEN 'last_90d'
    ELSE 'older'
  END AS bucket,
  COUNT(*)                                       AS posts_total,
  COUNT(*) FILTER (WHERE has_tagged_asset)       AS posts_with_tag,
  ROUND(100.0 * COUNT(*) FILTER (WHERE has_tagged_asset)
        / NULLIF(COUNT(*), 0), 1)                AS coverage_pct
FROM post_tagged
GROUP BY bucket
ORDER BY bucket;
```

| Zeitraum | Posts | Posts mit Tag | Coverage % |
|---|---:|---:|---:|
| last_7d | | | |
| last_30d | | | |
| last_90d | | | |
| older | | | |

### Q2 — Asset-Coverage direkt + Konsistenz

```sql
WITH mvp_channels AS (
  SELECT id, handle, market::text AS market, platform
  FROM creative_radar.channel
  WHERE mvp = TRUE AND active = TRUE
),
asset_join AS (
  SELECT a.id              AS asset_id,
         a.title_id         AS asset_title_id,
         a.post_id,
         p.channel_id,
         a.created_at
  FROM creative_radar.asset a
  JOIN creative_radar.post p ON p.id = a.post_id
  WHERE p.channel_id IN (SELECT id FROM mvp_channels)
)
SELECT c.platform, c.market, c.handle,
       COUNT(*)                                              AS assets_total,
       COUNT(*) FILTER (WHERE asset_title_id IS NOT NULL)    AS assets_with_title,
       ROUND(100.0 * COUNT(*) FILTER (WHERE asset_title_id IS NOT NULL)
             / NULLIF(COUNT(*), 0), 1)                       AS coverage_pct
FROM mvp_channels c
LEFT JOIN asset_join aj ON aj.channel_id = c.id
GROUP BY c.platform, c.market, c.handle
ORDER BY c.platform, c.market, c.handle;
```

| Platform | Market | Handle | Assets Total | Assets mit Title | Coverage % |
|---|---|---|---:|---:|---:|
| _<wolf-output>_ | | | | | |

### Q2b — Konsistenz-Check (Post-Level vs Asset-Level)

> **Wichtig:** Da `post.title_id` nicht existiert, ist die ursprünglich
> formulierte Konsistenz-Frage strukturell nicht beantwortbar. Statt-
> dessen: **Konsistenz innerhalb eines Posts** — haben Assets desselben
> Posts denselben Title?

```sql
WITH mvp_channels AS (
  SELECT id FROM creative_radar.channel
  WHERE mvp = TRUE AND active = TRUE
),
post_title_count AS (
  SELECT p.id AS post_id,
         COUNT(DISTINCT a.title_id) FILTER (WHERE a.title_id IS NOT NULL) AS distinct_titles,
         COUNT(*)                                                          AS total_assets,
         COUNT(*) FILTER (WHERE a.title_id IS NULL)                        AS untagged_assets
  FROM creative_radar.post p
  LEFT JOIN creative_radar.asset a ON a.post_id = p.id
  WHERE p.channel_id IN (SELECT id FROM mvp_channels)
  GROUP BY p.id
)
SELECT
  CASE
    WHEN total_assets = 0 THEN 'no_assets'
    WHEN distinct_titles = 0 THEN 'all_untagged'
    WHEN distinct_titles = 1 AND untagged_assets = 0 THEN 'consistent_tagged'
    WHEN distinct_titles = 1 AND untagged_assets > 0 THEN 'partial_tagged_consistent'
    WHEN distinct_titles >= 2 THEN 'inconsistent'
  END AS bucket,
  COUNT(*) AS posts
FROM post_title_count
GROUP BY 1
ORDER BY 1;
```

| Bucket | Posts |
|---|---:|
| no_assets | |
| all_untagged | |
| consistent_tagged | |
| partial_tagged_consistent | |
| inconsistent | |

---

## Q3 — Title-Population

### Q3a — Counts

```sql
SELECT
  COUNT(*)                                                AS titles_total,
  COUNT(*) FILTER (WHERE active = TRUE)                   AS titles_active,
  COUNT(*) FILTER (
    WHERE id IN (SELECT DISTINCT title_id FROM creative_radar.asset
                 WHERE title_id IS NOT NULL)
  )                                                       AS titles_referenced,
  COUNT(*) FILTER (
    WHERE id NOT IN (SELECT DISTINCT title_id FROM creative_radar.asset
                     WHERE title_id IS NOT NULL)
  )                                                       AS titles_orphan
FROM creative_radar.title;
```

| titles_total | titles_active | titles_referenced | titles_orphan |
|---:|---:|---:|---:|
| | | | |

### Q3b — Top-20 Titles by Asset-Count (MVP-Scope)

```sql
WITH mvp_channels AS (
  SELECT id FROM creative_radar.channel
  WHERE mvp = TRUE AND active = TRUE
)
SELECT t.title_original, t.franchise, t.priority::text AS priority,
       t.market_relevance::text AS market_relevance,
       COUNT(a.id)                                           AS asset_count,
       COUNT(DISTINCT p.channel_id)                          AS channel_count,
       COUNT(DISTINCT a.id) FILTER (WHERE c.market::text = 'DE') AS assets_de,
       COUNT(DISTINCT a.id) FILTER (WHERE c.market::text = 'US') AS assets_us
FROM creative_radar.title t
JOIN creative_radar.asset a ON a.title_id = t.id
JOIN creative_radar.post p ON p.id = a.post_id
JOIN creative_radar.channel c ON c.id = p.channel_id
WHERE p.channel_id IN (SELECT id FROM mvp_channels)
GROUP BY t.id, t.title_original, t.franchise, t.priority, t.market_relevance
ORDER BY asset_count DESC, t.title_original
LIMIT 20;
```

| Title | Franchise | Priority | Market-Rel | Assets | Channels | DE | US |
|---|---|---|---|---:|---:|---:|---:|
| _<wolf-output>_ | | | | | | | |

### Q3c — Title-Population gesamt (Vergleichszahl)

```sql
SELECT t.title_original, t.franchise,
       COUNT(a.id)              AS asset_count_global,
       COUNT(DISTINCT p.channel_id) AS channel_count_global
FROM creative_radar.title t
LEFT JOIN creative_radar.asset a ON a.title_id = t.id
LEFT JOIN creative_radar.post p  ON p.id = a.post_id
GROUP BY t.id, t.title_original, t.franchise
ORDER BY asset_count_global DESC, t.title_original
LIMIT 20;
```

---

## Q4 — Tagging-Mechanismus (Codebase-Findings)

> Vollständig aus der Codebase abgeleitet — definitiv, kein DB-Zugriff
> nötig.

### 4.1 Schreibpfade auf `asset.title_id`

Es gibt **drei** automatische Schreibpfade plus zwei manuelle:

| # | Pfad | Datei:Zeile | Trigger | Algorithmus |
|---|---|---|---|---|
| 1 | **Apify-Sync (Cron + Manual)** | `backend/app/api/monitor.py:104,127` (`_create_asset_from_item`) | Jeder Apify-Sync (IG/TT-Cron, `/api/monitor/apify-instagram`, `/api/monitor/apify-tiktok`) | Synchron beim Asset-Insert: `find_best_title_match(caption)` mit `is_safe_auto_match`-Guard. Wenn unsafe: zweiter Versuch mit erweiterten Feldern (caption + ocr_text + ai_summary + visual_notes + suggested_title + detected_keywords). |
| 2 | **Vision-Pipeline (post-hoc)** | `backend/app/services/visual_analysis.py:194-198` | Bei jedem `analyze_asset_visual`-Lauf, der für **bereits ungetaggte** Assets läuft (`if not title:`) | `_find_title_match(ocr_text, caption)` — naive substring-Suche `lower-haystack contains candidate`. **Kein** Confidence-Gate — erstes Match gewinnt. |
| 3 | **Manuelle Re-Match-Aktion** | `backend/app/services/title_rematch.py:42-58` über `POST /api/admin/titles/rematch-assets` | Manueller Trigger | Iteriert alle Assets mit `title_id IS NULL`, ruft `find_best_title_match` (full fields), bei `is_safe_auto_match` → setzt title_id + de_us_match_key, sonst → erstellt `TitleCandidate`. |
| 4 | Manuelle Single-Asset-Edit | `backend/app/api/assets.py:108-112` (`PATCH /api/assets/{id}`) | UI-Edit | Direkter Set ohne Match-Logik |
| 5 | Manueller Post-Import | `backend/app/api/posts.py:97,135` (`POST /api/posts/manual-import`) | UI-Manueller-Import | Wie #1, aber synchron im Endpoint-Body |

### 4.2 Match-Algorithmus (`find_best_title_match`, `whitelist_matcher.py:126`)

**Pipeline:**

1. **Normalisierung**: `unicodedata.NFKC` + `casefold` + Punctuation→Space.
2. **Kandidaten-Pool** pro `Title` (alle `active=TRUE`): `title_original`,
   `title_local`, `aliases[]`, `franchise`, `TitleKeyword.keyword[]`.
   Kandidaten ≤2 Zeichen oder in `_GENERIC_WORDS` (`movie`, `film`,
   `official`, `trailer`, `teaser`, `cinema`, `video`, `clip`)
   werden gefiltert.
3. **Strong-Hits** (sources: `exact`, `exact_local`, `exact_alias`,
   `hashtag`, `unique_text`):
   - **Hashtag-Match**: `#FooBarBaz` → split CamelCase + `-`/`_` → normalize
     → exact lookup gegen normalized-candidate-map.
   - **Exact-Match**: normalized_text == normalized_candidate → score=1.0.
   - **Phrase-Containment**: `_contains_phrase(haystack, candidate)` mit
     padded-string-search → score=1.0 wenn `unique_text`, 0.97 für
     alias/local.
4. **Weak-Hit** (Fuzzy): `SequenceMatcher.ratio()` > 0.72 → `source="fuzzy"`,
   confidence = ratio.
5. **Ambiguity-Guard**: ≥2 verschiedene Titles gewinnen Strong-Hits →
   `MatchResult(title=None, source="ambiguous")` (no auto-tag).

**`is_safe_auto_match`** (`whitelist_matcher.py:117`):
```python
return bool(match.title and
            match.source in {"exact", "exact_alias", "exact_local",
                             "hashtag", "unique_text"} and
            match.confidence >= 0.95)
```

→ Fuzzy-Hits werden NIE automatisch geschrieben (kommen aber als
`TitleCandidate` in den Review-Tisch).

### 4.3 Vision-Pipeline-Tagger (`_find_title_match`, `visual_analysis.py:106-117`)

**Achtung — schwächerer Algorithmus** als der Whitelist-Matcher:

```python
haystack = f"{visual_text}\n{caption}".lower()
for title in active_titles:
    candidates = [title.title_original, title.title_local, title.franchise,
                  *active_keywords]
    for candidate in candidates:
        if candidate and candidate.lower() in haystack:
            return title  # ← erstes Match gewinnt, kein Score, keine Ambiguity
```

**Risiken:**
- **Erstes Match in Title-Iterations-Reihenfolge gewinnt** — bei zwei
  Titles, die beide matchen würden (z.B. „Wicked" und „Wicked: For Good"),
  hängt das Ergebnis von der DB-Reihenfolge ab.
- **Keine Generic-Word-Filterung** — ein Keyword wie „film" würde alle
  Captions matchen.
- **Keine Hashtag-Splitting-Logik** — `#WickedMovie` würde nur greifen,
  wenn ein Title-Keyword exakt `wickedmovie` lautet.

→ Wenn dieser Pfad signifikante Coverage produziert (Q4-Verifikation:
welcher Anteil der getaggten Assets wurde durch Vision vs Apify-Sync
gesetzt), ist die Title-Tagging-Qualität fragil. Diagnose-Query unten.

### 4.4 Coverage-Boost-Trigger

**Es gibt KEINEN Cron-Job, der `rematch_unassigned_assets` automatisch
ausführt.** Der Endpoint `POST /api/admin/titles/rematch-assets`
existiert, wird aber nur manuell aufgerufen. Das heißt:
- Assets, die beim Apify-Sync nicht getaggt wurden, bekommen ihre
  zweite Chance erst beim Vision-Lauf (qualitativ schwächer).
- Assets ohne Vision-Lauf (`visual_analysis_status='pending'`) bleiben
  permanent ohne Title.

**Quick-Win:** Cron-Hook für `rematch_unassigned_assets` einbauen
(siehe Empfehlungen unten).

### 4.5 Diagnose-Query: Tagger-Quellen-Verteilung

```sql
-- Welcher Pipeline-Schritt hat getaggt?
-- Heuristik: visual_analysis_status hilft, den letzten Schritt zu
-- identifizieren. Ein Asset, das title_id≠NULL und
-- visual_analysis_status='analyzed' hat, kann von beiden Pfaden kommen
-- — dieser Query gruppiert nur grob.
WITH mvp_channels AS (
  SELECT id FROM creative_radar.channel
  WHERE mvp = TRUE AND active = TRUE
)
SELECT a.visual_analysis_status,
       COUNT(*)                                       AS assets_total,
       COUNT(*) FILTER (WHERE a.title_id IS NOT NULL) AS assets_tagged,
       ROUND(100.0 * COUNT(*) FILTER (WHERE a.title_id IS NOT NULL)
             / NULLIF(COUNT(*), 0), 1)                AS coverage_pct
FROM creative_radar.asset a
JOIN creative_radar.post p ON p.id = a.post_id
WHERE p.channel_id IN (SELECT id FROM mvp_channels)
GROUP BY a.visual_analysis_status
ORDER BY assets_total DESC;
```

| visual_analysis_status | Assets | Getagged | Coverage % |
|---|---:|---:|---:|
| _<wolf-output>_ | | | |

---

## Q5 — Cross-Market-Pairing

### Q5a — `de_us_match_key` Coverage MVP-Scope

```sql
WITH mvp_channels AS (
  SELECT id, market::text AS market FROM creative_radar.channel
  WHERE mvp = TRUE AND active = TRUE
),
asset_scope AS (
  SELECT a.id, a.title_id, a.de_us_match_key, mc.market
  FROM creative_radar.asset a
  JOIN creative_radar.post p ON p.id = a.post_id
  JOIN mvp_channels mc ON mc.id = p.channel_id
)
SELECT
  COUNT(*)                                                  AS assets_total,
  COUNT(*) FILTER (WHERE de_us_match_key IS NOT NULL)       AS with_match_key,
  COUNT(*) FILTER (WHERE de_us_match_key IS NULL)           AS without_match_key,
  ROUND(100.0 * COUNT(*) FILTER (WHERE de_us_match_key IS NOT NULL)
        / NULLIF(COUNT(*), 0), 1)                           AS coverage_pct
FROM asset_scope;
```

### Q5b — Echte DE↔US-Pairs (gleicher match_key in ≥2 Märkten)

```sql
WITH mvp_channels AS (
  SELECT id, market::text AS market FROM creative_radar.channel
  WHERE mvp = TRUE AND active = TRUE
),
key_market_buckets AS (
  SELECT a.de_us_match_key,
         mc.market,
         COUNT(*) AS asset_count
  FROM creative_radar.asset a
  JOIN creative_radar.post p ON p.id = a.post_id
  JOIN mvp_channels mc ON mc.id = p.channel_id
  WHERE a.de_us_match_key IS NOT NULL
  GROUP BY a.de_us_match_key, mc.market
)
SELECT
  COUNT(DISTINCT de_us_match_key)                                            AS distinct_keys,
  COUNT(DISTINCT de_us_match_key) FILTER (
    WHERE de_us_match_key IN (
      SELECT de_us_match_key
      FROM key_market_buckets
      GROUP BY de_us_match_key
      HAVING COUNT(DISTINCT market) >= 2
    )
  )                                                                          AS keys_with_multimarket
FROM key_market_buckets;
```

| distinct_keys | keys_with_multimarket |
|---:|---:|
| | |

### Q5c — KF1-Fokus: warnerbros DE↔US-Pair

```sql
SELECT c.handle, c.market::text AS market, c.platform,
       COUNT(a.id)                                           AS assets,
       COUNT(a.de_us_match_key)                              AS with_match_key,
       COUNT(DISTINCT a.de_us_match_key)                     AS distinct_keys,
       COUNT(DISTINCT a.title_id) FILTER (WHERE a.title_id IS NOT NULL) AS distinct_titles
FROM creative_radar.channel c
LEFT JOIN creative_radar.post p  ON p.channel_id = c.id
LEFT JOIN creative_radar.asset a ON a.post_id = p.id
WHERE c.handle IN ('warnerbros', 'warnerbrosde', 'warnerbrosdeutschland')
GROUP BY c.handle, c.market, c.platform
ORDER BY c.platform, c.market, c.handle;
```

| Handle | Market | Platform | Assets | match_key | distinct_keys | distinct_titles |
|---|---|---|---:|---:|---:|---:|
| _<wolf-output>_ | | | | | | |

### Q5d — warnerbros echte Cross-Market-Pairs

```sql
WITH wb_assets AS (
  SELECT a.id, a.title_id, a.de_us_match_key,
         c.market::text AS market, c.handle
  FROM creative_radar.asset a
  JOIN creative_radar.post p ON p.id = a.post_id
  JOIN creative_radar.channel c ON c.id = p.channel_id
  WHERE c.handle IN ('warnerbros', 'warnerbrosde', 'warnerbrosdeutschland')
    AND a.de_us_match_key IS NOT NULL
)
SELECT de_us_match_key,
       COUNT(*) FILTER (WHERE market = 'DE') AS de_assets,
       COUNT(*) FILTER (WHERE market = 'US') AS us_assets,
       STRING_AGG(DISTINCT handle, ', ')      AS handles
FROM wb_assets
GROUP BY de_us_match_key
HAVING COUNT(*) FILTER (WHERE market = 'DE') > 0
   AND COUNT(*) FILTER (WHERE market = 'US') > 0
ORDER BY de_assets + us_assets DESC
LIMIT 10;
```

---

## Empfehlung für KF5 (Decision-Tree)

> Die Empfehlung ist parametrisiert über die Coverage-Werte, die Wolf
> aus den Queries oben füllt. Drei Schwellen entscheiden:

### Eingaben

- **C₁** = Asset-Coverage MVP-Scope (Q2, Aggregat über alle MVP-Channels)
- **C₂** = Anzahl Titles mit ≥5 getaggten Assets im MVP-Scope (Q3b)
- **C₃** = Anzahl `de_us_match_key`-Buckets mit ≥1 DE und ≥1 US Asset im
  MVP-Scope (Q5b)

### Pfad

```
C₁ ≥ 40% UND C₂ ≥ 3                  → Variante B (Pro-Title-Briefing) MVP-fähig
                                       Begründung: ausreichend Datendichte
                                       für Pro-Title-Aggregation.
                                       Risiko: einzelne Titles können dünn sein,
                                       UI braucht Empty-State-Handling.

20% ≤ C₁ < 40% ODER C₂ < 3           → Variante B mit Einschränkungen
                                       (Whitelist 1-2 Top-Titles, Empty-State
                                       für Rest). UI muss 'low-data'-Banner
                                       zeigen.
                                       Quick-Wins (s.u.) parallel umsetzen.

C₁ < 20%                              → Variante B nicht empfohlen, Variante A
                                       (Format-Trends) als MVP-Pfad.
                                       Title-Tagging-Sprint vor erneuter
                                       Variante-B-Evaluation einplanen.

C₃ ≥ 3 (zusätzliche Voraussetzung    → Cross-Market-Vergleich Pro-Title in
für KF1-Use-Case)                     Variante B sinnvoll. Sonst KF1 in
                                       Variante B nur als Single-Market-View
                                       sinnvoll.
```

### Quick-Wins für Title-Tagging-Verbesserung

1. **Auto-Rematch-Cron** (1-2h Aufwand): Ergänze `_run_cron_sync_background`
   in `backend/app/api/cron.py:222` um einen `rematch_unassigned_assets`-
   Aufruf nach dem Vision-Schritt. Hebt Coverage post-hoc, kostet nichts
   extra. PR-Größe: ~10 Zeilen.
2. **`_find_title_match` in `visual_analysis.py:106` ersetzen** (~30 min):
   Schwacher Substring-Tagger durch `find_best_title_match` ersetzen.
   Reduziert Fehl-Tagging-Risiko, ist aber strikt-er — würde Coverage
   gegebenenfalls senken, dafür Qualität rauf.
3. **TitleKeyword-Pflege** (Wolf-Aktion): Top-Titles in Q3b durchgehen,
   fehlende Aliases/Hashtags ergänzen. `seeds.py:27-38` zeigt das
   etablierte Schema (Aliases als list).
4. **`TitleCandidate`-Review-Workflow** (UI-Sprint): aktuell entstehen
   Candidates aus Fuzzy-Matches, aber ich finde keinen Review-Endpoint,
   der sie systematisch resolve-t. Wolf-Verifikation: gibt es eine
   UI-Seite für Title-Candidate-Review?

---

## Daily-Report (Slack-fähig)

> Title-Tagging-Diagnose 2026-05-05: Schema-Anomalie aufgedeckt —
> `creative_radar.post` hat keine `title_id`-Spalte, Tagging existiert
> nur auf Asset-Ebene. Tagging-Mechanismus ist 3-stufig (Apify-Sync mit
> Whitelist-Matcher@0.95, Vision-Fallback mit naive substring,
> manueller Rematch-Endpoint ohne Cron). Kein Auto-Rematch-Cron — größter
> Quick-Win. Coverage-Zahlen offen (DB-Zugriff war im Diagnose-Container
> nicht verfügbar). Report mit ausführbaren psql-Blöcken liegt unter
> `docs/diagnostics/TITLE_TAGGING_STATUS_2026-05-05.md`. KF5-Empfehlung
> als parametrisierter Decision-Tree (C₁/C₂/C₃-Schwellen).

---

## Offene Punkte / Wolf-To-Dos

1. psql-Block-Outputs in den `_<wolf-output>_`-Tabellen-Slots ergänzen
   (5-10 Min Aufwand, Read-only).
2. Decision-Tree mit gefüllten C₁/C₂/C₃-Werten matchen → Variante A oder B.
3. Quick-Win-Auto-Rematch-Cron als Folge-Sprint einplanen (eigener
   Sprint, ~1-2h).
4. `TitleCandidate`-Review-UI-Status verifizieren (existiert
   Review-Workflow, oder bleibt das Backlog-Item?).
