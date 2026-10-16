import pytest
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db import make_engine
from app.models import Base, Industry, Source


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
