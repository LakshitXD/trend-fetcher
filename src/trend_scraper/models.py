from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

SHORTS_MAX_SECONDS = 180


class Format(StrEnum):
    SHORTS = "shorts"
    LONG = "long"


class Product(BaseModel):
    name: str
    description: str
    keywords: list[str] = []
    category: str = ""
    audience: str = ""


class Market(BaseModel):
    """One cell of the run matrix: where and in which format we look."""

    region: str | None  # ISO 3166 code like "IN"; None means global
    language: str | None = None  # relevanceLanguage hint like "hi" or "en"
    format: Format

    @property
    def label(self) -> str:
        return f"{self.region or 'global'}/{self.format}"


class Video(BaseModel):
    id: str
    title: str
    description: str = ""
    channel_id: str
    channel_title: str = ""
    channel_subscribers: int | None = None
    published_at: datetime
    duration_s: int
    views: int
    likes: int | None = None
    comments: int | None = None
    tags: list[str] = []
    thumbnail_url: str = ""
    source: str  # "youtube_api" or "yt_dlp"

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"

    @property
    def format(self) -> Format:
        return Format.SHORTS if self.duration_s <= SHORTS_MAX_SECONDS else Format.LONG


class TrendScore(BaseModel):
    views_per_day: float
    subscriber_multiple: float | None  # views / channel subscribers
    engagement_rate: float | None  # (likes + comments) / views
    likely_promoted: bool = False  # huge views with almost no engagement: usually a paid ad
    score: float


class Candidate(BaseModel):
    video: Video
    market: str
    query: str
    trend: TrendScore
    relevant: bool | None = None
    relevance_reason: str = ""


class Evidence(BaseModel):
    """What a depth tier gathered about a video. `missing` names what it could not get."""

    tier: str
    text: str
    missing: list[str] = []
    seconds: float = 0.0


class VideoAnalysis(BaseModel):
    video_id: str
    tier: str
    hook: str = Field(description="What happens in the first 3 seconds (Shorts) or 15 seconds (long-form) to stop the scroll")
    format_type: str = Field(description="e.g. unboxing, before/after, POV skit, listicle, review, tutorial, comparison, storytime")
    structure: list[str] = Field(description="Beat-by-beat outline of the video")
    pacing: str = Field(description="Cut frequency, length, energy; say 'unknown' if the evidence cannot show it")
    visual_style: str = Field(description="Framing, text overlays, setting; say 'unknown' if the evidence cannot show it")
    product_integration: str = Field(description="How a product appears, is shown, or is pitched; 'none' if absent")
    call_to_action: str
    title_pattern: str
    why_it_works: str
    confidence: str = Field(description="low, medium or high, given how much the evidence showed")


class Pattern(BaseModel):
    name: str
    description: str
    evidence_video_ids: list[str] = Field(description="IDs of the analysed videos that show this pattern")
    fit_for_product: str


class ContentIdea(BaseModel):
    title: str
    format: Format
    hook: str
    outline: list[str]
    based_on_patterns: list[str]
    why_it_fits: str


class Brief(BaseModel):
    summary: str
    patterns: list[Pattern]
    ideas: list[ContentIdea]
    avoid: list[str] = Field(description="What the trending videos suggest not to do")
