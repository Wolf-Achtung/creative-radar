from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4
import sqlalchemy as sa
from sqlmodel import Field, SQLModel, Relationship, Column, JSON

from app.config import settings


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_table_schema() -> Optional[str]:
    """Return 'creative_radar' for Postgres deploys, None for SQLite tests.

    F0.2 places all CR tables in the dedicated 'creative_radar' schema. SQLite
    (used by pytest with an in-memory DB) does not understand Postgres schemas
    in the same way, so we strip the schema clause when running against it.
    The check inspects every URL field that resolve_database_url() consults so
    a Production override path (DATABASE_PRIVATE_URL etc.) still flips the
    flag correctly.
    """
    candidates = (
        settings.database_url,
        settings.database_private_url,
        settings.database_public_url,
        settings.pghost,
    )
    for raw in candidates:
        if raw and "postgres" in str(raw).lower():
            return "creative_radar"
    return None


# Module-level constant so __table_args__ stays a plain dict literal at the
# call site. Re-evaluating per class is overkill — settings don't change at
# runtime once the app has booted.
_CR_TABLE_ARGS: dict = {"schema": _resolve_table_schema()} if _resolve_table_schema() else {}


def _fk(target: str) -> str:
    """Qualify a foreign-key target with the active CR schema, when present.

    SQLAlchemy resolves ``Field(foreign_key="title.id")`` by looking up the
    bare table name in the metadata registry. When tables register with a
    schema (Postgres production), the registry key becomes
    ``"creative_radar.title"`` — so the FK string must match that exact key.
    SQLite tests keep the bare form because the schema clause is absent
    there. Without this qualification the Postgres app boot fails with
    ``NoReferencedTableError``; SQLite tests stay green and hide the bug,
    which is why ``tests/test_orm_fk_resolution.py`` exists in addition to
    the regular suite.
    """
    schema = _resolve_table_schema()
    return f"{schema}.{target}" if schema else target


