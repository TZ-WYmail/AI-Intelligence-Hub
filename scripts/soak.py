"""Bounded, isolated synthetic soak for the real collection/queue/notification code.

This tool never touches the project database, network, model, or notification
providers.  It creates a brand-new private run directory under ``data/soak/``,
loads an explicit synthetic ``Settings`` (the project ``.env`` file is disabled
*before* ``app.db``/worker/pipeline are imported), and drives the existing RSS
parser, normalizer, dedup rules, queue claim/recover logic, and notification
state machine with a mock feed and a local notifier.

The run is intentionally small: one worker, five synthetic sources, one new
article plus one known article per source per interval.  A checkpoint is written
atomically after every batch.  The default duration is 86400 seconds, but the
tool itself never starts that long run; start it only after review.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import socket
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
# Support both ``python -m scripts.soak`` and ``python scripts/soak.py`` without
# relying on an inherited PYTHONPATH.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA = "soak/1"
SOAK_ROOT = REPO_ROOT / "data" / "soak"
FULL_SOAK_SECONDS = 86400.0
DEFAULT_DURATION_SECONDS = FULL_SOAK_SECONDS
MAX_DURATION_SECONDS = 7 * 86400.0
MIN_DURATION_SECONDS = 0.1
DEFAULT_INTERVAL_SECONDS = 60.0
MIN_INTERVAL_SECONDS = 0.05
MAX_INTERVAL_SECONDS = 86400.0
SOURCE_COUNT = 5
LEASE_SOURCE_ID = "soak-lease-probe"
INDUSTRY_ID = "soak-ai"
FAILURE_BATCH = 2
LEASE_BATCH = 3
REOPEN_BATCH = 4
MAX_TICKS_PER_BATCH = 200
# Continuity: allowed gap between completed batches.  Two intervals cover the
# normal sleep plus processing, with a small floor for short review runs.
MIN_CADENCE_GAP_SECONDS = 2.0
CADENCE_GAP_PADDING_SECONDS = 2.0
WALL_DRIFT_LIMIT_SECONDS = 5.0
CHECKPOINT_NAME = "checkpoint.json"
SUMMARY_NAME = "summary.json"
STOP_NAME = "stop"
DB_NAME = "soak.db"
LOG_NAME = "soak.log"
NOTIFICATIONS_NAME = "notifications.jsonl"
SOURCE_IDS = tuple(f"soak-src-{index}" for index in range(SOURCE_COUNT))
SOAK_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

SYNTHETIC_ENV = {
    "APP_ENV": "soak",
    "AI_ENABLED": "false",
    "AI_API_KEY": "",
    "AI_MODEL": "",
    "TELEGRAM_ENABLED": "true",
    "TELEGRAM_BOT_TOKEN": "synthetic-soak-token",
    "TELEGRAM_CHAT_ID": "synthetic-soak-chat",
    "FEISHU_ENABLED": "false",
    "FEISHU_WEBHOOK_URL": "",
    "QUIET_START_HOUR": "0",
    "QUIET_END_HOUR": "0",
    "BACKUP_ENABLED": "false",
    "DAILY_DIGEST_ENABLED": "false",
    "ADMIN_API_KEY": "",
    "JOB_MAX_ATTEMPTS": "5",
    "JOB_LEASE_SECONDS": "120",
    "HTTP_TIMEOUT": "5",
}

GUARD_SURFACES = (
    "socket.getaddrinfo",
    "socket.connect",
    "socket.connect_ex",
    "socket.sendto",
    "socket.sendmsg",
    "httpx.Client",
    "httpx.AsyncClient",
    "security.fetch",
    "rss.fetch",
    "gdelt.fetch",
)

LIMITATIONS = (
    "Synthetic RSS, local mock notifications, and a mocked HTTP_429 injected through the "
    "collector patch. No real network, AI, notification, third-party outage, OS crash, "
    "process kill, controller resume, or multi-worker race is exercised. Lease recovery "
    "is a synthetic stale row; the database reopen is a deliberate close/reopen, not crash "
    "recovery. A short run is code-path evidence, not 24h stability or a production SLA."
)


class SoakError(RuntimeError):
    """A safety or configuration problem; the run must not start."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class NetworkBlocked(RuntimeError):
    """Raised by the in-process network guard; counted and never retried."""


@dataclass(frozen=True)
class SoakConfig:
    run_dir: Path
    duration_seconds: float
    interval_seconds: float


@dataclass
class FeedState:
    batch: int = 0
    failure_pending: bool = False
    failure_injected: int = 0
    failure_batch: int | None = None
    successes: list[tuple[str, int]] = field(default_factory=list)
    duplicate_entries: int = 0
    lease_collects: int = 0


@dataclass
class RunState:
    batches: int = 0
    ticks: int = 0
    tick_limit_hits: int = 0
    notifications_delivered: int = 0
    clock_advances: int = 0
    stop_requested: bool = False
    failure_observed: bool = False
    failure_recovered: bool = False
    lease_injected: bool = False
    lease_job_id: str | None = None
    lease_result: dict = field(default_factory=dict)
    reopen_result: dict = field(default_factory=dict)
    unexpected_errors: list[dict] = field(default_factory=list)
    last_progress_at: float = 0.0
    last_progress_wall: float = 0.0
    allowed_gap_seconds: float = 0.0
    max_gap_seconds: float = 0.0
    max_wall_gap_seconds: float = 0.0
    final_gap_seconds: float = 0.0
    final_wall_gap_seconds: float = 0.0
    max_wall_drift_seconds: float = 0.0
    final_wall_drift_seconds: float = 0.0
    cadence_violations: list[dict] = field(default_factory=list)


