"""SQLite online backup, safe with WAL. python -m scripts.backup /secure/path/backup.db"""

import sqlite3
import sys
from pathlib import Path

from app.core.config import settings


def main():
    url = settings().database_url
    if not url.startswith("sqlite:///"):
        raise SystemExit("Use native database backup tools for non-SQLite databases")
    destination = Path(sys.argv[1])
    if destination.exists():
        raise SystemExit("Refusing to overwrite existing backup")
    with sqlite3.connect(url.removeprefix("sqlite:///")) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    destination.chmod(0o600)
    print("Backup complete")


if __name__ == "__main__":
    main()