class Market(str, Enum):
    DE = "DE"
    US = "US"
    INT = "INT"
    UK = "UK"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class Priority(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class AssetType(str, Enum):
    TRAILER = "Trailer"
    TRAILER_DROP = "Trailer Drop"
    TEASER = "Teaser"
    POSTER = "Poster"
    KEY_ART = "Poster / Key Art"
    STORY = "Story"
    KINETIC = "Kinetic"
    CHARACTER_CARD = "Character Card"
    CAST_POST = "Character / Cast Post"
    REVIEW_QUOTE = "Quote / Review"
    CTA_POST = "CTA Post"
    TICKET_CTA = "Ticket CTA"
    RELEASE_REMINDER = "Release Reminder"
    BEHIND_THE_SCENES = "Behind the Scenes"
    EVENT_FESTIVAL = "Event / Festival"
    SERIES_EPISODE_PUSH = "Series Episode Push"
    FRANCHISE_BRAND_POST = "Franchise / Brand Post"
    DISCOVERY = "Discovery"
    UNKNOWN = "Unknown"


class ReviewStatus(str, Enum):
    NEW = "new"
    APPROVED = "approved"
    HIGHLIGHT = "highlight"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class ReportStatus(str, Enum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    FINAL = "final"


class CandidateStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class CandidateSource(str, Enum):
    HASHTAG = "hashtag"
    TEXT = "text"
    OCR = "ocr"
    OPENAI = "openai"
    PERPLEXITY = "perplexity"
    MATCHER = "matcher"


class ChannelRole(str, Enum):
    STUDIO_DISTRIBUTOR = "studio_distributor"
    FRANCHISE = "franchise"
    TALENT_CAST = "talent_cast"
    REGIONAL = "regional"
    PUBLISHER_PLATFORM = "publisher_platform"


class QualityTier(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class AcquisitionStrategy(str, Enum):
    APIFY = "apify"
    YOUTUBE_API = "youtube_api"
    MANUAL = "manual"


class ChannelSegment(str, Enum):
    """Klassifizierungs-Steuerfeld fuer Non-Pair-Channels (Master-Plan-
    Schritt 2, Migrationen e1c93a4d7f08 + b8f2a7c40e91). Pair-Pool-Channels
    bleiben ``segment = NULL`` und tauchen in keinem Roundup auf — Pair-
    und Roundup-Pfad sind disjunkt. Die sechs Werte spiegeln Wolfs
    Klassifizierungs-Regel (Content-Positionierung schlaegt Konzern-
    Eigentum): Mainstream/Wide-Release → ``*_major``, Arthouse/Specialty
    → ``*_independent``. Streamer zaehlen als ``*_major`` bzw.
    ``de_verleih``. Reihenfolge identisch mit der ENUM-Werteliste in
    der Migration e1c93a4d7f08; pyenum.value ist die DB-Repraesentation.
    """
    US_MAJOR = "us_major"
    US_INDEPENDENT = "us_independent"
    UK_MAJOR = "uk_major"
    UK_INDEPENDENT = "uk_independent"
    DE_VERLEIH = "de_verleih"
    DE_INDEPENDENT = "de_independent"


def _enum_column(
    enum_cls,
    name: str,
    *,
    nullable: bool,
    server_default: Optional[str] = None,
    primary_key: bool = False,
) -> Column:
    """Build a column for one of the channel-registry enums. Postgres uses the
    native ENUM type defined in migration 7e3b2c4a8f51 (creative_radar schema);
    SQLite falls back to VARCHAR so the in-memory test DB and alembic-roundtrip
    test stay green. ``values_callable`` is critical — without it SQLAlchemy
    would write member NAMES (uppercase) to the DB instead of the lowercase
    enum values defined in the migration.

    ``primary_key`` is needed by ``SegmentRoundup.segment`` (Master-Plan-
    Schritt-3): SQLModel verbietet ``primary_key=True`` als Field-kwarg, wenn
    auch ``sa_column=...`` gesetzt ist — der PK-Flag muss in den
    ``Column``-Konstruktor selbst. ``segment`` als PK heisst ``nullable=False``
    implizit (alle Caller setzen das auch explizit, defensiv).
    """
    schema = _resolve_table_schema()
    if schema:
        col_type = sa.Enum(
            enum_cls,
            name=name,
            schema=schema,
            native_enum=True,
            values_callable=lambda x: [e.value for e in x],
            create_constraint=False,
            create_type=False,
        )
    else:
        col_type = sa.String()
    kwargs: dict = {"nullable": nullable}
    if server_default is not None:
        kwargs["server_default"] = server_default
    if primary_key:
        kwargs["primary_key"] = True
    return Column(col_type, **kwargs)


class Channel(SQLModel, table=True):
    __table_args__ = _CR_TABLE_ARGS
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str
    platform: str = "instagram"
    url: str
    handle: Optional[str] = None
    market: Market = Market.UNKNOWN
    channel_type: Optional[str] = None
    priority: Priority = Priority.B
    active: bool = True
    mvp: bool = False
    notes: Optional[str] = None
    channel_role: Optional[ChannelRole] = Field(
        default=None,
        sa_column=_enum_column(ChannelRole, "channel_role", nullable=True),
    )
    quality_tier: QualityTier = Field(
        default=QualityTier.P1,
        sa_column=_enum_column(QualityTier, "quality_tier", nullable=False, server_default="P1"),
    )
    acquisition_strategy: AcquisitionStrategy = Field(
        default=AcquisitionStrategy.APIFY,
        sa_column=_enum_column(AcquisitionStrategy, "acquisition_strategy", nullable=False, server_default="apify"),
    )
    monitoring_enabled: bool = Field(
        default=True,
        sa_column=Column(sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # Wir-Segment (21.08.2026): vom eigenen Team betreuter Kanal — die
    # Basis der "empfohlen → gemacht → gewirkt"-Auswertung. Wolf setzt
    # das Flag per Checkliste in Admin → Quellen (Migration d2e5c7a91f04).
    is_own: bool = Field(
        default=False,
        sa_column=Column(sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Master-Plan-Schritt-2: Klassifizierungs-Steuerfeld fuer den
    # Non-Pair-Roundup-Pfad. Pair-Pool-Channels haben ``segment = NULL``
    # und sind aus jedem Roundup-Generator-Lauf disjunkt ausgeschlossen.
    # Migrationen e1c93a4d7f08 (Spalte) + b8f2a7c40e91 (Backfill).
    # Lazy-Mirror bis hier: Schritt 2 hat die Spalte produktiv ohne
    # ORM-Spiegelung gefuellt; Schritt 3 (Roundup-Generator) ist der
    # erste Read-Pfad und braucht das Feld typed im ORM.
    segment: Optional[ChannelSegment] = Field(
        default=None,
        sa_column=_enum_column(ChannelSegment, "channel_segment", nullable=True),
    )
    # Audit-Felder, befüllt nur durch scripts/import_channels.py
    # (Sprint 5.3.X Perplexity-seed bulk-import). Read-only-by-convention
    # für alle anderen Code-Pfade — die admin/channels-Endpoints lassen
    # die Felder unangetastet, damit `import_source` weiterhin verlässlich
    # angibt, aus welchem Recherche-Batch ein Channel stammt.
    category: Optional[str] = None
    import_source: Optional[str] = None
    # Sprint 4.5 — bug 1 fix. Platform-native channel ID (e.g. YouTube
    # ``UCxxx``). Stored separately from ``handle`` because the YouTube
    # channels.list API cannot resolve legacy custom-URL slugs (``c/<slug>``)
    # via ``forHandle`` — only modern @handles or the UCxxx-ID. Four
    # production YT channels (NetflixDE, SonyPicturesEntertainment,
    # WaltDisneyStudios, WarnerBrosPictures) failed Sprint-4 sync because
    # of this. The Sprint-4.5 resolver prefers this column when populated.
    # Optional / unindexed: only the YT path consumes it today; IG/TT
    # don't need a separate platform-side ID.
    platform_channel_id: Optional[str] = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    posts: list["Post"] = Relationship(back_populates="channel")


class Title(SQLModel, table=True):
    __table_args__ = _CR_TABLE_ARGS
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tmdb_id: Optional[int] = Field(default=None, index=True)
    title_original: str
    title_local: Optional[str] = None
    franchise: Optional[str] = None
    content_type: str = "Film"
    market_relevance: Market = Market.MIXED
    release_date_de: Optional[date] = None
    release_date_us: Optional[date] = None
    source: str = "Manual"
    aliases: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # Trailer-Intelligence Stufe 1 (20.08.2026): TMDb-Genres in
    # TMDb-Reihenfolge — das erste ist das primaere, danach gruppiert
    # die Muster-Aggregation (services/trailer_patterns.py). Leer heisst
    # "noch nicht befuellt": die Spalte fuellt der Title-Sync bei jedem
    # Lauf aus den discover-Antworten; Bestandstitel bekommen ihr Genre
    # also beim naechsten Sync, der sie wieder sieht.
    genres: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    priority: Priority = Priority.B
    active: bool = True
    # Wir-Projekte (22.08.2026): Trailerhaus betreut keine kompletten
    # Kunden-Kanaele, sondern liefert pro FILMPROJEKT — die Wir-Einheit
    # ist deshalb der Titel, nicht der Kanal. ``wir_segment`` zaehlt
    # Posts als "gemacht", wenn ihr Asset auf einen Wir-Projekt-Titel
    # gemappt ist ODER ihr Kanal ``is_own`` traegt (Union; das
    # Kanal-Flag bleibt fuer echte eigene Kanaele bestehen). Markierung
    # in Admin → Quellen, Migration a9c4e7f21d05.
    is_own_project: bool = Field(
        default=False,
        sa_column=Column(sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    keywords: list["TitleKeyword"] = Relationship(back_populates="title")
    assets: list["Asset"] = Relationship(back_populates="title")


class TitleKeyword(SQLModel, table=True):
    __table_args__ = _CR_TABLE_ARGS
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    title_id: UUID = Field(foreign_key=_fk("title.id"))
    keyword: str
    keyword_type: str = "keyword"
    active: bool = True

    title: Title = Relationship(back_populates="keywords")


class Post(SQLModel, table=True):
    __table_args__ = _CR_TABLE_ARGS
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    channel_id: UUID = Field(foreign_key=_fk("channel.id"))
    platform: str = "instagram"
    post_url: str = Field(unique=True, index=True)
    external_id: Optional[str] = None
    published_at: Optional[datetime] = None
    detected_at: datetime = Field(default_factory=utc_now)
    caption: Optional[str] = None
    raw_payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    visible_likes: Optional[int] = None
    visible_comments: Optional[int] = None
    visible_views: Optional[int] = None
    visible_shares: Optional[int] = None
    visible_bookmarks: Optional[int] = None
    duration_seconds: Optional[int] = None
    media_type: Optional[str] = None
    status: str = "new"
    # Sprint 5.3.1: cross-platform AI analysis. ``analysis`` carries the
    # PostAnalysis dict (format/purpose/tone/lifecycle_stage + confidence
    # + classified_at + model strings); ``last_analyzed_at`` drives the
    # idempotent skip-pre-check in /api/admin/analyze/{channel_id}.
    analysis: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    last_analyzed_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    channel: Channel = Relationship(back_populates="posts")
    assets: list["Asset"] = Relationship(back_populates="post")


class Asset(SQLModel, table=True):
    __table_args__ = _CR_TABLE_ARGS
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    post_id: UUID = Field(foreign_key=_fk("post.id"))
    title_id: Optional[UUID] = Field(default=None, foreign_key=_fk("title.id"))
    asset_type: AssetType = AssetType.UNKNOWN
    language: str = "Unknown"
    screenshot_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    ocr_text: Optional[str] = None
    detected_keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    ai_summary_de: Optional[str] = None
    ai_summary_en: Optional[str] = None
    ai_trend_notes: Optional[str] = None
    confidence_score: Optional[float] = None
    review_status: ReviewStatus = ReviewStatus.NEW
    curator_note: Optional[str] = None
    include_in_report: bool = False
    is_highlight: bool = False

    # Visual & Placement Pack fields
    # Beobachtete Werte in der Live-DB: pending, running, analyzed, text_fallback,
    # no_source, fetch_failed, error. Der historische Wert "done" wird vom Selector
    # weiterhin akzeptiert (siehe ANALYSIS_DONE_STATES in report_selector.py), kommt
    # aber in der aktuellen Pipeline nicht mehr vor.
    visual_analysis_status: str = "pending"
    visual_source_url: Optional[str] = None
    visual_notes: Optional[str] = None
    placement_title_text: Optional[str] = None
    placement_position: Optional[str] = None
    placement_strength: Optional[str] = None
    has_title_placement: bool = False
    has_kinetic: bool = False
    kinetic_type: Optional[str] = None
    kinetic_text: Optional[str] = None
    de_us_match_key: Optional[str] = None
    visual_confidence_score: Optional[float] = None
    visual_evidence_url: Optional[str] = None
    visual_crop_title_url: Optional[str] = None
    visual_crop_cta_url: Optional[str] = None
    visual_crop_kinetic_url: Optional[str] = None
    visual_evidence_status: Optional[str] = None
    visual_evidence_pack: dict = Field(default_factory=dict, sa_column=Column(JSON))

    # Sprint 5.3.1: vision-pipeline fields. The new analyzer writes only
    # to these four columns; the legacy ai_summary_de/en + visual_*
    # fields above belong to the older plan-doc pipeline and are not
    # remapped (Wolf decision: additive reconcile, no mapping shim).
    # Idempotency key is the partial-unique index on (post_id, asset_url)
    # WHERE asset_url IS NOT NULL — see migration 9a2e7c4f5b18.
    asset_url: Optional[str] = None
    vision_description: Optional[str] = None
    vision_model: Optional[str] = None
    analyzed_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    post: Post = Relationship(back_populates="assets")
    title: Optional[Title] = Relationship(back_populates="assets")


class TitleSyncRun(SQLModel, table=True):
    __table_args__ = _CR_TABLE_ARGS
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    source: str = "tmdb"
    markets: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    date_from: date
    date_to: date
    fetched_count: int = 0
    upserted_count: int = 0
    deduped_count: int = 0
    status: str = "success"
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)


class TitleCandidate(SQLModel, table=True):
    __table_args__ = _CR_TABLE_ARGS
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    asset_id: UUID = Field(foreign_key=_fk("asset.id"), index=True)
    suggested_title: str
    suggested_franchise: Optional[str] = None
    source: CandidateSource = CandidateSource.TEXT
    confidence: float = 0.0
    status: CandidateStatus = CandidateStatus.OPEN
    # KI-Assist-Fortschritt (21.08.2026, Migration e7f3a9c258d1):
    # gepruefte Kandidaten werden markiert und beim naechsten Lauf
    # uebersprungen; die Begruendung steht als Hinweis in der Queue.
    llm_checked_at: Optional[datetime] = None
    llm_note: Optional[str] = Field(default=None, max_length=300)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WeeklyReport(SQLModel, table=True):
    __table_args__ = _CR_TABLE_ARGS
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    week_start: date
    week_end: date
    generated_at: datetime = Field(default_factory=utc_now)
    status: ReportStatus = ReportStatus.DRAFT
    executive_summary_de: Optional[str] = None
    executive_summary_en: Optional[str] = None
    trend_summary_de: Optional[str] = None
    html_url: Optional[str] = None
    pdf_url: Optional[str] = None
    html_content: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CostLog(SQLModel, table=True):
    """Persisted log of every paid external call (Apify, OpenAI). Phase 4 W4
    Task 4.4 / F0.6 — logging only, no hard cap. The hard cap is Phase 5+
    work; for now this table is the audit-trail and the data source for
    the GET /api/admin/cost-summary endpoint.

    Cost is stored in cents (integer) for both USD and EUR to avoid the
    floating-point rounding hell of summing thousands of small costs.
    EUR is derived at insert time from settings.usd_to_eur_rate so the
    rate snapshot used at logging time is preserved even if Wolf adjusts
    the rate later.
    """
    __table_args__ = _CR_TABLE_ARGS
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    timestamp: datetime = Field(default_factory=utc_now, index=True)
    provider: str = Field(index=True)  # 'apify' | 'openai'
    operation: str = Field(index=True)  # e.g. 'instagram_actor', 'vision_call', 'chat_completion'
    cost_usd_cents: int = 0
    # Sub-cent precision (1 cent = 1000 millicents). Added after the
    # cost-tracking diagnose 2026-05-11 caught int-cent flatten-to-zero
    # bug for OpenAI/Anthropic per-call costs. ``cost_usd_cents`` stays
    # for back-compat with historical Apify rows.
    cost_usd_millicents: int = 0
    cost_eur_cents: int = 0
    cost_meta: dict = Field(default_factory=dict, sa_column=Column(JSON))


class CronRun(SQLModel, table=True):
    """Sprint Cron-Background-Task — log row per cron-sync invocation.

    Status transitions: ``running`` -> ``completed`` (normal finish) or
    ``running`` -> ``failed`` (unexpected exception, with error_message).
    A run that stays on ``running`` longer than ``CRON_RUN_TIMEOUT_MINUTES``
    is treated as stale on the next trigger and force-marked ``failed``.
    """
    __tablename__ = "cron_run"
    __table_args__ = _CR_TABLE_ARGS
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    started_at: datetime = Field(default_factory=utc_now, index=True)
    completed_at: Optional[datetime] = None
    status: str = Field(default="running", index=True)
    run_index: int = 0
    summary_json: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    error_message: Optional[str] = None


class SegmentRoundup(SQLModel, table=True):
    """Persisted weekly Segment-Roundup — one row per (segment, iso_year, iso_week).

    Master-Plan-Schritt-3 (2026-05-25). Disjunkt zur Pair-Brief-Pipeline:
    Roundup-Pfad und Pair-Pfad teilen keinen Code, keine Tabelle, keine
    LLM-Prompt-Form. ``insight_report`` hat ``pair_key NOT NULL`` im PK,
    was eine Erweiterung mit Typ-Diskriminator zu invasiv gemacht haette
    (Lock-Pfad ``_acquire_brief_lock`` ist auch pair_key-keyed) — daher
    eigene Tabelle (Wolf-Ping-1 (a), 25.05.).

    Composite-PK ``(segment, iso_year, iso_week)`` spiegelt die natuerliche
    Cache-Lookup-Semantik: ein Roundup pro Segment pro Woche. Last-Write-
    Wins beim Regenerate (analog ``insight_report``).

    JSON-Blobs:
    - ``channels_aggregation`` ist eine ``SegmentAggregation``-Pydantic-
      Form (siehe ``app.schemas.insights``) mit Segment-Header + per-
      Channel-Stats + Top-N Posts. Liefert Audit-Trail und Frontend-
      Render-Material in einem Pass.
    - ``llm_output`` ist die deskriptive Synthese — eigenes Schema, keine
      Vergleichs- oder Cross-Segment-Aussagen (Roundup-Charakter laut
      Wolf-Festlegung 25.05.).

    ``window_days`` als Audit-Spalte: das Roundup-Default-Fenster ist
    14d (Wolf-Festlegung, bewusste Abweichung vom 30d-Pair-Fenster),
    parametrisiert. Wenn Wolf das Fenster spaeter aendert, sieht man
    pro Row, mit welchem Fenster der Brief generiert wurde.
    """
    __tablename__ = "segment_roundup"
    __table_args__ = _CR_TABLE_ARGS
    segment: ChannelSegment = Field(
        sa_column=_enum_column(
            ChannelSegment,
            "channel_segment",
            nullable=False,
            primary_key=True,
        ),
    )
    iso_year: int = Field(primary_key=True)
    iso_week: int = Field(primary_key=True)
    window_days: int = Field(nullable=False)
    channels_aggregation: dict = Field(sa_column=Column(JSON, nullable=False))
    llm_output: dict = Field(sa_column=Column(JSON, nullable=False))
    generated_at: datetime = Field(default_factory=utc_now, index=True)
    model: str = Field(max_length=64)
    cost_usd_cents: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


class InsightReport(SQLModel, table=True):
    """Persisted weekly briefing — one row per (pair_key, iso_year, iso_week).

    Sprint 1 (Persistenz). Composite PK matches the natural cache-lookup
    used by ``GET /api/insights/weekly`` and ``POST /api/admin/insights/regenerate``:
    a frontend reload of the same pair in the same ISO week serves the
    persisted row instead of triggering a fresh ~$0.40 Opus call. The
    ``force=true`` query param skips the cache lookup but still upserts
    the result (Last-Write-Wins on the composite PK), so regenerating a
    brief overwrites the stored one rather than producing duplicates.

    The ``aggregation`` and ``llm_output`` JSON blobs are the same shapes
    as the ``PairAggregation`` and ``LLMReport`` Pydantic models in
    ``app.schemas.insights`` — we store them serialised so the persisted
    payload survives schema additions on the Pydantic side without a
    migration (older rows just lack the new fields, which the Optional[]
    annotations on ``LLMReport`` already tolerate).
    """
    __tablename__ = "insight_report"
    __table_args__ = _CR_TABLE_ARGS
    pair_key: str = Field(primary_key=True, max_length=64)
    iso_year: int = Field(primary_key=True)
    iso_week: int = Field(primary_key=True)
    aggregation: dict = Field(sa_column=Column(JSON, nullable=False))
    llm_output: dict = Field(sa_column=Column(JSON, nullable=False))
    generated_at: datetime = Field(default_factory=utc_now, index=True)
    model: str = Field(max_length=64)
    cost_usd_cents: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


class ErForecastEinordnung(SQLModel, table=True):
    """#252 Split-Cache fuer die ER-Prognose — eine Row pro (pair_key,
    Ziel-ISO-Woche der Prognose).

    Die Regression ist gratis und laeuft bei jedem Aufruf live
    (``compute_market_timeline``-Kern); nur die LLM-Einordnung kostet
    einen Opus-Call. Diese Tabelle haelt genau den Einordnungs-TEXT vor,
    damit oeffentliche Aufrufe nicht pro Seitenansicht zahlen — max.
    9 Opus-Calls/Woche (ein Cache-Miss pro Pair, plus Cron-Warmup).

    Der Text ist IMMER die gegatete (public-safe) Fassung — er wird vom
    Admin- und Public-Pfad geteilt und darf keinen Prognosewert nennen,
    den das Ehrlichkeits-Gate der oeffentlichen Sicht entzieht.
    First-write-wins pro Woche (geschrieben nur bei Cache-Miss); der
    ``weeks``-Query-Param der Endpoints beeinflusst den Cache-Key bewusst
    nicht (die Ziel-Woche bleibt dieselbe).
    """
    __tablename__ = "er_forecast_einordnung"
    __table_args__ = _CR_TABLE_ARGS
    pair_key: str = Field(primary_key=True, max_length=64)
    iso_year: int = Field(primary_key=True)
    iso_week: int = Field(primary_key=True)
    einordnung: str
    model: str = Field(max_length=64)
    generated_at: datetime = Field(default_factory=utc_now, index=True)


class CutterWeeklyBriefing(SQLModel, table=True):
    """Persisted Cutter-Wochenbriefing — eine Row pro (iso_year, iso_week).

    Master-Plan-Sprint 2026-06-12, Trockenlauf-Phase: generieren +
    persistieren, KEIN Frontend-Pfad (nur Admin-/DB-Lesezugriff), bis die
    Evidenzschwelle an echten Wochen kalibriert ist.

    JSON-Blobs:
    - ``evidence`` ist das Kalibrierungs-Produkt (Wolf-Festlegung: nicht
      optional): ``CutterWeeklyEvidence``-Form mit p75-Schwellen,
      Kandidaten-Zahlen pro Plattform, freigegebenen UND verworfenen
      Mustern mit Grund, ``title_key_share``-Messung und den
      Forecast-Signalen des Laufs.
    - ``llm_output`` ist die zusammengebaute ``CutterWeeklyLLMReport``-Form
      (LLM-Bloecke + deterministische Leerlauf-Bloecke). NULLABLE —
      bewusste Abweichung von der Roundup-Konvention (persist-skip bei
      ``llm_output=None``): eine Woche, deren LLM-Synthese nach allen
      Anlaeufen an der strikten Citation-Validierung scheitert, wird
      TROTZDEM persistiert, weil der Evidence-Blob das eigentliche
      Produkt der Trockenlauf-Phase ist. ``raw_llm_text`` haelt in dem
      Fall die letzte verworfene Antwort fuer die Diagnose.

    ``model='none'`` markiert Leerlauf-Wochen ohne LLM-Call (keine
    Plattform freigegeben — der Report besteht aus Code-Bloecken).
    Last-Write-Wins beim Regenerate (analog ``insight_report``).
    """
    __tablename__ = "cutter_weekly_briefing"
    __table_args__ = _CR_TABLE_ARGS
    iso_year: int = Field(primary_key=True)
    iso_week: int = Field(primary_key=True)
    evidence: dict = Field(sa_column=Column(JSON, nullable=False))
    llm_output: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    raw_llm_text: Optional[str] = None
    generated_at: datetime = Field(default_factory=utc_now, index=True)
    model: str = Field(max_length=64)
    cost_usd_cents: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


class DesignerWeeklyBriefing(SQLModel, table=True):
    """Persisted Designer-Wochenbriefing — eine Row pro (iso_year, iso_week).

    Sprint 2026-07-06: mirror von ``CutterWeeklyBriefing`` field-fuer-field.
    Gleiche Trockenlauf-Logik (Feature-Flag default off, kein Frontend-Pfad),
    dieselbe geteilte Evidenz-Pipeline (``services/weekly_briefing_evidence.py``)
    — nur die LLM-Lens (Motion-/Grafik-Beobachtung statt Schnitt-Beobachtung,
    ``services/designer_weekly.py``) unterscheidet sich.

    JSON-Blobs:
    - ``evidence`` ist das Kalibrierungs-Produkt (analog Cutter): p75-
      Schwellen, Kandidaten-Zahlen pro Plattform, freigegebene UND
      verworfene Muster mit Grund, ``title_key_share``-Messung und die
      Forecast-Signale des Laufs (``WeeklyBriefingEvidence``-Form).
    - ``llm_output`` ist die zusammengebaute ``DesignerWeeklyLLMReport``-
      Form (LLM-Bloecke + deterministische Leerlauf-Bloecke). NULLABLE —
      bewusste Abweichung von der Roundup-Konvention (persist-skip bei
      ``llm_output=None``): eine Woche, deren LLM-Synthese nach allen
      Anlaeufen an der strikten Citation-Validierung scheitert, wird
      TROTZDEM persistiert, weil der Evidence-Blob das eigentliche
      Produkt der Trockenlauf-Phase ist. ``raw_llm_text`` haelt in dem
      Fall die letzte verworfene Antwort fuer die Diagnose.

    ``model='none'`` markiert Leerlauf-Wochen ohne LLM-Call (keine
    Plattform freigegeben — der Report besteht aus Code-Bloecken).
    Last-Write-Wins beim Regenerate (analog ``cutter_weekly_briefing``).
    """
    __tablename__ = "designer_weekly_briefing"
    __table_args__ = _CR_TABLE_ARGS
    iso_year: int = Field(primary_key=True)
    iso_week: int = Field(primary_key=True)
    evidence: dict = Field(sa_column=Column(JSON, nullable=False))
    llm_output: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    raw_llm_text: Optional[str] = None
    generated_at: datetime = Field(default_factory=utc_now, index=True)
    model: str = Field(max_length=64)
    cost_usd_cents: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


class PatternBriefing(SQLModel, table=True):
    """Persistierte Text-Bausteine aus dem Muster-Bericht — eine Row pro
    ``(mode, iso_year, iso_week)`` (Trailer-Intelligence Stufe 1,
    Schritt 3, 20.08.2026).

    ``mode`` unterscheidet die Baustein-Ebene: ``"genre"`` (Genre-Muster
    aus ``compute_trailer_patterns``, dieser Sprint) und spaeter
    ``"title"`` (Titel-Modus, zweiter PR laut Wolf-Entscheidung
    "Beides, Genre zuerst") — die Spalte steht im PK, damit der zweite
    Modus ohne Migration dazukommt.

    JSON-Blobs (Konvention wie ``cutter_weekly_briefing``):
    - ``evidence`` (NOT NULL) ist die deterministische Muster-Auswahl:
      belastbare Zellen mit ihren Zahlen plus die Top-Beispiel-Posts
      (URL, Caption, Lift), aus denen der Prompt gebaut wurde. Der Blob
      macht jeden Brief rekonstruier- und auditierbar — dieselbe Idee
      wie ``insight_report.aggregation`` bei der Citation-Auswertung.
    - ``llm_output`` NULLABLE: eine Woche, deren LLM-Antwort nach allen
      Anlaeufen an Parse/Schema scheitert, wird trotzdem persistiert
      (Evidence zaehlt); ``raw_llm_text`` traegt dann die letzte
      verworfene Antwort.

    ``model='none'`` markiert Leerlauf-Wochen ohne LLM-Call (kein
    belastbares Muster — bei leerer Genre-Abdeckung der Normalfall,
    bis der Title-Sync die Genres gefuellt hat).

    ``citation_dropped`` zaehlt Bausteine, die die Citation-Pruefung
    verworfen hat (cited_post_ids ausserhalb der mitgegebenen
    Beispiel-Posts). Steht als eigene Spalte, damit die Quote ohne
    JSON-Parsing ueber Wochen abfragbar ist — analog der
    Citation-Auswertung vom 20.08.2026.
    """
    __tablename__ = "pattern_briefing"
    __table_args__ = _CR_TABLE_ARGS
    mode: str = Field(primary_key=True, max_length=32)
    iso_year: int = Field(primary_key=True)
    iso_week: int = Field(primary_key=True)
    window_days: int = Field(nullable=False)
    evidence: dict = Field(sa_column=Column(JSON, nullable=False))
    llm_output: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    raw_llm_text: Optional[str] = None
    generated_at: datetime = Field(default_factory=utc_now, index=True)
    model: str = Field(max_length=64)
    cost_usd_cents: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    citation_dropped: int = Field(default=0, nullable=False)


class AppUser(SQLModel, table=True):
    """User-Allowlist fuer das E-Mail+Code-Login (Sprint User-Login 2026-07).

    ~15 bekannte Nutzer (Team/Kunden). Bewusst KEIN Passwort-Feld — der
    Login laeuft ausschliesslich ueber Einmal-Codes per E-Mail (Muster
    aus dem Referenzprojekt api-ki-backend-neu, dort Code-Whitelist im
    Quellcode; hier als DB-Tabelle, damit Wolf User ueber den
    Admin-Bereich pflegen kann statt zu deployen).

    ``email`` ist der Identity-Key (lowercase-normalisiert beim Anlegen
    und bei jedem Lookup). ``active=False`` sperrt den User sofort: die
    User-Session-Middleware prueft den Flag pro Request, ein noch
    gueltiges Session-Cookie hilft einem deaktivierten User nicht.
    """
    __tablename__ = "app_user"
    __table_args__ = _CR_TABLE_ARGS
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(unique=True, index=True, max_length=255)
    display_name: Optional[str] = Field(default=None, max_length=120)
    active: bool = True
    # Monitoring-Freischaltung pro Person (Wolf-Festlegung 2026-07-20):
    # User mit diesem Flag sehen nach dem normalen E-Mail-Code-Login
    # zusaetzlich die Nutzungs-Auswertung (/nutzung, Export-Downloads) —
    # NUR die; alle uebrigen Admin-Funktionen bleiben der Passwort-
    # Session vorbehalten. Kein geteiltes Zweit-Passwort: Zugang ist
    # individuell entziehbar und nachvollziehbar.
    can_view_usage: bool = Field(
        default=False,
        sa_column=Column(sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    created_at: datetime = Field(default_factory=utc_now)
    last_login_at: Optional[datetime] = None


class LoginCode(SQLModel, table=True):
    """Einmal-Login-Code (E-Mail-Versand). Flow-Parameter identisch zum
    Referenzprojekt: 6 Ziffern, 10 Minuten gueltig, Einmal-Nutzung.

    Abweichung zur Referenz (dort Redis/In-Memory, Klartext): der Code
    liegt hier NUR als SHA-256-Hash in der DB (Defense-in-Depth bei
    DB-Leak/Backup-Zugriff) und ueberlebt einen Railway-Restart.
    ``attempts`` zaehlt Fehlversuche pro Code — nach
    ``LOGIN_CODE_MAX_ATTEMPTS`` (5) ist der Code verbrannt, auch wenn
    er noch nicht abgelaufen ist (gegen Code-Raten trotz IP-Rotation).
    Pro request-code wird genau eine Row angelegt und alle aelteren
    offenen Rows der E-Mail geloescht (latest-code-wins).
    """
    __tablename__ = "login_code"
    __table_args__ = _CR_TABLE_ARGS
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(index=True, max_length=255)
    code_hash: str = Field(max_length=64)
    expires_at: datetime
    used_at: Optional[datetime] = None
    attempts: int = 0
    created_at: datetime = Field(default_factory=utc_now)


class UsageEvent(SQLModel, table=True):
    """Leichtgewichtiges Nutzungs-Event-Log: User x Aktion x Zeitpunkt.

    Sprint User-Login 2026-07 — Grundlage der "wer hat was genutzt"-
    Auswertung im Monitoring-Tab. Geschrieben nur, wenn die User-Session-
    Middleware eine eingeloggte E-Mail an den Request gehaengt hat
    (``request.state.user_email``); Admin-Zugriffe ueber die
    Passwort-Session erzeugen KEINE Events (Wolfs eigene Klicks wuerden
    die Team-Statistik verfaelschen).

    ``action`` ist ein kurzer Slug (``login``, ``brief_view``,
    ``title_view``, ``forecast_view``, ``landing_view``,
    ``report_download``); ``context`` traegt die Detail-Dimension
    (z. B. ``{"pair": "lionsgate", "iso_week": 29}``). Kein FK auf
    ``app_user`` — Events sollen die Loeschung eines Users ueberleben
    (Audit-Charakter), Join laeuft ueber die E-Mail.
    """
    __tablename__ = "usage_event"
    __table_args__ = _CR_TABLE_ARGS
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(index=True, max_length=255)
    action: str = Field(index=True, max_length=64)
    context: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now, index=True)


class TitleInsightReport(SQLModel, table=True):
    """Persisted title brief — one row per (title_id, iso_year, iso_week).

    Title analogue of ``insight_report`` (C4). Own table per plan: the pair
    table's ``pair_key`` NOT-NULL PK and PairAggregation-shaped ``aggregation``
    column don't fit a title-keyed row. ``aggregation`` is the serialised
    ``TitleAggregation`` dict, ``llm_output`` the serialised ``TitleLLMReport``
    — same JSON-blob approach as ``insight_report`` so Pydantic additions
    survive without a migration.
    """
    __tablename__ = "title_insight_report"
    __table_args__ = _CR_TABLE_ARGS
    title_id: UUID = Field(primary_key=True)
    iso_year: int = Field(primary_key=True)
    iso_week: int = Field(primary_key=True)
    window_days: int
    aggregation: dict = Field(sa_column=Column(JSON, nullable=False))
    llm_output: dict = Field(sa_column=Column(JSON, nullable=False))
    generated_at: datetime = Field(default_factory=utc_now, index=True)
    model: str = Field(max_length=64)
    cost_usd_cents: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


class VideoFeature(SQLModel, table=True):
    """Trailer-Intelligence Stufe 5 — Schnitt-Merkmale eines Videos.

    Eine Zeile je ausgewertetem Video. Gefuellt aus
    ``app.services.video_features.extract_features``; die Tabelle ist
    reiner Speicher und enthaelt keine Logik.

    Drei Entwurfsentscheidungen, die aus dem Stufe-5-Plan folgen und
    sonst spaeter teuer nachzuruesten waeren:

    ``post_id`` ist **optional**. Der empfohlene Machbarkeitsnachweis
    laeuft auf eigenem Material des Trailerhauses (Vorstufe, Abschnitt
    5a) — diese Videos haben keine Post-Zeile, weil sie nie gescraped
    wurden. Eine Pflicht-Fremdschluessel haette genau den Weg
    verbaut, der als erster gegangen werden soll.

    ``pair_key`` traegt den gemeinsamen Titel eines Trailer-und-Cutdown-
    Paares. Er ist der Grund, warum der gepaarte Test ueberhaupt moeglich
    ist, und der ist auf denselben Daten um ein Vielfaches
    trennschaerfer als der ungepaarte (gemessen: z = 3,40 gegen 0,32).
    Ohne diese Spalte waere die Paarung nach dem Import nicht mehr
    rekonstruierbar.

    ``tool`` und ``tool_version`` halten fest, welche Shot-Erkennung die
    Eingabe erzeugt hat. Verschiedene Detektoren schneiden verschieden
    empfindlich; Zahlen aus zwei Werkzeugen ohne diese Angabe zu mischen
    waere derselbe Fehler wie die Confounds aus Stufe 1.

    Die ``Optional``-Felder sind nicht messbar gewesen (zu wenige
    Einstellungen fuer eine Drittel-Aufteilung, keine Audiospur) — NULL
    heisst hier nie null.
    """
    __tablename__ = "video_feature"
    __table_args__ = _CR_TABLE_ARGS
    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # Herkunft
    post_id: Optional[UUID] = Field(
        default=None, foreign_key=_fk("post.id"), index=True
    )
    source: str = Field(default="unknown", max_length=32, index=True)
    external_ref: Optional[str] = Field(default=None, max_length=512)
    pair_key: Optional[str] = Field(default=None, max_length=255, index=True)
    format_class: str = Field(max_length=32, index=True)

    # laufzeitabhaengig — beschreiben, nicht zwischen Klassen vergleichen
    duration_seconds: float
    shot_count: int

    # skalenfrei — vergleichbar
    asl_seconds: float
    median_shot_seconds: float
    shot_length_cv: float
    longest_shot_position: float
    longest_shot_ratio: float
    asl_first_third_ratio: Optional[float] = None
    asl_middle_third_ratio: Optional[float] = None
    asl_last_third_ratio: Optional[float] = None
    rhythm_ratio: Optional[float] = None
    loudness_rise_position: Optional[float] = None
    loudness_peak_position: Optional[float] = None
    # Nur menschlich annotierbar (Plan B, Tap-Along): ein Ohr
    # unterscheidet Musik von Dialog, eine Lautheitskurve nicht.
    music_entry_position: Optional[float] = None

    # Nachvollziehbarkeit
    tool: Optional[str] = Field(default=None, max_length=64)
    tool_version: Optional[str] = Field(default=None, max_length=32)
    notes: list = Field(default_factory=list, sa_column=Column(JSON))
    analyzed_at: datetime = Field(default_factory=utc_now, index=True)
