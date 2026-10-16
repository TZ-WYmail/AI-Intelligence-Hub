import random
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.provider import OpenAIProvider, Provider
from app.collectors.gdelt import GDELTCollector
from app.collectors.rss import RSSCollector
from app.core.config import settings
from app.models import (
    Analysis,
    Article,
    Event,
    EventArticle,
    Industry,
    Metric,
    Notification,
    Source,
    now,
)
from app.notifications.adapters import recipient_key
from app.processing.classifier import classify
from app.processing.event_matcher import matching_event
from app.processing.normalizer import canonical, content_hash
from app.processing.scorer import score
from app.services.queue import enqueue


def collector(source: Source):
    return GDELTCollector() if source.source_type == "gdelt" else RSSCollector()


def collect(session: Session, source_id: str) -> int:
    source = session.get(Source, source_id)
    if not source or not source.enabled:
        return 0
    headers = {"User-Agent": "AIIntelligenceHub/0.1 (personal RSS reader)"}
    if source.etag:
        headers["If-None-Match"] = source.etag
    if source.last_modified:
        headers["If-Modified-Since"] = source.last_modified
    result = collector(source).collect(source.url, headers)
    inserted = 0
    for entry in result.entries:
        try:
            url = canonical(entry["original_url"])
        except ValueError:
            continue
        digest = content_hash(entry["title"], entry["summary"])
        existing = session.scalar(
            select(Article.id).where(
                or_(
                    Article.canonical_url == url,
                    Article.content_hash == digest,
                    (Article.source_id == source.id) & (Article.external_id == entry["external_id"]),
                )
            )
        )
        if existing:
            continue
        try:
            with session.begin_nested():
                article = Article(source_id=source.id, canonical_url=url, content_hash=digest, **entry)
                session.add(article)
                session.flush()
                enqueue(session, "analyze", "analyze:" + article.id, {"article_id": article.id})
                inserted += 1
        except IntegrityError:
            continue
    source.last_checked_at = source.last_success_at = now()
    source.checked_count += 1
    source.success_count += 1
    source.error_count = 0
    source.last_error = None
    source.status = "healthy"
    source.etag = result.headers.get("etag", source.etag)
    source.last_modified = result.headers.get("last-modified", source.last_modified)
    source.next_poll_at = now() + timedelta(seconds=source.poll_interval_seconds + random.uniform(0, 30))
    session.add(
        Metric(metric_name="collected_articles", metric_value=inserted, labels={"source_id": source.id})
    )
    return inserted


def create_notifications(
    session: Session, event: Event, article: Article, source: Source, industries: list[Industry], result: dict
) -> None:
    cfg = settings()
    relevant = [v for v in industries if v.id in article.industry_ids]
    critical = min([v.alert_threshold for v in relevant] or [cfg.critical_alert_threshold])
    level = (
        "P0"
        if event.importance_score >= critical
        else "P1"
        if event.importance_score >= cfg.important_alert_threshold
        else "P2"
    )
    if level == "P2":
        return
    channels = set(c for v in relevant for c in v.channels) if relevant else {"telegram", "feishu"}
    for channel in channels:
        if not getattr(cfg, f"{channel}_enabled"):
            continue
        note = Notification(
            event_id=event.id,
            channel=channel,
            recipient=recipient_key(channel, cfg),
            notification_type=level,
            payload={
                "level": level,
                "title": event.title,
                "score": event.importance_score,
                "verification": event.verification_status,
                "summary": event.summary,
                "industries": [v.name for v in relevant],
                "url": article.original_url,
                "source": source.name,
                "published_at": article.published_at.isoformat(),
                "collected_at": article.collected_at.isoformat(),
                "event_id": event.id,
            },
        )
        session.add(note)
        session.flush()
        # P1 jobs share a durable channel bucket; dispatched as one message by the worker.
        if level == "P1":
            bucket = int(now().timestamp()) // (cfg.p1_batch_minutes * 60)
            when = now().fromtimestamp((bucket + 1) * cfg.p1_batch_minutes * 60, tz=now().tzinfo)
            enqueue(
                session,
                "notify_batch",
                f"batch:{channel}:{bucket}",
                {"channel": channel, "cutoff": when.isoformat()},
                scheduled_at=when,
            )
        else:
            enqueue(session, "notify", "notify:" + note.id, {"notification_id": note.id})


