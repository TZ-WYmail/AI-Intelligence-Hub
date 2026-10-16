import json
import socket
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.ai.provider import OpenAIProvider
from app.collectors.gdelt import GDELTCollector
from app.core import security
from app.core.config import Settings
from app.models import Article, Event, Job, Notification, Source, now
from app.services import pipeline
from app.services.queue import claim, enqueue
from app.workers import run as worker
from tests.test_pipeline import mocked_collect


def test_pinned_dns_and_redirect_recheck(monkeypatch):
    original = httpx.Client
    observed = []

    def dns(host, *args, **kwargs):
        return [
            (socket.AF_INET, 1, 6, "", ("127.0.0.1" if host == "private.example" else "93.184.216.34", 443))
        ]

    def handler(request):
        observed.append(request)
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "public.example"
        assert request.extensions["sni_hostname"] == "public.example"
        return httpx.Response(302, headers={"Location": "https://private.example/a"})

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    with pytest.raises(security.UnsafeURL):
        security.fetch("https://public.example/feed")
    assert len(observed) == 1


def test_size_limit_and_http_retry_date(monkeypatch):
    original = httpx.Client
    monkeypatch.setattr(security, "resolve_public", lambda _: "93.184.216.34")
    monkeypatch.setattr(security, "settings", lambda: Settings(_env_file=None, max_response_bytes=1024))
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 2048))
        ),
    )
    with pytest.raises(security.RemoteError, match="RESPONSE_TOO_LARGE"):
        security.fetch("https://example.com")
    from email.utils import format_datetime

    assert 55 <= security.retry_seconds(format_datetime(now() + timedelta(seconds=60))) <= 60


def test_gdelt_fixture(monkeypatch):
    body = {
        "articles": [
            {
                "title": "AI News",
                "url": "https://example.com/a",
                "seendate": "20261016T080000Z",
                "language": "English",
            }
        ]
    }
    monkeypatch.setattr("app.collectors.gdelt.fetch", lambda *a: (200, {}, json.dumps(body).encode()))
    rows = GDELTCollector().collect("https://example.com", {}).entries
    assert rows[0]["publication_time_inferred"] and rows[0]["published_at"].tzinfo is not None


def test_bad_ai_json(monkeypatch):
    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "not JSON"}}]})
            )
        ),
    )
    with pytest.raises(ValueError):
        OpenAIProvider(Settings(_env_file=None)).analyze({"id": "a"}, [])


def test_priority_and_separate_sessions(db):
    enqueue(db, "collect", "collect:source", source_id="source")
    notification = enqueue(db, "notify", "notify:1", {"notification_id": "1"})
    db.commit()
    factory = sessionmaker(db.bind, expire_on_commit=False)
    with factory() as second:
        assert claim(second).id == notification.id
    assert claim(db).job_type == "collect"
    assert claim(db) is None


def test_timeout_isolation_and_db_recovery(db, monkeypatch):
    db.add(Source(id="bad", name="Bad", url="https://example.com/bad", enabled=True))
    db.commit()
    factory = sessionmaker(db.bind, expire_on_commit=False)
    monkeypatch.setattr(worker, "Session", factory)
    mocked_collect(monkeypatch)
    original = pipeline.collector

    class Timeout:
        def collect(self, *args):
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(
        pipeline, "collector", lambda source: Timeout() if source.id == "bad" else original(source)
    )
    enqueue(db, "collect", "collect:bad", source_id="bad")
    enqueue(db, "collect", "collect:source", source_id="source")
    db.commit()
    assert worker.tick()
    assert worker.tick()
    db.expire_all()
    assert db.get(Source, "bad").last_error == "ReadTimeout"
    assert db.scalar(select(func.count()).select_from(Article)) == 2
    # A database transaction error leaves pending work available after the database is writable again.
    from sqlalchemy.exc import OperationalError

    class BrokenSession:
        def __enter__(self):
            raise OperationalError("database locked", {}, None)

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(worker, "Session", BrokenSession)
    with pytest.raises(OperationalError):
        worker.tick()
    monkeypatch.setattr(worker, "Session", factory)
    assert worker.tick()


def test_p1_batched_and_quiet(db, monkeypatch):
    from app.core.config import settings
    from app.models import Industry
    from app.services.pipeline import create_notifications

    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mock")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "mock")
    settings.cache_clear()
    article = Article(
        source_id="source",
        title="fixture",
        summary="news",
        original_url="https://example.com/a",
        canonical_url="https://example.com/a",
        content_hash="1",
        published_at=now(),
        industry_ids=["ai"],
    )
    db.add(article)
    db.flush()
    industry = db.get(Industry, "ai")
    industry.alert_threshold = 85
    for i in range(2):
        event = Event(title=str(i), summary="fixture", importance_score=65)
        db.add(event)
        db.flush()
        create_notifications(db, event, article, db.get(Source, "source"), [industry], {})
    db.commit()
    tasks = db.scalars(select(Job).where(Job.job_type == "notify_batch")).all()
    assert len(tasks) == 1
    delivered = []
    monkeypatch.setattr(worker, "quiet", lambda: True)
    with pytest.raises(security.RemoteError, match="QUIET_HOURS"):
        worker.dispatch(db, tasks[0])
    monkeypatch.setattr(worker, "quiet", lambda: False)
    monkeypatch.setattr(worker.WebhookNotifier, "send", lambda _, p: delivered.append(p))
    worker.dispatch(db, tasks[0])
    assert len(delivered) == 1
    assert db.scalar(select(func.count()).select_from(Notification).where(Notification.status == "sent")) == 2
