import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.core.config import settings
from app.core.security import UnsafeURL, public_url, validate_url
from app.db import get_session
from app.models import (
    Analysis,
    Article,
    Event,
    EventArticle,
    Industry,
    Job,
    Metric,
    Notification,
    Source,
    now,
)
from app.notifications.adapters import recipient_key
from app.schemas import IndustryInput, ReadingPatch, ReportRequest, SourceInput
from app.services.pipeline import collector
from app.services.queue import JobRetryRejected, enqueue, retry_failed_job
from app.services.source_health import publication_health

app = FastAPI(title="AI Intelligence Hub", version="0.1.0", docs_url="/docs")
DB = Annotated[DBSession, Depends(get_session)]
_requests: dict[str, deque] = defaultdict(deque)


def authorize(request: Request, x_api_key: str = Header(default="")) -> None:
    key = settings().admin_api_key
    if not key or not secrets.compare_digest(key, x_api_key):
        raise HTTPException(401, "需要管理员 API Key")
    # Local process rate limiter; network reverse proxy should enforce production global quotas.
    identity = request.client.host if request.client else "local"
    queue = _requests[identity]
    clock = time.monotonic()
    while queue and queue[0] < clock - 60:
        queue.popleft()
    if len(queue) >= 240:
        raise HTTPException(429, "请求过于频繁", headers={"Retry-After": "60"})
    queue.append(clock)


@app.exception_handler(ValidationError)
async def validation_error(request, exc):
    return JSONResponse(status_code=422, content={"detail": "配置字段验证失败"})


@app.exception_handler(IntegrityError)
async def integrity_error(request, exc):
    return JSONResponse(status_code=409, content={"detail": "名称或唯一字段已存在，或数据仍被引用"})


@app.exception_handler(UnsafeURL)
async def unsafe_error(request, exc):
    return JSONResponse(status_code=422, content={"detail": "仅允许可解析的公网 HTTP(S) 地址"})


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    return JSONResponse(status_code=500, content={"detail": "内部错误，请查看任务状态"})


def row(item) -> dict:
    data = {c.name: getattr(item, c.name) for c in item.__table__.columns}
    if isinstance(item, Source):
        data["url"] = (
            public_url(item.url) if "key=" in item.url.lower() or "token=" in item.url.lower() else item.url
        )
    return data


def required(db: DBSession, model, identity: str):
    item = db.get(model, identity)
    if not item:
        raise HTTPException(404, "记录不存在")
    return item


def validate_industries(db, ids):
    if any(db.get(Industry, identity) is None for identity in ids):
        raise HTTPException(422, "行业 ID 不存在")


def commit(db, item):
    db.add(item)
    db.commit()
    return row(item)


@app.get("/api/v1/health/live")
def live():
    return {"status": "alive"}


@app.get("/api/v1/health/ready")
def ready(db: DB):
    db.execute(select(func.count()).select_from(Industry))
    return {"status": "ready", "auth_configured": bool(settings().admin_api_key)}


AUTH = [Depends(authorize)]


@app.get("/api/v1/industries", dependencies=AUTH)
def industries(db: DB):
    return [row(v) for v in db.scalars(select(Industry))]


@app.post("/api/v1/industries", dependencies=AUTH, status_code=201)
def add_industry(body: IndustryInput, db: DB):
    return commit(db, Industry(**body.model_dump()))


@app.patch("/api/v1/industries/{identity}", dependencies=AUTH)
def edit_industry(identity: str, body: dict, db: DB):
    item = required(db, Industry, identity)
    merged = IndustryInput.model_validate(
        {**{k: getattr(item, k) for k in IndustryInput.model_fields}, **body}
    )
    for key in body:
        if key not in IndustryInput.model_fields:
            raise HTTPException(422, "未知字段")
        setattr(item, key, getattr(merged, key))
    return commit(db, item)


@app.delete("/api/v1/industries/{identity}", dependencies=AUTH)
def delete_industry(identity: str, db: DB):
    required(db, Industry, identity)
    # JSON lists are portable in MVP; cleanup references atomically.
    for model in [Source, Article]:
        for item in db.scalars(select(model)):
            if identity in item.industry_ids:
                item.industry_ids = [v for v in item.industry_ids if v != identity]
    db.delete(db.get(Industry, identity))
    db.commit()
    return {"deleted": identity}


