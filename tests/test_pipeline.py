from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.collectors.base import Collection
from app.collectors.rss import parse_feed
from app.core.config import settings
from app.core.security import RemoteError
from app.models import Analysis, Article, Event, Job, Notification, now
from app.services import pipeline
from app.services.queue import claim, enqueue, recover
from app.workers import run as worker
from tests.test_processing import FEED


def mocked_collect(monkeypatch):
    class Fake:
        def collect(self, url, headers):
            return Collection(parse_feed(FEED), {"etag": '"fixture"'})

    monkeypatch.setattr(pipeline, "collector", lambda _: Fake())


def test_end_to_end_and_dedup(db, monkeypatch):
    mocked_collect(monkeypatch)
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mock-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "mock-chat")
    settings.cache_clear()
    assert pipeline.collect(db, "source") == 2
    db.commit()
    assert pipeline.collect(db, "source") == 0
    db.commit()
    for article in db.scalars(select(Article)).all():
        pipeline.analyze(db, article.id)
    db.commit()
    assert db.scalar(select(func.count()).select_from(Article)) == 2
    assert db.scalar(select(func.count()).select_from(Event)) == 2
    assert db.scalar(select(Article)).industry_ids == ["ai"]
    note = db.scalar(select(Notification))
    assert note is not None and note.status == "pending"
    delivered = []
    monkeypatch.setattr(worker.WebhookNotifier, "send", lambda _, p: delivered.append(p))
    task = db.scalar(select(Job).where(Job.job_type == "notify"))
    worker.dispatch(db, task)
    db.commit()
    assert delivered and db.get(Notification, note.id).status == "sent"
    worker.dispatch(db, task)
    assert len(delivered) == 1


def test_task_claim_recovery_and_idempotency(db):
    first = enqueue(db, "collect", "collect:source", source_id="source")
    assert enqueue(db, "collect", "collect:source", source_id="source") is None
    db.commit()
    task = claim(db)
    assert task.id == first.id and task.attempt_count == 1
    assert claim(db) is None
    task.started_at = now() - timedelta(seconds=1000)
    db.commit()
    assert recover(db) == 1
    db.commit()
    assert claim(db).attempt_count == 2


def test_worker_retry_and_restart(db, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(db.bind, expire_on_commit=False)
    monkeypatch.setattr(worker, "Session", factory)

    class Broken:
        def collect(self, *args):
            raise RemoteError("HTTP_429", 600)

    monkeypatch.setattr(pipeline, "collector", lambda _: Broken())
    task = enqueue(db, "collect", "collect:source", source_id="source")
    db.commit()
    assert worker.tick()
    db.expire_all()
    task = db.get(Job, task.id)
    assert task.status == "retry_wait" and (task.scheduled_at - now()).total_seconds() > 590
    mocked_collect(monkeypatch)
    task.scheduled_at = now() - timedelta(seconds=1)
    db.commit()
    assert worker.tick()
    db.expire_all()
    assert db.get(Job, task.id).status == "completed"
    with factory() as reopened:
        assert reopened.scalar(select(func.count()).select_from(Article)) == 2


def test_ai_failure_keeps_rules(db, monkeypatch):
    mocked_collect(monkeypatch)
    pipeline.collect(db, "source")
    db.commit()
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "mock")
    monkeypatch.setenv("AI_MODEL", "mock-model")
    settings.cache_clear()

    class FailedAI:
        def analyze(self, *args):
            raise ValueError("invalid JSON")

    article = db.scalar(select(Article))
    with pytest.raises(ValueError):
        pipeline.analyze(db, article.id, FailedAI())
    assert db.scalar(select(Event)) is not None
    assert article.processing_status == "ai_retry_wait"
    assert db.scalar(select(Analysis).where(Analysis.provider == "openai-compatible")).status == "failed"
    monkeypatch.setenv("AI_DAILY_REQUEST_LIMIT", "0")
    settings.cache_clear()
    pipeline.analyze(db, article.id, FailedAI())
    assert article.processing_status == "budget_degraded"
