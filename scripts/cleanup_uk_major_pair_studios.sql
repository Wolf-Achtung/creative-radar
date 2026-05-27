-- ============================================================
-- UK-Channel-Integration — uk_major-Bereinigung (Pair-Studios raus)
--
-- Briefing Wolf 27.05.2026, Schritt 2. Vorlauf: PR #177 hat die Pair-
-- Registry fuer Sony-IG-UK und Paramount-IG-UK auf die lebenden
-- Handles (@sonypicturesuk / @paramountuk) gezogen — alle 18 PAIR-
-- Studio-Rows in ``uk_major`` haben jetzt ein ehrliches Pair-Pendant
-- und koennen ohne Verlust aus dem Roundup-Segment genommen werden.
--
-- Ziel-Form NACH dem Skript:
--   * 18 Pair-Studio-Rows (Disney, Warner, Universal, Paramount,
--     Sony, Lionsgate, Prime Video — alle Plattform-Rows): ``segment``
--     auf NULL. Pair-Briefs lesen ``segment`` nicht, die Pair-
--     Zugehoerigkeit ist unberuehrt.
--   * 6 PAIRLOS-Rows (20th Century UK, StudioCanal UK, Disney+ UK,
--     Film4): unveraendert ``segment='uk_major'``. Bleiben sichtbar
--     im uk_major-Roundup, der nach Schritt 3 (CRON_ROUNDUP_SEGMENTS-
--     ENV) live geht.
--   * Andere Felder (``active``, ``mvp``, ``market``, ``url``,
--     ``handle``, ``notes``) der bereinigten Rows: nicht angefasst.
--
-- Ausfuehrung:
--   source ~/.creative-radar/db.env && psql "$CR_DB_URL" -f scripts/cleanup_uk_major_pair_studios.sql
--
-- Idempotent:
--   * Jeder UPDATE filtert ``WHERE id = '<uuid>' AND segment = 'uk_major'``.
--     Re-Run gegen schon-bereinigte Row (segment IS NULL) = No-Op.
--   * Strict-Match auf ``segment = 'uk_major'`` schuetzt davor, dass
--     ein spaeter manuell anders gesetzter segment-Wert (z.B.
--     ``uk_independent`` durch Wolf-Edit) versehentlich genullt wird.
--
-- Transaktion:
--   ON_ERROR_STOP + BEGIN/COMMIT umschliesst alle 18 UPDATEs. Ein
--   einzelner Fehler rollt alles zurueck.
--
-- Verifikation:
--   VORHER- und NACHHER-Snapshots zeigen die 18 PAIR-Ziel-Rows + die
--   6 PAIRLOS-Sicherheits-Rows. Erwartung NACHHER:
--     * 18 PAIR-Rows: ``segment IS NULL``, alles andere identisch.
--     * 6 PAIRLOS-Rows: weiterhin ``segment='uk_major'``.
--     * Count-Block: uk_major nur noch PAIRLOS-Rows.
--
-- Rollback (manuell, falls je gebraucht):
--   BEGIN;
--   UPDATE creative_radar.channel
--      SET segment = 'uk_major', updated_at = NOW()
--    WHERE id IN (<die 18 UUIDs unten>)
--      AND segment IS NULL;
--   COMMIT;
-- ============================================================

\set ON_ERROR_STOP on
SET search_path TO creative_radar, public;

\echo ''
\echo '=========================================================='
\echo '  UK-Major-Bereinigung — Pair-Studios raus aus Roundup-Segment'
\echo '=========================================================='
\echo ''
\echo '--- VORHER: 18 PAIR-Ziel-Rows (segment soll uk_major sein) ---'

SELECT
  id, handle, platform, market, segment, active, mvp
