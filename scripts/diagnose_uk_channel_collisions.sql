-- ============================================================
-- UK-Channel-Integration — Phase 0 Diagnose
--
-- Briefing: 26.05.2026. Vor jedem Eintrag der ~20 UK-Verleiher (CSV
-- ``uk_channels_integration.csv``) klaeren wir in psql, ob ein
-- (handle, platform)-Pendant schon in ``creative_radar.channel``
-- steht — als aktive Row, als Soft-Delete-Row (active=false), oder
-- als verwandte Schreibvariante.
--
-- Ausfuehrung:
--   source ~/.creative-radar/db.env && psql "$CR_DB_URL" -f scripts/diagnose_uk_channel_collisions.sql > /tmp/uk_collisions.txt
--
-- Read-only — keine UPDATEs, keine Transaktion noetig. Output an
-- Claude Code zurueckspielen (gist oder PR-Kommentar), damit der
-- Eintragungs-SQL-Generator (Phase 1) die richtigen Branches nimmt
-- (neu anlegen / reaktivieren / zusammenfuehren / handle korrigieren).
-- ============================================================

\set ON_ERROR_STOP on
SET search_path TO creative_radar, public;

\echo ''
\echo '=========================================================='
\echo '  Block 1 — Exakter (handle, platform)-Match pro CSV-Zeile'
\echo '=========================================================='
\echo 'Eine Zeile pro UK-CSV-Eintrag. ``count`` > 0 => Kollision,'
\echo 'Detail-Block 4 unten zeigt die kollidierenden Rows.'
\echo ''

WITH csv(handle, platform) AS (
  VALUES
    -- uk_major
    ('universalpicturesuk',     'instagram'),
    ('universalpicturesuk',     'tiktok'),
    ('warnerbrosuk',            'instagram'),
    ('warnerbrosuk',            'tiktok'),
    ('sonypicturesuk',          'instagram'),
    ('disneyuk',                'instagram'),
    ('disneyuk',                'tiktok'),
    ('20thcenturyuk',           'instagram'),
    ('paramountuk',             'instagram'),
    ('paramountpicturesuk',     'tiktok'),
    ('lionsgateuk',             'instagram'),
    ('studiocanaluk',           'instagram'),
    ('studiocanaluk',           'tiktok'),
    ('primevideouk',            'instagram'),
    ('primevideouk',            'tiktok'),
    ('disneyplusuk',            'instagram'),
    ('disneyplusuk',            'tiktok'),
    -- uk_independent
    ('signatureentertainmentuk','instagram'),
    ('signatureentertainment',  'tiktok'),
    ('arrowvideo',              'instagram'),
    ('arrow_video',             'tiktok'),
    ('britishfilminstitute',    'instagram'),
    ('britishfilminstitute',    'tiktok'),
    ('vertigoreleasing',        'instagram'),
    ('vertigoreleasing',        'tiktok'),
    ('modernfilmsent',          'instagram'),
    ('modernfilmsent',          'tiktok'),
    ('dogwoof',                 'instagram'),
    ('picturehouses',           'instagram'),
    ('peccapics',               'instagram'),
    ('park.circus.films',       'instagram'),
    ('eurekaentertainment',     'instagram')
)
SELECT
  csv.handle                          AS csv_handle,
  csv.platform                        AS csv_platform,
  COUNT(ch.id)                        AS hits,
  COUNT(ch.id) FILTER (WHERE ch.active)        AS hits_active,
  COUNT(ch.id) FILTER (WHERE NOT ch.active)    AS hits_inactive
FROM csv
LEFT JOIN channel ch
  ON LOWER(ch.handle) = LOWER(csv.handle)
 AND ch.platform      = csv.platform
GROUP BY csv.handle, csv.platform
ORDER BY hits DESC, csv.handle, csv.platform;

\echo ''
\echo '=========================================================='
\echo '  Block 2 — YouTube-Kanalnamen (CSV liefert keinen Handle)'
\echo '=========================================================='
\echo 'Die YT-Eintraege der CSV haben nur einen Kanalnamen, keine'
\echo 'finale URL/Handle. Diese Suche zeigt, ob fuer eine der'
\echo 'YT-Marken bereits ein Channel existiert (egal mit welchem'
\echo 'genauen handle/url). Wolf nutzt den Output, um pro Marke zu'
\echo 'entscheiden, ob die YT-Charge mitgezogen oder vertagt wird.'
\echo ''

