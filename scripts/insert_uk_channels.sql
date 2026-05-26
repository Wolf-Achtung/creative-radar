-- ============================================================
-- UK-Channel-Integration — Phase 1 Eintragung
--
-- Briefing v2 vom 26.05.2026 (PR #175). Grundlage: Diagnose-Output
-- aus ``scripts/diagnose_uk_channel_collisions.sql`` + Wolfs
-- gepruefte CSV ``uk_channels_integration_v2.csv``.
--
-- Ergebnis-Form:
--   * 16 Bestands-Rows (UK-Majors die schon active+mvp+market='UK'
--     sind) bekommen nur ``segment`` gesetzt.
--   * 21 neue Rows werden angelegt (active=true, mvp=true,
--     market='UK', segment, notes-Marker).
--   * Zwei tote Bestands-Rows ``sonypictures.uk`` IG (b1770de9-...)
--     und ``paramountpicturesuk`` IG (f0d76915-...) bleiben
--     unveraendert inaktiv — die echten Accounts kommen unter
--     anderen Handles als neue Rows.
--
-- Ausfuehrung:
--   source ~/.creative-radar/db.env && psql "$CR_DB_URL" -f scripts/insert_uk_channels.sql
--
-- Idempotent:
--   * UPDATE-Block filtert ``WHERE segment IS NULL`` — Re-Run = No-Op.
--   * INSERT-Block ``ON CONFLICT (handle, platform) WHERE active = true
--     DO NOTHING`` ueber den Partial-Unique-Index
--     ``channel_handle_platform_unique`` (Migration a3e7b5c19d42).
--     Re-Run gegen schon-vorhandene aktive Row = No-Op.
--
-- Transaktion:
--   ON_ERROR_STOP + BEGIN/COMMIT umschliesst beide Bloecke. Ein
--   einzelner Fehler rollt alles zurueck, keine Teilanwendung.
--
-- Verifikation:
--   VORHER- und NACHHER-Snapshots zeigen pro betroffener Row, was
--   gesetzt wurde. Erwartung NACHHER:
--     * 16 UPDATE-Rows: ``segment`` gefuellt, alles andere identisch.
--     * 21 INSERT-Rows: existieren mit ``segment``/``mvp=t``/
--       ``active=t``/``market=UK``.
--     * 2 tote Rows: ``active=f``, ``segment`` weiterhin NULL.
--
-- Rollback (manuell, falls je gebraucht):
--   Down-Strategie spiegelt die b8f2a7c40e91-Helper-Tabelle nicht —
--   dieses Skript ist ein bewusster Sprint-Commit. Falls Rueckbau:
--     BEGIN;
--     UPDATE creative_radar.channel SET segment = NULL
--       WHERE id IN (<16 update-ids>);
--     DELETE FROM creative_radar.channel
--       WHERE notes LIKE '%[2026-05-26: UK-Channel-Integration]%';
--     COMMIT;
-- ============================================================

\set ON_ERROR_STOP on
SET search_path TO creative_radar, public;

\echo ''
\echo '=========================================================='
\echo '  UK-Channel-Integration — Phase 1 Eintragung'
\echo '=========================================================='
\echo ''
\echo '--- VORHER: 16 UPDATE-Ziel-Rows (segment soll NULL sein) ---'

SELECT
  id,
  handle,
  platform,
  market,
  segment,
  active,
  mvp
FROM channel
WHERE id IN (
  -- Universal Pictures UK
  'c8182174-342f-4949-b346-bab63456923e',
  '3eaa42a2-1613-4a83-8f6b-138da30ba30c',
  '95cb7956-962d-443d-96ff-3452339522d2',
  -- Warner Bros UK & Ireland
  '66f9a7cb-bd2e-4de9-81d6-57a053010e39',
  'f02f040f-6667-49de-b363-10f92f357ee8',
  '1b2bbf28-635e-4ade-90f7-9f6b69dcb4d6',
  -- Disney UK
  '9b2ac814-9c90-416d-bfb6-08fe3247efc8',
  'b9071297-03e2-4a4e-83e5-1f336d622e47',
  'cf0f7040-ef84-45ab-a851-181854088b6d',
  -- Lionsgate UK
  '308a1c80-863f-48f9-8c95-f272a437b5c2',
  '8e8cdbfa-186b-495e-b595-dcb3fe4d50da',
  -- Prime Video UK & Ireland
  'd63c924f-25aa-4c10-b498-63c95225f1b6',
  'b48665a2-5efc-4ad5-83f6-b89c47884707',
  -- Paramount Pictures UK
  '67ff813d-e1f2-481e-8843-11e1400fd4d3',
  '89d1e1bb-edce-4e09-89a5-78190f2523f6',
  -- Sony Pictures Releasing UK
  'e94a4f22-a067-448b-91bc-2f2fd5637fe9'
)
ORDER BY handle, platform;

\echo ''
\echo '--- VORHER: 2 tote Bestands-Rows (bleiben unveraendert) ---'

SELECT
  id, handle, platform, market, segment, active, mvp,
  LEFT(COALESCE(notes, ''), 80) AS notes_preview
FROM channel
WHERE id IN (
  'b1770de9-0000-0000-0000-000000000000',
  'f0d76915-0000-0000-0000-000000000000'
)
   OR (LOWER(handle) IN ('sonypictures.uk', 'paramountpicturesuk')
       AND platform = 'instagram'
       AND active = false)
ORDER BY handle;

\echo ''
\echo '--- VORHER: Pre-Check der 21 INSERT-Ziel-Tupel ---'
\echo 'Erwartung: keine aktive Row mit diesem (handle, platform).'

SELECT
  csv.handle, csv.platform,
  COUNT(ch.id) FILTER (WHERE ch.active)     AS active_collisions,
  COUNT(ch.id) FILTER (WHERE NOT ch.active) AS inactive_homonyms
FROM (
  VALUES
    -- uk_major
    ('paramountuk',              'instagram'),
    ('sonypicturesuk',           'instagram'),
    ('studiocanaluk',            'instagram'),
    ('studiocanaluk',            'tiktok'),
    ('disneyplusuk',             'instagram'),
    ('disneyplusuk',             'tiktok'),
    ('20thcenturyuk',            'instagram'),
    -- uk_independent
    ('signatureentertainmentuk', 'instagram'),
    ('signatureentertainment',   'tiktok'),
    ('arrowvideo',               'instagram'),
    ('arrow_video',              'tiktok'),
    ('britishfilminstitute',     'instagram'),
    ('britishfilminstitute',     'tiktok'),
    ('vertigoreleasing',         'instagram'),
    ('vertigoreleasing',         'tiktok'),
    ('modernfilmsent',           'instagram'),
    ('modernfilmsent',           'tiktok'),
    ('dogwoof',                  'instagram'),
    ('peccapics',                'instagram'),
    ('park.circus.films',        'instagram'),
    ('eurekaentertainment',      'instagram')
) AS csv(handle, platform)
LEFT JOIN channel ch
  ON LOWER(ch.handle) = LOWER(csv.handle)
 AND ch.platform      = csv.platform
GROUP BY csv.handle, csv.platform
ORDER BY active_collisions DESC, csv.handle, csv.platform;

\echo ''
\echo '=========================================================='
\echo '  Transaktion'
\echo '=========================================================='

BEGIN;

-- ----------------------------------------------------------------
-- Block UPDATE: 16 Rows. Pro Row ein gezieltes UPDATE per id mit
-- ``segment IS NULL``-Guard (idempotent). Es werden bewusst nur
-- ``segment`` und ``updated_at`` angefasst — ``active``, ``mvp``,
-- ``market``, ``url``, ``handle`` der Bestands-Rows bleiben
-- unveraendert (Wolf-Spec).
-- ----------------------------------------------------------------

-- Universal Pictures UK
UPDATE channel SET segment = CAST('uk_major' AS creative_radar.channel_segment), updated_at = NOW()
 WHERE id = 'c8182174-342f-4949-b346-bab63456923e' AND segment IS NULL;
UPDATE channel SET segment = CAST('uk_major' AS creative_radar.channel_segment), updated_at = NOW()
 WHERE id = '3eaa42a2-1613-4a83-8f6b-138da30ba30c' AND segment IS NULL;
UPDATE channel SET segment = CAST('uk_major' AS creative_radar.channel_segment), updated_at = NOW()
 WHERE id = '95cb7956-962d-443d-96ff-3452339522d2' AND segment IS NULL;

-- Warner Bros UK & Ireland
UPDATE channel SET segment = CAST('uk_major' AS creative_radar.channel_segment), updated_at = NOW()
 WHERE id = '66f9a7cb-bd2e-4de9-81d6-57a053010e39' AND segment IS NULL;
UPDATE channel SET segment = CAST('uk_major' AS creative_radar.channel_segment), updated_at = NOW()
 WHERE id = 'f02f040f-6667-49de-b363-10f92f357ee8' AND segment IS NULL;
UPDATE channel SET segment = CAST('uk_major' AS creative_radar.channel_segment), updated_at = NOW()
 WHERE id = '1b2bbf28-635e-4ade-90f7-9f6b69dcb4d6' AND segment IS NULL;

-- Disney UK
UPDATE channel SET segment = CAST('uk_major' AS creative_radar.channel_segment), updated_at = NOW()
 WHERE id = '9b2ac814-9c90-416d-bfb6-08fe3247efc8' AND segment IS NULL;
UPDATE channel SET segment = CAST('uk_major' AS creative_radar.channel_segment), updated_at = NOW()
 WHERE id = 'b9071297-03e2-4a4e-83e5-1f336d622e47' AND segment IS NULL;
UPDATE channel SET segment = CAST('uk_major' AS creative_radar.channel_segment), updated_at = NOW()
 WHERE id = 'cf0f7040-ef84-45ab-a851-181854088b6d' AND segment IS NULL;

-- Lionsgate UK
UPDATE channel SET segment = CAST('uk_major' AS creative_radar.channel_segment), updated_at = NOW()
 WHERE id = '308a1c80-863f-48f9-8c95-f272a437b5c2' AND segment IS NULL;
UPDATE channel SET segment = CAST('uk_major' AS creative_radar.channel_segment), updated_at = NOW()
 WHERE id = '8e8cdbfa-186b-495e-b595-dcb3fe4d50da' AND segment IS NULL;

-- Prime Video UK & Ireland
UPDATE channel SET segment = CAST('uk_major' AS creative_radar.channel_segment), updated_at = NOW()
 WHERE id = 'd63c924f-25aa-4c10-b498-63c95225f1b6' AND segment IS NULL;
UPDATE channel SET segment = CAST('uk_major' AS creative_radar.channel_segment), updated_at = NOW()
 WHERE id = 'b48665a2-5efc-4ad5-83f6-b89c47884707' AND segment IS NULL;

-- Paramount Pictures UK
UPDATE channel SET segment = CAST('uk_major' AS creative_radar.channel_segment), updated_at = NOW()
 WHERE id = '67ff813d-e1f2-481e-8843-11e1400fd4d3' AND segment IS NULL;
UPDATE channel SET segment = CAST('uk_major' AS creative_radar.channel_segment), updated_at = NOW()
 WHERE id = '89d1e1bb-edce-4e09-89a5-78190f2523f6' AND segment IS NULL;

-- Sony Pictures Releasing UK
UPDATE channel SET segment = CAST('uk_major' AS creative_radar.channel_segment), updated_at = NOW()
 WHERE id = 'e94a4f22-a067-448b-91bc-2f2fd5637fe9' AND segment IS NULL;

-- ----------------------------------------------------------------
-- Block INSERT: 21 neue Rows. Pre-generierte UUIDs (Inline-
-- Literal — Migration e5d8f1a36b40 dokumentiert, dass pgcrypto
-- nicht vorausgesetzt wird). ``ON CONFLICT (handle, platform) WHERE
-- active = true DO NOTHING`` ueber den Partial-Unique-Index, damit
-- ein Re-Run gegen eine schon angelegte aktive Row ein No-Op ist.
--
-- Felder explizit: id, name, platform, url, handle, market,
-- priority, active, mvp, notes, segment, quality_tier,
-- acquisition_strategy, monitoring_enabled, created_at, updated_at.
-- channel_type/channel_role/category/import_source/platform_channel_id
-- bleiben NULL — werden vom Apify-Pfad nicht gebraucht.
-- ----------------------------------------------------------------

-- ===== uk_major INSERTs (7) =====

-- Paramount Pictures UK — IG (Bestand TT+YT bekommen segment via UPDATE)
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  '31dfcad5-37dd-438b-bde5-85c161fb3397',
  'Paramount Pictures UK', 'instagram',
  'https://www.instagram.com/paramountuk', 'paramountuk',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_major' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- Sony Pictures Releasing UK — IG
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  'c0e3993b-0742-4df4-bd5b-b0ef431c28aa',
  'Sony Pictures Releasing UK', 'instagram',
  'https://www.instagram.com/sonypicturesuk', 'sonypicturesuk',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_major' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- StudioCanal UK — IG
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  '768229a5-7fc8-45a5-92a3-48d801cc51c7',
  'StudioCanal UK', 'instagram',
  'https://www.instagram.com/studiocanaluk', 'studiocanaluk',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_major' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- StudioCanal UK — TT
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  '8a376df6-6b5d-4a12-b77b-1080b44f1b37',
  'StudioCanal UK', 'tiktok',
  'https://www.tiktok.com/@studiocanaluk', 'studiocanaluk',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_major' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- Disney+ UK — IG
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  '1c1d0e6b-8e25-47eb-8aa4-9a6404aba070',
  'Disney+ UK', 'instagram',
  'https://www.instagram.com/disneyplusuk', 'disneyplusuk',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_major' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- Disney+ UK — TT
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  '17845069-0a7d-497b-9da3-6d1941c7fefc',
  'Disney+ UK', 'tiktok',
  'https://www.tiktok.com/@disneyplusuk', 'disneyplusuk',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_major' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- 20th Century Studios UK — IG (kein separater UK-TT)
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  'bbeb4265-1c3e-464e-a045-16af72f20a0e',
  '20th Century Studios UK', 'instagram',
  'https://www.instagram.com/20thcenturyuk', '20thcenturyuk',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_major' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- ===== uk_independent INSERTs (14) =====

-- Signature Entertainment — IG
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  'bb91eaed-aed4-40a2-8b8c-bd9ef81aeb23',
  'Signature Entertainment', 'instagram',
  'https://www.instagram.com/signatureentertainmentuk', 'signatureentertainmentuk',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_independent' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- Signature Entertainment — TT (Handle weicht von IG ab: kein uk-Suffix)
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  'e13716a1-d432-4813-9875-5d2e2128a222',
  'Signature Entertainment', 'tiktok',
  'https://www.tiktok.com/@signatureentertainment', 'signatureentertainment',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_independent' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- Arrow Films / Arrow Video — IG
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  '6231d7e6-5746-4f40-8304-104956688dab',
  'Arrow Films / Arrow Video', 'instagram',
  'https://www.instagram.com/arrowvideo', 'arrowvideo',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_independent' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- Arrow Films / Arrow Video — TT (Handle mit Unterstrich)
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  '4059f1dc-4bfb-4239-b846-05950ddea0fc',
  'Arrow Films / Arrow Video', 'tiktok',
  'https://www.tiktok.com/@arrow_video', 'arrow_video',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_independent' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- BFI / British Film Institute — IG
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  '585eb23c-50b3-44a0-8829-f2539f7f7bec',
  'BFI / British Film Institute', 'instagram',
  'https://www.instagram.com/britishfilminstitute', 'britishfilminstitute',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_independent' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- BFI / British Film Institute — TT
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  '0fd004f8-f451-42d7-8843-bc9eb1178c26',
  'BFI / British Film Institute', 'tiktok',
  'https://www.tiktok.com/@britishfilminstitute', 'britishfilminstitute',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_independent' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- Vertigo Releasing — IG (Browser-verifiziert)
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  '73895388-ed16-463b-99be-86b4e77a4895',
  'Vertigo Releasing', 'instagram',
  'https://www.instagram.com/vertigoreleasing', 'vertigoreleasing',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_independent' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- Vertigo Releasing — TT (Browser-verifiziert)
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  '9aa87111-a733-4f98-9dc5-97eb03162a1c',
  'Vertigo Releasing', 'tiktok',
  'https://www.tiktok.com/@vertigoreleasing', 'vertigoreleasing',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_independent' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- Modern Films — IG
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  '80489b7d-071f-4f2a-9750-469c99fedd88',
  'Modern Films', 'instagram',
  'https://www.instagram.com/modernfilmsent', 'modernfilmsent',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_independent' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- Modern Films — TT
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  'e3813eb3-ddcf-4a82-b4f0-f2ed294dec71',
  'Modern Films', 'tiktok',
  'https://www.tiktok.com/@modernfilmsent', 'modernfilmsent',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_independent' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- Dogwoof — IG (kein TT)
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  'c4a535df-cc1d-4c17-bc77-9a710965e1cc',
  'Dogwoof', 'instagram',
  'https://www.instagram.com/dogwoof', 'dogwoof',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_independent' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- Peccadillo Pictures — IG
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  '927e03d2-f3aa-4fac-aa07-d8c0f5e824b2',
  'Peccadillo Pictures', 'instagram',
  'https://www.instagram.com/peccapics', 'peccapics',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_independent' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- Park Circus — IG (Handle mit Punkten)
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  '13bda2ce-dd01-4f6e-9e10-5e6e1e1c3223',
  'Park Circus', 'instagram',
  'https://www.instagram.com/park.circus.films', 'park.circus.films',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_independent' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

-- Eureka Entertainment — IG (kein TT kein YT)
INSERT INTO channel (
  id, name, platform, url, handle, market, priority, active, mvp,
  notes, segment, quality_tier, acquisition_strategy,
  monitoring_enabled, created_at, updated_at
) VALUES (
  'e0d73fe5-9c57-47cb-8b7e-ad1e10e10164',
  'Eureka Entertainment', 'instagram',
  'https://www.instagram.com/eurekaentertainment', 'eurekaentertainment',
  CAST('UK' AS public.market), CAST('B' AS public.priority),
  true, true,
  '[2026-05-26: UK-Channel-Integration]',
  CAST('uk_independent' AS creative_radar.channel_segment),
  'P1', 'apify', true, NOW(), NOW()
) ON CONFLICT (handle, platform) WHERE active = true DO NOTHING;

COMMIT;

\echo ''
\echo '=========================================================='
\echo '  NACHHER-Snapshots zur Verifikation'
\echo '=========================================================='
\echo ''
\echo '--- NACHHER: 16 UPDATE-Rows (segment gefuellt) ---'

SELECT
  id, handle, platform, market, segment, active, mvp
FROM channel
WHERE id IN (
  'c8182174-342f-4949-b346-bab63456923e',
  '3eaa42a2-1613-4a83-8f6b-138da30ba30c',
  '95cb7956-962d-443d-96ff-3452339522d2',
  '66f9a7cb-bd2e-4de9-81d6-57a053010e39',
  'f02f040f-6667-49de-b363-10f92f357ee8',
  '1b2bbf28-635e-4ade-90f7-9f6b69dcb4d6',
  '9b2ac814-9c90-416d-bfb6-08fe3247efc8',
  'b9071297-03e2-4a4e-83e5-1f336d622e47',
  'cf0f7040-ef84-45ab-a851-181854088b6d',
  '308a1c80-863f-48f9-8c95-f272a437b5c2',
  '8e8cdbfa-186b-495e-b595-dcb3fe4d50da',
  'd63c924f-25aa-4c10-b498-63c95225f1b6',
  'b48665a2-5efc-4ad5-83f6-b89c47884707',
  '67ff813d-e1f2-481e-8843-11e1400fd4d3',
  '89d1e1bb-edce-4e09-89a5-78190f2523f6',
  'e94a4f22-a067-448b-91bc-2f2fd5637fe9'
)
ORDER BY handle, platform;

\echo ''
\echo '--- NACHHER: 21 INSERT-Rows (segment + mvp + active gesetzt) ---'

SELECT
  id, handle, platform, market, segment, active, mvp,
  LEFT(COALESCE(notes, ''), 80) AS notes_preview
FROM channel
WHERE notes LIKE '%[2026-05-26: UK-Channel-Integration]%'
ORDER BY segment, handle, platform;

\echo ''
\echo '--- NACHHER: 2 tote Rows (Erwartung: unveraendert active=f, segment=NULL) ---'

SELECT
  id, handle, platform, market, segment, active, mvp,
  LEFT(COALESCE(notes, ''), 80) AS notes_preview
FROM channel
WHERE LOWER(handle) IN ('sonypictures.uk', 'paramountpicturesuk')
  AND platform = 'instagram'
  AND active = false
ORDER BY handle;

\echo ''
\echo '--- NACHHER: Zaehlung pro Segment fuer UK-Markt ---'

SELECT
  segment,
  platform,
  COUNT(*) AS rows,
  COUNT(*) FILTER (WHERE active AND mvp) AS active_mvp,
  COUNT(*) FILTER (WHERE NOT active)     AS inactive
FROM channel
WHERE market = 'UK'
GROUP BY segment, platform
ORDER BY segment NULLS LAST, platform;

\echo ''
\echo 'Erwartung:'
\echo '  * segment=uk_major / uk_independent: Rows wie CSV.'
\echo '  * segment=NULL (UK-Markt): nur die Block-5-Rows aus dem Briefing'
\echo '    (marvel_uk, starwarsuk, disneystudiosuk, netflixuk, paramountplusuk)'
\echo '    plus film4/hanway_films/wildbunchfilmlounge mit segment != NULL,'
\echo '    sowie zwei tote sonypictures.uk/paramountpicturesuk-Rows (active=f).'
\echo ''
\echo '=========================================================='
\echo '  Phase 1 Ende. Output zurueck an Claude Code (PR #175).'
\echo '=========================================================='
