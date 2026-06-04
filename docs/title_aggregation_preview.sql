-- Title-centric aggregation — raw-SQL equivalent of services.title_aggregation.aggregate_title
-- Read-only. Reproduces the per-platform / per-market / channels / top-posts /
-- span breakdown for ONE title, so the output is inspectable in the Railway
-- Data view without railway-run / deploy.
--
-- Change the title name (case-insensitive) and the window interval below.
-- Kennzahl logic matches insight_engine:
--   engagement = likes + comments + shares + bookmarks
--   activation = (likes + comments + saves) / views   [YouTube: without saves]
--
-- ``c.market`` is the Market ENUM -> cast ::text for string grouping.

-- Reusable windowed working set (dedup: EXISTS, so a post with several assets
-- of the same title is counted once). Run this CTE block prepended to each
-- SELECT below, or create a temp view.
WITH t AS (
    SELECT id, title_original, content_type, franchise, tmdb_id, aliases,
           release_date_de, release_date_us
    FROM creative_radar.title
    WHERE lower(title_original) = lower('Mortal Kombat')   -- <-- title here
    LIMIT 1
),
tp AS (
    SELECT p.*,
           c.platform AS ch_platform,
           c.market::text AS market,
           c.handle AS ch_handle,
           c.name   AS ch_name
    FROM creative_radar.post p
    JOIN creative_radar.channel c ON c.id = p.channel_id
    WHERE EXISTS (
        SELECT 1 FROM creative_radar.asset a, t
        WHERE a.post_id = p.id AND a.title_id = t.id
    )
),
tpw AS (   -- windowed + computed Kennzahlen
    SELECT *,
           (COALESCE(visible_likes,0) + COALESCE(visible_comments,0)
            + COALESCE(visible_shares,0) + COALESCE(visible_bookmarks,0)) AS engagement,
           CASE
             WHEN COALESCE(visible_views,0) = 0 THEN 0.0
             WHEN ch_platform = 'youtube'
               THEN (COALESCE(visible_likes,0)+COALESCE(visible_comments,0))::float / visible_views
             ELSE (COALESCE(visible_likes,0)+COALESCE(visible_comments,0)+COALESCE(visible_bookmarks,0))::float / visible_views
           END AS activation_rate
    FROM tp
    WHERE COALESCE(published_at, detected_at) >= now() - interval '30 days'  -- <-- window here
)

-- 1) Stammdaten + windowed totals + full data span (span ignores the window,
--    so you can see whether 30 days is too short for the campaign).
SELECT
    (SELECT title_original FROM t)                          AS title,
    (SELECT content_type   FROM t)                          AS content_type,
    (SELECT franchise      FROM t)                          AS franchise,
    COUNT(*)                                                AS posts_in_window,
    COALESCE(SUM(engagement),0)                             AS engagement_in_window,
    COALESCE(SUM(visible_views),0)                          AS views_in_window,
    ROUND(AVG(activation_rate)::numeric, 4)                 AS activation_avg,
    (SELECT COUNT(*) FROM tp)                               AS posts_all_time,
    (SELECT MIN(COALESCE(published_at, detected_at)) FROM tp) AS first_post_at,
    (SELECT MAX(COALESCE(published_at, detected_at)) FROM tp) AS last_post_at
FROM tpw;

-- 2) Per platform.  (re-run with the same WITH-block prepended)
-- SELECT ch_platform AS platform, COUNT(*) AS posts,
--        SUM(engagement) AS engagement_sum, ROUND(AVG(engagement),1) AS engagement_avg,
--        SUM(visible_views) AS views_sum, ROUND(AVG(activation_rate)::numeric,4) AS activation_avg
-- FROM tpw GROUP BY ch_platform ORDER BY engagement_sum DESC;

-- 3) Per market.
-- SELECT market, COUNT(*) AS posts, SUM(engagement) AS engagement_sum,
--        SUM(visible_views) AS views_sum, ROUND(AVG(activation_rate)::numeric,4) AS activation_avg
-- FROM tpw GROUP BY market ORDER BY engagement_sum DESC;

-- 4) Channels the title ran on (cross-pair visibility = which studio accounts).
-- SELECT ch_handle, ch_name, ch_platform AS platform, market,
--        COUNT(*) AS posts, SUM(engagement) AS engagement_sum
-- FROM tpw GROUP BY ch_handle, ch_name, ch_platform, market ORDER BY engagement_sum DESC;

-- 5) Top posts overall.
-- SELECT ch_platform AS platform, market, ch_handle, post_url,
--        engagement AS engagement_sum, visible_views AS views,
--        ROUND(activation_rate::numeric,4) AS activation
-- FROM tpw ORDER BY engagement DESC LIMIT 10;

-- 6) Weekly buckets (timeline).
-- SELECT EXTRACT(ISOYEAR FROM COALESCE(published_at, detected_at)) AS iso_year,
--        EXTRACT(WEEK    FROM COALESCE(published_at, detected_at)) AS iso_week,
--        COUNT(*) AS posts, SUM(engagement) AS engagement_sum
-- FROM tpw GROUP BY 1,2 ORDER BY 1,2;