def analyze(session: Session, article_id: str, provider: Provider | None = None) -> None:
    cfg = settings()
    article = session.get(Article, article_id)
    if article is None:
        return
    # Rules create a usable event even when AI fails. AI retry uses the same event.
    source = session.get(Source, article.source_id)
    industries = list(session.scalars(select(Industry).where(Industry.enabled.is_(True))))
    article.industry_ids = classify(article.title, article.summary, industries, source.industry_ids)
    rating, dimensions = score(article, source, article.industry_ids)
    previous = session.scalar(
        select(Analysis).where(Analysis.article_id == article.id).order_by(Analysis.created_at.desc())
    )
    # A resumed durable job can find a request interrupted by process shutdown.
    # Keep its reserved cost/quota because the provider may already have accepted it.
    if previous and previous.provider == "openai-compatible" and previous.status == "started":
        previous.status = "interrupted"
    event = session.get(Event, previous.event_id) if previous and previous.event_id else None
    result = {
        "industry_ids": article.industry_ids,
        "summary_zh": article.summary or article.title,
        "event_type": "news",
        "key_facts": [{"text": article.title, "source_article_id": article.id}],
        "entities": [],
        "importance_score": rating,
        "confidence_score": 0.4,
        "impact_analysis": "规则模式：尚未进行 AI 影响分析。",
        "verification_status": "unverified",
        "requires_follow_up": True,
        "reasoning_summary": "规则维度评分；未独立验证",
        "score_dimensions": dimensions,
    }
    if event is None:
        event = matching_event(session, article)
        linked = event is not None
        if event is None:
            event = Event(title=article.title, summary=result["summary_zh"], importance_score=rating)
            session.add(event)
            session.flush()
        else:
            event.last_updated_at = now()
        session.add(
            EventArticle(
                event_id=event.id,
                article_id=article.id,
                relation_type="similar_title_unverified" if linked else "initial",
            )
        )
        session.add(Analysis(article_id=article.id, event_id=event.id, analysis_json=result))
        article.processing_status = "rules_completed"
        if not linked:
            create_notifications(session, event, article, source, industries, result)
        session.add(
            Metric(
                metric_name="internal_processing_seconds",
                metric_value=(now() - article.collected_at).total_seconds(),
            )
        )
        session.commit()
    if not cfg.ai_enabled or len(article.title + article.summary) < 20:
        article.processing_status = "completed"
        return
    if previous and previous.provider == "openai-compatible" and previous.status == "completed":
        return
    today = now().replace(hour=0, minute=0, second=0, microsecond=0)
    month = today.replace(day=1)
    daily = session.scalar(
        select(func.count())
        .select_from(Analysis)
        .where(Analysis.provider == "openai-compatible", Analysis.created_at >= today)
    )
    spent = session.scalar(
        select(func.coalesce(func.sum(Analysis.estimated_cost), 0)).where(Analysis.created_at >= month)
    )
    # Reserve worst-case prompt/output cost before network call. One worker is supported for budgets.
    reserve = (
        (len(article.title + article.summary[:6000]) * 2 + 6000) * cfg.ai_input_cny_per_million
        + cfg.ai_max_output_tokens * cfg.ai_output_cny_per_million
    ) / 1_000_000
    if daily >= cfg.ai_daily_request_limit or spent + reserve > cfg.monthly_ai_budget_cny:
        article.processing_status = "budget_degraded"
        return
    usage = Analysis(
        article_id=article.id,
        event_id=event.id,
        provider="openai-compatible",
        model=cfg.ai_model,
        analysis_json={},
        estimated_cost=reserve,
        status="started",
    )
    session.add(usage)
    session.commit()
    try:
        ai, input_tokens, output_tokens = (provider or OpenAIProvider(cfg)).analyze(
            {"id": article.id, "title": article.title, "summary": article.summary[:6000]},
            [v.id for v in industries],
        )
    except Exception:
        usage.status = "failed"
        article.processing_status = "ai_retry_wait"
        session.commit()
        raise
    usage.analysis_json = ai.model_dump()
    usage.input_tokens, usage.output_tokens = input_tokens, output_tokens
    usage.estimated_cost = (
        (
            (input_tokens * cfg.ai_input_cny_per_million + output_tokens * cfg.ai_output_cny_per_million)
            / 1_000_000
        )
        if input_tokens or output_tokens
        else reserve
    )
    usage.status = "completed"
    article.industry_ids = sorted(set(article.industry_ids + ai.industry_ids))
    article.processing_status = "completed"
    # AI opinion is displayed separately; cannot alone upgrade notification eligibility or verification.
    relation = session.get(EventArticle, (event.id, article.id))
    if relation and relation.relation_type == "initial":
        event.summary = ai.summary_zh
        event.event_type = ai.event_type