@app.get("/api/v1/sources", dependencies=AUTH)
def sources(db: DB):
    health = publication_health(db)
    return [
        {
            **row(v),
            **health.get(
                v.id,
                {
                    "article_count": 0,
                    "inferred_date_count": 0,
                    "future_date_count": 0,
                    "latest_published_at": None,
                    "content_age_days": None,
                    "freshness_status": "no_articles",
                },
            ),
        }
        for v in db.scalars(select(Source))
    ]


@app.post("/api/v1/sources", dependencies=AUTH, status_code=201)
def add_source(body: SourceInput, db: DB):
    validate_url(body.url)
    validate_industries(db, body.industry_ids)
    return commit(db, Source(**body.model_dump()))


@app.get("/api/v1/sources/{identity}", dependencies=AUTH)
def get_source(identity: str, db: DB):
    return row(required(db, Source, identity))


@app.patch("/api/v1/sources/{identity}", dependencies=AUTH)
def edit_source(identity: str, body: dict, db: DB):
    item = required(db, Source, identity)
    from pydantic import ValidationError

    try:
        merged = SourceInput.model_validate(
            {**{k: getattr(item, k) for k in SourceInput.model_fields}, **body}
        )
    except ValidationError:
        raise HTTPException(422, "来源配置无效") from None
    validate_url(merged.url)
    validate_industries(db, merged.industry_ids)
    if merged.url != item.url:
        item.etag = item.last_modified = None
    for key in body:
        if key not in SourceInput.model_fields:
            raise HTTPException(422, "未知字段")
        setattr(item, key, getattr(merged, key))
    return commit(db, item)


@app.delete("/api/v1/sources/{identity}", dependencies=AUTH)
def delete_source(identity: str, db: DB):
    item = required(db, Source, identity)
    # Keep source evidence for archived articles; deletion is a documented soft delete.
    item.enabled, item.status = False, "deleted"
    return commit(db, item)


@app.post("/api/v1/sources/{identity}/run", dependencies=AUTH)
def run_source(identity: str, db: DB):
    item = required(db, Source, identity)
    if not item.enabled:
        raise HTTPException(409, "请先启用来源")
    job = enqueue(db, "collect", "collect:" + identity, source_id=identity)
    db.commit()
    return {"queued": bool(job), "job_id": job.id if job else None}


@app.post("/api/v1/sources/{identity}/test", dependencies=AUTH)
def test_source(identity: str, db: DB):
    item = required(db, Source, identity)
    try:
        result = collector(item).collect(item.url, {})
        return {"ok": True, "entries": len(result.entries)}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


