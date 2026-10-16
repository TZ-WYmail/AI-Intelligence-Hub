import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db import get_session, make_engine
from app.main import app
from app.models import Analysis, Article, Base, Industry, Source
from app.services.pipeline import analyze, collect
from tests.test_pipeline import mocked_collect


@pytest.fixture(autouse=True)
def isolate_real_credentials(monkeypatch):
    for key, value in {
        "AI_ENABLED": "false",
        "BACKUP_ENABLED": "false",
        "DAILY_DIGEST_ENABLED": "false",
        "AI_API_KEY": "mock",
        "AI_MODEL": "mock",
        "AI_THINKING_MODE": "provider_default",
        "TELEGRAM_ENABLED": "false",
        "FEISHU_ENABLED": "false",
        "TELEGRAM_BOT_TOKEN": "mock",
        "TELEGRAM_CHAT_ID": "mock",
        "FEISHU_WEBHOOK_URL": "https://example.com/webhook",
    }.items():
        monkeypatch.setenv(key, value)
    settings.cache_clear()
    yield
    settings.cache_clear()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin")
    settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        session.add(Industry(id="ai", name="AI", slug="ai", keywords=["AI", "人工智能"], alert_threshold=70))
        session.add(
            Source(
                id="source",
                name="Fixture",
                url="https://example.com/rss",
                enabled=True,
                industry_ids=["ai"],
                trust_level=1,
            )
        )
        session.commit()
        yield session
    engine.dispose()
    settings.cache_clear()


@pytest.fixture
def news_client(db, monkeypatch):
    mocked_collect(monkeypatch)
    collect(db, "source")
    db.commit()
    for article in db.scalars(select(Article)).all():
        analyze(db, article.id)
    article = db.scalar(select(Article).order_by(Article.title))
    rules = db.scalar(select(Analysis).where(Analysis.article_id == article.id))
    db.add(
        Analysis(
            article_id=article.id,
            event_id=rules.event_id,
            provider="openai-compatible",
            model="fixture",
            status="completed",
            analysis_json={"summary_zh": "独特中文摘要", "impact_analysis": "供应链机会"},
        )
    )
    db.commit()
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as client:
        client.headers["X-API-Key"] = "test-admin"
        yield client
    app.dependency_overrides.clear()
