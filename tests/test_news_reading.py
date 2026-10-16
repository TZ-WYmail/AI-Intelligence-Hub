from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from streamlit.testing.v1 import AppTest

from app.core.config import settings
from app.db import get_session
from app.main import app
from app.models import Analysis, Article, Job
from app.services.pipeline import analyze, collect
from tests.test_pipeline import mocked_collect


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


def test_chinese_ai_search_and_filters(news_client):
    for query in ["独特中文", "供应链机会"]:
        data = news_client.get("/api/v1/articles", params={"q": query}).json()
        assert data["total"] == 1
        assert data["items"][0]["ai_summary_zh"] == "独特中文摘要"
        assert data["items"][0]["verification_status"] == "unverified"
    assert news_client.get("/api/v1/articles?analysis_status=ai").json()["total"] == 1
    assert news_client.get("/api/v1/articles?analysis_status=rules").json()["total"] == 1
    assert news_client.get("/api/v1/articles?offset=20").json()["items"] == []
    assert news_client.get("/api/v1/articles?q=%25").json()["total"] == 0
    assert news_client.get("/api/v1/articles?analysis_status=invalid").status_code == 422


def test_manual_analysis_durable_and_idempotent(news_client, db, monkeypatch):
    rows = news_client.get("/api/v1/articles?analysis_status=rules").json()["items"]
    identity = rows[0]["id"]
    assert news_client.post(f"/api/v1/articles/{identity}/analyze").status_code == 409
    monkeypatch.setenv("AI_ENABLED", "true")
    settings.cache_clear()
    # Clear synthetic ingestion jobs, as a completed historical article would have no active job.
    for job in db.scalars(select(Job)).all():
        job.dedup_key = None
        job.status = "completed"
    db.commit()
    first = news_client.post(f"/api/v1/articles/{identity}/analyze").json()
    assert first["queued"] and db.get(Job, first["job_id"]).status == "pending"
    assert not news_client.post(f"/api/v1/articles/{identity}/analyze").json()["queued"]
    completed_id = news_client.get("/api/v1/articles?analysis_status=ai").json()["items"][0]["id"]
    assert news_client.post(f"/api/v1/articles/{completed_id}/analyze").json()["already_analyzed"]


def test_news_detail_is_lazy_and_renders_facts(news_client):
    calls = []

    def request(method, url, **kwargs):
        path = urlsplit(url).path
        calls.append(path)
        return news_client.request(
            method, path, headers=kwargs.get("headers"), json=kwargs.get("json"), params=kwargs.get("params")
        )

    with patch("httpx.request", request):
        ui = AppTest.from_file(str(Path("dashboard/Home.py").resolve()), default_timeout=20).run()
        ui.text_input[0].input("test-admin").run()
        ui.sidebar.radio[0].set_value("实时新闻").run()
        assert not ui.exception
        assert not any(path.startswith("/api/v1/articles/") for path in calls)
        details = [button for button in ui.button if button.label == "查看详情与分析"]
        assert len(details) == 2
        details[0].click().run()
        assert not ui.exception
        assert any(path.startswith("/api/v1/articles/") for path in calls)
