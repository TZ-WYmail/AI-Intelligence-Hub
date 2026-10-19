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
from app.core.config import Settings, settings
from app.models import Analysis, Article, Event, Job, Notification, Source, now
from app.notifications.adapters import recipient_key
from app.services import pipeline
from app.services.queue import claim, enqueue, recover
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


def p1_notification(db, identity: str, channel: str, created_at, status: str = "pending") -> Notification:
    event = Event(
        id="event-" + identity,
        title="fixture " + identity,
        summary="fixture",
        importance_score=65,
    )
    db.add(event)
    db.flush()
    note = Notification(
        id=identity,
        event_id=event.id,
        channel=channel,
        recipient=recipient_key(channel, settings()),
        notification_type="P1",
        payload={
            "level": "P1",
            "title": event.title,
            "score": 65,
            "verification": "unverified",
            "summary": "fixture",
            "url": "https://example.com/" + identity,
        },
        status=status,
        created_at=created_at,
    )
    db.add(note)
    db.flush()
    return note


def test_notify_batch_failure_only_marks_notifications_in_its_cutoff(db, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mock")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "mock")
    settings.cache_clear()
    old = p1_notification(db, "old-batch", "telegram", now() - timedelta(minutes=120))
    later = p1_notification(db, "later-batch", "telegram", now() - timedelta(minutes=5))
    job = Job(
        job_type="notify_batch",
        dedup_key="batch:telegram:old",
        payload={"channel": "telegram", "cutoff": (now() - timedelta(minutes=60)).isoformat()},
        scheduled_at=now() - timedelta(minutes=60),
        attempt_count=settings().job_max_attempts - 1,
    )
    db.add(job)
    db.commit()
    factory = sessionmaker(db.bind, expire_on_commit=False)
    monkeypatch.setattr(worker, "Session", factory)
    monkeypatch.setattr(worker, "quiet", lambda: False)

    def fail(*_):
        raise security.RemoteError("HTTP_500")

    monkeypatch.setattr(worker.WebhookNotifier, "send", fail)

    assert worker.tick()
    db.expire_all()
    assert db.get(Job, job.id).status == "failed"
    assert db.get(Notification, old.id).status == "failed"
    # The failed job must not mark a later batch's notifications as permanently failed.
    assert db.get(Notification, later.id).status == "pending"
    assert db.get(Notification, later.id).last_error is None


def test_recover_terminal_notify_syncs_job_and_notification_failure(db):
    note = p1_notification(db, "terminal-notify", "feishu", now() - timedelta(minutes=10))
    job = Job(
        job_type="notify",
        dedup_key="notify:" + note.id,
        payload={"notification_id": note.id},
        status="running",
        attempt_count=settings().job_max_attempts,
        started_at=now() - timedelta(seconds=settings().job_lease_seconds + 1),
    )
    db.add(job)
    db.commit()

    assert recover(db) == 1
    db.commit()
    assert job.status == "failed" and job.finished_at is not None and job.dedup_key is None
    assert note.status == "failed" and note.last_error == "LEASE_EXPIRED"


def test_recover_terminal_batch_respects_cutoff(db):
    old = p1_notification(db, "terminal-old", "telegram", now() - timedelta(minutes=120))
    later = p1_notification(db, "terminal-later", "telegram", now() - timedelta(minutes=5))
    job = Job(
        job_type="notify_batch",
        dedup_key="batch:telegram:terminal",
        payload={"channel": "telegram", "cutoff": (now() - timedelta(minutes=60)).isoformat()},
        status="running",
        attempt_count=settings().job_max_attempts,
        started_at=now() - timedelta(seconds=settings().job_lease_seconds + 1),
    )
    db.add(job)
    db.commit()

    assert recover(db) == 1
    db.commit()
    assert job.status == "failed" and job.finished_at is not None
    assert db.get(Notification, old.id).status == "failed"
    assert db.get(Notification, later.id).status == "pending"


def test_corrupt_lease_payload_isolated_and_failed(db, monkeypatch):
    corrupt = Job(
        job_type="notify_batch",
        dedup_key="batch:telegram:corrupt",
        payload={"channel": "telegram", "cutoff": "not-a-time"},
        status="running",
        attempt_count=1,
        started_at=now() - timedelta(seconds=settings().job_lease_seconds + 1),
    )
    note = p1_notification(db, "corrupt-scope", "telegram", now() - timedelta(minutes=10))
    db.add_all([corrupt, Job(job_type="collect", source_id="source", payload={}, status="pending")])
    db.commit()
    factory = sessionmaker(db.bind, expire_on_commit=False)
    monkeypatch.setattr(worker, "Session", factory)
    ran = []
    monkeypatch.setattr(worker, "dispatch", lambda session, job: ran.append(job.job_type))

    # recover must fail only the corrupt row; the next job still runs in the same tick.
    assert worker.tick()
    db.expire_all()
    assert ran == ["collect"]
    assert corrupt.status == "failed" and corrupt.last_error == "INVALID_CUTOFF"
    assert corrupt.finished_at is not None and corrupt.dedup_key is None
    # Corrupt payload scope is unknowable: do not guess wider and fail unrelated notifications.
    assert db.get(Notification, note.id).status == "pending"


def test_missing_notify_payload_is_failed_not_silently_completed(db, monkeypatch):
    job = Job(job_type="notify", dedup_key="notify:broken", payload={}, status="pending")
    db.add(job)
    db.commit()
    factory = sessionmaker(db.bind, expire_on_commit=False)
    monkeypatch.setattr(worker, "Session", factory)

    # The worker failure path must persist the failure even though syncing notification
    # state sees the same unusable payload instead of raising and rolling the commit back.
    assert worker.tick()
    db.expire_all()
    assert job.status == "failed" and job.last_error == "MISSING_NOTIFICATION_ID"
    assert job.finished_at is not None and job.dedup_key is None


def test_bad_collect_job_fails_without_blocking_next_job(db, monkeypatch):
    bad = Job(
        job_type="collect",
        dedup_key="collect:missing-source",
        payload={},
        status="pending",
        scheduled_at=now() - timedelta(seconds=1),
    )
    good = Job(job_type="collect", dedup_key="collect:source", payload={}, source_id="source")
    db.add_all([bad, good])
    db.commit()
    factory = sessionmaker(db.bind, expire_on_commit=False)
    monkeypatch.setattr(worker, "Session", factory)
    mocked_collect(monkeypatch)

    # Recording the failure must not raise on a job that has no source to update.
    assert worker.tick()
    db.expire_all()
    assert bad.status == "failed" and bad.last_error == "MISSING_SOURCE_ID"
    assert bad.finished_at is not None and bad.dedup_key is None
    # The next healthy collect job still runs and completes in its own tick.
    assert worker.tick()
    db.expire_all()
    assert good.status == "completed"
    assert db.scalar(select(func.count()).select_from(Article)) == 2


def test_recover_terminal_analyze_interrupts_started_usage(db):
    article = Article(
        id="review-article",
        source_id="source",
        title="AI release",
        summary="Mock data",
        original_url="https://example.com/review",
        canonical_url="https://example.com/review",
        content_hash="review",
        published_at=now(),
    )
    event = Event(id="review-event", title="AI release", summary="Mock data")
    db.add_all([article, event])
    db.flush()
    usage = Analysis(
        article_id=article.id,
        event_id=event.id,
        provider="openai-compatible",
        status="started",
        estimated_cost=0.1,
    )
    historical = Analysis(
        article_id=article.id,
        event_id=event.id,
        provider="openai-compatible",
        status="completed",
        estimated_cost=0.05,
    )
    db.add_all([usage, historical])
    db.add(
        Job(
            job_type="analyze",
            dedup_key="analyze:" + article.id,
            payload={"article_id": article.id},
            status="running",
            attempt_count=settings().job_max_attempts,
            started_at=now() - timedelta(seconds=settings().job_lease_seconds + 1),
        )
    )
    db.commit()

    assert recover(db) == 1
    db.commit()
    db.refresh(usage)
    # Close the orphaned attempt but keep the reserved/billed cost inside budget windows.
    assert usage.status == "interrupted" and usage.estimated_cost == 0.1
    # A provider-attempt history is immutable: never rewrite completed/failed analyses.
    assert db.get(Analysis, historical.id).status == "completed"


def test_recover_never_downgrades_or_resends_sent_notification(db):
    sent = p1_notification(db, "already-sent", "telegram", now() - timedelta(minutes=10), status="sent")
    sent.sent_at = now()
    pending = p1_notification(db, "sent-scope-later", "telegram", now() + timedelta(minutes=5))
    job = Job(
        job_type="notify_batch",
        dedup_key="batch:telegram:sent",
        payload={"channel": "telegram", "cutoff": now().isoformat()},
        status="running",
        attempt_count=settings().job_max_attempts,
        started_at=now() - timedelta(seconds=settings().job_lease_seconds + 1),
    )
    db.add(job)
    db.commit()

    assert recover(db) == 1
    db.commit()
    db.expire_all()
    assert job.status == "failed"
    # Delivered records are never downgraded to failed, and nothing is queued again.
    assert sent.status == "sent" and sent.sent_at is not None and sent.last_error is None
    assert db.get(Notification, pending.id).status == "pending"
    assert claim(db) is None
