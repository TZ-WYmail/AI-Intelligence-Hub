import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import DateTime, TypeDecorator


def now() -> datetime:
    return datetime.now(UTC)


def uid() -> str:
    return str(uuid.uuid4())


class UTCDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("timezone required")
            return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value else None


class Base(DeclarativeBase):
    pass


class Industry(Base):
    __tablename__ = "industries"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String, unique=True)
    slug: Mapped[str] = mapped_column(String, unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    exclusions: Mapped[list] = mapped_column(JSON, default=list)
    companies: Mapped[list] = mapped_column(JSON, default=list)
    alert_threshold: Mapped[int] = mapped_column(default=85)
    channels: Mapped[list] = mapped_column(JSON, default=lambda: ["telegram", "feishu"])
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now, onupdate=now)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String, unique=True)
    source_type: Mapped[str] = mapped_column(String, default="rss")
    url: Mapped[str] = mapped_column(Text)
    industry_ids: Mapped[list] = mapped_column(JSON, default=list)
    language: Mapped[str] = mapped_column(default="en")
    country: Mapped[str] = mapped_column(default="")
    priority: Mapped[int] = mapped_column(default=5)
    trust_level: Mapped[float] = mapped_column(default=0.7)
    poll_interval_seconds: Mapped[int] = mapped_column(default=900)
    enabled: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(default="unverified")
    error_count: Mapped[int] = mapped_column(default=0)
    checked_count: Mapped[int] = mapped_column(default=0)
    success_count: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    next_poll_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    etag: Mapped[str | None] = mapped_column(nullable=True)
    last_modified: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now, onupdate=now)


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str | None] = mapped_column(nullable=True)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    original_url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text, unique=True)
    author: Mapped[str] = mapped_column(default="")
    language: Mapped[str] = mapped_column(default="en")
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    publication_time_inferred: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now, index=True)
    content_hash: Mapped[str] = mapped_column(unique=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    bookmarked: Mapped[bool] = mapped_column(default=False, server_default="0", index=True)
    processing_status: Mapped[str] = mapped_column(default="pending")
    industry_ids: Mapped[list] = mapped_column(JSON, default=list)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(default="news")
    version: Mapped[int] = mapped_column(default=1)
    importance_score: Mapped[int] = mapped_column(default=0, index=True)
    verification_status: Mapped[str] = mapped_column(default="unverified")
    status: Mapped[str] = mapped_column(default="active")
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    last_updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class EventArticle(Base):
    __tablename__ = "event_articles"
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), primary_key=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("articles.id"), primary_key=True)
    relation_type: Mapped[str] = mapped_column(default="initial")


class Analysis(Base):
    __tablename__ = "ai_analyses"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    article_id: Mapped[str] = mapped_column(ForeignKey("articles.id"), index=True)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"), nullable=True)
    provider: Mapped[str] = mapped_column(default="rules")
    model: Mapped[str] = mapped_column(default="rules-v1")
    prompt_version: Mapped[str] = mapped_column(default="v1")
    analysis_json: Mapped[dict] = mapped_column(JSON, default=dict)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    estimated_cost: Mapped[float] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(default="completed")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now, index=True)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_queue", "status", "scheduled_at"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    job_type: Mapped[str] = mapped_column()
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    dedup_key: Mapped[str | None] = mapped_column(unique=True, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(default="pending")
    attempt_count: Mapped[int] = mapped_column(default=0)
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(nullable=True)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("event_id", "event_version", "channel", "recipient", "notification_type"),
    )
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"))
    event_version: Mapped[int] = mapped_column(default=1)
    channel: Mapped[str] = mapped_column()
    recipient: Mapped[str] = mapped_column(default="configured")
    notification_type: Mapped[str] = mapped_column(default="P0")
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(default="pending")
    attempt_count: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Metric(Base):
    __tablename__ = "system_metrics"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    metric_name: Mapped[str] = mapped_column(index=True)
    metric_value: Mapped[float] = mapped_column()
    labels: Mapped[dict] = mapped_column(JSON, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class Digest(Base):
    __tablename__ = "digests"
    __table_args__ = (UniqueConstraint("period_start", "period_end"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    title: Mapped[str] = mapped_column()
    period_start: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    period_end: Mapped[datetime] = mapped_column(UTCDateTime)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    article_count: Mapped[int] = mapped_column(default=0)
    markdown: Mapped[str] = mapped_column(Text)