class NetworkGuard:
    """Reject the project's real-network surfaces and count every attempt."""

    def __init__(self) -> None:
        self.attempts = 0
        self.surfaces: list[str] = []
        self._patches: list = []

    def _record(self, surface: str) -> None:
        self.attempts += 1
        if surface not in self.surfaces:
            self.surfaces.append(surface)

    def _block(self, surface: str):
        def blocked(*args, **kwargs):
            self._record(surface)
            raise NetworkBlocked(surface)

        return blocked

    def __enter__(self):
        import httpx

        from app.collectors import gdelt, rss
        from app.core import security

        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex
        original_sendto = socket.socket.sendto
        original_sendmsg = getattr(socket.socket, "sendmsg", None)

        def blocked_connect(sock, address):
            if getattr(sock, "family", None) in {socket.AF_INET, socket.AF_INET6}:
                self._record("socket.connect")
                raise NetworkBlocked("socket.connect")
            return original_connect(sock, address)

        def blocked_connect_ex(sock, address):
            if getattr(sock, "family", None) in {socket.AF_INET, socket.AF_INET6}:
                self._record("socket.connect_ex")
                raise NetworkBlocked("socket.connect_ex")
            return original_connect_ex(sock, address)

        def blocked_sendto(sock, *args, **kwargs):
            if getattr(sock, "family", None) in {socket.AF_INET, socket.AF_INET6}:
                self._record("socket.sendto")
                raise NetworkBlocked("socket.sendto")
            return original_sendto(sock, *args, **kwargs)

        self._patches = [
            patch.object(socket, "getaddrinfo", self._block("socket.getaddrinfo")),
            patch.object(socket.socket, "connect", blocked_connect),
            patch.object(socket.socket, "connect_ex", blocked_connect_ex),
            patch.object(socket.socket, "sendto", blocked_sendto),
            patch.object(httpx, "Client", self._block("httpx.Client")),
            patch.object(httpx, "AsyncClient", self._block("httpx.AsyncClient")),
            patch.object(security, "fetch", self._block("security.fetch")),
            patch.object(rss, "fetch", self._block("rss.fetch")),
            patch.object(gdelt, "fetch", self._block("gdelt.fetch")),
        ]
        if original_sendmsg is not None:

            def blocked_sendmsg(sock, *args, **kwargs):
                if getattr(sock, "family", None) in {socket.AF_INET, socket.AF_INET6}:
                    self._record("socket.sendmsg")
                    raise NetworkBlocked("socket.sendmsg")
                return original_sendmsg(sock, *args, **kwargs)

            self._patches.append(patch.object(socket.socket, "sendmsg", blocked_sendmsg))
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        for item in reversed(self._patches):
            item.stop()
        self._patches = []
        return False


def utcnow() -> datetime:
    return datetime.now(UTC)


def utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def generate_run_id() -> str:
    return utcnow().strftime("run-%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def validate_timing(duration_seconds, interval_seconds) -> tuple[float, float]:
    """Reject bools, NaN, infinities, non-numbers, and out-of-range values."""
    for value in (duration_seconds, interval_seconds):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SoakError("TIMING_INVALID")
    duration = float(duration_seconds)
    interval = float(interval_seconds)
    if duration <= 0:
        raise SoakError("DURATION_INVALID")
    if duration < MIN_DURATION_SECONDS:
        raise SoakError("DURATION_TOO_SMALL")
    if duration > MAX_DURATION_SECONDS:
        raise SoakError("DURATION_TOO_LARGE")
    if interval <= 0:
        raise SoakError("INTERVAL_INVALID")
    if interval < MIN_INTERVAL_SECONDS:
        raise SoakError("INTERVAL_TOO_SMALL")
    if interval > MAX_INTERVAL_SECONDS:
        raise SoakError("INTERVAL_TOO_LARGE")
    return duration, interval


def prepare_run_dir(raw_path: Path, soak_root: Path | None = None) -> tuple[Path, Path]:
    """Create a brand-new direct child of the tool's private soak root.

    Existing directories, symlinks, and paths outside ``data/soak/`` are refused.
    Nothing in an existing run is ever overwritten.
    """
    root = Path(soak_root) if soak_root is not None else SOAK_ROOT
    if root.is_symlink():
        raise SoakError("SOAK_ROOT_IS_SYMLINK")
    if soak_root is None and root.resolve(strict=False) != SOAK_ROOT:
        raise SoakError("SOAK_ROOT_UNSAFE")
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SoakError("SOAK_ROOT_CREATE_FAILED") from exc
    if root.is_symlink() or not root.is_dir():
        raise SoakError("SOAK_ROOT_UNSAFE")
    root_resolved = root.resolve()

    candidate = Path(os.path.abspath(os.fspath(raw_path)))
    try:
        parent = candidate.parent.resolve(strict=True)
    except OSError as exc:
        raise SoakError("RUN_DIR_PARENT_MISSING") from exc
    if parent != root_resolved:
        raise SoakError("RUN_DIR_OUTSIDE_SOAK_ROOT")
    if candidate.is_symlink() or candidate.exists():
        raise SoakError("RUN_DIR_EXISTS")
    try:
        candidate.mkdir(mode=0o700, exist_ok=False)
    except FileExistsError as exc:
        raise SoakError("RUN_DIR_EXISTS") from exc
    except OSError as exc:
        raise SoakError("RUN_DIR_CREATE_FAILED") from exc
    if candidate.is_symlink() or candidate.resolve().parent != root_resolved:
        raise SoakError("RUN_DIR_UNSAFE")

    database = candidate / DB_NAME
    if database.is_symlink() or database.exists():
        raise SoakError("RUN_DB_EXISTS")
    return candidate, database


def write_json_atomic(path: Path, payload: dict, *, expected_run_id: str, label: str = "CHECKPOINT") -> None:
    """Publish JSON via an exclusive random temp file in the target directory.

    Only a file owned by this run (same ``run_id``) may be replaced.  Symlinks are
    refused, and a pre-existing foreign sidecar is never clobbered.
    """
    path = Path(path)
    if path.is_symlink():
        raise SoakError(f"{label}_IS_SYMLINK")
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SoakError(f"{label}_UNREADABLE") from exc
        if not isinstance(existing, dict) or existing.get("run_id") != expected_run_id:
            raise SoakError(f"{label}_OWNER_MISMATCH")
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if path.is_symlink():
            raise SoakError(f"{label}_IS_SYMLINK")
        os.replace(temporary, path)
        temporary = None
    except SoakError:
        raise
    except OSError as exc:
        raise SoakError(f"{label}_WRITE_FAILED") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def append_jsonl(path: Path, payload: dict) -> None:
    """Append one synthetic JSON log line without following a changed symlink."""
    path = Path(path)
    if path.is_symlink():
        raise SoakError("LOG_IS_SYMLINK")
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def log_event(path: Path, run_id: str, event: str, **fields) -> None:
    append_jsonl(path, {"ts": utc_iso(utcnow()), "run_id": run_id, "event": event, **fields})


def full_soak_completed(duration_seconds: float, status: str, clock_source: str) -> bool:
    """Never let a mocked clock or a short requested duration claim a 24h soak."""
    return status == "completed" and clock_source == "monotonic" and duration_seconds >= FULL_SOAK_SECONDS


def completion_scope(status: str, clock_source: str, full_completed: bool) -> str:
    if full_completed:
        return "full_86400_target"
    if status == "stopped":
        return "stopped"
    if status != "completed":
        return "failed"
    if clock_source != "monotonic":
        return "mock_clock_run"
    return "requested_duration_only"


def allowed_gap_seconds(interval_seconds: float) -> float:
    """Maximum tolerated gap between completed batches."""
    return max(MIN_CADENCE_GAP_SECONDS, interval_seconds * 2 + CADENCE_GAP_PADDING_SECONDS)


def continuity_ok(state: RunState) -> bool:
    return (
        not state.cadence_violations
        and state.max_gap_seconds <= state.allowed_gap_seconds
        and state.max_wall_gap_seconds <= state.allowed_gap_seconds
        and state.max_wall_drift_seconds <= WALL_DRIFT_LIMIT_SECONDS
    )


def observe_gap(state: RunState, now: float, now_wall: float, phase: str, *, advance: bool) -> bool:
    """Record liveness from monotonic time and from UTC wall time.

    ``time.monotonic`` does not include machine suspend on Linux, so it cannot
    detect a laptop sleeping through the run.  The wall-clock gap is an
    independent, conservative discontinuity check; the wall/monotonic drift
    (wall elapsed minus monotonic elapsed since the last progress) grows during
    suspend but not during ordinary sleeping/waiting.  Both flag large backward
    or forward clock jumps.  They are not used for ``observed_seconds``.
    """
    gap = max(0.0, now - state.last_progress_at)
    wall_gap = abs(now_wall - state.last_progress_wall)
    wall_drift = abs((now_wall - state.last_progress_wall) - (now - state.last_progress_at))
    state.max_gap_seconds = max(state.max_gap_seconds, gap)
    state.max_wall_gap_seconds = max(state.max_wall_gap_seconds, wall_gap)
    state.max_wall_drift_seconds = max(state.max_wall_drift_seconds, wall_drift)
    reasons = []
    if gap > state.allowed_gap_seconds:
        reasons.append("monotonic")
    if wall_gap > state.allowed_gap_seconds:
        reasons.append("wall_clock")
    if wall_drift > WALL_DRIFT_LIMIT_SECONDS:
        reasons.append("wall_drift")
    ok = not reasons
    if reasons:
        violation = {
            "phase": phase,
            "reason": "+".join(reasons),
            "gap_seconds": round(gap, 3),
            "wall_gap_seconds": round(wall_gap, 3),
            "wall_drift_seconds": round(wall_drift, 3),
            "allowed_seconds": round(state.allowed_gap_seconds, 3),
            "wall_drift_limit_seconds": WALL_DRIFT_LIMIT_SECONDS,
        }
        if not state.cadence_violations or state.cadence_violations[-1] != violation:
            state.cadence_violations.append(violation)
    if advance:
        state.last_progress_at = now
        state.last_progress_wall = now_wall
    return ok


def record_final_gap(state: RunState, now: float, now_wall: float, phase: str) -> None:
    state.final_gap_seconds = round(max(0.0, now - state.last_progress_at), 3)
    state.final_wall_gap_seconds = round(abs(now_wall - state.last_progress_wall), 3)
    state.final_wall_drift_seconds = round(
        abs((now_wall - state.last_progress_wall) - (now - state.last_progress_at)), 3
    )
    observe_gap(state, now, now_wall, phase, advance=False)


def continuity_payload(state: RunState) -> dict:
    return {
        "allowed_gap_seconds": round(state.allowed_gap_seconds, 3),
        "wall_drift_limit_seconds": WALL_DRIFT_LIMIT_SECONDS,
        "max_gap_seconds": round(state.max_gap_seconds, 3),
        "max_wall_gap_seconds": round(state.max_wall_gap_seconds, 3),
        "max_wall_drift_seconds": round(state.max_wall_drift_seconds, 3),
        "final_gap_seconds": state.final_gap_seconds,
        "final_wall_gap_seconds": state.final_wall_gap_seconds,
        "final_wall_drift_seconds": state.final_wall_drift_seconds,
        "cadence_violations": state.cadence_violations,
        "progress_samples": state.batches,
        "ok": continuity_ok(state),
    }


def configure_isolated(database: Path):
    """Set up synthetic settings before importing app.db/worker/pipeline.

    ``Settings.model_config['env_file']`` is disabled before any ``Settings()``
    instance exists, so the project ``.env`` cannot supply DATABASE_URL, AI, or
    notification credentials even if the environment contains production values.
    """
    os.environ.update(SYNTHETIC_ENV)
    private_url = f"sqlite:///{database}"
    os.environ["DATABASE_URL"] = private_url
    # Even a stray backup job (none are enqueued here) must stay inside the run dir.
    os.environ["BACKUP_DIRECTORY"] = str(database.parent / "disabled-backups")

    import app.core.config as config

    config.Settings.model_config["env_file"] = None
    config.settings.cache_clear()
    # Explicitly build the same local Settings the project modules must observe.
    explicit = config.Settings(_env_file=None)
    if explicit.database_url != private_url or explicit.ai_enabled:
        raise SoakError("SETTINGS_ISOLATION_FAILED")

    from sqlalchemy.orm import sessionmaker

    import app.db as db
    import app.models as models
    import app.services.pipeline as pipeline
    import app.workers.run as worker

    # In the tool process the module-level engine was already created from the
    # private URL.  The explicit rebind also makes an in-process test safe and
    # guarantees every module shares this one engine.
    db.engine.dispose()
    engine = db.make_engine(private_url)
    factory = sessionmaker(engine, expire_on_commit=False)
    db.engine, db.Session, worker.Session = engine, factory, factory

    settings_obj = worker.settings()
    shared = (
        db.settings() is settings_obj is pipeline.settings()
        and settings_obj.database_url == private_url
        and settings_obj.database_url == explicit.database_url
        and settings_obj.ai_enabled is False
    )
    if not shared:
        raise SoakError("SETTINGS_NOT_SHARED")

    models.Base.metadata.create_all(engine)
    return SimpleNamespace(
        db=db,
        models=models,
        pipeline=pipeline,
        worker=worker,
        config=config,
        settings_obj=settings_obj,
        private_url=private_url,
        shared=shared,
    )


def seed_database(ctx) -> None:
    models = ctx.models
    with ctx.db.Session() as session:
        session.add(
            models.Industry(
                id=INDUSTRY_ID,
                name="Synthetic Soak Industry",
                slug="synthetic-soak",
                keywords=["synthetic"],
                enabled=True,
                alert_threshold=0,
                channels=["telegram"],
            )
        )
        for index, source_id in enumerate(SOURCE_IDS):
            session.add(
                models.Source(
                    id=source_id,
                    name=f"Synthetic Soak Source {index}",
                    source_type="rss",
                    url=f"https://soak.invalid/source/{index}/feed.xml",
                    industry_ids=[INDUSTRY_ID],
                    enabled=True,
                    trust_level=0.8,
                    poll_interval_seconds=60,
                )
            )
        session.add(
            models.Source(
                id=LEASE_SOURCE_ID,
                name="Synthetic Soak Lease Probe",
                source_type="rss",
                url="https://soak.invalid/lease/feed.xml",
                industry_ids=[INDUSTRY_ID],
                enabled=True,
                trust_level=0.8,
                poll_interval_seconds=60,
            )
        )
        session.commit()


def feed_item(index: int, batch: int) -> str:
    title = f"Synthetic soak source {index} fact {batch} reports stable throughput"
    summary = f"Mock synthetic article for source {index} batch {batch}; no real content."
    published = format_datetime(SOAK_EPOCH + timedelta(minutes=batch), usegmt=True)
    return (
        "<item>"
        f"<guid>soak-{index}-{batch}</guid>"
        f"<title>{html.escape(title)}</title>"
        f"<link>https://soak.invalid/source/{index}/article/{batch}</link>"
        f"<description>{html.escape(summary)}</description>"
        f"<pubDate>{published}</pubDate>"
        "</item>"
    )


def feed_body(index: int, batch: int) -> bytes:
    items = []
    if batch > 0:
        # The previous batch's article is re-sent verbatim; the real dedup rules
        # must leave the row count unchanged.
        items.append(feed_item(index, batch - 1))
    items.append(feed_item(index, batch))
    body = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        f"<title>Synthetic Soak {index}</title>"
        f"<link>https://soak.invalid/source/{index}</link>"
        "<description>synthetic</description>" + "".join(items) + "</channel></rss>"
    )
    return body.encode()