FROM channel
WHERE id IN (
  -- Disney (3)
  '9b2ac814-9c90-416d-bfb6-08fe3247efc8',
  'b9071297-03e2-4a4e-83e5-1f336d622e47',
  'cf0f7040-ef84-45ab-a851-181854088b6d',
  -- Warner (3)
  '66f9a7cb-bd2e-4de9-81d6-57a053010e39',
  'f02f040f-6667-49de-b363-10f92f357ee8',
  '1b2bbf28-635e-4ade-90f7-9f6b69dcb4d6',
  -- Universal (3)
  'c8182174-342f-4949-b346-bab63456923e',
  '3eaa42a2-1613-4a83-8f6b-138da30ba30c',
  '95cb7956-962d-443d-96ff-3452339522d2',
  -- Paramount (3: IG-insert + TT-update + YT-update)
  '31dfcad5-37dd-438b-bde5-85c161fb3397',
  '67ff813d-e1f2-481e-8843-11e1400fd4d3',
  '89d1e1bb-edce-4e09-89a5-78190f2523f6',
  -- Sony (2: IG-insert + YT-update)
  'c0e3993b-0742-4df4-bd5b-b0ef431c28aa',
  'e94a4f22-a067-448b-91bc-2f2fd5637fe9',
  -- Lionsgate (2)
  '308a1c80-863f-48f9-8c95-f272a437b5c2',
  '8e8cdbfa-186b-495e-b595-dcb3fe4d50da',
  -- Prime Video (2)
  'd63c924f-25aa-4c10-b498-63c95225f1b6',
  'b48665a2-5efc-4ad5-83f6-b89c47884707'
)
ORDER BY handle, platform;

\echo ''
\echo '--- VORHER: 6 PAIRLOS-Rows (bleiben uk_major) ---'

SELECT
  id, handle, platform, market, segment, active, mvp
FROM channel
WHERE id IN (
  'bbeb4265-1c3e-464e-a045-16af72f20a0e',   -- 20thcenturyuk IG
  '1c1d0e6b-8e25-47eb-8aa4-9a6404aba070',   -- disneyplusuk IG
  '17845069-0a7d-497b-9da3-6d1941c7fefc',   -- disneyplusuk TT
  '768229a5-7fc8-45a5-92a3-48d801cc51c7',   -- studiocanaluk IG
  '8a376df6-6b5d-4a12-b77b-1080b44f1b37'    -- studiocanaluk TT
)
   OR (LOWER(handle) = 'film4' AND platform = 'instagram')   -- vor PR #175 schon uk_major (backfill b8f2a7c40e91)
ORDER BY handle, platform;

\echo ''
\echo '=========================================================='
\echo '  Transaktion'
\echo '=========================================================='

BEGIN;

-- ----------------------------------------------------------------
-- 18 UPDATEs — segment NULL setzen, updated_at stempeln. Strict-Match
-- auf ``segment = 'uk_major'`` schuetzt vor versehentlichem Nullen,
-- falls ein Wert manuell zwischenzeitlich auf etwas anderes gesetzt
-- wurde. Re-Run = No-Op (NULL != 'uk_major' im Predicate).
-- ----------------------------------------------------------------

-- Disney UK (3 Plattformen)
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = '9b2ac814-9c90-416d-bfb6-08fe3247efc8' AND segment = 'uk_major';
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = 'b9071297-03e2-4a4e-83e5-1f336d622e47' AND segment = 'uk_major';
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = 'cf0f7040-ef84-45ab-a851-181854088b6d' AND segment = 'uk_major';

-- Warner Bros UK (3)
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = '66f9a7cb-bd2e-4de9-81d6-57a053010e39' AND segment = 'uk_major';
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = 'f02f040f-6667-49de-b363-10f92f357ee8' AND segment = 'uk_major';
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = '1b2bbf28-635e-4ade-90f7-9f6b69dcb4d6' AND segment = 'uk_major';

-- Universal Pictures UK (3)
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = 'c8182174-342f-4949-b346-bab63456923e' AND segment = 'uk_major';
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = '3eaa42a2-1613-4a83-8f6b-138da30ba30c' AND segment = 'uk_major';
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = '95cb7956-962d-443d-96ff-3452339522d2' AND segment = 'uk_major';

-- Paramount Pictures UK (3: IG @paramountuk, TT, YT)
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = '31dfcad5-37dd-438b-bde5-85c161fb3397' AND segment = 'uk_major';
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = '67ff813d-e1f2-481e-8843-11e1400fd4d3' AND segment = 'uk_major';
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = '89d1e1bb-edce-4e09-89a5-78190f2523f6' AND segment = 'uk_major';

