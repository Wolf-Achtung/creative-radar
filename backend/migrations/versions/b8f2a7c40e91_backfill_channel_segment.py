"""backfill channel.segment + correct channel.market for non-pair channels

Revision ID: b8f2a7c40e91
Revises: e1c93a4d7f08
Create Date: 2026-05-25

Master-Plan-Schritt-2, Commit 2/2. Klassifiziert die Non-Pair-Channels per
Wolf-Ping-2-Runde-2-Liste in das in e1c93a4d7f08 angelegte ``segment``-Feld
und korrigiert wo nötig den ``market``-Wert (Lesart C: zweispaltiger
Backfill). Pair-Channels werden NICHT berührt — Disjunkt-Vertrag aus dem
Briefing.

Klassifizierungs-Regel (Wolf 2026-05-25): Content-Positionierung schlägt
Konzern-Eigentum. Mainstream/Wide-Release → ``*_major``, Arthouse/Specialty
→ ``*_independent``. Streamer als ``*_major`` bzw. ``de_verleih``.
``segment = NULL`` ist explizite Restklasse (Trade/Discovery + INT).

Idempotenz: jeder UPDATE filtert ``WHERE segment IS NULL``. Re-Runs gegen
schon-klassifizierte Channels sind No-Ops. Neue Channels, die nach diesem
Backfill via Admin-UI hinzukommen, bleiben automatisch ``segment = NULL``
bis ein späterer Backfill-Lauf sie aufnimmt.

Match-Semantik:
- Handle-Match via ``LOWER(handle) = :handle_lower`` — fängt die
  CamelCase-YT-Handles (``ConstantinFilm``, ``DisneyPlus``, …) gemeinsam
  mit den lowercase IG/TT-Varianten.
- Platform-Match exakt gegen die DB-Werte ``instagram``/``tiktok``/
  ``youtube`` aus dem ``channel``-Schema, nicht ``ig``/``tt``/``yt``.

Reversible Down-Strategie via Helper-Tabelle:
Wolf-Vorgabe: "Down-Pfad gezielt pro Handle, nicht pauschal. Nur Handles,
deren ``market`` der Up-Pfad geändert hat, dürfen auf INT zurück; Handles,
die schon DE/US/UK waren, im Down-Pfad nicht anfassen."

Die zur Migrations-Schreibzeit unbekannte Information *"war ``market``
vor dem Up-Pfad gleich market_soll?"* wird zur Run-Zeit aufgenommen.
Der Up-Pfad legt eine Helper-Tabelle ``creative_radar._segment_backfill_rollback``
an und schreibt pro berührter ``(handle, platform)``-Zeile den
*ursprünglichen* ``market``-Wert hinein. Der Down-Pfad liest exakt aus
dieser Tabelle und setzt market + segment gezielt pro Handle zurück.
Nach abgeschlossenem Down-Pfad wird die Helper-Tabelle gedropped.

SQLite-Pfad: Der Backfill ist eine reine Data-Migration ohne Schema-
Aspekt, gegen die SQLite-Test-Fixtures (leere ``channel``-Tabelle, kein
``segment``-Spalten-Bootstrap via ``metadata.create_all``) ist sie ein
No-Op. Helper-Tabelle wird nicht angelegt, der ganze Pfad geguarded
durch eine Dialect-Weiche.
"""
from typing import Optional, Sequence, Tuple, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b8f2a7c40e91"
down_revision: Union[str, Sequence[str], None] = "e1c93a4d7f08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "creative_radar"
ROLLBACK_TABLE = "_segment_backfill_rollback"


