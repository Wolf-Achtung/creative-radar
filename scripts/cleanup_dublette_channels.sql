-- ============================================================
-- Daten-Hygiene-Sprint, Problem 3 — Studiocanal-/Sonypictures-/WOW-
-- Dubletten als Soft-Delete deaktivieren.
--
-- Ausfuehrung:
--   source ~/.creative-radar/db.env && psql "$CR_DB_URL" -f scripts/cleanup_dublette_channels.sql
--
-- Wolf-Freigabe 26.05.2026 nach psql-Diagnose ueber Query 3.1
-- (siehe PR #171-Kommentar). Drei Channel-Rows, je gleiche Plattform
-- + gleicher Markt, eindeutige veraltete Schreibweise mit
-- ``posts_14d = 0`` gegen eine lebende Row mit aktuellen Posts. Alle
-- anderen ``studiocanal``-/``sonypictures``-/``wow``-Rows sind
-- legitime Plattform-/Markt-Varianten und werden nicht angefasst.
--
-- Fix-Form:
--   1. Soft-Delete via ``active = false`` — KEIN DELETE.
--   2. Notes-Vermerk haengt einen 2026-05-26-Marker an. Reversibel.
--   3. Idempotent: zweiter Lauf laesst die Rows in Ruhe
--      (``active = false`` als WHERE-Gate; Notes-Append nur, wenn
--      der Marker noch nicht drinsteht).
--   4. Transaktion: ON_ERROR_STOP + BEGIN/COMMIT → bei einem Fehler
--      rollback komplett, keine Teilanwendung.
--
-- Rollback (manuell, falls je gebraucht):
--   BEGIN;
--   UPDATE creative_radar.channel
--      SET active = true
--    WHERE id IN (
--      'd71599a9-9b0c-4be1-9510-583bf1ab5581',  -- studiocanalde
--      'ee11e973-2c2b-412b-a281-939bfc8871a4',  -- sonypicturesde
--      '232f4e99-5f13-42ca-8dc5-1c824be94a70'   -- wowtv
--    );
--   -- Notes bleibt bewusst stehen — der Marker dokumentiert die
--   -- Historie, auch nach einem Rollback.
--   COMMIT;
-- ============================================================

\set ON_ERROR_STOP on
SET search_path TO creative_radar, public;

\echo ''
\echo '=== Daten-Hygiene Schritt 3 — Dubletten-Soft-Delete ==='
\echo ''
\echo 'VOR dem Update:'
SELECT
  id,
  handle,
  platform,
  market,
  active,
  LEFT(COALESCE(notes, ''), 80) AS notes_preview
FROM channel
WHERE id IN (
  'd71599a9-9b0c-4be1-9510-583bf1ab5581',
  'ee11e973-2c2b-412b-a281-939bfc8871a4',
  '232f4e99-5f13-42ca-8dc5-1c824be94a70'
)
ORDER BY handle;

BEGIN;

-- studiocanalde (veraltet) -> lebende Row ist studiocanal.de
UPDATE channel
   SET active = false,
       notes = CASE
                 WHEN COALESCE(notes, '') LIKE '%[2026-05-26: Daten-Hygiene Soft-Delete%'
                   THEN notes
                 ELSE TRIM(BOTH E'\n' FROM COALESCE(notes, ''))
                      || E'\n[2026-05-26: Daten-Hygiene Soft-Delete — Dublette zu @studiocanal.de]'
               END,
       updated_at = NOW()
 WHERE id = 'd71599a9-9b0c-4be1-9510-583bf1ab5581'
   AND active = true;

-- sonypicturesde (veraltet) -> lebende Row ist sonypictures.de
UPDATE channel
   SET active = false,
       notes = CASE
                 WHEN COALESCE(notes, '') LIKE '%[2026-05-26: Daten-Hygiene Soft-Delete%'
                   THEN notes
                 ELSE TRIM(BOTH E'\n' FROM COALESCE(notes, ''))
                      || E'\n[2026-05-26: Daten-Hygiene Soft-Delete — Dublette zu @sonypictures.de]'
               END,
       updated_at = NOW()
 WHERE id = 'ee11e973-2c2b-412b-a281-939bfc8871a4'
   AND active = true;

-- wowtv (veraltet) -> lebende Row ist wowtvde
UPDATE channel
   SET active = false,
       notes = CASE
                 WHEN COALESCE(notes, '') LIKE '%[2026-05-26: Daten-Hygiene Soft-Delete%'
                   THEN notes
                 ELSE TRIM(BOTH E'\n' FROM COALESCE(notes, ''))
                      || E'\n[2026-05-26: Daten-Hygiene Soft-Delete — Dublette zu @wowtvde]'
               END,
       updated_at = NOW()
 WHERE id = '232f4e99-5f13-42ca-8dc5-1c824be94a70'
   AND active = true;

COMMIT;

\echo ''
\echo 'NACH dem Update (Verifikation):'
SELECT
  id,
  handle,
  platform,
  market,
  active,
  LEFT(COALESCE(notes, ''), 160) AS notes_preview
FROM channel
WHERE id IN (
  'd71599a9-9b0c-4be1-9510-583bf1ab5581',
  'ee11e973-2c2b-412b-a281-939bfc8871a4',
  '232f4e99-5f13-42ca-8dc5-1c824be94a70'
)
ORDER BY handle;

\echo ''
\echo 'Erwartung: alle drei Rows ``active = f``, notes-Spalte traegt'
\echo 'den ``[2026-05-26: Daten-Hygiene Soft-Delete ...]``-Marker.'
\echo ''
\echo 'Lebende Pendants zur Kontrolle (sollten unangetastet sein):'
SELECT
  id,
  handle,
  platform,
  market,
  active
FROM channel
WHERE LOWER(handle) IN ('studiocanal.de', 'sonypictures.de', 'wowtvde')
ORDER BY handle, platform;
