import logging
from datetime import timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import delete, select, update

from app.core.config import settings
from app.core.logging import setup_logging
from app.db import Session
from app.models import Article, Job, Metric, Notification, Source, now
from app.services.queue import enqueue


def schedule() -> None:
    with Session() as session:
        for source in session.scalars(
            select(Source).where(Source.enabled.is_(True), Source.next_poll_at <= now())
        ):
            enqueue(session, "collect", "collect:" + source.id, source_id=source.id)
        session.add(Metric(metric_name="scheduler_heartbeat", metric_value=1))
        session.commit()


def retention() -> None:
    cfg = settings()
    with Session() as session:
        session.execute(
            update(Article)
            .where(Article.collected_at < now() - timedelta(days=cfg.raw_retention_days))
            .values(raw_payload=None)
        )
        session.execute(
            delete(Notification).where(
                Notification.status == "sent",
                Notification.sent_at < now() - timedelta(days=cfg.notification_retention_days),
            )
        )
        session.execute(
            delete(Job).where(
                Job.status.in_(["completed", "failed"]),
                Job.finished_at < now() - timedelta(days=cfg.job_retention_days),
            )
        )
        session.execute(delete(Metric).where(Metric.recorded_at < now() - timedelta(days=30)))
        session.commit()


def main() -> None:
    setup_logging()
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(schedule, "interval", seconds=10, max_instances=1, coalesce=True)
    scheduler.add_job(retention, "interval", hours=24, max_instances=1)
    try:
        schedule()
    except Exception as exc:
        logging.getLogger("scheduler").warning("initial_schedule error=%s", type(exc).__name__)
    scheduler.start()


if __name__ == "__main__":
    main()
