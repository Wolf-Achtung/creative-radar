-- ============================================================
-- UK-Channel-Integration — Phase 2 UK-Inventur (Wolf-Ping 2)
--
-- Briefing v2 vom 26.05.2026 (PR #175 ist Phase 1, merged). Diese
-- Query wird gefahren NACHDEM die neuen UK-Channels einmal manuell
-- ueber die Monitor-Endpoints gescraped wurden (IG mit
-- ``only_whitelist_matches=false``, TT analog, YT per-channel sync).
-- Liefert Wolf den Befund fuer Wolf-Ping 2: Reicht das UK-Inventar,
-- um ``uk_major``/``uk_independent`` im Roundup-Roll-out zu
-- aktivieren?
--
-- Ausfuehrung:
--   source ~/.creative-radar/db.env && psql "$CR_DB_URL" -f scripts/inventory_uk_segments.sql > /tmp/uk_inventory.txt
--
-- Fenster-Definition matched ``segment_roundup._channel_roundup_stats``
-- exakt: 14 Tage zurueck ab now(), Post-Match per OR-Branch
--   (published_at IS NOT NULL UND published_at im Fenster)
--   ODER (published_at IS NULL UND detected_at im Fenster)
-- — also kein Posts-Drift gegen die spaetere Roundup-Generierung.
--
-- Vergleichswerte: DE/US-Segmente werden als Referenz mitgemessen,
-- damit Wolf den UK-Befund einordnen kann ("datenreich" ist heute
-- relativ — die vier produktiven Segmente liefern N Posts).
-- ============================================================

\set ON_ERROR_STOP on
SET search_path TO creative_radar, public;

\echo ''
\echo '=========================================================='
\echo '  UK-Inventur fuer Wolf-Ping 2 (Roll-out-Entscheidung)'
\echo '=========================================================='
\echo 'Fenster: letzte 14 Tage. Referenz: bisher aktive Segmente'
\echo '(us_major/us_independent/de_verleih/de_independent).'
\echo ''

\echo '--- Block A — Posts pro Segment im 14-Tage-Fenster ---'

WITH win AS (
  SELECT NOW() - INTERVAL '14 days' AS w_start, NOW() AS w_end
),
posts_in_window AS (
  SELECT
    ch.segment,
    ch.platform,
    ch.id AS channel_id,
    ch.handle,
    p.id  AS post_id
  FROM channel ch
  LEFT JOIN post p
    ON p.channel_id = ch.id
   AND (
        (p.published_at IS NOT NULL
         AND p.published_at >= (SELECT w_start FROM win)
         AND p.published_at <= (SELECT w_end   FROM win))
     OR (p.published_at IS NULL
         AND p.detected_at >= (SELECT w_start FROM win)
         AND p.detected_at <= (SELECT w_end   FROM win))
   )
  WHERE ch.active = true
    AND ch.segment IS NOT NULL
)
SELECT
  segment,
  COUNT(DISTINCT channel_id)                                    AS channels_total,
  COUNT(DISTINCT channel_id) FILTER (WHERE post_id IS NOT NULL) AS channels_with_posts,
  COUNT(post_id)                                                AS posts_total
FROM posts_in_window
GROUP BY segment
ORDER BY
  CASE segment
    WHEN 'uk_major'        THEN 1
    WHEN 'uk_independent'  THEN 2
    WHEN 'us_major'        THEN 3
    WHEN 'us_independent'  THEN 4
    WHEN 'de_verleih'      THEN 5
    WHEN 'de_independent'  THEN 6
    ELSE 99
  END;

\echo ''
\echo '--- Block B — UK-Channels einzeln (mit/ohne Posts) ---'
\echo 'Zeigt pro UK-Channel, wie viele Posts in den 14d gefallen sind.'
\echo 'posts_14d=0-Faelle sind die spaeter zu pruefenden.'
\echo ''

WITH win AS (
  SELECT NOW() - INTERVAL '14 days' AS w_start, NOW() AS w_end
)
SELECT
  ch.segment,
  ch.platform,
  ch.handle,
  ch.name,
  COALESCE(stats.posts_14d, 0) AS posts_14d,
  ch.mvp,
  LEFT(COALESCE(ch.notes, ''), 50) AS notes_preview
FROM channel ch
LEFT JOIN (
  SELECT
    p.channel_id,
    COUNT(*) AS posts_14d
  FROM post p, win
  WHERE (
        (p.published_at IS NOT NULL
         AND p.published_at >= win.w_start
         AND p.published_at <= win.w_end)
     OR (p.published_at IS NULL
         AND p.detected_at >= win.w_start
         AND p.detected_at <= win.w_end)
   )
  GROUP BY p.channel_id
) AS stats ON stats.channel_id = ch.id
WHERE ch.active = true
  AND ch.segment IN ('uk_major', 'uk_independent')
ORDER BY ch.segment, COALESCE(stats.posts_14d, 0) DESC, ch.handle, ch.platform;

\echo ''
\echo '--- Block C — Apify-Scrape-Stand pro UK-Channel ---'
\echo 'Hilft bei der Diagnose, falls Posts ausbleiben: wann hat der'
\echo 'letzte Sync einen Post fuer den Channel gesehen?'
\echo ''

SELECT
  ch.segment,
  ch.platform,
  ch.handle,
  COUNT(p.id)            AS posts_total_lifetime,
  MAX(p.detected_at)     AS last_post_detected_at,
  MAX(p.published_at)    AS last_post_published_at,
  ch.created_at          AS channel_created_at
FROM channel ch
LEFT JOIN post p ON p.channel_id = ch.id
WHERE ch.active = true
  AND ch.segment IN ('uk_major', 'uk_independent')
GROUP BY ch.id, ch.segment, ch.platform, ch.handle, ch.created_at
ORDER BY ch.segment, ch.platform, ch.handle;

\echo ''
\echo '=========================================================='
\echo '  Hinweis Roll-out-Entscheidung (Wolf-Ping 2)'
\echo '=========================================================='
\echo ''
\echo 'Aktivieren wenn UK-Inventar vergleichbar mit den vier'
\echo 'produktiven Segmenten (Block A). Zwei ENV-Stellen + ein'
\echo 'Frontend-Listen-Eintrag, kein Schema-Change:'
\echo ''
\echo '  1. settings.cron_roundup_segments (Backend ENV)'
\echo '       us_major,us_independent,de_verleih,de_independent'
\echo '     -> us_major,us_independent,de_verleih,de_independent,uk_major,uk_independent'
\echo ''
\echo '  2. frontend/src/RoundupBlock.jsx — DISPLAY_SEGMENTS-Liste'
\echo '     ergaenzen um {key: "uk_major", label: "UK Major"},'
\echo '     {key: "uk_independent", label: "UK Independent"}'
\echo ''
\echo 'Falls UK-Inventar zu duenn: weiter ausgesetzt, Befund'
\echo 'dokumentieren, Channel-Erweiterung in einer Folge-Charge.'
\echo ''
