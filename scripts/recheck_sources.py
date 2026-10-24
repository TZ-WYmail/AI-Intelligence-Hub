"""Controlled public-source recheck for already-registered feeds.

This is intentionally narrower than ``scripts.verify_sources``:

* it only reads ``config/sources.yaml`` (never writes it, never changes enabled state);
* it performs one collection attempt per source, serially, with no automatic retry
  (the guarded fetch may follow validated redirects, each hop revalidated);
* per-domain request attempts are spaced, and all arXiv requests share a >=3.1s slot;
* it reuses the existing collectors and ``app.core.security.fetch`` limits;
* it writes a separate JSON report (no full text, no raw response bodies).

Exit codes: 0 = every source parsed, 1 = some source empty/failed, 2 = no source
parsed (total failure or empty config), 3 = config/output/integrity problem.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
import yaml

from app.collectors.gdelt import GDELTCollector
from app.collectors.rss import RSSCollector
from app.core import security
from app.core.config import settings

SCHEMA = "source-recheck/2"
ARXIV_INTERVAL_SECONDS = 3.1
DEFAULT_DOMAIN_INTERVAL_SECONDS = 1.0
REPO_ROOT = Path(__file__).resolve().parents[1]
PROTECTED_OUTPUTS = {
    (REPO_ROOT / "config" / "sources.yaml").resolve(),
    (REPO_ROOT / "docs" / "source-validation.json").resolve(),
}
ERROR_CODE_LIMIT = 160
CONTENT_TYPE_LIMIT = 120


class RecheckError(Exception):
    """Configuration, output-path, or integrity problem. No source is touched."""


class SourceRequestLimit(RuntimeError):
    """Guards the one-request-per-source rule for a single run."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def parse_config(raw: bytes) -> list[dict]:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RecheckError(f"CONFIG_YAML_INVALID:{type(exc).__name__}") from exc
    if not isinstance(data, list) or not data:
        raise RecheckError("CONFIG_EMPTY_OR_NOT_A_LIST")
    sources = []
    for index, row in enumerate(data):
        if not isinstance(row, dict):
            raise RecheckError(f"CONFIG_ENTRY_NOT_A_MAPPING:{index}")
        name = row.get("name")
        url = row.get("url")
        if not isinstance(name, str) or not name.strip():
            raise RecheckError(f"CONFIG_ENTRY_WITHOUT_NAME:{index}")
        if not isinstance(url, str) or not url.strip():
            raise RecheckError(f"CONFIG_ENTRY_WITHOUT_URL:{index}")
        source_type = row.get("source_type")
        if source_type is not None and not isinstance(source_type, str):
            raise RecheckError(f"CONFIG_ENTRY_INVALID_SOURCE_TYPE:{index}")
        sources.append(row)
    return sources