@app.get("/api/v1/articles/search", dependencies=AUTH)
@app.get("/api/v1/articles", dependencies=AUTH)
def articles(
    db: DB,
    q: str = Query("", max_length=200),
    industry_id: str | None = None,
    source_id: str | None = None,
    language: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    sort: str = Query("published_at", pattern="^(published_at|collected_at|importance_score)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    analysis_status: str = Query("all", pattern="^(all|ai|rules|failed)$"),
    reading_state: str = Query("all", pattern="^(all|unread|read|saved)$"),
):
    query = select(Article)
    if reading_state == "unread":
        query = query.where(Article.read_at.is_(None))
    elif reading_state == "read":
        query = query.where(Article.read_at.is_not(None))
    elif reading_state == "saved":
        query = query.where(Article.bookmarked.is_(True))
    from sqlalchemy import exists

    completed_ai = exists(
        select(Analysis.id).where(
            Analysis.article_id == Article.id,
            Analysis.provider == "openai-compatible",
            Analysis.status == "completed",
        )
    )
    if analysis_status == "ai":
        query = query.where(completed_ai)
    elif analysis_status == "rules":
        query = query.where(~completed_ai)
    elif analysis_status == "failed":
        query = query.where(Article.processing_status == "ai_retry_wait")
    if q:
        needle = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(
            or_(
                Article.title.ilike(needle, escape="\\"),
                Article.summary.ilike(needle, escape="\\"),
                Article.content.ilike(needle, escape="\\"),
                exists(
                    select(Analysis.id).where(
                        Analysis.article_id == Article.id,
                        Analysis.provider == "openai-compatible",
                        Analysis.status == "completed",
                        or_(
                            func.json_extract(Analysis.analysis_json, "$.summary_zh").ilike(
                                needle, escape="\\"
                            ),
                            func.json_extract(Analysis.analysis_json, "$.impact_analysis").ilike(
                                needle, escape="\\"
                            ),
                        ),
                    )
                ),
            )
        )
    if source_id:
        query = query.where(Article.source_id == source_id)
    if language:
        query = query.where(Article.language == language)
    for value, op in [(start, Article.published_at.__ge__), (end, Article.published_at.__le__)]:
        if value:
            if value.tzinfo is None:
                raise HTTPException(422, "时间参数必须包含时区")
            query = query.where(op(value))
    if industry_id:
        # JSON array containment on SQLite via json_each, avoiding substring collisions.
        from sqlalchemy import exists

        terms = func.json_each(Article.industry_ids).table_valued("value")
        query = query.where(exists(select(1).select_from(terms).where(terms.c.value == industry_id)))
    if sort == "importance_score":
        query = query.join(EventArticle, EventArticle.article_id == Article.id).join(
            Event, Event.id == EventArticle.event_id
        )
        order = Event.importance_score
    else:
        order = getattr(Article, sort)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    page = list(db.scalars(query.order_by(order.desc(), Article.id).offset(offset).limit(limit)))
    ids = [article.id for article in page]
    event_map = {
        article_id: event
        for article_id, event in db.execute(
            select(EventArticle.article_id, Event)
            .join(Event, Event.id == EventArticle.event_id)
            .where(EventArticle.article_id.in_(ids))
        )
    }
    ai_map = {}
    for analysis in db.scalars(
        select(Analysis)
        .where(
            Analysis.article_id.in_(ids),
            Analysis.provider == "openai-compatible",
            Analysis.status == "completed",
        )
        .order_by(Analysis.created_at.desc(), Analysis.id)
    ):
        ai_map.setdefault(analysis.article_id, analysis)
    items = []
    for article in page:
        item = row(article)
        item.pop("raw_payload", None)
        event = event_map.get(article.id)
        analysis = ai_map.get(article.id)
        item.update(
            importance_score=event.importance_score if event else 0,
            verification_status=event.verification_status if event else "unverified",
            event_id=event.id if event else None,
            ai_summary_zh=analysis.analysis_json.get("summary_zh") if analysis else None,
            ai_model=analysis.model if analysis else None,
        )
        items.append(item)
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@app.get("/api/v1/articles/{identity}", dependencies=AUTH)
def article_detail(identity: str, db: DB):
    item = row(required(db, Article, identity))
    item["analyses"] = [
        row(v)
        for v in db.scalars(
            select(Analysis).where(Analysis.article_id == identity).order_by(Analysis.created_at.desc())
        )
    ]
    return item


@app.patch("/api/v1/articles/{identity}/reading", dependencies=AUTH)
def update_reading(identity: str, body: ReadingPatch, db: DB):
    article = required(db, Article, identity)
    if body.read is None and body.bookmarked is None:
        raise HTTPException(422, "请提供阅读或收藏状态")
    if body.read is not None:
        article.read_at = (article.read_at or now()) if body.read else None
    if body.bookmarked is not None:
        article.bookmarked = body.bookmarked
    db.commit()
    return {"id": article.id, "read_at": article.read_at, "bookmarked": article.bookmarked}


@app.post("/api/v1/articles/{identity}/analyze", dependencies=AUTH)
def analyze_article(identity: str, db: DB):
    required(db, Article, identity)
    if not settings().ai_enabled:
        raise HTTPException(409, "AI 未启用，请先完成服务配置")
    done = db.scalar(
        select(Analysis.id).where(
            Analysis.article_id == identity,
            Analysis.provider == "openai-compatible",
            Analysis.status == "completed",
        )
    )
    if done:
        return {"queued": False, "already_analyzed": True, "job_id": None}
    job = enqueue(db, "analyze", "analyze:" + identity, {"article_id": identity})
    db.commit()
    return {"queued": bool(job), "already_analyzed": False, "job_id": job.id if job else None}


@app.get("/api/v1/events", dependencies=AUTH)
def events(
    db: DB,
    min_score: int = Query(0, ge=0, le=100),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    query = select(Event).where(Event.importance_score >= min_score)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    page = list(db.scalars(query.order_by(Event.last_updated_at.desc()).offset(offset).limit(limit)))
    counts = dict(
        db.execute(
            select(EventArticle.event_id, func.count())
            .where(EventArticle.event_id.in_([v.id for v in page]))
            .group_by(EventArticle.event_id)
        ).all()
    )
    return {"items": [{**row(v), "article_count": counts.get(v.id, 0)} for v in page], "total": total}


@app.get("/api/v1/events/{identity}", dependencies=AUTH)
def event_detail(identity: str, db: DB):
    item = row(required(db, Event, identity))
    item["articles"] = event_articles(identity, db)
    item["notifications"] = [
        row(v) for v in db.scalars(select(Notification).where(Notification.event_id == identity))
    ]
    return item


@app.get("/api/v1/events/{identity}/articles", dependencies=AUTH)
def event_articles(identity: str, db: DB):
    required(db, Event, identity)
    return [
        {
            **{k: v for k, v in row(article).items() if k != "raw_payload"},
            "source_name": source.name,
            "relation_type": relation.relation_type,
        }
        for article, source, relation in db.execute(
            select(Article, Source, EventArticle)
            .join(EventArticle, Article.id == EventArticle.article_id)
            .join(Source, Source.id == Article.source_id)
            .where(EventArticle.event_id == identity)
            .order_by(Article.published_at, Article.collected_at, Article.id)
        )
    ]


@app.get("/api/v1/notifications/channels", dependencies=AUTH)
def channels():
    return [
        {"channel": name, "enabled": getattr(settings(), f"{name}_enabled")}
        for name in ["telegram", "feishu"]
    ]


@app.get("/api/v1/notifications", dependencies=AUTH)
def notifications(db: DB, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    return [
        row(v)
        for v in db.scalars(
            select(Notification).order_by(Notification.created_at.desc()).offset(offset).limit(limit)
        )
    ]


@app.post("/api/v1/notifications/test", dependencies=AUTH)
def notification_test(db: DB, channel: str = Query(pattern="^(telegram|feishu)$")):
    if not getattr(settings(), f"{channel}_enabled"):
        raise HTTPException(409, "渠道未配置")
    event = Event(
        title="AIH 渠道测试（非真实新闻）", summary="管理员手动通知测试", importance_score=0, status="test"
    )
    db.add(event)
    db.flush()
    note = Notification(
        event_id=event.id,
        channel=channel,
        recipient=recipient_key(channel, settings()),
        notification_type="test",
        payload={
            "level": "TEST",
            "title": event.title,
            "summary": event.summary,
            "score": 0,
            "url": "https://github.com/TZ-WYmail/AI-Intelligence-Hub",
        },
    )
    db.add(note)
    db.flush()
    enqueue(db, "notify", "notify:" + note.id, {"notification_id": note.id})
    db.commit()
    return {"queued": True, "notification_id": note.id}


@app.get("/api/v1/jobs", dependencies=AUTH)
def jobs(
    db: DB,
    status: str = Query("all", pattern="^(all|pending|retry_wait|running|completed|failed)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    query = select(Job)
    if status != "all":
        query = query.where(Job.status == status)
    return [row(v) for v in db.scalars(query.order_by(Job.scheduled_at.desc()).offset(offset).limit(limit))]


@app.post("/api/v1/jobs/{identity}/retry", dependencies=AUTH)
def retry_job(identity: str, db: DB):
    job = required(db, Job, identity)
    try:
        queued_job, queued = retry_failed_job(db, job)
    except JobRetryRejected as exc:
        raise HTTPException(exc.status_code, exc.detail, headers={"X-Error-Code": exc.code}) from None
    return {
        "queued": queued,
        "job_id": queued_job.id,
        "job_type": queued_job.job_type,
        "status": queued_job.status,
        "deduplicated": not queued,
    }


@app.get("/api/v1/metrics", dependencies=AUTH)
def metrics(db: DB, limit: int = Query(100, ge=1, le=1000)):
    return [row(v) for v in db.scalars(select(Metric).order_by(Metric.recorded_at.desc()).limit(limit))]


@app.get("/api/v1/stats/overview", dependencies=AUTH)
def overview(db: DB):
    since = now() - timedelta(hours=24)

    def count(model, *conditions):
        return db.scalar(select(func.count()).select_from(model).where(*conditions))

    checked = db.scalar(select(func.coalesce(func.sum(Source.checked_count), 0)))
    success = db.scalar(select(func.coalesce(func.sum(Source.success_count), 0)))
    return {
        "articles_24h": count(Article, Article.collected_at >= since),
        "events_24h": count(Event, Event.first_seen_at >= since, Event.status != "test"),
        "critical_24h": count(
            Event, Event.first_seen_at >= since, Event.importance_score >= settings().critical_alert_threshold
        ),
        "active_sources": count(Source, Source.enabled.is_(True)),
        "collection_success_rate": success / checked if checked else None,
        "last_collection": db.scalar(select(func.max(Source.last_success_at))),
        "backlog": count(Job, Job.status.in_(["pending", "retry_wait", "running"])),
        "ai_calls": count(Analysis, Analysis.provider == "openai-compatible"),
        "ai_input_tokens": db.scalar(select(func.coalesce(func.sum(Analysis.input_tokens), 0))),
        "ai_output_tokens": db.scalar(select(func.coalesce(func.sum(Analysis.output_tokens), 0))),
        "ai_estimated_cost_cny": db.scalar(select(func.coalesce(func.sum(Analysis.estimated_cost), 0))),
        "scheduler_last_seen": db.scalar(
            select(func.max(Metric.recorded_at)).where(Metric.metric_name == "scheduler_heartbeat")
        ),
        "worker_last_seen": db.scalar(
            select(func.max(Metric.recorded_at)).where(Metric.metric_name == "worker_heartbeat")
        ),
        "timezone": settings().app_timezone,
        "ai_enabled": settings().ai_enabled,
    }


@app.get("/api/v1/stats/analytics", dependencies=AUTH)
def visual_analytics(db: DB, days: int = Query(7, ge=1, le=30)):
    from app.services.analytics import analytics

    return analytics(db, days)


@app.get("/api/v1/settings", dependencies=AUTH)
def safe_settings():
    cfg = settings()
    return {
        "timezone": cfg.app_timezone,
        "ai_enabled": cfg.ai_enabled,
        "ai_model": cfg.ai_model,
        "ai_thinking_mode": cfg.ai_thinking_mode,
        "ai_daily_request_limit": cfg.ai_daily_request_limit,
        "ai_timeout": cfg.ai_timeout,
        "backup_enabled": cfg.backup_enabled,
        "backup_keep_count": cfg.backup_keep_count,
        "backup_interval_hours": cfg.backup_interval_hours,
        "daily_digest_enabled": cfg.daily_digest_enabled,
        "daily_digest_hour": cfg.daily_digest_hour,
        "monthly_ai_budget_cny": cfg.monthly_ai_budget_cny,
        "critical_alert_threshold": cfg.critical_alert_threshold,
        "important_alert_threshold": cfg.important_alert_threshold,
        "raw_retention_days": cfg.raw_retention_days,
        "notification_retention_days": cfg.notification_retention_days,
        "ai_key_configured": bool(cfg.ai_api_key),
        "channels": channels(),
    }


@app.post("/api/v1/reports", dependencies=AUTH)
def report(body: ReportRequest, db: DB):
    if body.start.tzinfo is None or body.end.tzinfo is None or body.end <= body.start:
        raise HTTPException(422, "请提供带时区且有效的时间范围")
    data = articles(
        db,
        q="",
        industry_id=body.industry_id,
        source_id=None,
        language=None,
        start=body.start,
        end=body.end,
        sort="importance_score",
        offset=0,
        limit=200,
        analysis_status="all",
        reading_state="all",
    )
    from app.services.maintenance import markdown_text, markdown_url

    lines = [
        "# AIH 情报简报",
        f"来源发布时间：{body.start.isoformat()} 至 {body.end.isoformat()}",
        f"生成时间：{now().isoformat()}",
        "规则汇总；AI 观点不代表已验证事实。最多列出 200 条。",
        "",
    ]
    for item in data["items"]:
        lines += [
            f"- [{markdown_text(item['title'])}]({markdown_url(item['original_url'])}) · {item['importance_score']}分 · unverified",
            f"  {markdown_text((item.get('ai_summary_zh') or item['summary'])[:500])}",
        ]
    return {"markdown": "\n".join(lines), "total": data["total"]}


@app.get("/api/v1/reports", dependencies=AUTH)
def list_reports(db: DB, limit: int = Query(30, ge=1, le=100)):
    from app.models import Digest

    return [
        {k: v for k, v in row(item).items() if k != "markdown"}
        for item in db.scalars(select(Digest).order_by(Digest.period_start.desc()).limit(limit))
    ]


@app.post("/api/v1/reports/daily", dependencies=AUTH)
def queue_daily_report(db: DB):
    from zoneinfo import ZoneInfo

    from app.models import Digest

    local = now().astimezone(ZoneInfo(settings().app_timezone))
    end = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=1)
    existing = db.scalar(select(Digest).where(Digest.period_start == start, Digest.period_end == end))
    if existing:
        return {"queued": False, "report_id": existing.id}
    job = enqueue(
        db,
        "daily_digest",
        "digest:" + start.isoformat(),
        {"start": start.isoformat(), "end": end.isoformat()},
    )
    db.commit()
    return {"queued": bool(job), "report_id": None}


@app.get("/api/v1/reports/{identity}", dependencies=AUTH)
def get_report(identity: str, db: DB):
    from app.models import Digest

    return row(required(db, Digest, identity))
