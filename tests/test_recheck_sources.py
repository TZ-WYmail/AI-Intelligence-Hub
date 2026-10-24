import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from app.core import security
from scripts import recheck_sources as recheck

FIXED_TIME = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
RSS_FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Test</title>
<link>https://example.com</link><description>fixture</description>
<item><guid>1</guid><title>AI breakthrough</title><link>https://example.com/1</link>
<description>Summary text</description><pubDate>Fri, 16 Oct 2026 08:00:00 GMT</pubDate></item>
<item><guid>2</guid><title>Robot update</title><link>https://example.com/2</link></item>
</channel></rss>"""
EMPTY_FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title>
<link>https://example.com</link><description>no items</description></channel></rss>"""


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 6))
        self.value += seconds


def source(name, url, source_type="rss", enabled=False):
    return {
        "name": name,
        "source_type": source_type,
        "url": url,
        "industry_ids": ["ai"],
        "poll_interval_seconds": 1800,
        "trust_level": 0.9,
        "language": "en",
        "authentication": "none",
        "enabled": enabled,
    }


def write_config(tmp_path, rows):
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump(rows, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def fake_fetch(body, status=200, headers=None):
    calls = []

    def fetch(url, request_headers=None):
        calls.append(url)
        return status, headers or {"content-type": "application/rss+xml"}, body

    fetch.calls = calls
    return fetch


def fake_error(exc):
    calls = []

    def fetch(url, request_headers=None):
        calls.append(url)
        raise exc

    fetch.calls = calls
    return fetch


def run(tmp_path, rows, fetch, **kwargs):
    config = write_config(tmp_path, rows)
    before = config.read_bytes()
    clock = kwargs.pop("clock", FakeClock())
    report = recheck.run_recheck(
        config,
        tmp_path / "report.json",
        fetch=fetch,
        clock=kwargs.pop("wall_clock", lambda: FIXED_TIME),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        **kwargs,
    )
    assert config.read_bytes() == before
    return report, clock


def test_rss_success_writes_separate_report_and_keeps_config(tmp_path):
    fetch = fake_fetch(RSS_FEED, headers={"content-type": "application/rss+xml; charset=utf-8"})
    report, _ = run(tmp_path, [source("Example", "https://example.com/feed", enabled=True)], fetch)

    assert report["totals"] == {"sources": 1, "parsed": 1, "empty": 0, "failed": 0, "old_enabled": 1}
    assert report["exit_code"] == 0
    assert report["schema"] == "source-recheck/2"
    assert report["mode"]["max_collection_attempts_per_source"] == 1
    assert report["mode"]["automatic_retries"] == 0
    assert "max_requests_per_source" not in report["mode"]
    row = report["sources"][0]
    assert row["name"] == "Example"
    assert row["result"] == "parsed"
    assert row["old_enabled"] is True
    assert row["http_status"] == 200
    assert row["error"] is None
    assert row["format"] == "rss"
    assert row["articles"] == 2
    assert row["fields"] == {
        "title": 2,
        "original_url": 2,
        "summary": 1,
        "published_at": 2,
        "external_id": 2,
    }
    assert row["explicit_time_entries"] == 1
    assert row["latest_published_at"].startswith("2026-10-16T08:00:00")
    assert row["checked_at"] == "2026-10-24T12:00:00+00:00"
    assert row["elapsed_seconds"] >= 0
    serialized = json.dumps(report)
    assert "raw_payload" not in serialized
    assert "AI breakthrough" not in serialized
    assert "Summary text" not in serialized
    assert fetch.calls == ["https://example.com/feed"]

    output = Path(report["output"])
    assert output == (tmp_path / "report.json").resolve()
    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert report["config"]["unchanged_during_run"] is True


def test_gdelt_success_uses_registered_url_and_inferred_times(tmp_path):
    body = json.dumps(
        {
            "articles": [
                {
                    "title": "AI News",
                    "url": "https://example.com/a",
                    "seendate": "20261016T080000Z",
                    "language": "English",
                }
            ]
        }
    ).encode()
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc?query=artificial%20intelligence"
        "&mode=artlist&format=json&maxrecords=50&timespan=1d"
    )
    fetch = fake_fetch(body, headers={"content-type": "application/json"})
    report, _ = run(tmp_path, [source("GDELT AI", url, source_type="gdelt")], fetch)

    assert report["exit_code"] == 0
    row = report["sources"][0]
    assert row["result"] == "parsed"
    assert row["format"] == "gdelt-json"
    assert row["articles"] == 1
    assert row["explicit_time_entries"] == 0
    assert row["latest_published_at"] is None
    assert row["fields"]["title"] == 1
    assert row["fields"]["summary"] == 0
    assert fetch.calls == [url]