# Wolf-Ping-2-Runde-2-Liste (2026-05-25). Format: (handle, platform,
# market_soll, segment). ``segment = None`` bedeutet "überspringen — kein
# UPDATE für diese Zeile" (Trade/Discovery + INT-Channels bleiben
# außerhalb Roundup-Scope, market_soll ist hier irrelevant). Die Liste
# spiegelt Wolfs Klassifizierungs-Regel pro Channel; Reihenfolge per
# Segment gruppiert für Lesbarkeit, Reihenfolge der Ausführung ist
# bedeutungslos.
CLASSIFICATIONS: Sequence[Tuple[str, str, str, Optional[str]]] = (
    # ---------- DE — de_verleih ----------
    ("20thcenturystudiosde",     "instagram", "DE", "de_verleih"),
    ("20thCenturyStudiosDE",     "youtube",   "DE", "de_verleih"),
    ("constantinfilm",           "instagram", "DE", "de_verleih"),
    ("constantinfilm",           "tiktok",    "DE", "de_verleih"),
    ("ConstantinFilm",           "youtube",   "DE", "de_verleih"),
    ("leoninestudios",           "instagram", "DE", "de_verleih"),
    ("disneyplusde",             "instagram", "DE", "de_verleih"),
    ("hbomaxde",                 "instagram", "DE", "de_verleih"),
    ("skydeutschland",           "instagram", "DE", "de_verleih"),
    ("wowtv",                    "instagram", "DE", "de_verleih"),
    ("wowtvde",                  "instagram", "DE", "de_verleih"),
    ("discoveryplusde",          "instagram", "DE", "de_verleih"),
    ("sonypictures.de",          "instagram", "DE", "de_verleih"),
    ("studiocanal.de",           "instagram", "DE", "de_verleih"),
    ("studiocanalde",            "instagram", "DE", "de_verleih"),
    ("natgeodeutschland",        "instagram", "DE", "de_verleih"),
    ("starwars_de",              "instagram", "DE", "de_verleih"),
    ("DisneyDeutschlandDE",      "youtube",   "DE", "de_verleih"),
    ("MarvelHQDE",               "youtube",   "DE", "de_verleih"),
    ("ParamountPicturesGER",     "youtube",   "DE", "de_verleih"),
    ("StarWarsDeutschland",      "youtube",   "DE", "de_verleih"),
    ("dcmfilm",                  "instagram", "DE", "de_verleih"),

    # ---------- DE — de_independent ----------
    ("arsenal.filmverleih",      "instagram", "DE", "de_independent"),
    ("eksystent_filmverleih",    "instagram", "DE", "de_independent"),
    ("farbfilmverleih",          "instagram", "DE", "de_independent"),
    ("grandfilm_verleih",        "instagram", "DE", "de_independent"),
    ("pandorafilmverleih",       "instagram", "DE", "de_independent"),
    ("tobisfilm",                "instagram", "DE", "de_independent"),
    ("vueltagermany",            "instagram", "DE", "de_independent"),
    ("weltkinofilmverleih",      "instagram", "DE", "de_independent"),
    ("xverleih",                 "tiktok",    "DE", "de_independent"),
    ("capelightpictures",        "instagram", "DE", "de_independent"),
    ("alpenrepublik",            "instagram", "DE", "de_independent"),
    ("meteor_film",              "instagram", "DE", "de_independent"),
    ("mfa_film",                 "instagram", "DE", "de_independent"),
    ("neue_visionen",            "instagram", "DE", "de_independent"),
    ("piffl_medien",             "instagram", "DE", "de_independent"),
    ("rialtofilm",               "instagram", "DE", "de_independent"),
    ("majestic.film",            "instagram", "DE", "de_independent"),
    ("pantaleonfilms",           "instagram", "DE", "de_independent"),
    ("akkordfilm",               "instagram", "DE", "de_independent"),
    ("24bilder",                 "instagram", "DE", "de_independent"),
    ("flare.film.berlin",        "instagram", "DE", "de_independent"),
    ("riseandshinecinema",       "instagram", "DE", "de_independent"),

    # ---------- US — us_major ----------
    ("disney",                   "instagram", "US", "us_major"),
    ("disneychannel",            "instagram", "US", "us_major"),
    ("disney",                   "tiktok",    "US", "us_major"),
    ("disneyplus",               "instagram", "US", "us_major"),
    ("disneyplus",               "tiktok",    "US", "us_major"),
    ("DisneyPlus",               "youtube",   "US", "us_major"),
    ("disneyanimation",          "tiktok",    "US", "us_major"),
    ("hbo",                      "instagram", "US", "us_major"),
    ("hbomax",                   "instagram", "US", "us_major"),
    ("hbomaxmovies",             "instagram", "US", "us_major"),
    ("hulu",                     "instagram", "US", "us_major"),
    ("hulu",                     "tiktok",    "US", "us_major"),
    ("netflixfilm",              "instagram", "US", "us_major"),
    ("netflixgeeked",            "instagram", "US", "us_major"),
    ("netflixgeeked",            "tiktok",    "US", "us_major"),
    ("netflixqueue",             "instagram", "US", "us_major"),
    ("netflixtudum",             "instagram", "US", "us_major"),
    ("strongblacklead",          "instagram", "US", "us_major"),
    ("primevideo",               "instagram", "US", "us_major"),
    ("primevideo",               "tiktok",    "US", "us_major"),
    ("PrimeVideo",               "youtube",   "US", "us_major"),
    ("streamonmax",              "instagram", "US", "us_major"),
    ("Max",                      "youtube",   "US", "us_major"),
    ("AppleTV",                  "youtube",   "US", "us_major"),
    ("appletv",                  "instagram", "US", "us_major"),
    ("warnerbrosentertainment",  "instagram", "US", "us_major"),
    ("warnerbrosepics",          "instagram", "US", "us_major"),
    ("blumhouse",                "instagram", "US", "us_major"),
    ("legendary",                "instagram", "US", "us_major"),
    ("lucasfilm",                "instagram", "US", "us_major"),
    ("natgeo",                   "instagram", "US", "us_major"),
    ("lionsgatehorror",          "instagram", "US", "us_major"),
    ("mgmplus",                  "instagram", "US", "us_major"),

    # ---------- US — us_independent ----------
    ("a24",                      "instagram", "US", "us_independent"),
    ("neonrated",                "instagram", "US", "us_independent"),
    ("focusfeatures",            "tiktok",    "US", "us_independent"),
    ("searchlightpics",          "tiktok",    "US", "us_independent"),
    ("sonypicturesclassics",     "tiktok",    "US", "us_independent"),
    ("orionpictures",            "instagram", "US", "us_independent"),
    ("magnoliapics",             "instagram", "US", "us_independent"),
    ("verticalentertainment",    "instagram", "US", "us_independent"),
    ("rusticfilms",              "instagram", "US", "us_independent"),
    ("xyzfilms",                 "instagram", "US", "us_independent"),
    ("sabanfilms",               "instagram", "US", "us_independent"),
    ("squarepegfilms",           "instagram", "US", "us_independent"),
    ("portauprincefilms",        "instagram", "US", "us_independent"),
    ("independentfilmco",        "instagram", "US", "us_independent"),
    ("anapurna",                 "instagram", "US", "us_independent"),
    ("elarapictures",            "instagram", "US", "us_independent"),
    ("seruanimation",            "instagram", "US", "us_independent"),

    # ---------- UK — uk_major ----------
    ("film4",                    "instagram", "UK", "uk_major"),

    # ---------- UK — uk_independent ----------
    ("hanway_films",             "instagram", "UK", "uk_independent"),
    ("wildbunchfilmlounge",      "instagram", "UK", "uk_independent"),

    # ---------- NULL — übersprungen, kein UPDATE ----------
    ("moviepilot",               "tiktok",    "DE",  None),
    ("indiewire",                "tiktok",    "US",  None),
    ("plaion_dach",              "instagram", "INT", None),
    ("plaion_de",                "instagram", "INT", None),
    ("plaion_official",          "instagram", "INT", None),
    ("plaionpictures",           "instagram", "INT", None),
    ("bad_robot",                "instagram", "INT", None),
    ("madmanfilms",              "instagram", "INT", None),
    ("elevation_pics",           "instagram", "INT", None),
    ("universalpicturesau",      "instagram", "INT", None),
    ("studiocanal",              "instagram", "INT", None),
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # SQLite-Pfad: Data-Migration ohne Effekt — Test-Fixtures pflanzen die
    # ``channel``-Tabelle leer auf, der Backfill ist No-Op und braucht
    # die Helper-Tabelle nicht.
    if not _is_postgres():
        return

    conn = op.get_bind()

    # Helper-Tabelle für reversible Down-Strategie. PRIMARY KEY (handle,
    # platform) — gleiche Eindeutigkeit wie der Production-Unique-Index
    # ``a3e7b5c19d42``. Underscore-Prefix kennzeichnet dies als reines
    # Migration-Artefakt; nicht für Production-Code-Konsum.
    op.execute(sa.text(f"""
        CREATE TABLE {SCHEMA}.{ROLLBACK_TABLE} (
            handle      VARCHAR NOT NULL,
            platform    VARCHAR NOT NULL,
            market_old  VARCHAR NOT NULL,
            PRIMARY KEY (handle, platform)
        )
    """))

    for handle, platform, market_soll, segment in CLASSIFICATIONS:
        if segment is None:
            # Trade/Discovery + INT-Channels: bewusst übersprungen,
            # bleiben außerhalb Roundup-Scope (segment = NULL bleibt).
            continue

        handle_lower = handle.lower()

        # Pre-read: aktuellen market-Wert lesen, gleichzeitig
        # Idempotenz-Gate über ``segment IS NULL``. Wenn die Row schon
        # klassifiziert ist (re-run), liefert das SELECT nichts und
        # nichts wird angefasst. ``::text``-Cast hebt das ENUM-Type
        # ausdrücklich auf String, sicherer Round-Trip via Python.
        row = conn.execute(
            sa.text(f"""
                SELECT market::text
                FROM {SCHEMA}.channel
                WHERE LOWER(handle) = :h
                  AND platform = :p
                  AND segment IS NULL
            """),
            {"h": handle_lower, "p": platform},
        ).fetchone()

        if row is None:
            # Channel-Row existiert nicht (admin-add nicht gelaufen, oder
            # Wolf-Liste enthält einen Handle, der nie in der DB
            # auftauchte) ODER segment ist schon gesetzt (re-run).
            # Beide Fälle: still überspringen, idempotent.
            continue

        market_old = row[0]

        # Snapshot des alten market-Werts für den Down-Pfad.
        conn.execute(
            sa.text(f"""
                INSERT INTO {SCHEMA}.{ROLLBACK_TABLE}
                    (handle, platform, market_old)
                VALUES (:h, :p, :m_old)
            """),
            {"h": handle_lower, "p": platform, "m_old": market_old},
        )

        # Eigentlicher Up-UPDATE: setzt segment + (falls nötig) korrigiert
        # market. Keine expliziten ENUM-Casts: Postgres castet String-
        # Parameter implizit auf den deklarierten Spaltentyp. Vermeidet
        # gleichzeitig die Schema-Asymmetrie zwischen ``market`` (lebt
        # in ``public`` aus dem SQLModel-Bootstrap) und ``channel_segment``
        # (in ``creative_radar`` aus Commit 1). Implicit cast greift
        # gleichermaßen auf beide.
        conn.execute(
            sa.text(f"""
                UPDATE {SCHEMA}.channel
                SET market  = :m_soll,
                    segment = :s
                WHERE LOWER(handle) = :h
                  AND platform = :p
                  AND segment IS NULL
            """),
            {
                "m_soll": market_soll,
                "s": segment,
                "h": handle_lower,
                "p": platform,
            },
        )


def downgrade() -> None:
    # SQLite-Pfad: Up war No-Op, Down braucht nichts zu tun.
    if not _is_postgres():
        return

    conn = op.get_bind()

    # Liest die Helper-Tabelle als verbindliche "was hat Up tatsächlich
    # angefasst"-Liste. Schließt automatisch die Trade/INT-Skip-Zeilen
    # aus (die wurden gar nicht in die Helper-Tabelle eingetragen).
    rows = conn.execute(
        sa.text(f"""
            SELECT handle, platform, market_old
            FROM {SCHEMA}.{ROLLBACK_TABLE}
        """)
    ).fetchall()

    for handle, platform, market_old in rows:
        # Gezieltes UPDATE pro Handle. Setzt market auf den vor-Up-
        # Wert (kann INT, DE, US oder UK sein) und segment auf NULL.
        # Channels, die Up nicht angefasst hat (Trade/INT/nicht-
        # existent), kommen hier nicht vor — sie wurden nie eingetragen.
        conn.execute(
            sa.text(f"""
                UPDATE {SCHEMA}.channel
                SET market  = :m_old,
                    segment = NULL
                WHERE LOWER(handle) = :h
                  AND platform = :p
            """),
            {"m_old": market_old, "h": handle.lower(), "p": platform},
        )

    op.execute(sa.text(f"DROP TABLE {SCHEMA}.{ROLLBACK_TABLE}"))
