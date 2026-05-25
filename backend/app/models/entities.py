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
    priority: Priority = Priority.B
    active: bool = True
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
