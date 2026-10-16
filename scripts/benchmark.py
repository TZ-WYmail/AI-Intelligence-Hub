"""100 source / 3000 article synthetic queue benchmark; no external network calls."""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.collectors.base import Collection
from app.core.config import settings
from app.db import make_engine
from app.models import Article, Base, Industry, Job, Metric, Notification, Source, now
from app.services import pipeline
from app.services.queue import enqueue
from app.workers import run as worker


def percentile(values):
    return round(sorted(values)[int((len(values) - 1) * 0.95)], 4) if values else None


def main():
    os.environ.update(
        AI_ENABLED="false",
        TELEGRAM_ENABLED="true",
        TELEGRAM_BOT_TOKEN="synthetic",
        TELEGRAM_CHAT_ID="synthetic",
        FEISHU_ENABLED="false",
        QUIET_START_HOUR="0",
        QUIET_END_HOUR="0",
    )
    settings.cache_clear()
    with tempfile.TemporaryDirectory() as directory:
        engine = make_engine(f"sqlite:///{directory}/load.db")
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as db:
            db.add(Industry(id="ai", name="AI", slug="ai", keywords=["AI"]))
            for index in range(100):
                source = Source(
                    id=f"src-{index}",
                    name=f"Synthetic {index}",
                    url=f"https://example.com/{index}",
                    industry_ids=["ai"],
                    enabled=True,
                    trust_level=1,
                )
                db.add(source)
                db.flush()
                enqueue(db, "collect", "collect:" + source.id, source_id=source.id)
            db.commit()

        class FakeCollector:
            def collect(self, url, headers):
                return Collection(
                    [
                        dict(
                            external_id=str(i),
                            title=f"AI launches breakthrough {url} {i}",
                            summary="Synthetic source data, not real news",
                            content="",
                            original_url=f"{url}/{i}",
                            language="en",
                            author="fixture",
                            published_at=now(),
                            publication_time_inferred=False,
                            raw_payload={"synthetic": True},
                        )
                        for i in range(30)
                    ]
                )

        started = time.monotonic()
        accepted = []
        with (
            patch.object(worker, "Session", factory),
            patch.object(pipeline, "collector", lambda _: FakeCollector()),
            patch.object(worker.WebhookNotifier, "send", lambda _, payload: accepted.append(payload)),
        ):
            while worker.tick():
                pass
        elapsed = time.monotonic() - started
        with factory() as db:
            processing = list(
                db.scalars(
                    select(Metric.metric_value).where(Metric.metric_name == "internal_processing_seconds")
                )
            )
            notify = list(
                db.scalars(
                    select(Metric.metric_value).where(Metric.metric_name == "notification_queue_seconds")
                )
            )
            report = {
                "generated_at": now().isoformat(),
                "source_count": 100,
                "articles": db.scalar(select(func.count()).select_from(Article)),
                "notifications_sent": db.scalar(
                    select(func.count()).select_from(Notification).where(Notification.status == "sent")
                ),
                "failed_jobs": db.scalar(select(func.count()).select_from(Job).where(Job.status == "failed")),
                "elapsed_seconds": round(elapsed, 3),
                "processing_p95_seconds": percentile(processing),
                "mock_notification_p95_seconds": percentile(notify),
                "external_calls": False,
                "limitations": "Single worker; mock RSS and immediate mock notifications; no real network or AI latency. Not a production SLA.",
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
            Path("docs/benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        engine.dispose()


if __name__ == "__main__":
    main()
