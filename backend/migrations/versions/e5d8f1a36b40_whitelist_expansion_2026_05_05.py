"""whitelist expansion 2026-05-05 + data hygiene

Revision ID: e5d8f1a36b40
Revises: 6f8e3c1a47d2
Create Date: 2026-05-05 14:30:00.000000

Sprint Whitelist-Update + Daten-Hygiene 2026-05-05. Three things in one
migration so they ship together (the inserts depend on the new market
ENUM value):

1. ``ALTER TYPE creative_radar.market ADD VALUE 'UK'`` — UK markets get a
   first-class enum slot rather than collapsing into INT. Run inside an
   ``autocommit_block`` because Postgres demands the new value be visible
   to the transaction that uses it.
2. Three hygiene fixes, idempotent via WHERE-guards:
   - H1: drop the younger of the two ``warnerbros`` US/TikTok rows when
     a duplicate exists (ordered by ``created_at DESC LIMIT 1``).
   - H4: backfill ``channel_type`` for four DE/Instagram rows that came
     in with the column NULL.
   - H6: drop the ``@netflix`` US/UNKNOWN YouTube row, but only when
     the canonical ``Netflix`` US|INT YouTube row exists (EXISTS-guard).
3. UPSERT-by-Python-precheck for 39 new whitelist channels. ``(handle,
   platform)`` has no UNIQUE index in production (see
   ``scripts/import_channels.py``) so we SELECT-then-INSERT row by row.
   Re-running the migration is a no-op for any row already present.

Down-migration is partial: the 39 INSERTs are reverted by handle+platform,
the H4 backfill flips the four columns back to NULL. The two DELETEs
(H1, H6) and the UK enum extension are not reverted — DELETE because the
deleted rows aren't recoverable from migration state, ENUM because
Postgres doesn't support ALTER TYPE DROP VALUE.

SQLite path is a no-op: the alembic-roundtrip test runs against an empty
in-memory DB, so the data work has nothing to operate on, matching the
precedent in 6f8e3c1a47d2.
"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5d8f1a36b40"
down_revision: Union[str, Sequence[str], None] = "6f8e3c1a47d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"
IMPORT_SOURCE = "whitelist_expansion_2026_05_05"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _url(platform: str, handle: str) -> str:
    if platform == "instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    if platform == "youtube":
        return f"https://www.youtube.com/@{handle}"
    raise ValueError(f"unknown platform: {platform}")


# 39 new whitelist channels. Order is documentation: Tier-A pairs first,
# then UK, then Discovery. The Python-precheck loop handles duplicates so
# rows already seeded (e.g. netflix US/TT, neonrated US/IG) are skipped.
NEW_CHANNELS: list[dict] = [
    # Tier-A DE+US Direct Pairs (TikTok, 7)
    {"name": "Warner Bros. Deutschland", "platform": "tiktok", "handle": "warnerbrosdeutschland", "market": "DE", "channel_type": "Studio/Verleih", "priority": "A", "channel_role": "studio_distributor", "category": None, "notes": "444K Follower; Bio 'Wir haben die Filme'; Pair zu warnerbros US/TT"},
    {"name": "Universal Pictures Deutschland", "platform": "tiktok", "handle": "universalpicturesde", "market": "DE", "channel_type": "Studio/Verleih", "priority": "A", "channel_role": "studio_distributor", "category": None, "notes": "415.9K Follower; Pair zu universalpictures US/TT"},
    {"name": "Paramount Pictures Germany", "platform": "tiktok", "handle": "paramountpicturesgermany", "market": "DE", "channel_type": "Studio/Verleih", "priority": "A", "channel_role": "studio_distributor", "category": None, "notes": "160.6K Follower; Pair zu paramountpics US/TT"},
    {"name": "Sony Pictures Germany", "platform": "tiktok", "handle": "sonypicturesgermany", "market": "DE", "channel_type": "Studio/Verleih", "priority": "A", "channel_role": "studio_distributor", "category": None, "notes": "434.9K Follower; Pair zu sonypictures US/TT"},
    {"name": "Disney Deutschland", "platform": "tiktok", "handle": "disneyde", "market": "DE", "channel_type": "Streamer", "priority": "A", "channel_role": "publisher_platform", "category": None, "notes": "507K Follower"},
    {"name": "Netflix Deutschland", "platform": "tiktok", "handle": "netflixde", "market": "DE", "channel_type": "Streamer", "priority": "A", "channel_role": "publisher_platform", "category": None, "notes": "2.5M Follower"},
    {"name": "Prime Video Deutschland", "platform": "tiktok", "handle": "primevideode", "market": "DE", "channel_type": "Streamer", "priority": "A", "channel_role": "publisher_platform", "category": None, "notes": "2.8M Follower"},
    # Tier-A DE-Solitäre (TikTok, 3)
    {"name": "Constantin Film", "platform": "tiktok", "handle": "constantinfilm", "market": "DE", "channel_type": "Verleih/Produktion", "priority": "A", "channel_role": "studio_distributor", "category": None, "notes": "729K Follower; TikTok-Spotlight-Kooperation 2025"},
    {"name": "LEONINE Studios", "platform": "tiktok", "handle": "leoninestudios", "market": "DE", "channel_type": "Studio/Verleih", "priority": "B", "channel_role": "studio_distributor", "category": None, "notes": "A24-Output-Deal-Partner DE"},
    {"name": "X Verleih", "platform": "tiktok", "handle": "xverleih", "market": "DE", "channel_type": "Verleih/Produktion", "priority": "B", "channel_role": "studio_distributor", "category": None, "notes": "Arthouse; Tom Tykwer"},
    # Tier-A US-Studios (TikTok, 7)
    {"name": "Disney Studios", "platform": "tiktok", "handle": "disneystudios", "market": "US", "channel_type": "Studio/Verleih", "priority": "A", "channel_role": "studio_distributor", "category": None, "notes": "Pair zu disneyde (TT) und disneydeutschland (IG)"},
    {"name": "Disney Animation", "platform": "tiktok", "handle": "disneyanimation", "market": "US", "channel_type": "Studio/Verleih", "priority": "A", "channel_role": "studio_distributor", "category": None, "notes": "Pair zu disneydeutschland (IG)"},
    {"name": "20th Century Studios", "platform": "tiktok", "handle": "20thcentury", "market": "US", "channel_type": "Studio/Verleih", "priority": "A", "channel_role": "studio_distributor", "category": None, "notes": "Pair zu 20thcenturystudiosde (IG)"},
    {"name": "Focus Features", "platform": "tiktok", "handle": "focusfeatures", "market": "US", "channel_type": "Studio/Verleih", "priority": "B", "channel_role": "studio_distributor", "category": None, "notes": "DE-Verleih wechselt (LEONINE)"},
    {"name": "Searchlight Pictures", "platform": "tiktok", "handle": "searchlightpics", "market": "US", "channel_type": "Studio/Verleih", "priority": "B", "channel_role": "studio_distributor", "category": None, "notes": "Pair zu disneystudiosuk (Cross)"},
    {"name": "Sony Pictures Classics", "platform": "tiktok", "handle": "sonypicturesclassics", "market": "US", "channel_type": "Studio/Verleih", "priority": "B", "channel_role": "studio_distributor", "category": None, "notes": "DE-Vertrieb wechselnd"},
    {"name": "Disney+", "platform": "tiktok", "handle": "disneyplus", "market": "US", "channel_type": "Streamer", "priority": "A", "channel_role": "publisher_platform", "category": None, "notes": "Pair zu disneyplusde (IG)"},
    # Tier-A US-Streamer + Sub-Accounts (TikTok, 3)
    {"name": "Netflix", "platform": "tiktok", "handle": "netflix", "market": "US", "channel_type": "Streamer", "priority": "A", "channel_role": "publisher_platform", "category": None, "notes": "50.6M Follower; Hauptaccount"},
    {"name": "Hulu", "platform": "tiktok", "handle": "hulu", "market": "US", "channel_type": "Streamer", "priority": "B", "channel_role": "publisher_platform", "category": None, "notes": "5.8M Follower; fließt via Hulu-Tile in disneyplusde"},
    {"name": "Netflix Geeked", "platform": "tiktok", "handle": "netflixgeeked", "market": "US", "channel_type": "Streamer", "priority": "A", "channel_role": None, "category": None, "notes": "4.5M Follower; Genre-Sub (Sci-Fi/Anime)"},
    # Tier-A US Netflix-Subs / Editorial (Instagram, 5)
    {"name": "Netflix Queue", "platform": "instagram", "handle": "netflixqueue", "market": "US", "channel_type": "Streamer", "priority": "B", "channel_role": None, "category": None, "notes": "Awards/Editorial-Sub"},
    {"name": "Netflix Geeked", "platform": "instagram", "handle": "netflixgeeked", "market": "US", "channel_type": "Streamer", "priority": "A", "channel_role": None, "category": None, "notes": "1M Follower; Anime/Sci-Fi-Sub"},
    {"name": "Strong Black Lead", "platform": "instagram", "handle": "strongblacklead", "market": "US", "channel_type": "Streamer", "priority": "C", "channel_role": None, "category": None, "notes": "2M Follower; Diversitäts-Sub (Netflix)"},
    {"name": "Netflix Tudum", "platform": "instagram", "handle": "netflixtudum", "market": "US", "channel_type": "Streamer", "priority": "C", "channel_role": None, "category": None, "notes": "445K Follower; Editorial-Sub"},
    {"name": "HBO Max Movies", "platform": "instagram", "handle": "hbomaxmovies", "market": "US", "channel_type": "Streamer", "priority": "A", "channel_role": None, "category": None, "notes": "447K Follower; Film-Vertical"},
    # Tier-A DE Streamer (Instagram, 1)
    {"name": "HBO Max DE", "platform": "instagram", "handle": "hbomaxde", "market": "DE", "channel_type": "Streamer", "priority": "A", "channel_role": "publisher_platform", "category": None, "notes": "65K Follower; Pair zu hbomax US"},
    # Tier-B UK (Instagram, 3)
    {"name": "Disney UK", "platform": "instagram", "handle": "disneyuk", "market": "UK", "channel_type": "Streamer", "priority": "A", "channel_role": "regional", "category": None, "notes": "401K Follower"},
    {"name": "Disney Studios UK", "platform": "instagram", "handle": "disneystudiosuk", "market": "UK", "channel_type": "Studio/Verleih", "priority": "A", "channel_role": "regional", "category": None, "notes": "304K Follower"},
    {"name": "Marvel UK", "platform": "instagram", "handle": "marvel_uk", "market": "UK", "channel_type": "Studio/Verleih", "priority": "A", "channel_role": "regional", "category": None, "notes": "674K Follower"},
    # Tier-B UK (TikTok, 7)
    {"name": "Paramount Pictures UK", "platform": "tiktok", "handle": "paramountpicturesuk", "market": "UK", "channel_type": "Studio/Verleih", "priority": "A", "channel_role": "regional", "category": None, "notes": "636K Follower"},
    {"name": "Paramount+ UK", "platform": "tiktok", "handle": "paramountplusuk", "market": "UK", "channel_type": "Streamer", "priority": "A", "channel_role": "regional", "category": None, "notes": "853K Follower"},
    {"name": "Prime Video UK", "platform": "tiktok", "handle": "primevideouk", "market": "UK", "channel_type": "Streamer", "priority": "A", "channel_role": "regional", "category": None, "notes": "5.2M Follower"},
    {"name": "Warner Bros. UK", "platform": "tiktok", "handle": "warnerbrosuk", "market": "UK", "channel_type": "Studio/Verleih", "priority": "A", "channel_role": "regional", "category": None, "notes": "1.8M Follower"},
    {"name": "Universal Pictures UK", "platform": "tiktok", "handle": "universalpicturesuk", "market": "UK", "channel_type": "Studio/Verleih", "priority": "A", "channel_role": "regional", "category": None, "notes": "902K Follower"},
    {"name": "Lionsgate UK", "platform": "tiktok", "handle": "lionsgateuk", "market": "UK", "channel_type": "Studio/Verleih", "priority": "B", "channel_role": "regional", "category": None, "notes": "613K Follower"},
    {"name": "Sony Pictures UK", "platform": "tiktok", "handle": "sonypictures.uk", "market": "UK", "channel_type": "Studio/Verleih", "priority": "B", "channel_role": "regional", "category": None, "notes": "1.2M Follower"},
    # Tier-C / Discovery (3)
    {"name": "NEON", "platform": "instagram", "handle": "neonrated", "market": "US", "channel_type": "Verleih/Produktion", "priority": "B", "channel_role": "studio_distributor", "category": None, "notes": "452K Follower; Indie; kein konstantes DE-Pair"},
    {"name": "Moviepilot", "platform": "tiktok", "handle": "moviepilot", "market": "DE", "channel_type": None, "priority": "B", "channel_role": None, "category": "discovery", "notes": "256K Follower; DE-Marktstandard; Discovery"},
    {"name": "IndieWire", "platform": "tiktok", "handle": "indiewire", "market": "US", "channel_type": None, "priority": "C", "channel_role": None, "category": "discovery", "notes": "US-Trade; Discovery"},
]


def upgrade() -> None:
    if not _is_postgres():
        return

    bind = op.get_bind()

    # Step 1: extend the market enum with 'UK'. ADD VALUE needs to be
    # committed before the new value can be referenced in INSERTs in the
    # same migration, so we exit the per-migration transaction here.
    with op.get_context().autocommit_block():
        op.execute(
            f"ALTER TYPE {SCHEMA}.market ADD VALUE IF NOT EXISTS 'UK'"
        )

    # Step 2a: H1 — delete the younger duplicate of warnerbros US/TikTok,
    # but only when the duplicate actually exists (>=2 rows).
    bind.execute(
        sa.text(
            f"""
            DELETE FROM {SCHEMA}.channel
            WHERE id = (
                SELECT id FROM {SCHEMA}.channel
                WHERE handle = 'warnerbros'
                  AND platform = 'tiktok'
                  AND market = 'US'
                ORDER BY created_at DESC
                LIMIT 1
            )
            AND (
                SELECT COUNT(*) FROM {SCHEMA}.channel
                WHERE handle = 'warnerbros'
                  AND platform = 'tiktok'
                  AND market = 'US'
            ) >= 2
            """
        )
    )

    # Step 2b: H4 — backfill channel_type on four DE/Instagram rows.
    # Idempotent via channel_type IS NULL guard.
    bind.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA}.channel SET channel_type = 'Streamer'
            WHERE handle IN ('paramountplusde', 'wowtvde')
              AND platform = 'instagram'
              AND channel_type IS NULL
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA}.channel SET channel_type = 'Studio/Verleih'
            WHERE handle IN ('sonypictures.de', 'studiocanal.de')
              AND platform = 'instagram'
              AND channel_type IS NULL
            """
        )
    )

    # Step 2c: H6 — drop the '@netflix' US/UNKNOWN YouTube row but only
    # when the canonical 'Netflix' row exists, so we never create a gap.
    bind.execute(
        sa.text(
            f"""
            DELETE FROM {SCHEMA}.channel
            WHERE handle = '@netflix'
              AND platform = 'youtube'
              AND EXISTS (
                  SELECT 1 FROM {SCHEMA}.channel
                  WHERE handle = 'Netflix'
                    AND platform = 'youtube'
                    AND market IN ('US', 'INT')
              )
            """
        )
    )

    # Step 3: UPSERT-by-Python-precheck of 39 channels. SELECT-then-INSERT
    # because (handle, platform) has no UNIQUE index in production.
    # UUIDs generated in Python — gen_random_uuid() depends on pgcrypto
    # being installed, which we don't assume in this schema.
    insert_sql = sa.text(
        f"""
        INSERT INTO {SCHEMA}.channel (
            id, name, platform, url, handle, market, channel_type,
            priority, active, mvp, notes, channel_role, quality_tier,
            acquisition_strategy, monitoring_enabled, category,
            import_source, created_at, updated_at
        )
        VALUES (
            CAST(:id AS uuid), :name, :platform, :url, :handle,
            CAST(:market AS {SCHEMA}.market),
            :channel_type,
            CAST(:priority AS {SCHEMA}.priority),
            true, false, :notes,
            CAST(:channel_role AS {SCHEMA}.channel_role),
            'P1', 'apify', true, :category, :import_source, NOW(), NOW()
        )
        """
    )
    select_sql = sa.text(
        f"""
        SELECT 1 FROM {SCHEMA}.channel
        WHERE handle = :handle AND platform = :platform
        LIMIT 1
        """
    )

    for ch in NEW_CHANNELS:
        existing = bind.execute(
            select_sql,
            {"handle": ch["handle"], "platform": ch["platform"]},
        ).first()
        if existing:
            continue
        bind.execute(
            insert_sql,
            {
                "id": str(uuid.uuid4()),
                "name": ch["name"],
                "platform": ch["platform"],
                "url": _url(ch["platform"], ch["handle"]),
                "handle": ch["handle"],
                "market": ch["market"],
                "channel_type": ch["channel_type"],
                "priority": ch["priority"],
                "notes": ch["notes"],
                "channel_role": ch["channel_role"],
                "category": ch["category"],
                "import_source": IMPORT_SOURCE,
            },
        )


