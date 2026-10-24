import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from scripts import soak


def unique_run_dir(name: str) -> Path:
    return soak.SOAK_ROOT / f"test-{name}-{uuid.uuid4().hex[:10]}"


def clean_env(**overrides) -> dict:
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "APP_TIMEZONE"}}
    env.update(overrides)
    return env


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_soak_process(run_dir: Path, duration: str, interval: str, *, cwd: Path, env: dict):
    return subprocess.run(
        [
            sys.executable,
            str(soak.REPO_ROOT / "scripts" / "soak.py"),
            "--duration-seconds",
            duration,
            "--interval-seconds",
            interval,
            "--run-dir",
            str(run_dir),
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_timing_arguments_are_finite_positive_and_capped():
    assert soak.validate_timing(86400, 60) == (86400.0, 60.0)
    assert soak.validate_timing(3, 0.2) == (3.0, 0.2)
    for value in (0, -1, float("nan"), float("inf"), float("-inf"), True, "1", None):
        with pytest.raises(soak.SoakError):
            soak.validate_timing(value, 60)
        with pytest.raises(soak.SoakError):
            soak.validate_timing(60, value)
    with pytest.raises(soak.SoakError) as small:
        soak.validate_timing(0.01, 60)
    assert small.value.code == "DURATION_TOO_SMALL"
    with pytest.raises(soak.SoakError) as large:
        soak.validate_timing(soak.MAX_DURATION_SECONDS + 1, 60)
    assert large.value.code == "DURATION_TOO_LARGE"
    with pytest.raises(soak.SoakError) as short_interval:
        soak.validate_timing(60, 0.01)
    assert short_interval.value.code == "INTERVAL_TOO_SMALL"
    with pytest.raises(soak.SoakError) as long_interval:
        soak.validate_timing(60, soak.MAX_INTERVAL_SECONDS + 1)
    assert long_interval.value.code == "INTERVAL_TOO_LARGE"


def test_run_dir_must_be_new_and_inside_private_root(tmp_path):
    root = tmp_path / "soak"
    run_dir, database = soak.prepare_run_dir(root / "run-1", soak_root=root)
    assert run_dir.is_dir() and not database.exists()
    with pytest.raises(soak.SoakError) as existing:
        soak.prepare_run_dir(run_dir, soak_root=root)
    assert existing.value.code == "RUN_DIR_EXISTS"

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(soak.SoakError) as escaped:
        soak.prepare_run_dir(outside / "run", soak_root=root)
    assert escaped.value.code == "RUN_DIR_OUTSIDE_SOAK_ROOT"

    victim = tmp_path / "victim"
    victim.mkdir()
    link = root / "linked-run"
    link.symlink_to(victim)
    with pytest.raises(soak.SoakError) as symlink:
        soak.prepare_run_dir(link, soak_root=root)
    assert symlink.value.code == "RUN_DIR_EXISTS"

    root_link = tmp_path / "soak-link"
    root_link.symlink_to(root)
    with pytest.raises(soak.SoakError) as root_symlink:
        soak.prepare_run_dir(root_link / "run-2", soak_root=root_link)
    assert root_symlink.value.code == "SOAK_ROOT_IS_SYMLINK"


def test_checkpoint_is_atomic_and_never_clobbers_a_foreign_sidecar(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    checkpoint = run_dir / soak.CHECKPOINT_NAME
    soak.write_json_atomic(checkpoint, {"run_id": "ours", "value": 1}, expected_run_id="ours")
    soak.write_json_atomic(checkpoint, {"run_id": "ours", "value": 2}, expected_run_id="ours")
    assert read_json(checkpoint)["value"] == 2
    assert checkpoint.stat().st_mode & 0o777 == 0o600
    assert not list(run_dir.glob(".checkpoint.json.*.tmp"))

    foreign = run_dir / soak.SUMMARY_NAME
    foreign.write_text('{"run_id": "other", "value": "keep"}', encoding="utf-8")
    with pytest.raises(soak.SoakError) as owner:
        soak.write_json_atomic(
            foreign, {"run_id": "ours", "value": 3}, expected_run_id="ours", label="SUMMARY"
        )
    assert owner.value.code == "SUMMARY_OWNER_MISMATCH"
    assert read_json(foreign) == {"run_id": "other", "value": "keep"}
    assert not list(run_dir.glob(".summary.json.*.tmp"))

    victim = tmp_path / "victim.json"
    victim.write_text("untouched", encoding="utf-8")
    linked = run_dir / "linked.json"
    linked.symlink_to(victim)
    with pytest.raises(soak.SoakError) as symlink:
        soak.write_json_atomic(linked, {"run_id": "ours"}, expected_run_id="ours")
    assert symlink.value.code == "CHECKPOINT_IS_SYMLINK"
    assert victim.read_text(encoding="utf-8") == "untouched"


def valid_summary() -> dict:
    return {
        "status": "completed",
        "duration_seconds": 3.0,
        "observed_seconds": 3.1,
        "backlog": {"pending": 0, "retry_wait": 0, "running": 0},
        "failed_jobs": 0,
        "network_attempts": 0,
        "unexpected_errors": [],
        "source_errors": [],
        "ai_model_calls": 0,
        "articles": 10,
        "events": 10,
        "notifications_created": 10,
        "notifications_sent": 10,
        "mock_notifications_delivered": 10,
        "duplicate_checks": {
            "articles_expected": 10,
            "duplicate_entries_served": 5,
            "duplicate_rows_inserted": 0,
            "unique_article_sets_match": True,
        },
        "failure_recovery": {"observed": True, "recovered": True},
        "lease_recovery": {"recovered": True},
        "db_reopen": {
            "performed": True,
            "committed_rows_preserved": True,
            "integrity_check": "ok",
        },
        "continuity": {
            "allowed_gap_seconds": 2.4,
            "wall_drift_limit_seconds": 5.0,
            "max_gap_seconds": 0.3,
            "max_wall_gap_seconds": 0.3,
            "max_wall_drift_seconds": 0.1,
            "final_gap_seconds": 0.1,
            "final_wall_gap_seconds": 0.1,
            "final_wall_drift_seconds": 0.1,
            "cadence_violations": [],
            "progress_samples": 10,
            "ok": True,
        },
        "isolated": {
            "env_file_disabled": True,
            "private_database": True,
            "settings_shared": True,
            "ai_enabled": False,
        },
    }


def test_completion_requires_assertions_not_just_a_status():
    assert soak.completion_failures(valid_summary()) == []
    mutations = [
        (lambda s: s["backlog"].__setitem__("pending", 1), "backlog.pending=1"),
        (lambda s: s.__setitem__("failed_jobs", 1), "failed_jobs=1"),
        (lambda s: s.__setitem__("network_attempts", 1), "network_attempts=1"),
        (lambda s: s.__setitem__("unexpected_errors", [{"kind": "x"}]), "unexpected_errors"),
        (lambda s: s.__setitem__("source_errors", [{"source_id": "x"}]), "source_errors"),
        (
            lambda s: s["duplicate_checks"].__setitem__("duplicate_rows_inserted", 1),
            "duplicate_rows_inserted=1",
        ),
        (
            lambda s: s["duplicate_checks"].__setitem__("duplicate_entries_served", 0),
            "no_duplicate_feed_items",
        ),
        (lambda s: s.__setitem__("events", 9), "events_articles_mismatch"),
        (lambda s: s["failure_recovery"].__setitem__("recovered", False), "failure_recovery_incomplete"),
        (lambda s: s["lease_recovery"].__setitem__("recovered", False), "lease_recovery_incomplete"),
        (lambda s: s["db_reopen"].__setitem__("integrity_check", "corrupt"), "db_reopen_incomplete"),
        (lambda s: s["continuity"].__setitem__("ok", False), "continuity_gap"),
        (lambda s: s["continuity"].__setitem__("max_wall_gap_seconds", 9.0), "continuity_wall_gap_exceeded"),
        (
            lambda s: s["continuity"].__setitem__("max_wall_drift_seconds", 9.0),
            "continuity_wall_drift_exceeded",
        ),
        (lambda s: s["isolated"].__setitem__("env_file_disabled", False), "isolation.env_file_disabled"),
        (lambda s: s["isolated"].__setitem__("ai_enabled", True), "isolation.ai_enabled"),
        (lambda s: s.__setitem__("observed_seconds", 1.0), "observed_seconds_short"),
    ]
    for mutate, expected in mutations:
        candidate = deepcopy(valid_summary())
        mutate(candidate)
        assert expected in soak.completion_failures(candidate)


def test_mock_clock_or_short_duration_never_claims_24h():
    assert soak.full_soak_completed(soak.FULL_SOAK_SECONDS, "completed", "mock") is False
    assert soak.full_soak_completed(soak.FULL_SOAK_SECONDS, "completed", "monotonic") is True
    assert soak.full_soak_completed(3, "completed", "monotonic") is False
    assert soak.full_soak_completed(soak.FULL_SOAK_SECONDS, "stopped", "monotonic") is False
    assert soak.completion_scope("completed", "mock", False) == "mock_clock_run"
    assert soak.completion_scope("completed", "monotonic", False) == "requested_duration_only"
    assert soak.completion_scope("stopped", "monotonic", False) == "stopped"
    assert soak.allowed_gap_seconds(60) == 122.0
    assert soak.allowed_gap_seconds(0.1) == 2.2


def test_wall_clock_gap_detects_suspend_when_monotonic_does_not():
    state = soak.RunState(
        last_progress_at=10.0,
        last_progress_wall=1_000.0,
        allowed_gap_seconds=122.0,
    )
    # Monotonic barely advances while the wall clock is 30s ahead (machine
    # suspend or a large time adjustment).  The wall gap is inside the batch
    # tolerance, but the wall/monotonic drift exposes the discontinuity.
    assert soak.observe_gap(state, 10.2, 1_030.0, "wall_test", advance=False) is False
    assert state.max_gap_seconds == pytest.approx(0.2)
    assert state.max_wall_gap_seconds == pytest.approx(30.0)
    assert state.max_wall_drift_seconds > soak.WALL_DRIFT_LIMIT_SECONDS
    assert state.cadence_violations[0]["reason"] == "wall_drift"
    assert soak.continuity_ok(state) is False
    assert soak.continuity_payload(state)["ok"] is False


def test_network_guard_blocks_and_counts_all_project_surfaces(tmp_path):
    from app.core import security

    original_getaddrinfo = socket.getaddrinfo
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto
    original_sendmsg = getattr(socket.socket, "sendmsg", None)
    original_fetch = security.fetch
    original_client = httpx.Client
    guard = soak.NetworkGuard()
    unix_path = tmp_path / "guard.sock"
    unix_reader = None
    unix_sender = None
    try:
        with guard:
            with pytest.raises(soak.NetworkBlocked):
                socket.create_connection(("example.com", 80), timeout=0.1)
            with pytest.raises(soak.NetworkBlocked):
                security.fetch("https://example.com/feed")
            with pytest.raises(soak.NetworkBlocked):
                httpx.Client()
            inet = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                with pytest.raises(soak.NetworkBlocked):
                    inet.connect(("192.0.2.1", 9))
                with pytest.raises(soak.NetworkBlocked):
                    inet.connect_ex(("192.0.2.1", 9))
                with pytest.raises(soak.NetworkBlocked):
                    inet.sendto(b"x", ("192.0.2.1", 9))
                if hasattr(inet, "sendmsg"):
                    with pytest.raises(soak.NetworkBlocked):
                        inet.sendmsg([b"x"])
            finally:
                inet.close()

            # Unix sockets keep their normal semantics inside the guard.
            unix_reader, unix_sender = socket.socketpair()
            if hasattr(unix_sender, "sendmsg"):
                unix_sender.sendmsg([b"unix"])
                assert unix_reader.recv(4) == b"unix"
            unix_reader.close()
            unix_reader = None
            unix_sender.close()
            unix_sender = None

            unix_reader = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            unix_reader.bind(str(unix_path))
            unix_sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            unix_sender.sendto(b"u", str(unix_path))
            assert unix_reader.recv(1) == b"u"
    finally:
        for item in (unix_reader, unix_sender):
            if item is not None:
                item.close()
        if unix_path.exists():
            unix_path.unlink()

    assert guard.attempts >= 6
    for surface in (
        "socket.getaddrinfo",
        "socket.connect",
        "socket.connect_ex",
        "socket.sendto",
        "security.fetch",
        "httpx.Client",
    ):
        assert surface in guard.surfaces
    if original_sendmsg is not None:
        assert "socket.sendmsg" in guard.surfaces
    assert socket.getaddrinfo is original_getaddrinfo
    assert socket.socket.connect_ex is original_connect_ex
    assert socket.socket.sendto is original_sendto
    if original_sendmsg is not None:
        assert socket.socket.sendmsg is original_sendmsg
    assert security.fetch is original_fetch
    assert httpx.Client is original_client


def test_short_soak_recovers_and_preserves_isolated_committed_data(tmp_path):
    run_dir = unique_run_dir("complete")
    canary = tmp_path / "canary.db"
    dotenv_canary = tmp_path / "dotenv-canary.db"
    fake_cwd = tmp_path / "fake-cwd"
    fake_cwd.mkdir()
    (fake_cwd / "config").symlink_to(soak.REPO_ROOT / "config", target_is_directory=True)
    (fake_cwd / ".env").write_text(
        f"DATABASE_URL=sqlite:///{dotenv_canary}\n"
        "APP_TIMEZONE=Not/A_Real_Zone\n"
        "AI_ENABLED=true\n"
        "AI_API_KEY=dotenv-secret-canary\n"
        "AI_MODEL=dotenv-model-canary\n",
        encoding="utf-8",
    )
    env = clean_env(
        DATABASE_URL=f"sqlite:///{canary}",
        AI_ENABLED="true",
        AI_API_KEY="prod-secret-canary",
        AI_MODEL="prod-model-canary",
        TELEGRAM_ENABLED="true",
        TELEGRAM_BOT_TOKEN="prod-secret-canary",
        TELEGRAM_CHAT_ID="prod-chat-canary",
    )
    started = time.monotonic()
    try:
        proc = run_soak_process(run_dir, "4", "0.2", cwd=fake_cwd, env=env)
        elapsed = time.monotonic() - started
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        summary = read_json(run_dir / soak.SUMMARY_NAME)
        checkpoint = read_json(run_dir / soak.CHECKPOINT_NAME)

        assert summary["status"] == "completed"
        assert summary["error_code"] is None
        assert summary["clock"] == "monotonic"
        assert summary["full_soak_completed"] is False
        assert summary["completion_scope"] == "requested_duration_only"
        assert summary["observed_seconds"] >= 3.99
        assert summary["observed_seconds"] <= elapsed + 0.5
        assert summary["batches"] >= 5
        assert summary["isolated"] == {
            "env_file_disabled": True,
            "ai_enabled": False,
            "private_database": True,
            "settings_shared": True,
            "network_guard_active": True,
        }
        assert summary["network_attempts"] == 0
        assert summary["network_surfaces"] == []
        assert summary["ai_model_calls"] == 0
        assert summary["unexpected_errors"] == []
        assert summary["failed_checks"] == []
        assert summary["failed_jobs"] == 0
        assert summary["source_errors"] == []
        assert summary["backlog"] == {"pending": 0, "retry_wait": 0, "running": 0}
        continuity = summary["continuity"]
        assert continuity["ok"] is True
        assert continuity["allowed_gap_seconds"] == 2.4
        assert continuity["max_gap_seconds"] <= continuity["allowed_gap_seconds"]
        assert continuity["max_wall_gap_seconds"] <= continuity["allowed_gap_seconds"]
        assert continuity["max_wall_drift_seconds"] <= continuity["wall_drift_limit_seconds"]
        assert continuity["wall_drift_limit_seconds"] == 5.0
        assert continuity["cadence_violations"] == []

        duplicate = summary["duplicate_checks"]
        assert duplicate["articles_expected"] == summary["articles"] > 0
        assert duplicate["duplicate_entries_served"] > 0
        assert duplicate["batches_with_duplicate_feed_items"] > 0
        assert duplicate["duplicate_rows_inserted"] == 0
        assert duplicate["unique_article_sets_match"] is True
        assert summary["events"] == summary["articles"]
        assert summary["notifications_created"] == summary["events"]
        assert summary["notifications_sent"] == summary["notifications_created"]
        assert summary["mock_notifications_delivered"] == summary["notifications_sent"]

        failure = summary["failure_recovery"]
        assert failure["observed"] is True and failure["recovered"] is True
        assert failure["simulated_schedule_advance"] == 1
        assert summary["expected_failures"][0]["kind"] == "mock_source_http_429"
        assert summary["lease_recovery"]["recovered"] is True
        assert summary["lease_recovery"]["status_after"] == "completed"
        assert summary["db_reopen"]["performed"] is True
        assert summary["db_reopen"]["committed_rows_preserved"] is True
        assert summary["db_reopen"]["integrity_check"] == "ok"
        assert summary["db_reopen"]["before"] == summary["db_reopen"]["after"]

        assert checkpoint["run_id"] == summary["run_id"]
        assert checkpoint["status"] == "completed"
        assert checkpoint["continuity"]["ok"] is True
        assert isinstance(summary["pid"], int) and summary["pid"] > 0

        log_lines = [
            json.loads(line) for line in (run_dir / soak.LOG_NAME).read_text(encoding="utf-8").splitlines()
        ]
        assert log_lines[-1]["event"] == "finish"
        assert log_lines[-1]["status"] == "completed"
        assert all(line["run_id"] == summary["run_id"] for line in log_lines)

        notifications = [
            json.loads(line)
            for line in (run_dir / soak.NOTIFICATIONS_NAME).read_text(encoding="utf-8").splitlines()
        ]
        assert len(notifications) == summary["notifications_sent"]
        assert all(item["synthetic"] is True for item in notifications)
        assert all(item["run_id"] == summary["run_id"] for item in notifications)
        assert all(item["title"].startswith("Synthetic soak source") for item in notifications)

        database = run_dir / soak.DB_NAME
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            assert connection.execute("select count(*) from articles").fetchone()[0] == summary["articles"]
            assert (
                connection.execute(
                    "select count(*) from ("
                    "select source_id, external_id, count(*) c from articles "
                    "group by source_id, external_id having c > 1)"
                ).fetchone()[0]
                == 0
            )
            assert (
                connection.execute(
                    "select count(*) from ai_analyses where provider = 'openai-compatible'"
                ).fetchone()[0]
                == 0
            )
            assert (
                connection.execute("select count(*) from jobs where status != 'completed'").fetchone()[0] == 0
            )
            assert (
                connection.execute("select count(*) from sources where status != 'healthy'").fetchone()[0]
                == 0
            )
            assert (
                connection.execute("select count(*) from notifications where status = 'sent'").fetchone()[0]
                == summary["notifications_sent"]
            )

        # The project .env cannot supply a database or credentials: neither the
        # poison environment canary nor the fake .env database may be created.
        assert not canary.exists()
        assert not dotenv_canary.exists()
        assert not list(tmp_path.glob("canary.db*"))
        for path in run_dir.rglob("*"):
            if path.is_file():
                content = path.read_bytes()
                for secret in (b"prod-secret-canary", b"dotenv-secret-canary", b"prod-chat-canary"):
                    assert secret not in content
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_short_run_never_claims_completion(tmp_path):
    run_dir = unique_run_dir("too-short")
    try:
        proc = run_soak_process(
            run_dir,
            "0.2",
            "0.2",
            cwd=soak.REPO_ROOT,
            env=clean_env(),
        )
        assert proc.returncode == 1, (proc.stdout, proc.stderr)
        summary = read_json(run_dir / soak.SUMMARY_NAME)
        assert summary["status"] == "failed"
        assert summary["error_code"] == "VERIFICATION_FAILED"
        assert summary["full_soak_completed"] is False
        assert summary["completion_scope"] == "failed"
        assert summary["failed_checks"]
        assert "failure_recovery_incomplete" in summary["failed_checks"]
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="SIGSTOP unavailable")
def test_sigstop_across_deadline_fails_continuity(tmp_path):
    run_dir = unique_run_dir("sigstop")
    proc = subprocess.Popen(
        [
            sys.executable,
            str(soak.REPO_ROOT / "scripts" / "soak.py"),
            "--duration-seconds",
            "2",
            "--interval-seconds",
            "0.1",
            "--run-dir",
            str(run_dir),
        ],
        cwd=soak.REPO_ROOT,
        env=clean_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                stdout, stderr = proc.communicate()
                pytest.fail(f"soak exited before SIGSTOP: {stdout} {stderr}")
            checkpoint_path = run_dir / soak.CHECKPOINT_NAME
            if checkpoint_path.exists():
                try:
                    checkpoint = read_json(checkpoint_path)
                except ValueError:
                    checkpoint = {}
                if checkpoint.get("batches", 0) >= 1:
                    break
            time.sleep(0.02)
        else:
            pytest.fail("soak did not produce a running checkpoint before SIGSTOP")

        os.kill(proc.pid, signal.SIGSTOP)
        time.sleep(3.0)
        os.kill(proc.pid, signal.SIGCONT)
        stdout, stderr = proc.communicate(timeout=20)
        assert proc.returncode == 1, (stdout, stderr)
        summary = read_json(run_dir / soak.SUMMARY_NAME)
        assert summary["status"] == "failed"
        assert summary["error_code"] == "CONTINUITY_GAP"
        assert summary["full_soak_completed"] is False
        continuity = summary["continuity"]
        assert continuity["ok"] is False
        assert continuity["cadence_violations"]
        assert (
            continuity["max_gap_seconds"] > continuity["allowed_gap_seconds"]
            or continuity["max_wall_gap_seconds"] > continuity["allowed_gap_seconds"]
        )
        assert "continuity_gap" in summary["failed_checks"]
        assert any(error.get("kind") == "continuity_gap" for error in summary["unexpected_errors"])
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
        shutil.rmtree(run_dir, ignore_errors=True)


def test_stop_file_stops_cleanly_as_stopped_not_completed(tmp_path):
    run_dir = unique_run_dir("stop")
    env = clean_env()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(soak.REPO_ROOT / "scripts" / "soak.py"),
            "--duration-seconds",
            "30",
            "--interval-seconds",
            "0.2",
            "--run-dir",
            str(run_dir),
        ],
        cwd=soak.REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                stdout, stderr = proc.communicate()
                pytest.fail(f"soak exited before stop: {stdout} {stderr}")
            checkpoint_path = run_dir / soak.CHECKPOINT_NAME
            if checkpoint_path.exists():
                try:
                    checkpoint = read_json(checkpoint_path)
                except ValueError:
                    checkpoint = {}
                if checkpoint.get("batches", 0) >= 1:
                    break
            time.sleep(0.05)
        else:
            pytest.fail("soak did not produce a running checkpoint with one batch")

        requested_at = time.monotonic()
        (run_dir / soak.STOP_NAME).write_text("stop\n", encoding="utf-8")
        stdout, stderr = proc.communicate(timeout=20)
        stopped_after = time.monotonic() - requested_at
        assert proc.returncode == 0, (stdout, stderr)
        assert stopped_after < 10
        summary = read_json(run_dir / soak.SUMMARY_NAME)
        assert summary["status"] == "stopped"
        assert summary["error_code"] == "STOP_REQUESTED"
        assert summary["full_soak_completed"] is False
        assert summary["completion_scope"] == "stopped"
        assert "stop_requested" not in summary
        checkpoint = read_json(run_dir / soak.CHECKPOINT_NAME)
        assert checkpoint["status"] == "stopped"
        assert checkpoint["stop_requested"] is True
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
        shutil.rmtree(run_dir, ignore_errors=True)