def test_empty_feed_is_counted_separately(tmp_path):
    fetch = fake_fetch(EMPTY_FEED)
    report, _ = run(tmp_path, [source("Empty", "https://example.com/empty")], fetch)

    assert report["exit_code"] == 2
    assert report["totals"]["parsed"] == 0
    assert report["totals"]["empty"] == 1
    assert report["totals"]["failed"] == 0
    assert report["sources"][0]["result"] == "empty"
    assert report["sources"][0]["articles"] == 0


def test_broken_feed_is_a_parse_failure(tmp_path):
    fetch = fake_fetch(b"not a feed")
    report, _ = run(tmp_path, [source("Broken", "https://example.com/broken")], fetch)

    assert report["exit_code"] == 2
    assert report["totals"]["failed"] == 1
    row = report["sources"][0]
    assert row["result"] == "failed"
    assert row["error"]["category"] == "parse"
    assert row["error"]["code"] == "ValueError:INVALID_FEED"
    assert row["http_status"] == 200


@pytest.mark.parametrize(
    ("exc", "category", "code", "http_status"),
    [
        (security.RemoteError("TOTAL_TIMEOUT"), "timeout", "TOTAL_TIMEOUT", None),
        (security.RemoteError("HTTP_403"), "http", "HTTP_403", 403),
        (httpx.ConnectTimeout("connect timed out"), "timeout", "ConnectTimeout", None),
        (httpx.ConnectError("dns failure"), "network", "ConnectError", None),
    ],
)
def test_failures_are_classified(tmp_path, exc, category, code, http_status):
    report, _ = run(tmp_path, [source("Failing", "https://example.com/fail")], fake_error(exc))

    row = report["sources"][0]
    assert report["exit_code"] == 2
    assert row["result"] == "failed"
    assert row["error"] == {"category": category, "code": code}
    assert row["http_status"] == http_status


def test_partial_run_exits_one(tmp_path):
    fetch = fake_fetch(RSS_FEED)
    rows = [source("Good", "https://good.example/feed"), source("Bad", "https://bad.example/feed")]
    original = fetch

    def mixed(url, request_headers=None):
        if "bad" in url:
            raise security.RemoteError("HTTP_500")
        return original(url, request_headers)

    report, _ = run(tmp_path, rows, mixed)

    assert report["totals"] == {"sources": 2, "parsed": 1, "empty": 0, "failed": 1, "old_enabled": 0}
    assert report["exit_code"] == 1


def test_total_failure_never_reports_success(tmp_path):
    fetch = fake_error(security.RemoteError("HTTP_503"))
    rows = [source("A", "https://a.example/feed"), source("B", "https://b.example/feed")]
    report, _ = run(tmp_path, rows, fetch)

    assert report["totals"]["parsed"] == 0
    assert report["totals"]["failed"] == 2
    assert report["exit_code"] == 2
    assert (tmp_path / "report.json").is_file()


def test_protected_and_existing_output_paths_are_refused(tmp_path):
    config = write_config(tmp_path, [source("A", "https://example.com/feed")])

    with pytest.raises(recheck.RecheckError, match="OUTPUT_PATH_PROTECTED"):
        recheck.ensure_output_path(recheck.REPO_ROOT / "config" / "sources.yaml", config, force=True)
    with pytest.raises(recheck.RecheckError, match="OUTPUT_PATH_PROTECTED"):
        recheck.ensure_output_path(recheck.REPO_ROOT / "docs" / "source-validation.json", config, True)
    with pytest.raises(recheck.RecheckError, match="OUTPUT_PATH_IS_CONFIG"):
        recheck.ensure_output_path(config, config, force=True)

    existing = tmp_path / "report.json"
    existing.write_text("{}", encoding="utf-8")
    with pytest.raises(recheck.RecheckError, match="OUTPUT_EXISTS_USE_FORCE"):
        recheck.ensure_output_path(existing, config, force=False)
    assert recheck.ensure_output_path(existing, config, force=True) == existing.resolve()


def test_arxiv_requests_are_at_least_three_seconds_apart(tmp_path):
    rows = [
        source("arXiv cs.AI", "https://rss.arxiv.org/rss/cs.AI"),
        source("arXiv cs.LG", "https://rss.arxiv.org/rss/cs.LG"),
    ]
    report, clock = run(tmp_path, rows, fake_fetch(RSS_FEED))

    assert report["exit_code"] == 0
    assert clock.sleeps == [3.1]


