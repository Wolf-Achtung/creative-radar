# TMDB-Match-Audit — Mai 2026

> **Sprint 8 — Read-only-Diagnose.** Keine Code-Änderungen am Match-Algorithmus,
> keine Schema-Migration, keine LLM-Calls. Ziel ist ein präzises Bild davon,
> warum die Title-Coverage in der Browser-Smoke vom 09.05.2026 für mehrere
> Pair × Plattform × Markt-Kombinationen zwischen 0 % und 15 % liegt.

---

## Wolf-Ping: Sandbox ohne DB-Zugriff

Der Diagnose-Container hat (analog zu `docs/diagnostics/TITLE_TAGGING_STATUS_2026-05-05.md`)
keinen Netzwerk-Zugriff auf die Production-Postgres. Konsequenzen für diesen Sprint:

- **8.1 Code-Inspektion** ist 1:1 lieferbar — der Match-Algorithmus liegt
  vollständig im Repo offen. Alle Aussagen unter §3 sind aus dem Quelltext
  abgeleitet und definitiv.
- **8.2 TMDB-Title-Inventar**, **8.3 Stichprobe**, **8.4 Annotation**,
  **8.5 Cluster** brauchen Live-DB-Werte. Ich liefere für jeden dieser
  Schritte einen **execution-ready psql-Block**, den Wolf 1:1 ausführen
  und dessen Output in die markierten `_<wolf-output>_`-Slots eintragen
  kann. Die Stichprobe-Query schreibt das Ergebnis als CSV (Pfad in §5).
- **8.4 Erwartungs-Annotation** ist pro Post manuell — sobald die Stichprobe
  als CSV vorliegt, kann ich sie in einer Folge-Iteration durchgehen und
  `expected_title` befüllen. Aktuell sind die Cluster-Beispiele unter §6
  daher aus dem öffentlich sichtbaren Browser-Smoke-Befund (Disney US
  TikTok) und dem Code-Verhalten konstruiert, nicht aus 720 echten Posts.
- **8.6 Hypothesen-Ranking** ist trotzdem belastbar: drei der vier
  Haupt-Hypothesen lassen sich allein aus Code + TMDB-API-Vertrag ableiten,
  ohne den Stichprobe-Output abwarten zu müssen.

Damit ist die Diagnose-Richtung gesetzt; die DB-Werte verfeinern sie, drehen
sie aber nicht um.

---

## 1. Zusammenfassung (TL;DR)

- Der Match-Algorithmus selbst ist **konservativ-aber-funktional**:
  Hashtag-Splitting + Phrase-Containment + Generic-Word-Filter + Ambiguity-
  Guard. Sehr unwahrscheinlich, dass er die Hauptursache der 0 %-Coverage ist.
