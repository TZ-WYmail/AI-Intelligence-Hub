from datetime import timedelta

from sqlalchemy import func, select

from app.models import Analysis, Article, Event, EventArticle, Notification, Source, now
from app.processing.event_matcher import matching_event, title_similarity
from app.services.pipeline import analyze


def article(db, identity, source, title, **values):
    item = Article(
        id=identity,
        source_id=source,
        external_id=identity,
        title=title,
        summary="AI announcement " + identity,
        original_url=f"https://example.com/{identity}",
        canonical_url=f"https://example.com/{identity}",
        content_hash=identity,
        published_at=values.pop("published_at", now()),
        **values,
    )
    db.add(item)
    db.flush()
    return item


TITLE = "NVIDIA announces new AI platform for robotics and industrial manufacturing"


def test_title_guardrails():
    assert title_similarity(TITLE, TITLE.upper() + "!") == 1
    assert title_similarity("x" * 10000, "x" * 10000) == 0
    assert title_similarity(TITLE, TITLE.replace("NVIDIA", "Intel")) == 0
    assert title_similarity("AI news", "AI news") == 0
    assert title_similarity(TITLE + " 2026", TITLE + " 2027") == 0
    assert title_similarity(TITLE + " approved", TITLE + " not approved") == 0
    assert (
        title_similarity(
            "某科技公司发布新一代人工智能计算平台，支持行业应用",
            "某科技公司否认发布新一代人工智能计算平台，支持行业应用",
        )
        == 0
    )


def test_cross_source_linking_preserves_reports_and_notification_idempotency(db, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mock-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "mock-chat")
    from app.core.config import settings

    settings.cache_clear()
    db.add(Source(id="other", name="Second publisher", url="https://other.example/rss", enabled=True))
    db.flush()
    first = article(db, "first", "source", TITLE)
    analyze(db, first.id)
    db.commit()
    note_count = db.scalar(select(func.count()).select_from(Notification))
    second = article(db, "second", "other", TITLE.upper() + "!")
    analyze(db, second.id)
    db.commit()
    analyze(db, second.id)
    db.commit()
    assert db.scalar(select(func.count()).select_from(Event)) == 1
    assert db.scalar(select(func.count()).select_from(Article)) == 2
    assert db.scalar(select(func.count()).select_from(EventArticle)) == 2
    assert db.scalar(select(func.count()).select_from(Analysis)) == 2
    event = db.scalar(select(Event))
    assert event.verification_status == "unverified" and event.version == 1
    assert db.get(EventArticle, (event.id, second.id)).relation_type == "similar_title_unverified"
    assert db.scalar(select(func.count()).select_from(Notification)) == note_count
    # Same-source follow-ups, missing dates and older reports remain separate.
    for identity, source, values in [
        ("same", "source", {}),
        ("second-publisher-followup", "other", {}),
        ("inferred", "other", {"publication_time_inferred": True}),
        ("old", "other", {"published_at": now() - timedelta(days=4)}),
    ]:
        item = article(db, identity, source, TITLE, **values)
        assert matching_event(db, item) is None


def test_event_api_timeline(news_client):
    response = news_client.get("/api/v1/events").json()
    event = response["items"][0]
    assert event["article_count"] == 1
    detail = news_client.get("/api/v1/events/" + event["id"]).json()
    assert detail["articles"][0]["source_name"] == "Fixture"
    assert detail["articles"][0]["relation_type"] == "initial"
    assert "raw_payload" not in detail["articles"][0]


def test_source_health_excludes_inferred_and_future_dates(db, news_client):
    db.add(Source(id="health", name="Dated publisher", url="https://health.example/rss"))
    db.flush()
    article(db, "dated", "health", TITLE, published_at=now() - timedelta(days=10))
    article(db, "inferred-date", "health", TITLE, publication_time_inferred=True)
    article(db, "future-date", "health", TITLE, published_at=now() + timedelta(days=5))
    db.commit()
    source = next(item for item in news_client.get("/api/v1/sources").json() if item["id"] == "health")
    assert source["article_count"] == 3
    assert source["inferred_date_count"] == 1
    assert source["future_date_count"] == 1
    assert source["content_age_days"] == 10
    assert source["freshness_status"] == "older_than_7_days"


def test_interrupted_ai_reservation_remains_counted_on_recovery(db, monkeypatch):
    first = article(db, "interrupted-ai", "source", TITLE)
    analyze(db, first.id)
    db.commit()
    rules = db.scalar(select(Analysis).where(Analysis.article_id == first.id))
    usage = Analysis(
        article_id=first.id,
        event_id=rules.event_id,
        provider="openai-compatible",
        status="started",
        estimated_cost=0.1,
    )
    db.add(usage)
    db.commit()
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_DAILY_REQUEST_LIMIT", "1")
    from app.core.config import settings

    settings.cache_clear()

    class NeverCall:
        def analyze(self, *args):
            raise AssertionError("Reservation already consumes today's quota")

    analyze(db, first.id, NeverCall())
    db.commit()
    assert usage.status == "interrupted" and usage.estimated_cost == 0.1
    assert first.processing_status == "budget_degraded"
    assert db.scalar(select(func.count()).select_from(Event)) == 1