def load_config(path: Path) -> tuple[list[dict], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RecheckError(f"CONFIG_READ_FAILED:{type(exc).__name__}") from exc
    return parse_config(raw), raw


def domain_key(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host == "arxiv.org" or host.endswith(".arxiv.org"):
        return "arxiv.org"
    return host


def interval_for(key: str, domain_interval: float) -> float:
    if key == "arxiv.org":
        return ARXIV_INTERVAL_SECONDS
    return domain_interval


class DomainPacer:
    """Serial spacing between request attempts; arXiv uses one global slot."""

    def __init__(
        self,
        domain_interval: float = DEFAULT_DOMAIN_INTERVAL_SECONDS,
        monotonic=time.monotonic,
        sleep=time.sleep,
    ):
        if (
            not isinstance(domain_interval, (int, float))
            or isinstance(domain_interval, bool)
            or not math.isfinite(domain_interval)
            or domain_interval < 0
        ):
            raise RecheckError("DOMAIN_INTERVAL_INVALID")
        self.domain_interval = domain_interval
        self._monotonic = monotonic
        self._sleep = sleep
        self._last: dict[str, float] = {}

    def wait(self, url: str) -> float:
        key = domain_key(url)
        interval = interval_for(key, self.domain_interval)
        last = self._last.get(key)
        delay = 0.0
        if last is not None:
            delay = max(0.0, interval - (self._monotonic() - last))
            if delay:
                self._sleep(delay)
        self._last[key] = self._monotonic()
        return delay


class RecordingFetch:
    """Wraps the guarded fetch, records HTTP status, and caps attempts at one.

    A logical attempt may follow validated redirects inside ``security.fetch``;
    those hops are not retries and are not counted separately here.
    """

    def __init__(self, fetch=security.fetch):
        self._fetch = fetch
        self.calls = 0
        self.status: int | None = None
        self.headers: dict = {}

    def __call__(self, url: str, headers: dict | None = None):
        self.calls += 1
        if self.calls > 1:
            raise SourceRequestLimit("MORE_THAN_ONE_REQUEST")
        status, response_headers, body = self._fetch(url, headers)
        self.status = status
        self.headers = dict(response_headers or {})
        return status, response_headers, body


def short_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return text[:ERROR_CODE_LIMIT]


def classify_failure(exc: BaseException) -> tuple[str, str, int | None]:
    if isinstance(exc, security.UnsafeURL):
        return "security", f"UnsafeURL:{short_error(exc)}", None
    if isinstance(exc, security.RemoteError):
        code = str(exc.code)
        if code.startswith("HTTP_"):
            try:
                status = int(code.split("_", 1)[1])
            except (IndexError, ValueError):
                status = None
            return "http", code, status
        if code == "TOTAL_TIMEOUT":
            return "timeout", code, None
        if code == "RESPONSE_TOO_LARGE":
            return "size_limit", code, None
        if code == "TOO_MANY_REDIRECTS":
            return "redirect", code, None
        return "network", code, None
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", type(exc).__name__, None
    if isinstance(exc, httpx.TransportError):
        return "network", type(exc).__name__, None
    if isinstance(exc, SourceRequestLimit):
        return "request_limit", short_error(exc), None
    if isinstance(exc, json.JSONDecodeError):
        return "parse", "JSONDecodeError", None
    if isinstance(exc, ValueError):
        return "parse", f"ValueError:{short_error(exc)}", None
    return "unknown", type(exc).__name__, None


def summarize_entries(entries: list[dict]) -> tuple[dict, datetime | None, int]:
    names = ["title", "original_url", "summary", "published_at", "external_id"]
    counts = {name: sum(1 for entry in entries if entry.get(name) not in (None, "")) for name in names}
    explicit = [
        entry["published_at"]
        for entry in entries
        if not entry.get("publication_time_inferred") and entry.get("published_at")
    ]
    latest = max(explicit) if explicit else None
    return counts, latest, len(explicit)


def failed_row(source: dict, category: str, code: str, checked_at: str) -> dict:
    return {
        "name": source["name"],
        "source_type": source.get("source_type"),
        "url": source["url"],
        "old_enabled": bool(source.get("enabled", False)),
        "checked_at": checked_at,
        "result": "failed",
        "http_status": None,
        "error": {"category": category, "code": code[:ERROR_CODE_LIMIT]},
        "format": None,
        "content_type": None,
        "articles": 0,
        "fields": {},
        "explicit_time_entries": 0,
        "latest_published_at": None,
        "not_modified": False,
    }


def check_source(source: dict, pacer: DomainPacer, fetch=security.fetch, clock=utc_now) -> dict:
    started = time.monotonic()
    checked_at = clock().isoformat()
    source_type = source.get("source_type")
    row = failed_row(source, "config", f"UNSUPPORTED_SOURCE_TYPE:{source_type}", checked_at)
    if not isinstance(source_type, str) or source_type not in {"rss", "gdelt"}:
        row["elapsed_seconds"] = round(time.monotonic() - started, 2)
        return row

    recorder = RecordingFetch(fetch)
    try:
        pacer.wait(source["url"])
        with (
            patch("app.collectors.rss.fetch", recorder),
            patch("app.collectors.gdelt.fetch", recorder),
        ):
            collector = RSSCollector() if source_type == "rss" else GDELTCollector()
            collection = collector.collect(source["url"], {})
        if recorder.calls > 1:  # defensive; RecordingFetch already raises on the 2nd call
            raise SourceRequestLimit("MORE_THAN_ONE_REQUEST")
        counts, latest, explicit = summarize_entries(collection.entries)
        row.update(
            result="parsed" if collection.entries else "empty",
            http_status=recorder.status,
            error=None,
            format="rss" if source_type == "rss" else "gdelt-json",
            content_type=(str(recorder.headers.get("content-type", ""))[:CONTENT_TYPE_LIMIT] or None),
            articles=len(collection.entries),
            fields=counts,
            explicit_time_entries=explicit,
            latest_published_at=latest.isoformat() if latest else None,
            not_modified=bool(collection.not_modified),
        )
    except Exception as exc:  # every failure is classified, never silently enabled
        category, code, status = classify_failure(exc)
        row["error"] = {"category": category, "code": code[:ERROR_CODE_LIMIT]}
        if status is not None:
            row["http_status"] = status
        elif recorder.status is not None:
            row["http_status"] = recorder.status
    finally:
        row["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return row


def output_error_code(exc: OSError) -> str:
    if isinstance(exc, PermissionError):
        return "OUTPUT_PERMISSION_DENIED"
    return f"OUTPUT_WRITE_FAILED:{type(exc).__name__}"


def ensure_output_path(path: Path, config_path: Path, force: bool) -> Path:
    candidate = Path(os.path.abspath(path))
    try:
        if candidate.is_symlink():
            raise RecheckError("OUTPUT_PATH_IS_SYMLINK")
        if candidate.is_dir():
            raise RecheckError("OUTPUT_PATH_IS_DIRECTORY")
        resolved = candidate.resolve()
    except RecheckError:
        raise
    except OSError as exc:
        raise RecheckError(output_error_code(exc)) from exc
    if candidate in PROTECTED_OUTPUTS or resolved in PROTECTED_OUTPUTS:
        raise RecheckError("OUTPUT_PATH_PROTECTED")
    if resolved == config_path.resolve():
        raise RecheckError("OUTPUT_PATH_IS_CONFIG")
    if candidate.exists() and not force:
        raise RecheckError("OUTPUT_EXISTS_USE_FORCE")
    return candidate


def write_report(path: Path, report: dict, force: bool = False) -> None:
    """Publish atomically without touching pre-existing sidecars or symlinks.

    The temporary file is created exclusively with a random name in the target
    directory. Without ``force`` the final name is published with a no-clobber
    hard link, so a report created by another process during the run survives.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RecheckError(output_error_code(exc)) from exc
    temporary = None
    try:
        fd, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        temporary = Path(temporary_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if force:
            if path.is_symlink():
                raise RecheckError("OUTPUT_PATH_IS_SYMLINK")
            os.replace(temporary, path)
            temporary = None
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise RecheckError("OUTPUT_EXISTS_DURING_RUN") from exc
            try:
                temporary.unlink()
            except OSError:
                pass
            temporary = None
    except RecheckError:
        raise
    except OSError as exc:
        raise RecheckError(output_error_code(exc)) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def exit_code_for(totals: dict, config_unchanged: bool) -> int:
    if not config_unchanged:
        return 3
    if totals["sources"] == 0 or totals["parsed"] == 0:
        return 2
    if totals["failed"] or totals["empty"]:
        return 1
    return 0


def run_recheck(
    config_path: Path,
    output_path: Path,
    *,
    domain_interval: float = DEFAULT_DOMAIN_INTERVAL_SECONDS,
    force: bool = False,
    fetch=security.fetch,
    clock=utc_now,
    monotonic=time.monotonic,
    sleep=time.sleep,
) -> dict:
    if (
        not isinstance(domain_interval, (int, float))
        or isinstance(domain_interval, bool)
        or not math.isfinite(domain_interval)
        or domain_interval < 0
    ):
        raise RecheckError("DOMAIN_INTERVAL_INVALID")
    config_path = Path(config_path)
    output_path = ensure_output_path(Path(output_path), config_path, force)
    sources, config_before = load_config(config_path)
    pacer = DomainPacer(domain_interval, monotonic=monotonic, sleep=sleep)
    rows = [check_source(source, pacer, fetch=fetch, clock=clock) for source in sources]

    try:
        config_after = config_path.read_bytes()
    except OSError:
        config_after = None
    config_unchanged = config_after == config_before

    totals = {
        "sources": len(rows),
        "parsed": sum(1 for row in rows if row["result"] == "parsed"),
        "empty": sum(1 for row in rows if row["result"] == "empty"),
        "failed": sum(1 for row in rows if row["result"] == "failed"),
        "old_enabled": sum(1 for row in rows if row["old_enabled"]),
    }
    cfg = settings()
    report = {
        "schema": SCHEMA,
        "generated_at": clock().isoformat(),
        "output": str(output_path),
        "config": {
            "path": display_path(config_path),
            "sha256": hashlib.sha256(config_before).hexdigest(),
            "unchanged_during_run": config_unchanged,
        },
        "mode": {
            "transport": "serial_single_attempt",
            "max_collection_attempts_per_source": 1,
            "automatic_retries": 0,
            "domain_interval_seconds": domain_interval,
            "arxiv_interval_seconds": ARXIV_INTERVAL_SECONDS,
            "http_timeout_seconds": cfg.http_timeout,
            "max_response_bytes": cfg.max_response_bytes,
            "redirects": "validated per hop by app.core.security.fetch (max 5)",
            "raw_bodies_saved": False,
        },
        "totals": totals,
        "exit_code": 0,
        "sources": rows,
    }
    report["exit_code"] = exit_code_for(totals, config_unchanged)
    write_report(output_path, report, force=force)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One controlled recheck of registered public sources.")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "sources.yaml")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "docs" / f"source-recheck-{utc_now():%Y-%m-%d}.json",
        help="Separate JSON report path. config/sources.yaml and docs/source-validation.json are refused.",
    )
    parser.add_argument(
        "--domain-interval",
        type=float,
        default=DEFAULT_DOMAIN_INTERVAL_SECONDS,
        help="Seconds between requests to the same domain (arXiv is always >=3.1s).",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing report path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run_recheck(
            args.config,
            args.output,
            domain_interval=args.domain_interval,
            force=args.force,
        )
    except RecheckError as exc:
        payload = {"code": str(exc), "exit_code": 3}
        print("RECHECK_ERROR " + json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 3
    except OSError:
        payload = {"code": "OUTPUT_WRITE_FAILED", "exit_code": 3}
        print("RECHECK_ERROR " + json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 3

    for row in report["sources"]:
        code = (row["error"] or {}).get("code", "")
        print(
            f"{row['name']} | {row['result']} | http={row['http_status']} | "
            f"entries={row['articles']} | error={code}"
        )
    summary = {
        "exit_code": report["exit_code"],
        "totals": report["totals"],
        "output": report["output"],
        "config_unchanged": report["config"]["unchanged_during_run"],
    }
    print("RECHECK_SUMMARY " + json.dumps(summary, ensure_ascii=False))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