EMPTY_FEED = (
    b'<?xml version="1.0"?><rss version="2.0"><channel>'
    b"<title>Empty Synthetic Feed</title>"
    b"<link>https://soak.invalid/lease</link>"
    b"<description>empty</description>"
    b"</channel></rss>"
)


def make_synthetic_feed(ctx, feed_state: FeedState):
    from app.collectors.base import Collection
    from app.collectors.rss import parse_feed
    from app.core.security import RemoteError

    class SyntheticFeed:
        def __init__(self, source):
            self.source = source

        def collect(self, url, headers):
            if self.source.id == LEASE_SOURCE_ID:
                feed_state.lease_collects += 1
                return Collection(parse_feed(EMPTY_FEED), {"etag": "synthetic-lease"})
            try:
                index = SOURCE_IDS.index(self.source.id)
            except ValueError:
                raise RemoteError("UNKNOWN_SOAK_SOURCE", 0) from None
            if (
                feed_state.failure_pending
                and self.source.id == SOURCE_IDS[0]
                and feed_state.batch == FAILURE_BATCH
            ):
                feed_state.failure_pending = False
                feed_state.failure_injected += 1
                feed_state.failure_batch = feed_state.batch
                raise RemoteError("HTTP_429", 0)
            batch = feed_state.batch
            if batch > 0 and (self.source.id, batch) not in feed_state.successes:
                feed_state.duplicate_entries += 1
            feed_state.successes.append((self.source.id, batch))
            return Collection(parse_feed(feed_body(index, batch)), {"etag": f"synthetic-{batch}"})

    return lambda source: SyntheticFeed(source)


def make_local_notifier(state: RunState, path: Path, run_id: str):
    class LocalNotifier:
        def __init__(self, channel, config):
            self.channel = channel

        def send(self, payload):
            append_jsonl(
                path,
                {
                    "ts": utc_iso(utcnow()),
                    "run_id": run_id,
                    "channel": self.channel,
                    "level": payload.get("level"),
                    "title": payload.get("title"),
                    "score": payload.get("score"),
                    "url": payload.get("url"),
                    "synthetic": True,
                },
            )
            state.notifications_delivered += 1

    return LocalNotifier


def stop_requested(run_dir: Path) -> bool:
    return os.path.lexists(run_dir / STOP_NAME)


def database_counts(ctx) -> dict:
    from sqlalchemy import func, select

    models = ctx.models
    with ctx.db.Session() as session:
        job_status = dict(
            session.execute(select(models.Job.status, func.count()).group_by(models.Job.status)).all()
        )
        failed_jobs = [
            {"job_type": job.job_type, "error": job.last_error}
            for job in session.scalars(
                select(models.Job).where(models.Job.status == "failed").order_by(models.Job.id)
            )
        ]
        source_errors = [
            {"source_id": source.id, "status": source.status, "error": source.last_error}
            for source in session.scalars(
                select(models.Source).where(models.Source.status != "healthy").order_by(models.Source.id)
            )
        ]
        return {
            "articles": session.scalar(select(func.count()).select_from(models.Article)),
            "events": session.scalar(select(func.count()).select_from(models.Event)),
            "notifications": session.scalar(select(func.count()).select_from(models.Notification)),
            "notifications_sent": session.scalar(
                select(func.count())
                .select_from(models.Notification)
                .where(models.Notification.status == "sent")
            ),
            "jobs": session.scalar(select(func.count()).select_from(models.Job)),
            "job_status": job_status,
            "failed_jobs": failed_jobs,
            "source_errors": source_errors,
            "ai_model_calls": session.scalar(
                select(func.count())
                .select_from(models.Analysis)
                .where(models.Analysis.provider == "openai-compatible")
            ),
        }


