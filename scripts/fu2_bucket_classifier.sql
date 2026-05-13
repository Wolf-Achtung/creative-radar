-- ============================================================
-- FU-2 Bucket-Klassifizierung — UK-Zombie Channels
-- ------------------------------------------------------------
-- Ausführung NACH So-Cron 17.05.2026 (oder späterer Lauf):
--   source ~/.creative-radar/db.env && psql "$CR_DB_URL" -f fu2_bucket_classifier.sql
--
-- Output:
--   1. Cron-Run-Info (welcher Lauf gerade klassifiziert wird)
--   2. Klassifizierungs-Tabelle: jeder zero_yield-Channel → Bucket A/B/C
--   3. SQL-Vorlage für Bucket-A-Promotions (auskommentiert, manuell anpassen)
--
-- Bucket-Definitionen (Stand 13.05.2026):
--   A = Channel existiert nicht (Account-Page 404) → deactivate + negative-findings.md
--       Bekannt deaktiviert: paramountpicturesuk IG, sonypictures.uk IG (commit 11bb288)
--   B = Channel existiert, Apify scrapt silent 0 → bleibt aktiv (noch)
--       Bekannt: netflixuk IG/TT, warnerbrosuk IG/TT, paramountpicturesuk TT
--   C = Unbekannt → Browser-Check nötig, dann in Bucket A oder B einordnen
-- ============================================================

SET search_path TO creative_radar, public;

\echo ''
\echo '=== FU-2 Bucket-Klassifizierung ==='
\echo ''

-- ---------- Cron-Run-Info ----------
\echo '--- Klassifizierter Cron-Run ---'

SELECT
    id::text AS run_id,
    (started_at AT TIME ZONE 'Europe/Berlin')::timestamp(0) AS started_berlin,
    status,
    (summary_json->'platforms'->'instagram'->>'zero_yield_count')::int AS ig_zero,
    (summary_json->'platforms'->'tiktok'->>'zero_yield_count')::int AS tt_zero
FROM cron_run
WHERE status = 'success'
  AND started_at >= NOW() - INTERVAL '7 days'
ORDER BY started_at DESC
LIMIT 1;

\echo ''
\echo '--- Bucket-Klassifizierung ---'

-- ---------- Klassifizierung ----------
WITH latest_run AS (
    SELECT id, started_at, summary_json
    FROM cron_run
    WHERE status = 'success'
      AND started_at >= NOW() - INTERVAL '7 days'
    ORDER BY started_at DESC
    LIMIT 1
),
zero_yield_ig AS (
    SELECT
        'instagram' AS platform,
        item->>'handle'     AS handle,
        item->>'channel_id' AS channel_id,
        item->>'market'     AS market,
        item->>'url'        AS url
    FROM latest_run,
         jsonb_array_elements(
             COALESCE(summary_json->'platforms'->'instagram'->'zero_yield_channels', '[]'::jsonb)
         ) AS item
),
zero_yield_tt AS (
    SELECT
        'tiktok' AS platform,
        item->>'handle'     AS handle,
        item->>'channel_id' AS channel_id,
        item->>'market'     AS market,
        item->>'url'        AS url
    FROM latest_run,
         jsonb_array_elements(
             COALESCE(summary_json->'platforms'->'tiktok'->'zero_yield_channels', '[]'::jsonb)
         ) AS item
),
all_zero AS (
    SELECT * FROM zero_yield_ig
    UNION ALL
    SELECT * FROM zero_yield_tt
),
classified AS (
    SELECT
        handle, platform, channel_id, market, url,
        CASE
            -- Bucket A (bereits deaktiviert) — Warning falls hier auftaucht
            WHEN (handle, platform) IN (
                ('paramountpicturesuk', 'instagram'),
                ('sonypictures.uk',     'instagram')
            ) THEN 'A-DEACTIVATED-LEAK'
            -- Bucket B (Known Scrape-Fail) — bleibt aktiv
            WHEN (handle, platform) IN (
                ('netflixuk',           'instagram'),
                ('netflixuk',           'tiktok'),
                ('warnerbrosuk',        'instagram'),
                ('warnerbrosuk',        'tiktok'),
                ('paramountpicturesuk', 'tiktok')
            ) THEN 'B-KNOWN-SCRAPE-FAIL'
            -- Rest: Browser-Check
            ELSE 'C-NEEDS-BROWSER-CHECK'
        END AS bucket
    FROM all_zero
)
SELECT
    bucket,
    platform,
    handle,
    market,
    url
FROM classified
ORDER BY bucket, platform, handle;

\echo ''
\echo '--- Bucket-Counts ---'

WITH latest_run AS (
    SELECT summary_json
    FROM cron_run
    WHERE status = 'success'
      AND started_at >= NOW() - INTERVAL '7 days'
    ORDER BY started_at DESC
    LIMIT 1
),
zero_yield_ig AS (
    SELECT 'instagram' AS platform, item->>'handle' AS handle
    FROM latest_run,
         jsonb_array_elements(
             COALESCE(summary_json->'platforms'->'instagram'->'zero_yield_channels', '[]'::jsonb)
         ) AS item
),
zero_yield_tt AS (
    SELECT 'tiktok' AS platform, item->>'handle' AS handle
    FROM latest_run,
         jsonb_array_elements(
             COALESCE(summary_json->'platforms'->'tiktok'->'zero_yield_channels', '[]'::jsonb)
         ) AS item
),
all_zero AS (
    SELECT * FROM zero_yield_ig UNION ALL SELECT * FROM zero_yield_tt
),
classified AS (
    SELECT
        CASE
            WHEN (handle, platform) IN (
                ('paramountpicturesuk', 'instagram'),
                ('sonypictures.uk',     'instagram')
            ) THEN 'A-DEACTIVATED-LEAK'
            WHEN (handle, platform) IN (
                ('netflixuk',           'instagram'),
                ('netflixuk',           'tiktok'),
                ('warnerbrosuk',        'instagram'),
                ('warnerbrosuk',        'tiktok'),
                ('paramountpicturesuk', 'tiktok')
            ) THEN 'B-KNOWN-SCRAPE-FAIL'
            ELSE 'C-NEEDS-BROWSER-CHECK'
        END AS bucket
    FROM all_zero
)
SELECT bucket, COUNT(*) AS count
FROM classified
GROUP BY bucket
ORDER BY bucket;

\echo ''
\echo '============================================================'
\echo '  Nach Browser-Verifikation der C-Channels:                  '
\echo '                                                             '
\echo '  Existiert nicht       → SQL-Vorlage unten + negative-findings.md ergänzen'
\echo '  Existiert, 0-yield    → Known-Bucket-B-Liste hier in SQL erweitern'
\echo '  Existiert, wenig Posts → keep, ggf. mvp=false setzen      '
\echo '============================================================'
\echo ''

-- ============================================================
-- SQL-Vorlage für Bucket-A-Promotions (NICHT automatisch ausführen!)
-- Pro Browser-verifiziertem Bucket-C-Channel anpassen:
-- ============================================================
--
-- UPDATE creative_radar.channel
-- SET active = false,
--     mvp = false,
--     import_source = 'fu2_bucket_a_deactivated_2026_05_XX'
-- WHERE handle = 'HANDLE_HIER'
--   AND platform = 'PLATFORM_HIER'
-- RETURNING id, handle, platform, active, mvp, import_source;
--
-- Danach: docs/negative-findings.md ergänzen + commit + push.
-- ============================================================