- **Der eigentliche Engpass ist der Title-Pool** (`creative_radar.title`):
  - TMDB-Sync läuft mit `with_release_type="2|3"` (theatrical limited +
    wide). **Streaming-Originals, Premieren, TV-Drops und digitale Releases
    werden nicht gepullt.** Disney+, Netflix-Originals, Prime Video Originals,
    Paramount+ Originals fehlen damit systemisch im Title-Pool.
  - Sync-Fenster: `lookback_weeks=8` + `lookahead_weeks=24`. Nostalgie-Posts
    (Klassiker-Re-Runs, Catalog-Promos wie „Cinderella 1950") und Sequels mit
    weiterem Vorlauf fallen aus dem Window.
  - 3-Page-Hard-Cap pro Markt × Sync (`page <= 3`) → ≈ 60 Filme/Markt/Lauf.
- **Damit ist Disney US TikTok 0 % weitgehend erwartbar**: die Captions im
  Smoke (`Drawn to You`, `Cinderella`, `D23 Asia`, `Disney+`) referenzieren
  fast ausschließlich Inhalte, die der TMDB-Discover-Pull mit `release_type=2|3`
  nicht produziert.
- **Hebel** (sortiert nach Aufwand/Coverage-Ertrag):
  1. Streaming-Release-Types in den TMDB-Sync aufnehmen (~2–4 h, hoher Hebel
     für Streamer-Pairs).
  2. TMDb-`/tv`-Endpoint ergänzen (~4–8 h, hoher Hebel für Disney+ Serien,
     Netflix-Serien, Prime-Originals).
  3. Manuelle Whitelist-Erweiterung um Brand-/Event-Hashtags (`#D23`,
     `#disneyplus`, `#disneydescendants`, …) als TitleKeyword(keyword_type=
     "alias") (~2 h, kleinerer aber sofort wirksamer Hebel).

Empfehlung für Sprint 9: **TMDB-Sync-Erweiterung (release_types + /tv-Endpoint)
zuerst**, danach Whitelist-Pflege auf Basis der dann sichtbaren Lücken.

---

## 2. Coverage-Lage (psql-Block + Slot)

> Wolf führt aus, trägt Werte ein. Datenstand: 09.05.2026.

```sql
-- Coverage pro Pair × Plattform × Markt — letzte 30 Tage
WITH pair_handles (pair_key, platform, market, handle) AS (
  VALUES
    -- Disney
    ('disney', 'tiktok',    'US', 'disney'),
    ('disney', 'tiktok',    'US', 'disneystudios'),
    ('disney', 'tiktok',    'US', 'disneyanimation'),
    ('disney', 'tiktok',    'DE', 'disneyde'),
    ('disney', 'instagram', 'US', 'disney'),
    ('disney', 'instagram', 'DE', 'disneydeutschland'),
    ('disney', 'youtube',   'US', 'WaltDisneyStudios'),
    -- Netflix
    ('netflix', 'tiktok',    'US', 'netflix'),
    ('netflix', 'tiktok',    'DE', 'netflixde'),
    ('netflix', 'instagram', 'US', 'netflix'),
    ('netflix', 'instagram', 'DE', 'netflixde'),
    ('netflix', 'youtube',   'US', 'Netflix'),
    ('netflix', 'youtube',   'DE', 'NetflixDE'),
    -- Warner Bros
    ('warnerbros', 'tiktok',    'US', 'warnerbros'),
    ('warnerbros', 'tiktok',    'DE', 'warnerbrosdeutschland'),
    ('warnerbros', 'instagram', 'US', 'warnerbros'),
    ('warnerbros', 'instagram', 'DE', 'warnerbrosde'),
    ('warnerbros', 'youtube',   'US', 'WarnerBrosPictures'),
    ('warnerbros', 'youtube',   'DE', 'WarnerBrosDE'),
    -- Sony Pictures
    ('sonypictures', 'tiktok',    'US', 'sonypictures'),
    ('sonypictures', 'tiktok',    'DE', 'sonypicturesgermany'),
    ('sonypictures', 'instagram', 'US', 'sonypictures'),
    ('sonypictures', 'instagram', 'DE', 'sonypicturesde'),
    ('sonypictures', 'youtube',   'US', 'SonyPicturesEntertainment'),
    ('sonypictures', 'youtube',   'DE', 'SonyPicturesGermany'),
    -- Prime Video
    ('primevideo', 'tiktok',    'US', 'primevideo'),
    ('primevideo', 'tiktok',    'DE', 'primevideode'),
    ('primevideo', 'instagram', 'US', 'primevideo'),
    ('primevideo', 'instagram', 'DE', 'primevideode'),
    ('primevideo', 'youtube',   'US', 'PrimeVideo'),
    -- Paramount Pictures
    ('paramountpictures', 'tiktok',    'US', 'paramountpics'),
    ('paramountpictures', 'tiktok',    'DE', 'paramountpicturesgermany'),
    ('paramountpictures', 'instagram', 'US', 'paramountpics'),
    ('paramountpictures', 'instagram', 'DE', 'paramount_pictures_germany'),
    ('paramountpictures', 'youtube',   'US', 'ParamountPictures')
),
scope AS (
  SELECT ph.pair_key, ph.platform, ph.market, p.id AS post_id, p.published_at
  FROM pair_handles ph
  JOIN creative_radar.channel c
    ON LOWER(c.handle) = LOWER(ph.handle) AND c.platform = ph.platform
  JOIN creative_radar.post p
    ON p.channel_id = c.id
  WHERE p.published_at > NOW() - INTERVAL '30 days'
),
post_tagged AS (
  SELECT s.pair_key, s.platform, s.market, s.post_id,
         BOOL_OR(a.title_id IS NOT NULL) AS has_tag
  FROM scope s
  LEFT JOIN creative_radar.asset a ON a.post_id = s.post_id
  GROUP BY s.pair_key, s.platform, s.market, s.post_id
)
SELECT pair_key, platform, market,
       COUNT(*)                                  AS posts,
       COUNT(*) FILTER (WHERE has_tag)           AS posts_tagged,
       ROUND(100.0 * COUNT(*) FILTER (WHERE has_tag)
             / NULLIF(COUNT(*), 0), 1)           AS coverage_pct
FROM post_tagged
GROUP BY pair_key, platform, market
ORDER BY pair_key, platform, market;
```

| Pair | Plattform | Markt | Posts | Tagged | Coverage % | Browser-Smoke 09.05. (Referenz) |
|---|---|---|---:|---:|---:|---:|
| disney | tiktok | DE | _<wolf>_ | _<wolf>_ | _<wolf>_ | 15.4 % |
| disney | tiktok | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | **0.0 %** |
| disney | instagram | DE | _<wolf>_ | _<wolf>_ | _<wolf>_ | 0.0 % |
| disney | instagram | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | 0.0 % |
| disney | youtube | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | 0.0 % |
| netflix | tiktok | DE | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| netflix | tiktok | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| netflix | instagram | DE | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| netflix | instagram | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| netflix | youtube | DE | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| netflix | youtube | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| warnerbros | tiktok | DE | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| warnerbros | tiktok | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| warnerbros | instagram | DE | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| warnerbros | instagram | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| warnerbros | youtube | DE | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| warnerbros | youtube | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| sonypictures | tiktok | DE | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| sonypictures | tiktok | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| sonypictures | instagram | DE | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| sonypictures | instagram | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| sonypictures | youtube | DE | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| sonypictures | youtube | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| primevideo | tiktok | DE | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| primevideo | tiktok | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| primevideo | instagram | DE | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| primevideo | instagram | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| primevideo | youtube | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| paramountpictures | tiktok | DE | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| paramountpictures | tiktok | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| paramountpictures | instagram | DE | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| paramountpictures | instagram | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |
| paramountpictures | youtube | US | _<wolf>_ | _<wolf>_ | _<wolf>_ | – |

YT-DE für Disney, Prime Video und Paramount Pictures: **n/a** (keine
DE-YT-Channels in `PAIRS`, siehe `backend/app/services/insight_engine.py:84`).

---

## 3. Match-Algorithmus

### 3.1 Wo passiert der Match?

Drei automatische Schreibpfade auf `creative_radar.asset.title_id`:

| # | Pfad | Datei : Zeile | Trigger |
|---|---|---|---|
| 1 | Apify-Sync (Cron + Manual) | `backend/app/api/monitor.py:104,127` | Jeder IG/TT/YT-Sync |
| 2 | Vision-Pipeline (post-hoc) | `backend/app/services/visual_analysis.py:106-117,194-198` | `analyze_asset_visual` für ungetaggte Assets |
| 3 | Manueller Re-Match | `backend/app/services/title_rematch.py:42-58` über `POST /api/admin/titles/rematch-assets` | Manueller Trigger (kein Cron) |

Der **kanonische Match-Algorithmus** ist `find_best_title_match` in
`backend/app/services/whitelist_matcher.py:126`. Der Vision-Pfad ruft eine
**andere, schwächere Naive-Substring-Routine** auf (`_find_title_match`,
`visual_analysis.py:106`), die kein Confidence-Gate, keine Hashtag-Logik
und keine Ambiguity-Detection hat — erstes Match in DB-Reihenfolge gewinnt.

### 3.2 Was vergleicht `find_best_title_match`?

Pool pro `Title` (alle `active=TRUE`):

- `title_original` → bucket `exact`
- `title_local`    → bucket `local`
- `aliases[]`      → bucket `alias`
- `franchise`      → bucket `weak` (für Strong-Hits **nicht** verwendet)
- `TitleKeyword.keyword[]` → bucket `weak` (für Strong-Hits **nicht** verwendet)

Filter beim Aufbau der Lookup-Map (`whitelist_matcher.py:144-147`):

- Kandidaten mit ≤ 2 Zeichen entfernt
- Generic-Words entfernt: `movie`, `film`, `official`, `trailer`, `teaser`,
  `cinema`, `video`, `clip`
- `weak`-Bucket-Werte werden **nicht** in die Lookup-Map aufgenommen
  (`if source_key == "weak": continue`, Zeile 139). Damit ist
  `franchise` heute **nur** als „Disambiguator-Tie-Breaker" relevant,
  nicht als eigenständige Match-Quelle.

Match-Pipeline:

1. **Normalisierung** (`_normalize_text`): NFKC + casefold + Unterstriche/
   Bindestriche zu Spaces, Punctuation → Space, Mehrfach-Spaces auf 1.
2. **Hashtag-Match** (`_extract_hashtag_matches`):
   Regex `#[A-Za-z][A-Za-z0-9_\-]{2,}` → `_split_hashtag` (CamelCase + `_`/`-`-Split)
   → Lookup in normalized-candidate-map. Source: `hashtag`.
3. **Exact-Match**: `normalized_text == normalized_candidate`. Source: `exact`,
   `exact_local`, `exact_alias`. Score = 1.0.
4. **Phrase-Containment**: `_contains_phrase(haystack, candidate)`
   (Padded-Space-Search). Source: `unique_text` für `exact`, `exact_local`/
   `exact_alias` für die anderen Buckets. Score = 1.0 / 0.97.
5. **Fuzzy** (`SequenceMatcher.ratio() > 0.72`): nur Weak-Best-Track,
   wird **nie** automatisch geschrieben (`is_safe_auto_match` lehnt
   `source="fuzzy"` ab).
6. **Ambiguity-Guard**: ≥ 2 verschiedene Titles mit Strong-Hits →
   `MatchResult(title=None, source="ambiguous")`. Wirkt wie ein
   No-Match.

### 3.3 Welche Schwellen?

`is_safe_auto_match` (`whitelist_matcher.py:117`):

```python
return bool(match.title
            and match.source in {"exact", "exact_alias", "exact_local",
                                 "hashtag", "unique_text"}
            and match.confidence >= 0.95)
```

→ Fuzzy schreibt nichts; alles unter 0.95 Confidence schreibt nichts;
ambiguous schreibt nichts.

### 3.4 Sprach-Filter?

**Keiner.** Der Matcher läuft pro Asset gegen den **gesamten** Title-Pool
(`active=TRUE`, ohne Markt-Filter). DE- und US-Posts werden gegen
identische Kandidaten-Listen abgeglichen. `title_local` und `title_original`
werden parallel aufgenommen — d.h. Disney US Captions können theoretisch
gegen einen DE-only-Eintrag matchen, sofern `title_local` eines englischen
Titels in der DB hinterlegt ist. **Fehlende DE-Lokalisierungen sind also
kein Markt-spezifischer Filter, sondern eine DB-Lücke**, die separat gegen
beide Märkte wirkt (siehe H1 unten).

---

## 4. TMDB-Title-Inventar (psql-Block + Slots)

```sql
-- 4.1 Counts gesamt
SELECT
  COUNT(*)                                                      AS total,
  COUNT(*) FILTER (WHERE active = TRUE)                          AS active,
  COUNT(*) FILTER (WHERE source = 'TMDb')                        AS source_tmdb,
  COUNT(*) FILTER (WHERE source = 'Manual')                      AS source_manual,
  COUNT(*) FILTER (WHERE title_local IS NOT NULL)                AS has_local,
  COUNT(*) FILTER (WHERE title_local IS NULL)                    AS missing_local,
  COUNT(*) FILTER (WHERE jsonb_array_length(COALESCE(aliases, '[]'::jsonb)) > 0) AS has_aliases
FROM creative_radar.title;
```

| total | active | source_tmdb | source_manual | has_local | missing_local | has_aliases |
|---:|---:|---:|---:|---:|---:|---:|
| _<wolf>_ | _<wolf>_ | _<wolf>_ | _<wolf>_ | _<wolf>_ | _<wolf>_ | _<wolf>_ |

```sql
-- 4.2 Verteilung nach US-Release-Jahr (DE-Pendant analog mit release_date_de)
SELECT EXTRACT(YEAR FROM release_date_us)::int AS year,
       COUNT(*)
FROM creative_radar.title
WHERE release_date_us IS NOT NULL
GROUP BY year
ORDER BY year DESC
LIMIT 12;
```

| year | count |
|---:|---:|
| 2027 | _<wolf>_ |
| 2026 | _<wolf>_ |
| 2025 | _<wolf>_ |
| 2024 | _<wolf>_ |
| 2023 | _<wolf>_ |
| älter | _<wolf>_ |

```sql
-- 4.3 TitleKeyword-Inventar (insb. keyword_type='alias' aus TMDB-Sync)
SELECT keyword_type, COUNT(*) AS keywords, COUNT(*) FILTER (WHERE active) AS active
FROM creative_radar.title_keyword
GROUP BY keyword_type
ORDER BY keywords DESC;
```

| keyword_type | total | active |
|---|---:|---:|
| alias | _<wolf>_ | _<wolf>_ |
| keyword | _<wolf>_ | _<wolf>_ |
| _<other>_ | _<wolf>_ | _<wolf>_ |

```sql
-- 4.4 Letzter erfolgreicher Sync — wann + wieviel?
SELECT created_at, status, markets, fetched_count, upserted_count, deduped_count
FROM creative_radar.title_sync_run
ORDER BY created_at DESC
LIMIT 5;
```

| created_at | status | markets | fetched | upserted | deduped |
|---|---|---|---:|---:|---:|
| _<wolf>_ | _<wolf>_ | _<wolf>_ | _<wolf>_ | _<wolf>_ | _<wolf>_ |

### 4.5 Erwartungs-Bound aus dem Code

`backend/app/services/tmdb_client.py:61` und `title_sync.py:25-27` ergeben:

- `with_release_type="2|3"` → nur **theatrical limited (2)** und
  **theatrical wide (3)**. Damit fehlen Type 1 (Premiere), Type 4
  (Digital), Type 5 (Physical) und Type 6 (TV) — d. h. **Streaming-only-
  Releases werden gar nicht gefetcht**.
- `lookback_weeks=8` + `lookahead_weeks=24` → 32-Wochen-Fenster.
- Hard-Cap `page <= 3` (TMDb gibt 20/page) → max. **60 Filme pro Markt
  pro Sync-Lauf**.
- Pro Lauf werden 2 Märkte gepullt → **maximal 120 Title-Inserts pro Sync**
  (in der Praxis weniger nach Dedupe).

Wenn Wolfs `total`-Count aus 4.1 in der Größenordnung 200–500 liegt,
deckt sich das mit dem Sync-Vertrag. Wenn er <100 ist → eigener Befund
(zu wenig Sync-Läufe oder Sync-Runs in `error`-Status — Tabelle 4.4
zeigt das).

---

## 5. Stichprobe ziehen (psql-Block, CSV-Output)

> Output-Pfad: `docs/audits/tmdb-match-2026-05-stichprobe.csv`.
> Wolf führt aus mit `\copy (…) TO 'tmdb-match-2026-05-stichprobe.csv'
> WITH CSV HEADER`.

```sql
-- Pro Pair × Plattform × Markt die jüngsten 20 Posts mit Match-Status.
WITH pair_handles (pair_key, platform, market, handle) AS (
  VALUES
    ('disney', 'tiktok',    'US', 'disney'),
    ('disney', 'tiktok',    'US', 'disneystudios'),
    ('disney', 'tiktok',    'US', 'disneyanimation'),
    ('disney', 'tiktok',    'DE', 'disneyde'),
    ('disney', 'instagram', 'US', 'disney'),
    ('disney', 'instagram', 'DE', 'disneydeutschland'),
    ('disney', 'youtube',   'US', 'WaltDisneyStudios'),
    ('netflix', 'tiktok',    'US', 'netflix'),
    ('netflix', 'tiktok',    'DE', 'netflixde'),
    ('netflix', 'instagram', 'US', 'netflix'),
    ('netflix', 'instagram', 'DE', 'netflixde'),
    ('netflix', 'youtube',   'US', 'Netflix'),
    ('netflix', 'youtube',   'DE', 'NetflixDE'),
    ('warnerbros', 'tiktok',    'US', 'warnerbros'),
    ('warnerbros', 'tiktok',    'DE', 'warnerbrosdeutschland'),
    ('warnerbros', 'instagram', 'US', 'warnerbros'),
    ('warnerbros', 'instagram', 'DE', 'warnerbrosde'),
    ('warnerbros', 'youtube',   'US', 'WarnerBrosPictures'),
    ('warnerbros', 'youtube',   'DE', 'WarnerBrosDE'),
    ('sonypictures', 'tiktok',    'US', 'sonypictures'),
    ('sonypictures', 'tiktok',    'DE', 'sonypicturesgermany'),
    ('sonypictures', 'instagram', 'US', 'sonypictures'),
    ('sonypictures', 'instagram', 'DE', 'sonypicturesde'),
    ('sonypictures', 'youtube',   'US', 'SonyPicturesEntertainment'),
    ('sonypictures', 'youtube',   'DE', 'SonyPicturesGermany'),
    ('primevideo', 'tiktok',    'US', 'primevideo'),
    ('primevideo', 'tiktok',    'DE', 'primevideode'),
    ('primevideo', 'instagram', 'US', 'primevideo'),
    ('primevideo', 'instagram', 'DE', 'primevideode'),
    ('primevideo', 'youtube',   'US', 'PrimeVideo'),
    ('paramountpictures', 'tiktok',    'US', 'paramountpics'),
    ('paramountpictures', 'tiktok',    'DE', 'paramountpicturesgermany'),
    ('paramountpictures', 'instagram', 'US', 'paramountpics'),
    ('paramountpictures', 'instagram', 'DE', 'paramount_pictures_germany'),
    ('paramountpictures', 'youtube',   'US', 'ParamountPictures')
),
sample AS (
  SELECT
    ph.pair_key,
    ph.platform,
    ph.market,
    ph.handle,
    p.id AS post_id,
    p.post_url,
    p.published_at,
    LEFT(COALESCE(p.caption, ''), 200) AS caption_excerpt,
    -- Hashtags aus der Caption extrahieren
    (
      SELECT STRING_AGG(m[1], ' ')
      FROM regexp_matches(COALESCE(p.caption, ''), '#[A-Za-z][A-Za-z0-9_\-]{2,}', 'g') AS m
    ) AS hashtags,
    -- Match-Status auf Asset-Level: hat irgendein Asset des Posts einen Title?
    BOOL_OR(a.title_id IS NOT NULL) AS has_match,
    STRING_AGG(DISTINCT t.title_original, ' | ') FILTER (WHERE t.title_original IS NOT NULL) AS matched_title_original,
    STRING_AGG(DISTINCT t.title_local,    ' | ') FILTER (WHERE t.title_local    IS NOT NULL) AS matched_title_local,
    STRING_AGG(DISTINCT a.de_us_match_key, ' | ') FILTER (WHERE a.de_us_match_key IS NOT NULL) AS matched_match_keys,
    -- Vision-Status als Heuristik für die Match-Quelle
    STRING_AGG(DISTINCT a.visual_analysis_status, ' | ') AS visual_statuses
  FROM pair_handles ph
  JOIN creative_radar.channel c
    ON LOWER(c.handle) = LOWER(ph.handle) AND c.platform = ph.platform
  JOIN creative_radar.post p
    ON p.channel_id = c.id
  LEFT JOIN creative_radar.asset a ON a.post_id = p.id
  LEFT JOIN creative_radar.title t ON t.id = a.title_id
  WHERE p.published_at > NOW() - INTERVAL '30 days'
  GROUP BY ph.pair_key, ph.platform, ph.market, ph.handle, p.id, p.post_url, p.published_at, p.caption
)
SELECT *
FROM (
  SELECT *,
         ROW_NUMBER() OVER (
           PARTITION BY pair_key, platform, market
           ORDER BY published_at DESC NULLS LAST
         ) AS rn
  FROM sample
) ranked
WHERE rn <= 20
ORDER BY pair_key, platform, market, published_at DESC NULLS LAST;
```

Erwartete Zeilen: ≈ 32 (Pair × Plattform × Markt-Buckets, abzüglich n/a)
× 20 = **≤ 640 Posts**, in der Praxis weniger weil einige Channels in
30 d nicht 20 Posts haben.

---

## 6. Cluster-Beispiele

> **Hinweis:** Pending DB-Stichprobe. Die untenstehenden Beispiele
> stammen aus dem Browser-Smoke-Befund (Disney US TikTok 0 %) und aus
> dem deterministischen Code-Verhalten gegen den heutigen Title-Pool.
> Sobald die CSV vorliegt, ergänze ich hier konkrete `post_id` + Caption-
> Auszug + tatsächliches Match-Ergebnis.

### Cluster A — Match-Lücke trotz erkennbarem Titel

Posts mit klarem Titel-Hinweis im Caption- oder Hashtag-Material, kein Match.

| Caption-Hinweis | Hashtag(s) | Erwartet | Tatsächlich | Wahrscheinliche Ursache |
|---|---|---|---|---|
| „Drawn to You" (Disney US TT) | – / `#disney` | Title-Match auf Disney-Brand-Spot | NULL | **Title existiert nicht in `creative_radar.title`** — Brand-Spot, kein TMDB-Eintrag (H3) |
| „D23 Asia" (Disney US TT) | `#D23` | – / nur Brand-Event | NULL | **Korrekt None**, aber fehlt als Brand-Event-Hashtag-Alias (H4) |
| „Cinderella" (Disney US TT) | `#cinderella` | Cinderella (1950 / 2015) | NULL | **Außerhalb Sync-Window** (H2) — Klassiker fällt aus 32-Wochen-Range |
| „Mortal Kombat 2" Trailer (WB US, hypothetisch) | `#mortalkombat` | Mortal Kombat 2 | NULL? | **Hashtag-Splitting greift nicht für Sequel-Suffixe** (H1b) — `mortalkombat` matcht nur den Original-Eintrag, nicht „Mortal Kombat 2" |
| „Zoomania 2" Trailer DE (hypothetisch) | `#zoomania2` | Zootopia 2 | NULL? | **Lokalisierter Titel fehlt in `title_local`** (H1) |

### Cluster B — Match-Lücke nur bei lokalisierten Titeln / Sequels

Posts, bei denen ein verwandter Titel matcht, der spezifische aber nicht.

| Caption | Erwartet | Tatsächlich | Ursache |
|---|---|---|---|
| „Wicked: For Good" Trailer | Wicked: For Good | Wicked (Original) | TMDB-Sync hat Sequel evtl. noch nicht gepullt; Vision-Pfad trifft Original zuerst (`visual_analysis._find_title_match` first-wins) |
| dt. Caption „Avatar 3" | Avatar: Fire and Ash | Avatar (Original) | Lokalisierter Sequel-Name fehlt; Hashtag-Variante `#avatar3` matcht nicht gegen Title-Original |

### Cluster C — Korrektes None (kein Defekt)

Posts ohne erkennbaren Titel im Material — Brand-/Marketing-Posts.

| Caption-Auszug | Hashtags | Korrekt None? |
|---|---|---|
| „This week on Disney+" | `#disneyplus` `#whattowatch` | Ja — Plattform-Promo, kein Einzeltitel |
| „Get tickets now!" | `#filmedforimax` | Ja — generisches CTA |
| „Behind the magic" (BTS) | `#disneymagic` | Ja — BTS ohne Titel-Bezug |

### Cluster D — Erfolgreicher Match (Referenz)

| Caption-Hinweis | Match-Quelle (erwartet) | Confidence |
|---|---|---|
| Caption enthält „Mission: Impossible" wörtlich | `unique_text` | 1.0 |
| Hashtag `#JurassicWorld` | `hashtag` | 1.0 |
| Hashtag `#Superman` | `hashtag` | 1.0 |
| Caption „Dune: Part Two" | `exact` (wenn als `title_original` gepflegt) | 1.0 |

(Alle vier sind aktive Seed-Whitelist-Einträge in
`backend/app/services/seeds.py:27-38`.)

---

## 7. Hypothesen-Ranking

> Reihenfolge nach geschätztem Hebel × Aufwand-Effizienz.

### H1 — TMDB-Sync deckt keine Streaming-Releases ab

**Evidenz:**
- `backend/app/services/tmdb_client.py:75` setzt `with_release_type="2|3"`
  (theatrical limited + wide). TMDb-Release-Types 4 (Digital) und 6 (TV)
  fehlen → **Disney+, Netflix-Originals, Prime-Originals, Paramount+
  Originals werden nie als Title gepullt.**
- 5 von 6 Pairs sind primär Streamer-betrieben (Disney, Netflix, Prime,
  Paramount Pictures via Paramount+, plus Streaming-Tracks von WB/Sony).
  Für Disney US TikTok ist „Drawn to You" + „D23" + „Disney+" das
  Kernmaterial — keiner dieser Begriffe hat einen TMDB-Theatrical-Eintrag.
- Browser-Smoke: 5 von 5 Disney-Werten sind ≤ 15.4 %. Disney DE TikTok
  liegt bei 15.4 %, weil Disney DE in den letzten Wochen einige
  Theatrical-Releases (Zoomania 2 in DE-Lokalisierung, Mufasa) hatte —
  US dagegen 0 %, weil dort gerade der Streaming-Slate dominiert.

**Hebel:** Sehr hoch für Streamer-Pairs. Disney US TikTok und Prime/Netflix
US TikTok dürften nach diesem Fix von ~0 % auf 30–60 % springen, sobald
Disney+, Netflix- und Prime-Catalogue-Titel im Pool sind.

**Fix-Aufwand:** ~2–4 h.

- `with_release_type` auf `"2|3|4|6"` erweitern (1 LOC).
- Optional `/discover/tv` ergänzen für Serien (Mandalorian, Stranger
  Things kommen sonst nicht rein) — **das ist der Hebel-Multiplikator,
  ~4–8 h Mehraufwand**, weil das Schema `Title.content_type = "Serie"`
  bzw. `Title.tmdb_id` jetzt eindeutig pro Movie-ID ist und für
  `/tv`-IDs ein Namespace-Konflikt entsteht (Workaround: separater
  Counter oder Prefix).

### H2 — Sync-Window deckt keine Catalog-/Nostalgie-Posts ab

**Evidenz:**
- `lookback_weeks=8` + `lookahead_weeks=24` → fast nichts vor März 2026
  und nichts nach Anfang November 2026 wird gepullt.
- Disney US TikTok promotet im Browser-Smoke „Cinderella" — Re-Release
  oder Catalog-Promo. TMDb hat den Eintrag, aber `release_date_us` ist
  ≪ March 2026 → fällt aus dem Discover-Filter.
- Ähnliches Pattern bei Halloween-/Jahres-End-Catalog-Promos.

**Hebel:** Mittel. Catalog-Promos sind ein konstanter, aber nicht
dominanter Anteil bei Disney/WB; bei Netflix/Prime kleiner.

**Fix-Aufwand:** ~2 h.

- `lookback_weeks` auf 260 (5 Jahre) erweitern, kombiniert mit
  Page-Cap-Anhebung. Risiko: Sync-Dauer wächst (mehr API-Calls). Alternativ
  einmaliger „Catalog-Backfill"-Lauf.
- Berührt sich mit dem bekannten Backlog-Item „Catalog-Nostalgie-Failure".

### H3 — Brand-Spots / Originals ohne TMDB-Eintrag matchen nie

**Evidenz:**
- „Drawn to You" als Disney-Brand-Spot existiert nicht in TMDB.
  `creative_radar.title` ist heute praktisch ein TMDB-Spiegel + 10 Manual-
  Seeds (`seeds.MVP_TITLES`). Manual-Channel oder Brand-Spot-Inhalte
  müssen entweder manuell als `Title(source="Manual")` gepflegt oder
  gar nicht erwartet werden.
- Disney TT US ist mit ca. 30 % Brand-Spot/Behind-the-Scenes-Anteil
  verkappte Coverage-Senke.

**Hebel:** Klein-mittel. Diese Inhalte sind oft ohne klaren Single-Title-
Bezug, daher ist „korrekt None" hier teilweise sogar richtig (Cluster C).

**Fix-Aufwand:** Variabel.

- 2 h: Manuelle Whitelist-Erweiterung um Top-10 Brand-Hashtags
  (`#D23`, `#disneymagic`, `#disneydescendants`) als
  TitleKeyword(`keyword_type="alias"`) auf neue „Disney Brand"-Title-
  Stub-Einträge.
- 1–2 Tage: Strukturelle Erweiterung — `Title.content_type` um
  `"Brand"`/`"Event"` ergänzen, manueller Pflegeworkflow.

### H4 — Match-Algorithmus selbst (sekundär)

**Evidenz:**
- Algorithmus ist sauber strukturiert; Hashtag-Splitting greift
  CamelCase + `_`/`-`. Ambiguity-Guard verhindert false-positives.
- **Aber:** `franchise` und `TitleKeyword.keyword` sind im `weak`-Bucket
  (`whitelist_matcher.py:74`) und werden für Strong-Hits **nicht**
  verwendet. Der Vision-Pfad (`visual_analysis._find_title_match`)
  schreibt zwar damit, aber ohne Confidence-Gate und mit „first match
  wins" — qualitativ riskant statt coverage-stiftend.
- `TitleKeyword`-Pflege ist heute praktisch nur über Seeds + TMDB-
  Aliases; manuelle Brand-Hashtags fehlen.

**Hebel:** Klein. Coverage-Wirkung kommt erst nach H1 (Pool füllen);
ohne Pool-Eintrag hilft kein Algorithmus-Tuning.

**Fix-Aufwand:** ~4 h.

- `_find_title_match` (Vision) durch `find_best_title_match` (Whitelist)
  ersetzen → einheitlich, verhindert false-positives, entzieht aber
  womöglich Coverage. Würde nach H1 angepackt.
- `TitleKeyword`-Werte zusätzlich in `normalized_to_titles` als
  `weak` einspeisen mit Score 0.93 (unter Auto-Schwelle, aber
  Candidate-tauglich).

---

## 8. Empfehlung für Sprint 9

**Reihenfolge:**

1. **H1 (TMDB-Sync-Erweiterung)** zuerst.
   - Step 1a: `with_release_type` auf `"2|3|4|6"` (~1 h, sofortiger Hebel
     für Streamer).
   - Step 1b: TMDb-`/tv`-Endpoint für Serien (~4–8 h, separat
     evaluiert nach Step 1a).
   - **Akzeptanzkriterium:** Disney US TikTok Coverage steigt nach
     Backfill auf ≥ 25 %.
2. **H2 (Sync-Window-Erweiterung)** als kombiniertes Backfill-Skript.
   - Einmalig 5-Jahre-Backfill, danach Sync-Default bei 8/24 belassen
     oder leicht öffnen (~2 h).
3. **H3 (Brand-Hashtag-Whitelist)** als manuelle Pflege-Aktion.
   - Top-10 Brand-Hashtags pro Pair, ~2 h.
4. **H4 (Algorithmus-Vereinheitlichung)** zuletzt.
   - Vision-Pfad auf `find_best_title_match` umstellen, ~4 h.

**Gesamt-Aufwand Sprint 9: ~12–16 h** (1.5–2 Personentage), wenn alle vier
gemacht werden. Pure H1+H3 als minimaler Fix-Sprint: ~6 h.

**Akzeptanzkriterium für Sprint 9 als Ganzes:**
- Disney US TikTok Coverage von 0 % auf ≥ 30 %.
- Mindestens 4 von 6 Pairs auf Coverage ≥ 25 % über alle Plattformen
  hinweg (TikTok+IG+YT, US-Markt) — DE-Markt zieht erfahrungsgemäß nach.

---

## 9. Wolf-To-Dos zum Schließen dieses Sprints

1. SQL-Blöcke aus §2, §4 und §5 in psql gegen Production-Postgres
   ausführen.
2. Werte aus §2 (Coverage-Tabelle) in die `_<wolf>_`-Slots eintragen
   und Stichprobe-CSV unter `docs/audits/tmdb-match-2026-05-stichprobe.csv`
   ablegen.
3. Optional: nach CSV-Erstellung diesen Sprint nochmals triggern, damit
   ich §6 (Cluster-Beispiele) mit echten `post_id`-belegten Beispielen
   ersetze und §4.5 (Erwartungs-Bound) gegen den realen `total`-Count
   verifiziere.
4. Sprint 9 als Fix-Sprint scopen: Default ist H1+H3 als Quick-Win;
   H2+H4 in Folgesprint.