def article_pairs(ctx) -> set[tuple[str, int]]:
    from sqlalchemy import select

    models = ctx.models
    with ctx.db.Session() as session:
        return {
            (source_id, int(external_id.rsplit("-", 1)[-1]))
            for source_id, external_id in session.execute(
                select(models.Article.source_id, models.Article.external_id)
            ).all()
        }


def enqueue_collects(ctx) -> int:
    from app.services.queue import enqueue

    models = ctx.models
    enqueued = 0
    with ctx.db.Session() as session:
        for source_id in SOURCE_IDS:
            source = session.get(models.Source, source_id)
            if (
                source
                and source.enabled
                and enqueue(session, "collect", f"collect:{source_id}", source_id=source_id)
            ):
                enqueued += 1
        session.commit()
    return enqueued


def run_ticks(ctx, state: RunState, run_dir: Path, limit: int) -> tuple[int, bool]:
    ticks = 0
    while ticks < limit:
        if stop_requested(run_dir):
            state.stop_requested = True
            return ticks, False
        if not ctx.worker.tick():
            return ticks, False
        ticks += 1
    return ticks, True


def inject_lease(ctx, state: RunState) -> str | None:
    models = ctx.models
    started = models.now() - timedelta(seconds=ctx.settings_obj.job_lease_seconds + 1)
    with ctx.db.Session() as session:
        job = models.Job(
            job_type="collect",
            dedup_key=f"collect:{LEASE_SOURCE_ID}",
            payload={},
            source_id=LEASE_SOURCE_ID,
            status="running",
            attempt_count=0,
            started_at=started,
        )
        session.add(job)
        session.commit()
        state.lease_injected = True
        state.lease_job_id = job.id
        return job.id


def finish_lease(ctx, state: RunState) -> None:
    if not state.lease_job_id:
        return
    with ctx.db.Session() as session:
        job = session.get(ctx.models.Job, state.lease_job_id)
        status = job.status if job else "missing"
        attempts = job.attempt_count if job else 0
    state.lease_result = {
        "injected": True,
        "job_type": "collect",
        "status_before": "running",
        "status_after": status,
        "attempts_after": attempts,
        "lease_seconds": ctx.settings_obj.job_lease_seconds,
        "recovered": status == "completed",
    }


def recover_injected_failure(ctx, feed_state: FeedState, state: RunState, run_dir: Path) -> None:
    from sqlalchemy import select

    if state.stop_requested or not feed_state.failure_injected:
        return
    models = ctx.models
    source_id = SOURCE_IDS[0]
    with ctx.db.Session() as session:
        job = session.scalar(
            select(models.Job).where(
                models.Job.dedup_key == f"collect:{source_id}",
                models.Job.status == "retry_wait",
            )
        )
        if job is None:
            state.unexpected_errors.append({"kind": "expected_failure_missing", "source_id": source_id})
            return
        job_id = job.id
        state.failure_observed = job.last_error == "HTTP_429"
        # Simulated clock advance: move only this synthetic job's schedule to
        # "now" so the bounded run can observe the retry.  No system clock is
        # changed and this is not a real outage recovery.
        job.scheduled_at = models.now()
        session.commit()
        state.clock_advances += 1
    ticks, limit = run_ticks(ctx, state, run_dir, MAX_TICKS_PER_BATCH)
    state.ticks += ticks
    if limit:
        state.tick_limit_hits += 1
    with ctx.db.Session() as session:
        job = session.get(models.Job, job_id)
        completed = job is not None and job.status == "completed"
    state.failure_recovered = (
        completed
        and state.failure_observed
        and (source_id, feed_state.failure_batch or 0) in feed_state.successes
    )


def reopen_database(ctx, state: RunState) -> None:
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    before = database_counts(ctx)
    ctx.db.engine.dispose()
    engine = ctx.db.make_engine(ctx.private_url)
    factory = sessionmaker(engine, expire_on_commit=False)
    ctx.db.engine, ctx.db.Session, ctx.worker.Session = engine, factory, factory
    with factory() as session:
        integrity = session.execute(text("PRAGMA integrity_check")).scalar()
    after = database_counts(ctx)
    keys = ("articles", "events", "notifications", "jobs")
    preserved = all(before[key] == after[key] for key in keys)
    state.reopen_result = {
        "performed": True,
        "batch": REOPEN_BATCH,
        "integrity_check": integrity,
        "before": {key: before[key] for key in keys},
        "after": {key: after[key] for key in keys},
        "committed_rows_preserved": preserved,
    }


def expected_failures(state: RunState, feed_state: FeedState) -> list[dict]:
    if not feed_state.failure_injected:
        return [
            {
                "kind": "mock_source_http_429",
                "source_id": SOURCE_IDS[0],
                "observed": False,
                "recovered": False,
            }
        ]
    return [
        {
            "kind": "mock_source_http_429",
            "source_id": SOURCE_IDS[0],
            "batch": feed_state.failure_batch,
            "observed": state.failure_observed,
            "recovered": state.failure_recovered,
            "simulated_schedule_advance": state.clock_advances,
        }
    ]


def collect_unexpected(ctx, counts: dict, state: RunState, guard: NetworkGuard) -> list[dict]:
    unexpected = list(state.unexpected_errors)
    for job in counts["failed_jobs"]:
        unexpected.append({"kind": "failed_job", **job})
    if guard.attempts:
        unexpected.append({"kind": "network_blocked", "attempts": guard.attempts, "surfaces": guard.surfaces})
    if state.tick_limit_hits:
        unexpected.append({"kind": "tick_limit_hit", "count": state.tick_limit_hits})
    if state.notifications_delivered != counts["notifications_sent"]:
        unexpected.append(
            {
                "kind": "notification_count_mismatch",
                "delivered": state.notifications_delivered,
                "sent": counts["notifications_sent"],
            }
        )
    if counts["ai_model_calls"]:
        unexpected.append({"kind": "ai_model_calls", "count": counts["ai_model_calls"]})
    if state.cadence_violations:
        unexpected.append({"kind": "continuity_gap", "violations": state.cadence_violations})
    return unexpected


