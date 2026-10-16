"""Background service health probe based on durable heartbeat metrics."""

import sys

from sqlalchemy import func, select

from app.db import Session
from app.models import Metric, now


def main():
    name = sys.argv[1]
    if name not in {"worker", "scheduler"}:
        raise SystemExit(2)
    with Session() as db:
        seen = db.scalar(
            select(func.max(Metric.recorded_at)).where(Metric.metric_name == name + "_heartbeat")
        )
    raise SystemExit(0 if seen and (now() - seen).total_seconds() < 150 else 1)


if __name__ == "__main__":
    main()