SELECT
  id, handle, name, url, market, active,
  LEFT(COALESCE(notes, ''), 80) AS notes_preview
FROM channel
WHERE platform = 'youtube'
  AND (
       LOWER(name) ~ 'universal pictures uk'
    OR LOWER(name) ~ 'warner bros.* uk'
    OR LOWER(name) ~ 'sony pictures.*uk'
    OR LOWER(name) ~ 'disney uk'
    OR LOWER(name) ~ '20th century.*uk'
    OR LOWER(name) ~ 'paramount.*uk'
    OR LOWER(name) ~ 'lionsgate.*uk'
    OR LOWER(name) ~ 'studiocanal uk'
    OR LOWER(name) ~ 'prime video uk'
    OR LOWER(name) ~ 'disney\+ uk'
    OR LOWER(name) ~ 'signature entertainment'
    OR LOWER(name) ~ 'arrow video'
    OR LOWER(name) ~ 'bfi|british film institute'
    OR LOWER(name) ~ 'vertigo releasing'
    OR LOWER(name) ~ 'modern films'
    OR LOWER(name) ~ 'dogwoof'
    OR LOWER(name) ~ 'picturehouse'
    OR LOWER(name) ~ 'peccadillo'
    OR LOWER(name) ~ 'park circus'
    OR LOWER(name) ~ 'eureka entertainment'
  )
ORDER BY LOWER(name);

\echo ''
\echo '=========================================================='
\echo '  Block 3 — Bestand bereits auf Segment ``uk_*`` klassifiziert'
\echo '=========================================================='
\echo 'Was die Backfill-Migration ``b8f2a7c40e91`` schon als UK'
\echo 'markiert hat (Erwartung: film4 / hanway_films /'
\echo 'wildbunchfilmlounge). Wenn weitere Rows auftauchen, vor'
\echo 'der Phase-1-Eintragung Bescheid geben.'
\echo ''

SELECT
  id, handle, platform, name, market, segment, active,
  LEFT(COALESCE(notes, ''), 80) AS notes_preview
FROM channel
WHERE segment IN ('uk_major', 'uk_independent')
ORDER BY segment, platform, handle;

\echo ''
\echo '=========================================================='
\echo '  Block 4 — Bekannte Kollisions-Kandidaten (Studio-Pattern)'
\echo '=========================================================='
\echo 'Vollstaendiger Detail-Snapshot fuer die im CSV markierten'
\echo '``pruefen``-Faelle plus die in der Hygiene-Diagnose bekannten'
\echo 'studiocanal/sonypictures-Varianten. Zeigt active-Status,'
\echo 'Markt, Segment und notes — Wolf entscheidet daraus pro Row,'
\echo 'ob "neu anlegen" / "reaktivieren" / "Handle korrigieren".'
\echo ''

SELECT
  id, handle, platform, market, segment, active, mvp,
  LEFT(COALESCE(notes, ''), 120) AS notes_preview
FROM channel
WHERE LOWER(handle) ~ 'sonypictures|studiocanal|disneyuk|disneyplusuk|warnerbrosuk'
   OR LOWER(handle) ~ 'paramount.*uk|universalpictures.*uk|20thcentury.*uk'
   OR LOWER(handle) ~ 'lionsgate.*uk|primevideo.*uk'
   OR LOWER(handle) ~ 'picturehouse|vertigo|arrow.?video|signature'
   OR LOWER(handle) ~ 'britishfilm|bfi|dogwoof|modernfilms|peccadillo|park.circus|eurekaentertainment'
ORDER BY LOWER(handle), platform, active DESC;

\echo ''
\echo '=========================================================='
\echo '  Block 5 — Vorhandener ``market = UK``-Bestand insgesamt'
\echo '=========================================================='
\echo 'Damit Wolf sieht, was der Markt heute hergibt (Erwartung'
\echo 'aus Backfill: 3-4 Rows). Hilft bei der Phase-2-Entscheidung'
\echo 'ueber das UK-Inventar.'
\echo ''

SELECT
  id, handle, platform, name, segment, active, mvp,
  LEFT(COALESCE(notes, ''), 80) AS notes_preview
FROM channel
WHERE market = 'UK'
ORDER BY platform, handle;

\echo ''
\echo '=========================================================='
\echo '  Diagnose Ende — Output bitte an Claude Code zurueckspielen.'
\echo '=========================================================='