def build_summary(
    *,
    ctx,
    counts: dict,
    state: RunState,
    feed_state: FeedState,
    guard: NetworkGuard,
    config: SoakConfig,
    run_id: str,
    run_dir: Path,
    started_at: str,
    observed_seconds: float,
    clock_source: str,
    status: str,
    error_code: str | None,
) -> dict:
    expected_pairs = set(feed_state.successes)
    actual_pairs = article_pairs(ctx) if ctx is not None else set()
    articles_actual = counts["articles"]
    backlog = {key: counts["job_status"].get(key, 0) for key in ("pending", "retry_wait", "running")}
    failed_jobs = counts["job_status"].get("failed", 0)
    isolation = {
        "env_file_disabled": ctx is not None and ctx.config.Settings.model_config.get("env_file") is None,
        "ai_enabled": bool(ctx is not None and ctx.settings_obj.ai_enabled),
        "private_database": bool(
            ctx is not None
            and ctx.db.engine.url.database
            and Path(ctx.db.engine.url.database).resolve() == (run_dir / DB_NAME).resolve()
        ),
        "settings_shared": bool(ctx is not None and ctx.shared),
        "network_guard_active": True,
    }
    summary = {
        "schema": SCHEMA,
        "run_id": run_id,
        "status": status,
        "error_code": error_code,
        "started_at": started_at,
        "updated_at": utc_iso(utcnow()),
        "pid": os.getpid(),
        "run_dir": str(run_dir),
        "clock": clock_source,
        "duration_seconds": config.duration_seconds,
        "interval_seconds": config.interval_seconds,
        "observed_seconds": observed_seconds,
        "full_duration_target_seconds": FULL_SOAK_SECONDS,
        "full_soak_completed": full_soak_completed(config.duration_seconds, status, clock_source),
        "completion_scope": completion_scope(
            status,
            clock_source,
            full_soak_completed(config.duration_seconds, status, clock_source),
        ),
        "simulated_sources": SOURCE_COUNT,
        "probe_sources": 1,
        "batches": state.batches,
        "ticks": state.ticks,
        "tick_limit_hits": state.tick_limit_hits,
        "articles": articles_actual,
        "events": counts["events"],
        "notifications_created": counts["notifications"],
        "notifications_sent": counts["notifications_sent"],
        "mock_notifications_delivered": state.notifications_delivered,
        "ai_model_calls": counts["ai_model_calls"],
        "backlog": backlog,
        "failed_jobs": failed_jobs,
        "failed_job_errors": counts["failed_jobs"],
        "source_errors": counts["source_errors"],
        "backlog_reasons": {
            "active_jobs": backlog,
            "failed_jobs": failed_jobs,
            "source_errors": len(counts["source_errors"]),
        },
        "duplicate_checks": {
            "batches_with_duplicate_feed_items": len(
                {batch for source_id, batch in expected_pairs if source_id in SOURCE_IDS and batch > 0}
            ),
            "duplicate_entries_served": feed_state.duplicate_entries,
            "articles_expected": len(expected_pairs),
            "articles_actual": articles_actual,
            "duplicate_rows_inserted": max(0, articles_actual - len(expected_pairs)),
            "unique_article_sets_match": actual_pairs == expected_pairs,
        },
        "failure_recovery": {
            "kind": "mock_source_http_429",
            "source_id": SOURCE_IDS[0],
            "batch": feed_state.failure_batch,
            "observed": state.failure_observed,
            "recovered": state.failure_recovered,
            "simulated_schedule_advance": state.clock_advances,
        },
        "lease_recovery": state.lease_result,
        "db_reopen": state.reopen_result,
        "network_attempts": guard.attempts,
        "network_surfaces": guard.surfaces,
        "network_guard_surfaces": list(GUARD_SURFACES),
        "continuity": continuity_payload(state),
        "isolated": isolation,
        "expected_failures": expected_failures(state, feed_state),
        "unexpected_errors": collect_unexpected(ctx, counts, state, guard),
        "failed_checks": [],
        "limitations": LIMITATIONS,
    }
    return summary


def build_checkpoint(
    *,
    counts: dict,
    state: RunState,
    feed_state: FeedState,
    guard: NetworkGuard,
    config: SoakConfig,
    run_id: str,
    started_at: str,
    observed_seconds: float,
    clock_source: str,
    status: str,
    error_code: str | None,
) -> dict:
    backlog = {key: counts["job_status"].get(key, 0) for key in ("pending", "retry_wait", "running")}
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "status": status,
        "error_code": error_code,
        "started_at": started_at,
        "updated_at": utc_iso(utcnow()),
        "pid": os.getpid(),
        "clock": clock_source,
        "duration_seconds": config.duration_seconds,
        "interval_seconds": config.interval_seconds,
        "observed_seconds": observed_seconds,
        "batches": state.batches,
        "ticks": state.ticks,
        "articles": counts["articles"],
        "events": counts["events"],
        "notifications_created": counts["notifications"],
        "notifications_sent": counts["notifications_sent"],
        "backlog": backlog,
        "failed_jobs": counts["job_status"].get("failed", 0),
        "duplicate_entries_served": feed_state.duplicate_entries,
        "failure_recovery": {
            "observed": state.failure_observed,
            "recovered": state.failure_recovered,
            "batch": feed_state.failure_batch,
        },
        "lease_recovery": state.lease_result,
        "db_reopen": state.reopen_result,
        "network_attempts": guard.attempts,
        "continuity": continuity_payload(state),
        "expected_failures": expected_failures(state, feed_state),
        "unexpected_errors": state.unexpected_errors,
        "stop_requested": state.stop_requested,
    }


