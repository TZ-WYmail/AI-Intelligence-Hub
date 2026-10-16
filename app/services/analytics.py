"""Read-only article analytics. Region matching is explicit text evidence, never geocoding."""

import re
from collections import Counter, defaultdict
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Article, Event, EventArticle, Industry, Source, now


@lru_cache(maxsize=1)
def regions() -> list[dict]:
    return yaml.safe_load(Path("config/regions.yaml").read_text())


@lru_cache(maxsize=1)
def region_patterns() -> list[tuple[dict, list[tuple[str, re.Pattern]]]]:
    return [
        (
            region,
            [
                (
                    alias,
                    re.compile(
                        r"(?<!\w)" + re.escape(alias) + r"(?!\w)" if alias.isascii() else re.escape(alias),
                        re.I,
                    ),
                )
                for alias in region["aliases"]
            ],
        )
        for region in regions()
    ]


def mentioned_regions(text: str) -> list[tuple[str, str]]:
    # Bound work on untrusted text; do not interpret company domicile as event geography.
    text = text[:16000]
    matches = [
        (region["id"], alias, match.start(), match.end())
        for region, patterns in region_patterns()
        for alias, pattern in patterns
        for match in pattern.finditer(text)
    ]
    result = {}
    for identity, alias, start, end in matches:
        # Prefer the full name: 印度尼西亚 must not also count as 印度.
        if not any(
            other_start <= start and other_end >= end and other_end - other_start > end - start
            for _, _, other_start, other_end in matches
        ):
            result.setdefault(identity, alias)
    return list(result.items())


def analytics(db: Session, days: int) -> dict:
    cfg = settings()
    clock = now()
    since = clock - timedelta(days=days)
    tz = ZoneInfo(cfg.app_timezone)
    step = timedelta(hours=1) if days == 1 else timedelta(days=1)
    fmt = "%m-%d %H:00" if days == 1 else "%m-%d"
    cursor = since.astimezone(tz).replace(minute=0, second=0, microsecond=0)
    if days != 1:
        cursor = cursor.replace(hour=0)
    buckets = []
    while cursor <= clock.astimezone(tz):
        buckets.append(cursor.strftime(fmt))
        cursor += step
    categories = {item.id: item.name for item in db.scalars(select(Industry))}
    categories["uncategorized"] = "未分类"
    scores = (
        select(EventArticle.article_id, func.max(Event.importance_score).label("score"))
        .join(Event, Event.id == EventArticle.event_id)
        .group_by(EventArticle.article_id)
        .subquery()
    )
    rows = db.execute(
        select(
            Article.id,
            Article.title,
            Article.summary,
            Article.original_url,
            Article.industry_ids,
            Article.language,
            Article.collected_at,
            Source.name,
            Source.country,
            scores.c.score,
        )
        .join(Source, Source.id == Article.source_id)
        .outerjoin(scores, scores.c.article_id == Article.id)
        .where(Article.collected_at >= since, Article.collected_at <= clock)
        .order_by(Article.collected_at.desc())
        .execution_options(yield_per=500)
    )
    industry_counts, source_counts, languages, importance, timeline = (Counter() for _ in range(5))
    industry_time = defaultdict(Counter)
    region_counts, source_regions = Counter(), Counter()
    samples = defaultdict(list)
    matched, source_mapped, total = 0, 0, 0
    valid_regions = {item["id"] for item in regions()}
    for identity, title, summary, url, ids, language, collected, source, country, rating in rows:
        total += 1
        bucket = collected.astimezone(tz).strftime(fmt)
        timeline[bucket] += 1
        source_counts[source] += 1
        languages[language or "unknown"] += 1
        label = (
            "unscored"
            if rating is None
            else "critical"
            if rating >= cfg.critical_alert_threshold
            else "important"
            if rating >= cfg.important_alert_threshold
            else "ordinary"
        )
        importance[label] += 1
        for industry in set(ids or ["uncategorized"]):
            industry_counts[industry] += 1
            industry_time[industry][bucket] += 1
        mentions = mentioned_regions(title + " " + summary)
        if mentions:
            matched += 1
        for region_id, alias in mentions:
            region_counts[region_id] += 1
            if len(samples[region_id]) < 3:
                samples[region_id].append(
                    {
                        "article_id": identity,
                        "title": title[:240],
                        "url": url,
                        "source": source,
                        "matched_term": alias,
                    }
                )
        country = (country or "").upper()
        if country in valid_regions:
            source_regions[country] += 1
            source_mapped += 1
    return {
        "start": since,
        "end": clock,
        "days": days,
        "timezone": cfg.app_timezone,
        "time_basis": "collected_at",
        "total_articles": total,
        "mention_matched_articles": matched,
        "mention_unknown_articles": total - matched,
        "source_mapped_articles": source_mapped,
        "source_unknown_articles": total - source_mapped,
        "regions": [
            {
                **{k: v for k, v in region.items() if k != "aliases"},
                "mentions": region_counts[region["id"]],
                "source_articles": source_regions[region["id"]],
                "samples": samples[region["id"]],
            }
            for region in regions()
        ],
        "industries": [
            {
                "id": key,
                "name": categories.get(key, key),
                "count": industry_counts[key],
                "timeline": [industry_time[key][bucket] for bucket in buckets],
            }
            for key in categories
        ],
        "timeline": [{"time": bucket, "count": timeline[bucket]} for bucket in buckets],
        "importance": [
            {"id": key, "count": importance[key]} for key in ["critical", "important", "ordinary", "unscored"]
        ],
        "thresholds": {"critical": cfg.critical_alert_threshold, "important": cfg.important_alert_threshold},
        "sources": [{"name": name, "count": count} for name, count in source_counts.most_common()],
        "languages": [{"name": name, "count": count} for name, count in languages.most_common()],
    }