def downgrade() -> None:
    if not _is_postgres():
        return

    bind = op.get_bind()

    # Step 3 reverse: delete the 39 inserts by (handle, platform). Scoped
    # additionally by import_source so we never touch rows that pre-dated
    # this migration with the same handle.
    delete_sql = sa.text(
        f"""
        DELETE FROM {SCHEMA}.channel
        WHERE handle = :handle
          AND platform = :platform
          AND import_source = :import_source
        """
    )
    for ch in NEW_CHANNELS:
        bind.execute(
            delete_sql,
            {
                "handle": ch["handle"],
                "platform": ch["platform"],
                "import_source": IMPORT_SOURCE,
            },
        )

    # Step 2b reverse: flip channel_type back to NULL on the four H4 rows.
    bind.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA}.channel SET channel_type = NULL
            WHERE handle IN ('paramountplusde', 'wowtvde')
              AND platform = 'instagram'
              AND channel_type = 'Streamer'
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA}.channel SET channel_type = NULL
            WHERE handle IN ('sonypictures.de', 'studiocanal.de')
              AND platform = 'instagram'
              AND channel_type = 'Studio/Verleih'
            """
        )
    )

    # H1 (warnerbros duplicate DELETE) and H6 (@netflix DELETE) are not
    # restored on downgrade — the deleted rows aren't preserved in
    # migration state, and reconstructing them risks creating wrong data.
    # The 'UK' enum value also stays — Postgres has no ALTER TYPE DROP
    # VALUE. Both decisions are documented in the module docstring.