def test_same_domain_requests_are_spaced(tmp_path):
    rows = [
        source("NASA A", "https://www.nasa.gov/a/feed/"),
        source("NASA B", "https://www.nasa.gov/b/feed/"),
    ]
    report, clock = run(tmp_path, rows, fake_fetch(RSS_FEED), domain_interval=1.0)

    assert report["exit_code"] == 0
    assert clock.sleeps == [1.0]


def test_different_domains_do_not_wait(tmp_path):
    rows = [source("A", "https://a.example/feed"), source("B", "https://b.example/feed")]
    report, clock = run(tmp_path, rows, fake_fetch(RSS_FEED))

    assert report["exit_code"] == 0
    assert clock.sleeps == []


def test_each_source_gets_one_collection_attempt(tmp_path):
    fetch = fake_fetch(RSS_FEED)
    rows = [source("A", "https://example.com/feed"), source("B", "https://example.com/feed2")]
    report, _ = run(tmp_path, rows, fetch)

    assert report["totals"]["parsed"] == 2
    assert fetch.calls == ["https://example.com/feed", "https://example.com/feed2"]


def test_recording_fetch_rejects_a_second_request():
    recorder = recheck.RecordingFetch(lambda url, headers=None: (200, {}, b""))
    recorder("https://example.com")

    with pytest.raises(recheck.SourceRequestLimit):
        recorder("https://example.com")


def test_config_change_during_run_is_reported(tmp_path):
    config = write_config(tmp_path, [source("A", "https://example.com/feed")])

    def mutating_fetch(url, request_headers=None):
        config.write_text("- name: changed\n", encoding="utf-8")
        return 200, {"content-type": "application/rss+xml"}, RSS_FEED

    report = recheck.run_recheck(
        config,
        tmp_path / "report.json",
        fetch=mutating_fetch,
        clock=lambda: FIXED_TIME,
        monotonic=FakeClock().monotonic,
        sleep=lambda seconds: None,
    )

    assert report["config"]["unchanged_during_run"] is False
    assert report["exit_code"] == 3


def test_checked_at_is_utc_for_every_source(tmp_path):
    rows = [source("A", "https://a.example/feed"), source("B", "https://b.example/feed")]
    report, _ = run(tmp_path, rows, fake_fetch(RSS_FEED))

    assert report["generated_at"].endswith("+00:00")
    for row in report["sources"]:
        assert row["checked_at"].endswith("+00:00")
        assert datetime.fromisoformat(row["checked_at"]).utcoffset().total_seconds() == 0


def test_preexisting_unrelated_tmp_file_is_preserved(tmp_path):
    sidecar = tmp_path / "report.json.tmp"
    sidecar.write_text("keep me", encoding="utf-8")
    report, _ = run(
        tmp_path,
        [source("Example", "https://example.com/feed")],
        fake_fetch(RSS_FEED),
    )

    assert report["exit_code"] == 0
    assert sidecar.read_text(encoding="utf-8") == "keep me"
    assert (tmp_path / "report.json").is_file()


def test_sidecar_symlink_to_config_is_never_followed(tmp_path):
    config = write_config(tmp_path, [source("Example", "https://example.com/feed")])
    before = config.read_bytes()
    sidecar = tmp_path / "report.json.tmp"
    os.symlink(config, sidecar)

    report = recheck.run_recheck(
        config,
        tmp_path / "report.json",
        fetch=fake_fetch(RSS_FEED),
        clock=lambda: FIXED_TIME,
        monotonic=FakeClock().monotonic,
        sleep=lambda seconds: None,
    )

    assert report["exit_code"] == 0
    assert report["config"]["unchanged_during_run"] is True
    assert config.read_bytes() == before
    assert sidecar.is_symlink()
    assert sidecar.resolve() == config.resolve()


@pytest.mark.parametrize("force", [False, True])
def test_output_symlink_is_refused_even_with_force(tmp_path, force):
    target = tmp_path / "target.txt"
    target.write_text("original", encoding="utf-8")
    output = tmp_path / "report.json"
    os.symlink(target, output)
    config = write_config(tmp_path, [source("Example", "https://example.com/feed")])
    fetch = fake_fetch(RSS_FEED)

    with pytest.raises(recheck.RecheckError, match="OUTPUT_PATH_IS_SYMLINK"):
        recheck.run_recheck(
            config,
            output,
            fetch=fetch,
            force=force,
            clock=lambda: FIXED_TIME,
            monotonic=FakeClock().monotonic,
            sleep=lambda seconds: None,
        )

    assert fetch.calls == []
    assert output.is_symlink()
    assert target.read_text(encoding="utf-8") == "original"


