from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4
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
