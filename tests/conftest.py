import pytest
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db import make_engine
from app.models import Base, Industry, Source


@pytest.fixture(autouse=True)
def isolate_real_credentials(monkeypatch):
    for key, value in {
        "AI_ENABLED": "false",
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
