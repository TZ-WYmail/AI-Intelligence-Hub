"""Start/stop four local processes. Use Docker for automatic crash restart."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data/local-processes.json"
COMMANDS = {
    "api": ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--no-access-log"],
    "worker": ["-m", "app.workers.run"],
    "scheduler": ["-m", "app.scheduler.run"],
    "dashboard": [
        "-m",
        "streamlit",
        "run",
        "dashboard/Home.py",
        "--server.address=127.0.0.1",
        "--server.port=8501",
        "--browser.gatherUsageStats=false",
        "--server.headless=true",
    ],
}


def alive(pid):
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return Path(f"/proc/{pid}/cwd").resolve() == ROOT
    except (OSError, FileNotFoundError):
        return False


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if action == "up":
        environment = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=environment, check=True
        )
        subprocess.run([sys.executable, "-m", "scripts.bootstrap"], cwd=ROOT, env=environment, check=True)
        for name, args in COMMANDS.items():
            if alive(state.get(name, -999999)):
                continue
            with (ROOT / f"data/{name}.log").open("ab") as log:
                process = subprocess.Popen(
                    [sys.executable, *args],
                    cwd=ROOT,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            state[name] = process.pid
        STATE.write_text(json.dumps(state))
    elif action == "down":
        for pid in state.values():
            if alive(pid):
                os.killpg(pid, signal.SIGTERM)
        STATE.unlink(missing_ok=True)
        print("Stopped local services")
        return
    elif action != "status":
        raise SystemExit("Usage: python -m scripts.local up|down|status")
    for name, pid in state.items():
        print(name, "running" if alive(pid) else "stopped")


if __name__ == "__main__":
    main()
