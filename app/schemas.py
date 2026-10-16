from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IndustryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(pattern=r"^[a-z0-9_-]{1,60}$")
    description: str = Field("", max_length=2000)
    enabled: bool = True
    keywords: list[str] = Field(default_factory=list, max_length=200)
    exclusions: list[str] = Field(default_factory=list, max_length=200)
    companies: list[str] = Field(default_factory=list, max_length=200)
    alert_threshold: int = Field(85, ge=0, le=100)
    channels: list[Literal["telegram", "feishu"]] = Field(default_factory=lambda: ["telegram", "feishu"])

    @field_validator("keywords", "exclusions", "companies")
    @classmethod
    def terms(cls, values):
        if any(not v.strip() or len(v) > 200 for v in values):
            raise ValueError("Keywords must be nonempty and at most 200 chars")
        return values


class SourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    source_type: Literal["rss", "gdelt"] = "rss"
    url: str = Field(max_length=2048)
    industry_ids: list[str] = Field(default_factory=list, max_length=50)
    language: str = Field("en", max_length=30)
    country: str = Field("", max_length=50)
    priority: int = Field(5, ge=1, le=10)
    trust_level: float = Field(0.7, ge=0, le=1)
    poll_interval_seconds: int = Field(900, ge=120, le=86400)
    enabled: bool = False


class Fact(BaseModel):
    text: str = Field(max_length=1000)
    source_article_id: str


class AIResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    industry_ids: list[str] = Field(max_length=20)
    event_type: str = Field(max_length=80)
    summary_zh: str = Field(max_length=5000)
    key_facts: list[Fact] = Field(max_length=20)
    entities: list[str] = Field(max_length=50)
    importance_score: int = Field(ge=0, le=100)
    confidence_score: float = Field(ge=0, le=1)
    impact_analysis: str = Field(max_length=5000)
    verification_status: Literal["confirmed", "partially_confirmed", "unverified", "disputed"]
    requires_follow_up: bool
    reasoning_summary: str = Field(max_length=2000)


class ReportRequest(BaseModel):
    start: datetime
    end: datetime
    industry_id: str | None = None
