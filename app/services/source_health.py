"""Separate transport health from the age and reliability of publication timestamps."""

from datetime import timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Article, now


def publication_health(session: Session) -> dict[str, dict]:
    clock = now()
    result = {}
    for source_id, total, inferred, future, latest in session.execute(
        select(
            Article.source_id,
            func.count(),
            func.sum(case((Article.publication_time_inferred.is_(True), 1), else_=0)),
            func.sum(case((Article.published_at > clock + timedelta(minutes=5), 1), else_=0)),
            func.max(
                case(
                    (
                        Article.publication_time_inferred.is_(False)
                        & (Article.published_at <= clock + timedelta(minutes=5)),
                        Article.published_at,
                    )
                )
            ),
        ).group_by(Article.source_id)
    ):
        age = max(0, (clock - latest).total_seconds() / 86400) if latest else None
        result[source_id] = {
            "article_count": total,
            "inferred_date_count": inferred,
            "future_date_count": future,
            "latest_published_at": latest,
            "content_age_days": round(age, 1) if age is not None else None,
            "freshness_status": "unknown" if age is None else "older_than_7_days" if age > 7 else "recent",
        }
    return result
