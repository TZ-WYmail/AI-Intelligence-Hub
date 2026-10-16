import logging
import time
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.security import RemoteError
from app.db import Session
from app.models import Job, Metric, Notification, Source, now
from app.notifications.adapters import WebhookNotifier, recipient_key
from app.services.pipeline import analyze, collect
from app.services.queue import claim, recover

logger = logging.getLogger("worker")


def quiet() -> bool:
    cfg = settings()
    hour = now().astimezone(ZoneInfo(cfg.app_timezone)).hour
    start, end = cfg.quiet_start_hour, cfg.quiet_end_hour
    return (start <= hour < end) if start < end else (hour >= start or hour < end) if start != end else False


def dispatch(session, job: Job) -> None:
    if job.job_type == "collect":
        collect(session, job.source_id)
    elif job.job_type == "analyze":
        analyze(session, job.payload["article_id"])
    elif job.job_type in {"notify", "notify_batch"}:
        if job.job_type == "notify":
            note = session.get(Notification, job.payload["notification_id"])
            notes = [note] if note and note.status != "sent" else []
        else:
            from app.processing.normalizer import timestamp

            notes = list(
                session.scalars(
                    select(Notification).where(
                        Notification.channel == job.payload["channel"],
                        Notification.notification_type == "P1",
                        Notification.status.in_(["pending", "retry_wait"]),
                        Notification.created_at <= timestamp(job.payload["cutoff"]),
                    )
                )
            )
        if not notes:
            return
        if any(note.recipient != recipient_key(note.channel, settings()) for note in notes):
            raise RemoteError("RECIPIENT_CONFIG_CHANGED", 3600)
        if not getattr(settings(), f"{notes[0].channel}_enabled"):
            raise RemoteError("CHANNEL_DISABLED", 3600)
        if notes[0].notification_type == "P1" and quiet():
            raise RemoteError("QUIET_HOURS", 3600)
        for offset in range(0, len(notes), 5):
            chunk = notes[offset : offset + 5]
            payload = dict(chunk[0].payload)
            if len(chunk) > 1:
                payload["title"] = f"重要情报汇总（{len(chunk)} 条）"
                payload["summary"] = "\n".join(
                    f"{n.payload['title'][:140]} {n.payload['url']}" for n in chunk
                )
            for note in chunk:
                note.attempt_count += 1
            session.commit()
            WebhookNotifier(chunk[0].channel, settings()).send(payload)
            for note in chunk:
                note.status, note.sent_at, note.last_error = "sent", now(), None
                session.add(
                    Metric(
                        metric_name="notification_queue_seconds",
                        metric_value=(now() - note.created_at).total_seconds(),
                        labels={"level": note.notification_type, "channel": note.channel},
                    )
                )
                from app.models import Article, EventArticle

                article = session.scalar(
                    select(Article)
                    .join(EventArticle, Article.id == EventArticle.article_id)
                    .where(EventArticle.event_id == note.event_id)
                )
                if article and not article.publication_time_inferred:
                    session.add(
                        Metric(
                            metric_name="end_to_end_seconds",
                            metric_value=(now() - article.published_at).total_seconds(),
                            labels={"source_id": article.source_id},
                        )
                    )
            session.commit()
    else:
        raise ValueError("UNKNOWN_JOB_TYPE")


def tick() -> bool:
    with Session() as session:
        recover(session)
        session.commit()
        job = claim(session)
        if not job:
            return False
        try:
            dispatch(session, job)
            job.status, job.finished_at, job.last_error, job.dedup_key = "completed", now(), None, None
            session.commit()
        except Exception as exc:
            session.rollback()
            job = session.get(Job, job.id)
            code = exc.code if isinstance(exc, RemoteError) else type(exc).__name__
            if isinstance(exc, RemoteError) and exc.code == "QUIET_HOURS":
                # Intentional scheduling deferral must not exhaust failure retries.
                job.attempt_count = max(0, job.attempt_count - 1)
            delay = min(3600, 30 * 2**job.attempt_count)
            if isinstance(exc, RemoteError):
                delay = max(delay, exc.retry_after)
            job.status = "retry_wait" if job.attempt_count < settings().job_max_attempts else "failed"
            job.last_error = code
            job.scheduled_at = now() + timedelta(seconds=delay)
            if job.status == "failed":
                job.dedup_key = None
                job.finished_at = now()
            if job.job_type == "collect":
                source = session.get(Source, job.source_id)
                source.error_count += 1
                source.checked_count += 1
                source.last_checked_at = now()
                source.last_error, source.status = code, "backoff"
                source.next_poll_at = job.scheduled_at
            if job.job_type == "notify":
                note = session.get(Notification, job.payload["notification_id"])
                note.status, note.last_error = job.status, code
            if job.job_type == "notify_batch":
                for note in session.scalars(
                    select(Notification).where(
                        Notification.channel == job.payload["channel"],
                        Notification.notification_type == "P1",
                        Notification.status != "sent",
                    )
                ):
                    note.last_error = code
                    if job.status == "failed":
                        note.status = "failed"
            session.commit()
            logger.warning("job=%s type=%s error=%s", job.id, job.job_type, code)
        return True


def main() -> None:
    setup_logging()
    heartbeat_at = 0.0
    while True:
        try:
            if time.monotonic() - heartbeat_at > 30:
                with Session() as session:
                    session.add(Metric(metric_name="worker_heartbeat", metric_value=1))
                    session.commit()
                heartbeat_at = time.monotonic()
            if not tick():
                time.sleep(1)
        except Exception as exc:
            logger.error("worker_loop error=%s", type(exc).__name__)
            time.sleep(5)


if __name__ == "__main__":
    main()