def test_output_created_during_run_is_not_overwritten(tmp_path):
    config = write_config(tmp_path, [source("Example", "https://example.com/feed")])
    output = tmp_path / "report.json"

    def racing_fetch(url, request_headers=None):
        output.write_text("previous", encoding="utf-8")
        return 200, {"content-type": "application/rss+xml"}, RSS_FEED

    with pytest.raises(recheck.RecheckError, match="OUTPUT_EXISTS_DURING_RUN"):
        recheck.run_recheck(
            config,
            output,
            fetch=racing_fetch,
            clock=lambda: FIXED_TIME,
            monotonic=FakeClock().monotonic,
            sleep=lambda seconds: None,
        )

    assert output.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_force_allows_overwriting_an_existing_report(tmp_path):
    output = tmp_path / "report.json"
    output.write_text("old report", encoding="utf-8")
    report, _ = run(
        tmp_path,
        [source("Example", "https://example.com/feed")],
        fake_fetch(RSS_FEED),
        force=True,
    )

    assert report["exit_code"] == 0
    assert json.loads(output.read_text(encoding="utf-8"))["schema"] == "source-recheck/2"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -1.0])
def test_invalid_domain_interval_is_rejected_before_requests(tmp_path, value):
    config = write_config(tmp_path, [source("Example", "https://example.com/feed")])
    output = tmp_path / "report.json"
    fetch = fake_fetch(RSS_FEED)

    with pytest.raises(recheck.RecheckError, match="DOMAIN_INTERVAL_INVALID"):
        recheck.run_recheck(
            config,
            output,
            domain_interval=value,
            fetch=fetch,
            clock=lambda: FIXED_TIME,
            monotonic=FakeClock().monotonic,
            sleep=lambda seconds: None,
        )

    assert fetch.calls == []
    assert not output.exists()


@pytest.mark.parametrize("source_type", [["rss"], {"type": "rss"}])
def test_invalid_source_type_container_is_rejected_at_config_stage(tmp_path, source_type):
    row = source("Example", "https://example.com/feed")
    row["source_type"] = source_type
    config = write_config(tmp_path, [row])
    output = tmp_path / "report.json"
    fetch = fake_fetch(RSS_FEED)

    with pytest.raises(recheck.RecheckError, match="CONFIG_ENTRY_INVALID_SOURCE_TYPE:0"):
        recheck.run_recheck(
            config,
            output,
            fetch=fetch,
            clock=lambda: FIXED_TIME,
            monotonic=FakeClock().monotonic,
            sleep=lambda seconds: None,
        )

    assert fetch.calls == []
    assert not output.exists()


def test_output_io_error_is_short_exit_3_without_traceback(tmp_path, capsys):
    config = write_config(tmp_path, [{"name": "NoType", "url": "https://example.com/feed"}])
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("keep", encoding="utf-8")
    output = parent_file / "report.json"

    code = recheck.main(["--config", str(config), "--output", str(output)])
    captured = capsys.readouterr()

    assert code == 3
    assert "RECHECK_ERROR" in captured.err
    assert "OUTPUT_WRITE_FAILED:FileExistsError" in captured.err
    assert "Traceback" not in captured.err
    assert parent_file.read_text(encoding="utf-8") == "keep"


def test_config_io_error_is_short_exit_3_without_traceback(tmp_path, capsys):
    output = tmp_path / "report.json"

    code = recheck.main(["--config", str(tmp_path), "--output", str(output)])
    captured = capsys.readouterr()

    assert code == 3
    assert "RECHECK_ERROR" in captured.err
    assert "CONFIG_READ_FAILED:IsADirectoryError" in captured.err
    assert "Traceback" not in captured.err
    assert not output.exists()


def test_temp_file_is_cleaned_when_publish_fails(tmp_path, monkeypatch):
    config = write_config(tmp_path, [source("Example", "https://example.com/feed")])
    output = tmp_path / "report.json"

    def fail_link(*args, **kwargs):
        raise OSError("publish failed")

    monkeypatch.setattr(recheck.os, "link", fail_link)

    with pytest.raises(recheck.RecheckError, match="OUTPUT_WRITE_FAILED:OSError"):
        recheck.run_recheck(
            config,
            output,
            fetch=fake_fetch(RSS_FEED),
            clock=lambda: FIXED_TIME,
            monotonic=FakeClock().monotonic,
            sleep=lambda seconds: None,
        )

    assert not output.exists()
    assert list(tmp_path.glob(".*.tmp")) == []
