from datetime import timedelta

from sqlalchemy import case, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Analysis, Job, Notification, now
from app.processing.normalizer import timestamp


class InvalidJobPayload(ValueError):
    """A job's own parameters are unusable; retrying the same payload cannot help."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def payload_error(job: Job) -> str | None:
    """Return a short error code when a job is missing or carries unusable parameters."""
    payload = job.payload
    if not isinstance(payload, dict):
        return "INVALID_JOB_PAYLOAD"
    if job.job_type == "collect":
        return None if job.source_id else "MISSING_SOURCE_ID"
    if job.job_type == "analyze":
        return None if payload.get("article_id") else "MISSING_ARTICLE_ID"
    if job.job_type == "notify":
        return None if payload.get("notification_id") else "MISSING_NOTIFICATION_ID"
    if job.job_type == "notify_batch":
        if not payload.get("channel"):
            return "MISSING_CHANNEL"
        if not payload.get("cutoff"):
            return "MISSING_CUTOFF"
        try:
            timestamp(payload["cutoff"])
        except (TypeError, ValueError, OverflowError):
            return "INVALID_CUTOFF"
        return None
    if job.job_type == "daily_digest":
        for field in ("start", "end"):
            if not payload.get(field):
                return "MISSING_DIGEST_WINDOW"
            try:
                timestamp(payload[field])
            except (TypeError, ValueError, OverflowError):
                return "INVALID_DIGEST_WINDOW"
        return None
    if job.job_type == "backup":
        return None
    return "UNKNOWN_JOB_TYPE"


def notifications_for_job(session: Session, job: Job) -> list[Notification]:
    """Select the notifications a job owns, using the same boundary for send and failure."""
    if payload_error(job):
        return []
    if job.job_type == "notify":
        note = session.get(Notification, job.payload["notification_id"])
        return [note] if note and note.status != "sent" else []
    if job.job_type == "notify_batch":
        channel, cutoff = job.payload["channel"], job.payload["cutoff"]
        return list(
            session.scalars(
                select(Notification).where(
                    Notification.channel == channel,
                    Notification.notification_type == "P1",
                    Notification.status.in_(["pending", "retry_wait"]),
                    Notification.created_at <= timestamp(cutoff),
                )
            )
        )
    return []


def sync_notification_state(session: Session, job: Job, status: str, error: str) -> None:
    # Bad parameters make the job's notification scope unknowable; never guess a
    # wider scope and never re-raise into the worker's own failure handling.
    if payload_error(job):
        return
    for note in notifications_for_job(session, job):
        note.status, note.last_error = status, error


def interrupt_started_analysis(session: Session, job: Job) -> None:
    """Close an AI attempt that was in flight when its final lease expired."""
    article_id = job.payload.get("article_id") if isinstance(job.payload, dict) else None
    if not article_id:
        return
    usage = session.scalar(
        select(Analysis)
        .where(
            Analysis.article_id == article_id,
            Analysis.provider == "openai-compatible",
            Analysis.status == "started",
        )
        .order_by(Analysis.created_at.desc())
    )
    if usage:
        # Keep estimated_cost: the provider may have billed the attempt, and the
        # reserved cost still occupies the daily/monthly budget window.
        usage.status = "interrupted"


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
        error = payload_error(job)
        if error:
            # A corrupt payload is terminal and local to this job: fail it here so
            # one bad row cannot abort recovery (and every later job) with ValueError.
            job.status, job.last_error = "failed", error
            job.scheduled_at, job.finished_at, job.dedup_key = now(), now(), None
            continue
        job.status = "retry_wait" if job.attempt_count < settings().job_max_attempts else "failed"
        job.last_error = "LEASE_EXPIRED"
        job.scheduled_at = now()
        if job.status == "failed":
            job.dedup_key = None
            job.finished_at = now()
            if job.job_type == "analyze":
                interrupt_started_analysis(session, job)
        # Keep the delivery record consistent with the recovered job, scoped to its cutoff.
        sync_notification_state(session, job, job.status, "LEASE_EXPIRED")
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
