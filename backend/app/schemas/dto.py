from datetime import date, datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel
from app.models.entities import AssetType, Market, Priority, ReviewStatus, ReportStatus


class ChannelCreate(BaseModel):
    name: str
    url: str
    handle: Optional[str] = None
    market: Market = Market.UNKNOWN
    channel_type: Optional[str] = None
    priority: Priority = Priority.B
    active: bool = True
    mvp: bool = False
    notes: Optional[str] = None


class ChannelUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    handle: Optional[str] = None
    market: Optional[Market] = None
    channel_type: Optional[str] = None
    priority: Optional[Priority] = None
    active: Optional[bool] = None
    mvp: Optional[bool] = None
    notes: Optional[str] = None


class TitleCreate(BaseModel):
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
    keywords: list[str] = []


class KeywordCreate(BaseModel):
    keyword: str
    keyword_type: str = "keyword"


class ManualPostImport(BaseModel):
    channel_id: UUID
    post_url: str
    title_id: Optional[UUID] = None
    published_at: Optional[datetime] = None
    caption: Optional[str] = None
    media_type: Optional[str] = None
    asset_type: AssetType = AssetType.UNKNOWN
    screenshot_url: Optional[str] = None
    ocr_text: Optional[str] = None


class AnalyzeInstagramLinkRequest(BaseModel):
    post_url: str
    channel_id: Optional[UUID] = None
    title_id: Optional[UUID] = None
    caption_hint: Optional[str] = None
    asset_type_hint: AssetType = AssetType.UNKNOWN


class ApifyMonitorRequest(BaseModel):
    channel_ids: list[UUID] = []
    max_channels: int = 5
    results_limit_per_channel: int = 5
    only_whitelist_matches: bool = True


class AssetReviewUpdate(BaseModel):
    review_status: ReviewStatus
    include_in_report: bool = False
    is_highlight: bool = False
    curator_note: Optional[str] = None


class GenerateWeeklyReportRequest(BaseModel):
    week_start: date
    week_end: date
    include_only_reviewed: bool = True


class ReportStatusUpdate(BaseModel):
    status: ReportStatus
