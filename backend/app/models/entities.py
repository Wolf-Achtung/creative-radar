from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4
from sqlmodel import Field, SQLModel, Relationship, Column, JSON


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


class Channel(SQLModel, table=True):
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
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    title_original: str
    title_local: Optional[str] = None
    franchise: Optional[str] = None
    content_type: str = "Film"
    market_relevance: Market = Market.MIXED
    release_date_de: Optional[date] = None
    release_date_us: Optional[date] = None
    priority: Priority = Priority.B
    active: bool = True
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    keywords: list["TitleKeyword"] = Relationship(back_populates="title")
    assets: list["Asset"] = Relationship(back_populates="title")


class TitleKeyword(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    title_id: UUID = Field(foreign_key="title.id")
    keyword: str
    keyword_type: str = "keyword"
    active: bool = True

    title: Title = Relationship(back_populates="keywords")


class Post(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    channel_id: UUID = Field(foreign_key="channel.id")
    platform: str = "instagram"
    post_url: str = Field(unique=True, index=True)
    published_at: Optional[datetime] = None
    detected_at: datetime = Field(default_factory=utc_now)
    caption: Optional[str] = None
    raw_payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    visible_likes: Optional[int] = None
    visible_comments: Optional[int] = None
    visible_views: Optional[int] = None
    media_type: Optional[str] = None
    status: str = "new"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    channel: Channel = Relationship(back_populates="posts")
    assets: list["Asset"] = Relationship(back_populates="post")


class Asset(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    post_id: UUID = Field(foreign_key="post.id")
    title_id: Optional[UUID] = Field(default=None, foreign_key="title.id")
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
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    post: Post = Relationship(back_populates="assets")
    title: Optional[Title] = Relationship(back_populates="assets")


class WeeklyReport(SQLModel, table=True):
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
