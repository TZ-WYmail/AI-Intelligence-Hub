from datetime import datetime, timedelta

from sqlalchemy import case, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Analysis, Article, Job, Notification, Source, now
from app.processing.normalizer import timestamp

ACTIVE_JOB_STATUSES = ("pending", "retry_wait", "running")
RETRYABLE_JOB_TYPES = frozenset({"collect", "analyze", "daily_digest", "backup"})


class InvalidJobPayload(ValueError):
    """A job's own parameters are unusable; retrying the same payload cannot help."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class JobRetryRejected(ValueError):
    """A failed job cannot be safely requeued; carries the HTTP response contract."""

    def __init__(self, code: str, detail: str, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.status_code = status_code


def identifier_error(value) -> str | None:
    """Reject empty and non-string identifiers instead of trusting truthiness.

    A list/dict/int is truthy but cannot address a row safely; letting it reach a
    query either raises an opaque 500 or silently matches nothing.
    """
    if not value:
        return "MISSING"
    if not isinstance(value, str) or not value.strip():
        return "INVALID"
    return None


def timestamp_error(value) -> tuple[datetime | None, str | None]:
    """Parse a payload timestamp, rejecting non-string values that ``timestamp`` coerces."""
    if not value:
        return None, "MISSING"
    if not isinstance(value, str):
        return None, "INVALID"
    try:
        return timestamp(value), None
    except (TypeError, ValueError, OverflowError):
        return None, "INVALID"


def payload_error(job: Job) -> str | None:
    """Return a short error code when a job is missing or carries unusable parameters."""
    payload = job.payload
    if not isinstance(payload, dict):
        return "INVALID_JOB_PAYLOAD"
    if job.job_type == "collect":
        return "MISSING_SOURCE_ID" if identifier_error(job.source_id) else None
    if job.job_type == "analyze":
        error = identifier_error(payload.get("article_id"))
        return ("MISSING_ARTICLE_ID" if error == "MISSING" else "INVALID_ARTICLE_ID") if error else None
    if job.job_type == "notify":
        error = identifier_error(payload.get("notification_id"))
        return (
            ("MISSING_NOTIFICATION_ID" if error == "MISSING" else "INVALID_NOTIFICATION_ID")
            if error
            else None
        )
    if job.job_type == "notify_batch":
        channel = payload.get("channel")
        if not channel:
            return "MISSING_CHANNEL"
        # Membership tests on untrusted JSON must not see list/dict values: an
        # unhashable payload would raise TypeError out of recover and abort the
        # whole tick instead of failing only this job.
        if not isinstance(channel, str) or channel not in {"telegram", "feishu"}:
            return "INVALID_CHANNEL"
        _, error = timestamp_error(payload.get("cutoff"))
        if error:
            return "MISSING_CUTOFF" if error == "MISSING" else "INVALID_CUTOFF"
        return None
    if job.job_type == "daily_digest":
        start, start_error = timestamp_error(payload.get("start"))
        end, end_error = timestamp_error(payload.get("end"))
        if start_error or end_error:
            if "MISSING" in {start_error, end_error}:
                return "MISSING_DIGEST_WINDOW"
            return "INVALID_DIGEST_WINDOW"
        if end <= start:
            return "INVALID_DIGEST_WINDOW"
        return None
    if job.job_type == "backup":
        return None
    return "UNKNOWN_JOB_TYPE"


def job_business_key(job: Job) -> str:
    """Canonical enqueue key for the business work a retryable job represents."""
    if job.job_type == "collect":
        return "collect:" + str(job.source_id)
    if job.job_type == "analyze":
        return "analyze:" + str(job.payload["article_id"])
    if job.job_type == "daily_digest":
        return "digest:" + str(job.payload["start"])
    if job.job_type == "backup":
        return "maintenance:backup"
    raise ValueError("UNKNOWN_JOB_TYPE")


def retry_rejection(session: Session, job: Job) -> JobRetryRejected | None:
    """Validate every precondition before a failed job may be requeued."""
    if job.job_type in {"notify", "notify_batch"}:
        return JobRetryRejected("NOTIFICATION_RETRY_FORBIDDEN", "通知任务不支持手动重试")
    if job.job_type not in RETRYABLE_JOB_TYPES:
        return JobRetryRejected("UNSUPPORTED_JOB_TYPE", "该任务类型不支持手动重试")
    error = payload_error(job)
    if error:
        return JobRetryRejected("INVALID_JOB_PAYLOAD", f"任务参数无效（{error}），无法安全重试", 422)
    if job.job_type == "collect":
        source = session.get(Source, job.source_id)
        if source is None:
            return JobRetryRejected("SOURCE_MISSING", "来源不存在，无法重试")
        if not source.enabled:
            return JobRetryRejected("SOURCE_DISABLED", "来源已停用，请先启用")
    elif job.job_type == "analyze" and session.get(Article, job.payload["article_id"]) is None:
        return JobRetryRejected("ARTICLE_MISSING", "文章不存在，无法重试")
    return None


def retry_failed_job(session: Session, job: Job) -> tuple[Job, bool]:
    """Requeue exactly one failed job; returns (job, whether this call queued it).

    The transition uses a compare-and-set on status='failed'. A Worker may claim
    the row between our read and write, so a read-only status check is never
    enough. Failure history (attempt_count, last_error) is intentionally kept.
    """
    # Keep a plain identifier: rollback expires the ORM object, and a concurrent
    # retention delete would make reading ``job.id`` raise ObjectDeletedError
    # instead of the intended 404 response.
    job_id = job.id
    if job.job_type in {"notify", "notify_batch"}:
        raise JobRetryRejected("NOTIFICATION_RETRY_FORBIDDEN", "通知任务不支持手动重试")
    if job.job_type not in RETRYABLE_JOB_TYPES:
        raise JobRetryRejected("UNSUPPORTED_JOB_TYPE", "该任务类型不支持手动重试")
    if job.status in {"pending", "retry_wait"}:
        return job, False
    if job.status == "running":
        raise JobRetryRejected("JOB_RUNNING", "任务正在运行，不能重试")
    if job.status == "completed":
        raise JobRetryRejected("JOB_COMPLETED", "任务已完成，不能重试")
    if job.status != "failed":
        raise JobRetryRejected("JOB_NOT_FAILED", "只有失败任务可以重试")

    rejection = retry_rejection(session, job)
    if rejection:
        raise rejection

    key = job_business_key(job)
    active = session.scalar(
        select(Job).where(
            Job.dedup_key == key,
            Job.id != job_id,
            Job.status.in_(ACTIVE_JOB_STATUSES),
        )
    )
    if active:
        return active, False
    try:
        changed = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "failed")
            .values(status="pending", scheduled_at=now(), dedup_key=key)
        )
    except IntegrityError:
        # A concurrent retry/enqueue took the canonical business key first.
        session.rollback()
        active = session.scalar(select(Job).where(Job.dedup_key == key, Job.status.in_(ACTIVE_JOB_STATUSES)))
        if active:
            return active, False
        raise JobRetryRejected("RETRY_CONFLICT", "任务状态冲突，请刷新后重试") from None
    if changed.rowcount != 1:
        # The row left 'failed' after our read (claimed by a Worker, or removed
        # by retention). Re-read by stored id only; never touch the expired object.
        session.rollback()
        current = session.get(Job, job_id)
        if current is None:
            raise JobRetryRejected("JOB_NOT_FOUND", "任务不存在", 404)
        if current.status in {"pending", "retry_wait"}:
            return current, False
        raise JobRetryRejected("JOB_STATE_CHANGED", "任务状态已变化，不能重试")
    session.commit()
    session.refresh(job)
    return job, True


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
