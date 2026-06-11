-- Backfill: negative visible_likes -> NULL (Sprint negative-likes-sentinel, 2026-06-11)
--
-- Befund (Recon KW 24): Apify-Instagram liefert likesCount = -1 als
-- Sentinel fuer "Likes verborgen" (Hide-like-counts). 39 Bestands-Rows,
-- alle Instagram, alle -1. Der Ingest-Guard (_count_or_none in
-- apify_connector.py) verhindert neue Faelle; dieses Skript raeumt den
-- Bestand ab. Fix-Entscheidung Wolf: NULL (nicht 0) — NULL = "unbekannt",
-- analog zur Foto-Post-View-Behandlung; 0 waere ein behaupteter Messwert
-- und verfaelscht ER-Summen (market_timeline) und Aktivierungs-Raten.
--
-- Idempotent: zweiter Lauf findet 0 Rows und aendert nichts.
-- updated_at wird bewusst NICHT angefasst — reine Daten-Hygiene, kein
-- inhaltliches Update des Posts.
--
-- Aufruf:
--   source ~/.creative-radar/db.env && psql "$CR_DB_URL" -f scripts/backfill_negative_likes_null.sql
--
-- Erwartung: rows_before = 39 (Stand Recon 11.06.), rows_after = 0.

SET search_path TO creative_radar, public;

BEGIN;

SELECT count(*) AS rows_before_backfill
FROM post
WHERE visible_likes < 0;

UPDATE post
SET visible_likes = NULL
WHERE visible_likes < 0;

SELECT count(*) AS rows_after_backfill
FROM post
WHERE visible_likes < 0;

COMMIT;
