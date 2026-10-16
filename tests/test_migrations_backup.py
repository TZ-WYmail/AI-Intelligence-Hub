import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_real_migration_roundtrip_and_backup(tmp_path):
    database = tmp_path / "migrated.db"
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database}"}
    environment.pop("PYTHONPATH", None)
    for arguments in [["-m", "alembic", "upgrade", "head"], ["-m", "scripts.bootstrap"]]:
        subprocess.run([sys.executable, *arguments], env=environment, check=True, capture_output=True)
    with sqlite3.connect(database) as db:
        assert db.execute("select count(*) from industries").fetchone()[0] == 8
        assert db.execute("select version_num from alembic_version").fetchone()[0] == "0001"
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
    backup = tmp_path / "backup.db"
    subprocess.run(
        [sys.executable, "-m", "scripts.backup", str(backup)],
        env=environment,
        check=True,
        capture_output=True,
    )
    with sqlite3.connect(backup) as db:
        assert db.execute("select count(*) from industries").fetchone()[0] == 8
    refused = subprocess.run(
        [sys.executable, "-m", "scripts.backup", str(backup)], env=environment, capture_output=True
    )
    assert refused.returncode != 0
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        env=environment,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], env=environment, check=True, capture_output=True
    )
    assert Path(database).exists()
