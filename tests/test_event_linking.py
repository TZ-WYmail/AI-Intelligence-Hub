from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.models import Analysis, Article, Event, EventArticle, Job, Notification, Source, now
from app.notifications.adapters import WebhookNotifier
from app.processing.event_matcher import matching_event, normalized_title, title_similarity
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

# Each pair describes a different fact but previously normalized to the same token
# bag (or scored above the old fuzzy threshold). They must never be linked.
DIFFERENT_FACT_PAIRS = [
    pytest.param(
        "NVIDIA invests 10 million dollars in 20 new AI research centres",
        "NVIDIA invests 20 million dollars in 10 new AI research centres",
        id="amount-role-swap",
    ),
    pytest.param(
        "Company reports -10 percent revenue change for its global artificial intelligence division",
        "Company reports +10 percent revenue change for its global artificial intelligence division",
        id="sign",
    ),
    pytest.param(
        "Company reports \u221210 percent revenue change for its global artificial intelligence division",
        "Company reports -10 percent revenue change for its global artificial intelligence division",
        id="unicode-minus",
    ),
    pytest.param(
        "Company invests $10 million in a new artificial intelligence research centre",
        "Company invests \u20ac10 million in a new artificial intelligence research centre",
        id="currency",
    ),
    pytest.param(
        "NVIDIA invests 20 million dollars in a startup building AI research centres",
        "A startup invests 20 million dollars in NVIDIA building AI research centres",
        id="entity-role-swap",
    ),
    pytest.param(
        "NVIDIA acquires Arm in a landmark artificial intelligence semiconductor deal approved by global "
        "regulators amid intense industry scrutiny and political debate",
        "Arm acquires NVIDIA in a landmark artificial intelligence semiconductor deal approved by global "
        "regulators amid intense industry scrutiny and political debate",
        id="long-entity-role-swap",
    ),
    pytest.param(
        "NVIDIA announces new AI research centres in Europe",
        "NVIDIA does not announce new AI research centres in Europe",
        id="negation",
    ),
    pytest.param(
        "NVIDIA announces new AI research centres in Europe?",
        "NVIDIA announces new AI research centres in Europe",
        id="question",
    ),
    pytest.param(
        "Samsung launches Galaxy S26+ and S26 Ultra flagship smartphones",
        "Samsung launches Galaxy S26 and S26 Ultra flagship smartphones",
        id="product-plus",
    ),
    pytest.param(
        "LLVM releases new C++ compiler for embedded systems developers",
        "LLVM releases new C compiler for embedded systems developers",
        id="product-cpp",
    ),
    pytest.param(
        "英伟达计划向二十家新的人工智能研究中心投资一千万美元",
        "英伟达计划向一千万家新的人工智能研究中心投资二十万美元",
        id="zh-numeral-role-swap",
    ),
    pytest.param(
        "甲公司以十亿元正式收购乙公司旗下的人工智能芯片设计业务",
        "乙公司以十亿元正式收购甲公司旗下的人工智能芯片设计业务",
        id="zh-entity-role-swap",
    ),
    pytest.param(
        "NVIDIA invests 20 million dollars in new AI research centres",
        "NVIDIA invests 30 million dollars in new AI research centres",
        id="number",
    ),
    pytest.param("N" * 601, "N" * 601, id="overlong-identical"),
]


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


def test_normalized_title_preserves_factual_signals():
    assert normalized_title("Title!") == "title"
    assert normalized_title("Title?") == "title?"
    assert normalized_title("S26+") == "s26+"
    assert normalized_title("C++") == "c++"
    assert normalized_title("\u221210") == "\u221210"


def test_case_normalization_keeps_sharp_s_and_ligatures_distinct():
    assert normalized_title("Straße") == "straße"
    assert normalized_title("STRASSE") == "strasse"
    assert normalized_title("Oﬃce") == "oﬃce"
    assert (
        title_similarity(
            "Berlin institute opens new artificial intelligence campus on Friedrichstraße",
            "Berlin institute opens new artificial intelligence campus on Friedrichstrasse",
        )
        == 0
    )
    assert (
        title_similarity(
            "New oﬀice opens for artificial intelligence research across Europe",
            "New office opens for artificial intelligence research across Europe",
        )
        == 0
    )


@pytest.mark.parametrize(
    "variant",
    [
        TITLE.upper() + "!",
        TITLE + "!!!",
        TITLE + " !",
        "  " + TITLE.replace(" ", " \t ") + "  ",
        TITLE.replace("NVIDIA", "nvidia"),
    ],
)
def test_typographic_variants_link(variant):
    assert title_similarity(TITLE, variant) == 1


@pytest.mark.parametrize(("left", "right"), DIFFERENT_FACT_PAIRS)
def test_similar_but_different_facts_never_link(left, right):
    assert title_similarity(left, right) == 0
    assert title_similarity(right, left) == 0


def test_compatibility_characters_are_not_folded():
    # NFKC would equate these; NFC deliberately keeps technical identifiers distinct.
    assert (
        title_similarity(
            "Research centre 1 opens for artificial intelligence safety testing",
            "Research centre \u2460 opens for artificial intelligence safety testing",
        )
        == 0
    )
    assert (
        title_similarity(
            "Samsung launches Galaxy S26+ and S26 Ultra flagship smartphones",
            "Samsung launches Galaxy \uff3326\uff0b and S26 Ultra flagship smartphones",
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


@pytest.mark.parametrize(("left", "right"), DIFFERENT_FACT_PAIRS[:8])
def test_pipeline_keeps_different_facts_as_separate_events(db, monkeypatch, left, right):
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mock-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "mock-chat")
    from app.core.config import settings

    settings.cache_clear()
    db.add(
        Source(
            id="other",
            name="Second publisher",
            url="https://other.example/rss",
            enabled=True,
            industry_ids=["ai"],
            trust_level=1,
        )
    )
    db.flush()
    delivered = []
    monkeypatch.setattr(WebhookNotifier, "send", lambda _, payload: delivered.append(payload))

    first = article(db, "first", "source", left)
    analyze(db, first.id)
    db.commit()
    second = article(db, "second", "other", right)
    analyze(db, second.id)
    db.commit()

    # Notifications stay in the durable queue; no worker/network dispatch happens here.
    assert delivered == []
    events = list(db.scalars(select(Event)))
    assert len(events) == 2
    assert {event.title for event in events} == {left, right}
    assert all(event.verification_status == "unverified" and event.version == 1 for event in events)
    assert db.scalar(select(func.count()).select_from(Article)) == 2
    assert db.scalar(select(func.count()).select_from(EventArticle)) == 2
    analyses = list(db.scalars(select(Analysis)))
    assert len(analyses) == 2
    event_ids = {analysis.event_id for analysis in analyses}
    assert len(event_ids) == 2
    first_event = db.scalar(select(Analysis.event_id).where(Analysis.article_id == first.id))
    second_event = db.scalar(select(Analysis.event_id).where(Analysis.article_id == second.id))
    assert first_event != second_event
    assert db.get(EventArticle, (first_event, first.id)).relation_type == "initial"
    assert db.get(EventArticle, (second_event, second.id)).relation_type == "initial"
    notes = list(db.scalars(select(Notification)))
    assert len(notes) == 2
    assert {note.status for note in notes} == {"pending"}
    assert {note.event_id for note in notes} == event_ids
    notify_jobs = list(db.scalars(select(Job).where(Job.job_type == "notify")))
    assert len(notify_jobs) == 2
    assert {job.status for job in notify_jobs} == {"pending"}


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