-- Sony Pictures UK (2: IG @sonypicturesuk, YT)
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = 'c0e3993b-0742-4df4-bd5b-b0ef431c28aa' AND segment = 'uk_major';
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = 'e94a4f22-a067-448b-91bc-2f2fd5637fe9' AND segment = 'uk_major';

-- Lionsgate UK (2)
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = '308a1c80-863f-48f9-8c95-f272a437b5c2' AND segment = 'uk_major';
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = '8e8cdbfa-186b-495e-b595-dcb3fe4d50da' AND segment = 'uk_major';

-- Prime Video UK (2)
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = 'd63c924f-25aa-4c10-b498-63c95225f1b6' AND segment = 'uk_major';
UPDATE channel SET segment = NULL, updated_at = NOW()
 WHERE id = 'b48665a2-5efc-4ad5-83f6-b89c47884707' AND segment = 'uk_major';

COMMIT;

\echo ''
\echo '=========================================================='
\echo '  NACHHER-Snapshots zur Verifikation'
\echo '=========================================================='
\echo ''
\echo '--- NACHHER: 18 PAIR-Rows (segment soll NULL sein) ---'

SELECT
  id, handle, platform, market, segment, active, mvp
FROM channel
WHERE id IN (
  '9b2ac814-9c90-416d-bfb6-08fe3247efc8',
  'b9071297-03e2-4a4e-83e5-1f336d622e47',
  'cf0f7040-ef84-45ab-a851-181854088b6d',
  '66f9a7cb-bd2e-4de9-81d6-57a053010e39',
  'f02f040f-6667-49de-b363-10f92f357ee8',
  '1b2bbf28-635e-4ade-90f7-9f6b69dcb4d6',
  'c8182174-342f-4949-b346-bab63456923e',
  '3eaa42a2-1613-4a83-8f6b-138da30ba30c',
  '95cb7956-962d-443d-96ff-3452339522d2',
  '31dfcad5-37dd-438b-bde5-85c161fb3397',
  '67ff813d-e1f2-481e-8843-11e1400fd4d3',
  '89d1e1bb-edce-4e09-89a5-78190f2523f6',
  'c0e3993b-0742-4df4-bd5b-b0ef431c28aa',
  'e94a4f22-a067-448b-91bc-2f2fd5637fe9',
  '308a1c80-863f-48f9-8c95-f272a437b5c2',
  '8e8cdbfa-186b-495e-b595-dcb3fe4d50da',
  'd63c924f-25aa-4c10-b498-63c95225f1b6',
  'b48665a2-5efc-4ad5-83f6-b89c47884707'
)
ORDER BY handle, platform;

\echo ''
\echo '--- NACHHER: 6 PAIRLOS-Rows (Erwartung unveraendert uk_major) ---'

SELECT
  id, handle, platform, market, segment, active, mvp
FROM channel
WHERE id IN (
  'bbeb4265-1c3e-464e-a045-16af72f20a0e',
  '1c1d0e6b-8e25-47eb-8aa4-9a6404aba070',
  '17845069-0a7d-497b-9da3-6d1941c7fefc',
  '768229a5-7fc8-45a5-92a3-48d801cc51c7',
  '8a376df6-6b5d-4a12-b77b-1080b44f1b37'
)
   OR (LOWER(handle) = 'film4' AND platform = 'instagram')
ORDER BY handle, platform;

\echo ''
\echo '--- NACHHER: uk_major-Count pro Plattform (Erwartung nur PAIRLOS) ---'

SELECT
  platform,
  COUNT(*)                              AS rows,
  COUNT(*) FILTER (WHERE active AND mvp) AS active_mvp,
  STRING_AGG(handle, ', ' ORDER BY handle) AS handles
FROM channel
WHERE segment = 'uk_major'
GROUP BY platform
ORDER BY platform;

\echo ''
\echo 'Erwartung:'
\echo '  * instagram: 4 Rows — 20thcenturyuk, disneyplusuk, film4, studiocanaluk'
\echo '  * tiktok:    2 Rows — disneyplusuk, studiocanaluk'
\echo '  * youtube:   0 Rows (alle UK-Major-YT-Rows waren PAIR-Studios)'
\echo ''
\echo '=========================================================='
\echo '  Bereinigung Ende. Output zurueck an Claude Code.'
\echo '=========================================================='