def completion_failures(summary: dict) -> list[str]:
    """Assertions that must pass before a normal run may claim completion."""
    failures = []
    backlog = summary.get("backlog", {})
    for key in ("pending", "retry_wait", "running"):
        if backlog.get(key, 0):
            failures.append(f"backlog.{key}={backlog[key]}")
    if summary.get("failed_jobs", 0):
        failures.append(f"failed_jobs={summary['failed_jobs']}")
    if summary.get("network_attempts", 0):
        failures.append(f"network_attempts={summary['network_attempts']}")
    if summary.get("unexpected_errors"):
        failures.append("unexpected_errors")
    if summary.get("source_errors"):
        failures.append("source_errors")
    if summary.get("ai_model_calls", 0):
        failures.append("ai_model_calls")
    if summary.get("observed_seconds", 0) < summary.get("duration_seconds", 0):
        failures.append("observed_seconds_short")

    continuity = summary.get("continuity", {})
    if not continuity.get("ok"):
        failures.append("continuity_gap")
    if continuity.get("max_gap_seconds", 0) > continuity.get("allowed_gap_seconds", 0):
        failures.append("continuity_max_gap_exceeded")
    if continuity.get("max_wall_gap_seconds", 0) > continuity.get("allowed_gap_seconds", 0):
        failures.append("continuity_wall_gap_exceeded")
    if continuity.get("max_wall_drift_seconds", 0) > continuity.get(
        "wall_drift_limit_seconds", WALL_DRIFT_LIMIT_SECONDS
    ):
        failures.append("continuity_wall_drift_exceeded")

    duplicate = summary.get("duplicate_checks", {})
    if duplicate.get("articles_expected", 0) <= 0:
        failures.append("no_unique_articles")
    if duplicate.get("duplicate_entries_served", 0) <= 0:
        failures.append("no_duplicate_feed_items")
    if duplicate.get("duplicate_rows_inserted", 0) != 0:
        failures.append(f"duplicate_rows_inserted={duplicate['duplicate_rows_inserted']}")
    if not duplicate.get("unique_article_sets_match"):
        failures.append("unique_article_sets_mismatch")
    if summary.get("articles") != duplicate.get("articles_expected"):
        failures.append("articles_expected_mismatch")
    if summary.get("events") != summary.get("articles"):
        failures.append("events_articles_mismatch")
    if summary.get("notifications_created") != summary.get("events"):
        failures.append("notifications_created_mismatch")
    if summary.get("notifications_sent") != summary.get("notifications_created"):
        failures.append("notifications_not_all_sent")
    if summary.get("mock_notifications_delivered") != summary.get("notifications_sent"):
        failures.append("mock_notification_delivery_mismatch")

    failure = summary.get("failure_recovery", {})
    if not (failure.get("observed") and failure.get("recovered")):
        failures.append("failure_recovery_incomplete")
    lease = summary.get("lease_recovery", {})
    if not lease.get("recovered"):
        failures.append("lease_recovery_incomplete")
    reopen = summary.get("db_reopen", {})
    if not (
        reopen.get("performed")
        and reopen.get("committed_rows_preserved")
        and reopen.get("integrity_check") == "ok"
    ):
        failures.append("db_reopen_incomplete")

    isolated = summary.get("isolated", {})
    for key in ("env_file_disabled", "private_database", "settings_shared"):
        if not isolated.get(key):
            failures.append(f"isolation.{key}")
    if isolated.get("ai_enabled"):
        failures.append("isolation.ai_enabled")
    return failures


