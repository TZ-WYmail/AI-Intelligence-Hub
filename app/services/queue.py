from datetime import timedelta

from sqlalchemy import case, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Job, now


def enqueue(
    session: Session,
    kind: str,
    key: str,
    payload: dict | None = None,
    source_id: str | None = None,
    scheduled_at=None,
) -> Job | None:
    if session.scalar(select(Job).where(Job.dedup_key == key)):
        return None
    try:
        with session.begin_nested():
            job = Job(
                job_type=kind,
                dedup_key=key,
                payload=payload or {},
                source_id=source_id,
                scheduled_at=scheduled_at or now(),
            )
            session.add(job)
            session.flush()
        return job
    except IntegrityError:
        return None


def recover(session: Session) -> int:
    cutoff = now() - timedelta(seconds=settings().job_lease_seconds)
    jobs = session.scalars(select(Job).where(Job.status == "running", Job.started_at < cutoff)).all()
    for job in jobs:
        job.status = "retry_wait" if job.attempt_count < settings().job_max_attempts else "failed"
        job.last_error = "LEASE_EXPIRED"
        job.scheduled_at = now()
        if job.status == "failed":
            job.dedup_key = None
    return len(jobs)


def claim(session: Session) -> Job | None:
    # Atomic compare-and-set prevents two processes from owning the same job.
    job_id = session.scalar(
        select(Job.id)
        .where(Job.status.in_(["pending", "retry_wait"]), Job.scheduled_at <= now())
        .order_by(
            case(
                (Job.job_type == "notify", 0),
                (Job.job_type == "notify_batch", 1),
                (Job.job_type == "analyze", 2),
                else_=3,
            ),
            Job.scheduled_at,
        )
        .limit(1)
    )
    if not job_id:
        return None
    changed = session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status.in_(["pending", "retry_wait"]))
        .values(status="running", started_at=now(), attempt_count=Job.attempt_count + 1)
    )
    session.commit()
    return session.get(Job, job_id) if changed.rowcount == 1 else None
