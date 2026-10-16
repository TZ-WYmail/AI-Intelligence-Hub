import sqlite3
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models import Article, Digest, Job, Metric
from app.services import maintenance
from app.workers import run as worker


def test_persistent_reading_and_saved_filters(news_client, db):
    rows = news_client.get("/api/v1/articles").json()["items"]
    identity = rows[0]["id"]
    assert news_client.get("/api/v1/articles?reading_state=unread").json()["total"] == 2
    assert (
        news_client.patch(
            f"/api/v1/articles/{identity}/reading", json={"read": True, "bookmarked": True}
        ).status_code
        == 200
    )
    first = db.get(Article, identity).read_at
    news_client.patch(f"/api/v1/articles/{identity}/reading", json={"read": True})
    assert db.get(Article, identity).read_at == first
    assert news_client.get("/api/v1/articles?reading_state=read").json()["total"] == 1
    assert news_client.get("/api/v1/articles?reading_state=saved").json()["total"] == 1
    factory = sessionmaker(db.bind)
    with factory() as reopened:
        assert reopened.get(Article, identity).bookmarked
    news_client.patch(f"/api/v1/articles/{identity}/reading", json={"read": False})
    assert news_client.get("/api/v1/articles?reading_state=unread").json()["total"] == 2
    assert news_client.patch(f"/api/v1/articles/{identity}/reading", json={}).status_code == 422
    assert (
        news_client.patch(f"/api/v1/articles/{identity}/reading", json={"read": True, "extra": 1}).status_code
        == 422
    )
    assert news_client.get("/api/v1/articles?reading_state=bad").status_code == 422
    assert news_client.patch("/api/v1/articles/missing/reading", json={"read": True}).status_code == 404


def test_backup_integrity_rotation_and_restore(db, tmp_path, monkeypatch):
    location = tmp_path / "backups"
    location.mkdir()
    preserved = location / "manual.db"
    preserved.write_text("keep")
    monkeypatch.setenv("DATABASE_URL", str(db.bind.url))
    monkeypatch.setenv("BACKUP_DIRECTORY", str(location))
    monkeypatch.setenv("BACKUP_KEEP_COUNT", "2")
    settings.cache_clear()
    for _ in range(3):
        assert maintenance.backup_database(db)["integrity"] == "ok"
        db.commit()
    generated = list(location.glob("aih-*.db"))
    assert len(generated) == 2 and preserved.exists()
    for path in generated:
        assert path.stat().st_mode & 0o777 == 0o600
        with sqlite3.connect(path) as restored:
            assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert restored.execute("select count(*) from industries").fetchone()[0] == 1
    assert not list(location.glob("*.tmp"))


def test_daily_digest_persistence_and_safe_markdown(news_client, db):
    article = db.scalar(select(Article))
    article.title = "<img src=x> [unsafe](javascript:alert(1))"
    article.original_url = "https://example.com/a(b)"
    db.commit()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2027, 1, 1, tzinfo=UTC)
    first = maintenance.generate_daily_digest(db, start, end)
    db.commit()
    assert maintenance.generate_daily_digest(db, start, end).id == first.id
    assert first.article_count == 2
    assert "独特中文摘要" in first.markdown and "来源：" in first.markdown
    assert "\\<img" in first.markdown and "/a%28b%29" in first.markdown
    listing = news_client.get("/api/v1/reports").json()
    assert len(listing) == 1 and "markdown" not in listing[0]
    assert news_client.get("/api/v1/reports/" + first.id).json()["markdown"] == first.markdown
    assert db.scalar(select(func.count()).select_from(Digest)) == 1
    # Existing custom reporting still accepts literal date filters and produces safe output.
    custom = news_client.post("/api/v1/reports", json={"start": start.isoformat(), "end": end.isoformat()})
    assert custom.status_code == 200 and "\\<img" in custom.json()["markdown"]


def test_scheduled_tasks_are_durable_and_idempotent(db, monkeypatch):
    monkeypatch.setenv("BACKUP_ENABLED", "true")
    monkeypatch.setenv("DAILY_DIGEST_ENABLED", "true")
    monkeypatch.setenv("DAILY_DIGEST_HOUR", "8")
    settings.cache_clear()
    # Shanghai 08:30 -> prior local day, with UTC conversion at the database boundary.
    fixed = datetime(2026, 10, 17, 0, 30, tzinfo=UTC)
    monkeypatch.setattr(maintenance, "now", lambda: fixed)
    maintenance.enqueue_maintenance(db)
    maintenance.enqueue_maintenance(db)
    db.commit()
    jobs = list(db.scalars(select(Job)))
    assert len(jobs) == 2 and {job.job_type for job in jobs} == {"backup", "daily_digest"}
    digest_job = next(job for job in jobs if job.job_type == "daily_digest")
    assert digest_job.payload["start"] == "2026-10-16T00:00:00+08:00"
    worker.dispatch(db, digest_job)
    db.commit()
    assert db.scalar(select(Digest)) is not None
    for job in jobs:
        job.status, job.dedup_key = "completed", None
    db.add(Metric(metric_name="backup_attempt", metric_value=1, recorded_at=fixed))
    db.commit()
    maintenance.enqueue_maintenance(db)
    db.commit()
    assert db.scalar(select(func.count()).select_from(Job)) == 2
    monkeypatch.setattr(maintenance, "now", lambda: fixed + timedelta(days=1))
    maintenance.enqueue_maintenance(db)
    db.commit()
    assert db.scalar(select(func.count()).select_from(Job)) == 4


def test_daily_report_button_queues_without_model_call(news_client, db):
    first = news_client.post("/api/v1/reports/daily").json()
    assert first["queued"]
    assert not news_client.post("/api/v1/reports/daily").json()["queued"]
    job = db.scalar(select(Job).where(Job.job_type == "daily_digest"))
    worker.dispatch(db, job)
    db.commit()
    assert news_client.post("/api/v1/reports/daily").json()["report_id"]