def run_soak(
    config: SoakConfig,
    *,
    clock=time.monotonic,
    wall_clock=time.time,
    sleep=time.sleep,
    clock_source: str = "monotonic",
    soak_root: Path | None = None,
) -> dict:
    run_dir, database = prepare_run_dir(config.run_dir, soak_root)
    run_id = run_dir.name
    started_at = utc_iso(utcnow())
    started_monotonic = clock()
    state = RunState()
    feed_state = FeedState()
    state.last_progress_at = started_monotonic
    state.last_progress_wall = wall_clock()
    state.allowed_gap_seconds = allowed_gap_seconds(config.interval_seconds)
    guard = NetworkGuard()
    ctx = None
    log_path = run_dir / LOG_NAME
    checkpoint_path = run_dir / CHECKPOINT_NAME
    summary_path = run_dir / SUMMARY_NAME
    notifications_path = run_dir / NOTIFICATIONS_NAME
    status = "running"
    error_code = None
    last_counts = {
        "articles": 0,
        "events": 0,
        "notifications": 0,
        "notifications_sent": 0,
        "jobs": 0,
        "job_status": {},
        "failed_jobs": [],
        "source_errors": [],
        "ai_model_calls": 0,
    }

    def observed() -> float:
        return round(clock() - started_monotonic, 3)

    write_json_atomic(
        checkpoint_path,
        build_checkpoint(
            counts=last_counts,
            state=state,
            feed_state=feed_state,
            guard=guard,
            config=config,
            run_id=run_id,
            started_at=started_at,
            observed_seconds=0.0,
            clock_source=clock_source,
            status=status,
            error_code=error_code,
        ),
        expected_run_id=run_id,
    )
    log_event(
        log_path,
        run_id,
        "start",
        duration_seconds=config.duration_seconds,
        interval_seconds=config.interval_seconds,
        simulated_sources=SOURCE_COUNT,
        pid=os.getpid(),
    )

    try:
        ctx = configure_isolated(database)
        seed_database(ctx)
        last_counts = database_counts(ctx)

        with (
            guard,
            patch.object(ctx.pipeline, "collector", make_synthetic_feed(ctx, feed_state)),
            patch.object(
                ctx.worker,
                "WebhookNotifier",
                make_local_notifier(state, notifications_path, run_id),
            ),
        ):
            deadline = started_monotonic + config.duration_seconds
            while True:
                now = clock()
                if not observe_gap(state, now, wall_clock(), "deadline_boundary", advance=False):
                    break
                if stop_requested(run_dir):
                    state.stop_requested = True
                    break
                if now >= deadline:
                    break
                batch = state.batches
                feed_state.batch = batch
                if batch == FAILURE_BATCH:
                    feed_state.failure_pending = True
                lease_job = inject_lease(ctx, state) if batch == LEASE_BATCH else None
                enqueued = enqueue_collects(ctx)
                log_event(
                    log_path,
                    run_id,
                    "batch_start",
                    batch=batch,
                    enqueued=enqueued,
                    failure_pending=feed_state.failure_pending,
                    lease_injected=bool(lease_job),
                )
                ticks, limit_hit = run_ticks(ctx, state, run_dir, MAX_TICKS_PER_BATCH)
                state.ticks += ticks
                if limit_hit:
                    state.tick_limit_hits += 1
                if state.stop_requested:
                    break
                if batch == FAILURE_BATCH:
                    recover_injected_failure(ctx, feed_state, state, run_dir)
                    log_event(
                        log_path,
                        run_id,
                        "failure_recovery",
                        batch=batch,
                        observed=state.failure_observed,
                        recovered=state.failure_recovered,
                    )
                if lease_job:
                    finish_lease(ctx, state)
                    log_event(
                        log_path,
                        run_id,
                        "lease_recovery",
                        batch=batch,
                        result=state.lease_result,
                    )
                if batch == REOPEN_BATCH:
                    reopen_database(ctx, state)
                    log_event(
                        log_path,
                        run_id,
                        "db_reopen",
                        result=state.reopen_result,
                    )
                state.batches += 1
                batch_gap_ok = observe_gap(state, clock(), wall_clock(), "batch_complete", advance=True)
                last_counts = database_counts(ctx)
                log_event(
                    log_path,
                    run_id,
                    "batch_end",
                    batch=batch,
                    ticks=ticks,
                    articles=last_counts["articles"],
                    events=last_counts["events"],
                    notifications_sent=last_counts["notifications_sent"],
                )
                write_json_atomic(
                    checkpoint_path,
                    build_checkpoint(
                        counts=last_counts,
                        state=state,
                        feed_state=feed_state,
                        guard=guard,
                        config=config,
                        run_id=run_id,
                        started_at=started_at,
                        observed_seconds=observed(),
                        clock_source=clock_source,
                        status="running",
                        error_code=None,
                    ),
                    expected_run_id=run_id,
                )
                if not batch_gap_ok:
                    break

                next_batch = started_monotonic + state.batches * config.interval_seconds
                while True:
                    if stop_requested(run_dir):
                        state.stop_requested = True
                        break
                    remaining = min(next_batch, deadline) - clock()
                    if remaining <= 0:
                        break
                    sleep(min(remaining, 1.0))
                if state.stop_requested:
                    break

            if not state.cadence_violations and not state.stop_requested:
                ticks, limit_hit = run_ticks(ctx, state, run_dir, MAX_TICKS_PER_BATCH)
                state.ticks += ticks
                if limit_hit:
                    state.tick_limit_hits += 1
            record_final_gap(state, clock(), wall_clock(), "final")
            if state.cadence_violations:
                status = "failed"
                error_code = "CONTINUITY_GAP"
            elif state.stop_requested:
                status = "stopped"
                error_code = "STOP_REQUESTED"
            else:
                status = "completed"
    except KeyboardInterrupt:
        status = "stopped"
        error_code = "INTERRUPTED"
        state.stop_requested = True
    except Exception as exc:
        status = "failed"
        error_code = getattr(exc, "code", None) or type(exc).__name__
        state.unexpected_errors.append({"kind": "run_exception", "code": error_code})

    if ctx is not None:
        try:
            last_counts = database_counts(ctx)
        except Exception as exc:
            error_code = error_code or type(exc).__name__
            state.unexpected_errors.append({"kind": "final_snapshot_failed", "code": type(exc).__name__})
            if status == "completed":
                status = "failed"

    record_final_gap(state, clock(), wall_clock(), "final_acceptance")
    if state.cadence_violations and status != "failed":
        status = "failed"
        error_code = "CONTINUITY_GAP"

    summary = build_summary(
        ctx=ctx,
        counts=last_counts,
        state=state,
        feed_state=feed_state,
        guard=guard,
        config=config,
        run_id=run_id,
        run_dir=run_dir,
        started_at=started_at,
        observed_seconds=observed(),
        clock_source=clock_source,
        status=status,
        error_code=error_code,
    )
    # Re-check after the snapshot/summary build: a pause during finalization
    # must not be hidden by the deadline exit path.
    record_final_gap(state, clock(), wall_clock(), "final_acceptance")
    summary["continuity"] = continuity_payload(state)
    summary["observed_seconds"] = observed()
    if state.cadence_violations and status != "failed":
        status = "failed"
        error_code = "CONTINUITY_GAP"
        summary["unexpected_errors"] = collect_unexpected(ctx, last_counts, state, guard)
    if status == "completed":
        failures = completion_failures(summary)
        if failures:
            status = "failed"
            error_code = "VERIFICATION_FAILED"
            summary["failed_checks"] = failures
    if summary["unexpected_errors"] and status == "completed":
        status = "failed"
        error_code = "UNEXPECTED_ERROR"
    if state.cadence_violations and "continuity_gap" not in summary["failed_checks"]:
        summary["failed_checks"].append("continuity_gap")
    summary["status"] = status
    summary["error_code"] = error_code
    summary["updated_at"] = utc_iso(utcnow())
    summary["full_soak_completed"] = full_soak_completed(config.duration_seconds, status, clock_source)
    summary["completion_scope"] = completion_scope(status, clock_source, summary["full_soak_completed"])

    write_json_atomic(summary_path, summary, expected_run_id=run_id, label="SUMMARY")
    write_json_atomic(
        checkpoint_path,
        build_checkpoint(
            counts=last_counts,
            state=state,
            feed_state=feed_state,
            guard=guard,
            config=config,
            run_id=run_id,
            started_at=started_at,
            observed_seconds=summary["observed_seconds"],
            clock_source=clock_source,
            status=status,
            error_code=error_code,
        ),
        expected_run_id=run_id,
    )
    log_event(
        log_path,
        run_id,
        "finish",
        status=status,
        error_code=error_code,
        batches=state.batches,
        observed_seconds=summary["observed_seconds"],
        failed_checks=summary["failed_checks"],
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bounded synthetic soak for the AI Intelligence Hub (no real network)."
    )
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=DEFAULT_DURATION_SECONDS,
        help=f"requested run duration (default {DEFAULT_DURATION_SECONDS:g}, max {MAX_DURATION_SECONDS:g})",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"delay between batches (default {DEFAULT_INTERVAL_SECONDS:g})",
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="new direct child of data/soak/; default is data/soak/<generated-run-id>",
    )
    return parser


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
        duration, interval = validate_timing(args.duration_seconds, args.interval_seconds)
        rules = Path.cwd() / "config" / "rules.yaml"
        if not rules.is_file() or rules.resolve() != (REPO_ROOT / "config" / "rules.yaml").resolve():
            raise SoakError("RUN_FROM_REPO_ROOT")
        run_dir = args.run_dir or (SOAK_ROOT / generate_run_id())
        summary = run_soak(SoakConfig(run_dir=run_dir, duration_seconds=duration, interval_seconds=interval))
    except SoakError as exc:
        print(f"soak error={exc.code}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("soak error=INTERRUPTED", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"soak error={type(exc).__name__}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "run_id": summary["run_id"],
                "run_dir": summary["run_dir"],
                "status": summary["status"],
                "error_code": summary["error_code"],
                "observed_seconds": summary["observed_seconds"],
                "full_soak_completed": summary["full_soak_completed"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if summary["status"] in {"completed", "stopped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
