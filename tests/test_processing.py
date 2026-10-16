from datetime import timedelta

import pytest

from app.collectors.rss import parse_feed
from app.core.config import Settings
from app.models import Article, Industry, Source, now
from app.processing.classifier import classify, matches
from app.processing.normalizer import canonical, clean, content_hash, timestamp
from app.processing.scorer import score
from app.schemas import AIResult

FEED = """<?xml version="1.0"?><rss version="2.0"><channel><title>Test</title><link>https://example.com</link><description>fixture</description>
<item><guid>1</guid><title>AI launches breakthrough</title><link>https://example.com/news?utm_source=rss</link><description>&lt;b&gt;AI news&lt;/b&gt;&lt;script&gt;steal()&lt;/script&gt;</description><pubDate>Fri, 16 Oct 2026 08:00:00 GMT</pubDate></item>
<item><guid>2</guid><title>人工智能 新闻</title><link>https://example.com/2</link></item></channel></rss>""".encode()


def test_rss_and_safety():
    rows = parse_feed(FEED)
    assert len(rows) == 2
    assert rows[0]["summary"] == "AI news"
    assert rows[0]["published_at"].tzinfo is not None
    assert rows[1]["publication_time_inferred"]
    assert rows[1]["language"] == "zh"
    assert "script" in str(rows[0]["raw_payload"])
    assert clean('<img onerror="evil()">hello<script>evil()</script>') == "hello"
    with pytest.raises(ValueError):
        parse_feed(b"not an rss feed")


def test_normalization():
    assert canonical("https://EXAMPLE.com/a?utm_source=x&b=2&a=1#part") == "https://example.com/a?a=1&b=2"
    assert content_hash("AI  News", "x") == content_hash("ai news", "x")
    assert timestamp("2026-10-16T08:00:00+08:00").hour == 0
    for value in ["javascript:alert(1)", "file:///etc/passwd", "https://user:password@example.com"]:
        with pytest.raises(ValueError):
            canonical(value)


def test_classification_and_scoring(db):
    industry = db.get(Industry, "ai")
    assert not matches("AI", "chair")
    assert classify("AI launch", "", [industry], []) == ["ai"]
    industry.exclusions = ["rumor"]
    assert classify("AI rumor", "", [industry], ["ai"]) == []
    article = Article(
        title="AI launches breakthrough", summary="", published_at=now(), publication_time_inferred=False
    )
    points, dims = score(article, db.get(Source, "source"), ["ai"])
    assert points >= 80 and dims["confirmation"] == 0
    article.published_at = now() - timedelta(days=7)
    assert score(article, db.get(Source, "source"), ["ai"])[0] < points


def test_config_and_ai_schema():
    with pytest.raises(ValueError):
        Settings(_env_file=None, critical_alert_threshold=30, important_alert_threshold=60)
    with pytest.raises(ValueError):
        Settings(_env_file=None, ai_enabled=True, ai_api_key="", ai_model="")
    with pytest.raises(ValueError):
        AIResult.model_validate_json('{"importance_score": 200}')
