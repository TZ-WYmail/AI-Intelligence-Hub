from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import sessionmaker

from app.db import get_session
from app.main import app
from app.models import Article, Job, Source, now
from app.services.queue import JobRetryRejected, retry_failed_job

HEADERS = {"X-API-Key": "test-admin"}


@pytest.fixture
def client(db):
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def failed_job(job_id: str, job_type: str = "collect", **overrides) -> Job:
    fields = dict(
        id=job_id,
        job_type=job_type,
        status="failed",
        attempt_count=3,
        last_error="HTTP_500",
        scheduled_at=now() - timedelta(minutes=5),
        started_at=now() - timedelta(minutes=6),
        finished_at=now() - timedelta(minutes=4),
        payload={},
    )
    if job_type == "collect":
        fields["source_id"] = "source"
    fields.update(overrides)
    return Job(**fields)


def article(db) -> Article:
    item = Article(
        id="article",
        source_id="source",
        title="Fixture",
        summary="Fixture",
        original_url="https://example.com/article",
        canonical_url="https://example.com/article",
        content_hash="article",
        published_at=now(),
    )
    db.add(item)
    return item


def test_jobs_listing_keeps_list_contract_and_validates_status(db, client):
    db.add_all(
        [
            failed_job("failed"),
            Job(id="pending", job_type="backup", payload={}, status="pending"),
        ]
    )
    db.commit()

    assert client.get("/api/v1/jobs").status_code == 401
    assert client.post("/api/v1/jobs/failed/retry").status_code == 401

    listing = client.get("/api/v1/jobs", headers=HEADERS)
    assert listing.status_code == 200
    assert isinstance(listing.json(), list) and len(listing.json()) == 2

    failed = client.get("/api/v1/jobs", headers=HEADERS, params={"status": "failed"})
    assert failed.status_code == 200
    assert [item["id"] for item in failed.json()] == ["failed"]

    invalid = client.get("/api/v1/jobs", headers=HEADERS, params={"status": "bogus"})
    assert invalid.status_code == 422


def test_retry_failed_job_requeues_and_repeat_click_is_idempotent(db, client):
    db.add(failed_job("job"))
    db.commit()

    first = client.post("/api/v1/jobs/job/retry", headers=HEADERS)
    assert first.status_code == 200
    assert first.json() == {
        "queued": True,
        "job_id": "job",
        "job_type": "collect",
        "status": "pending",
        "deduplicated": False,
    }

    db.expire_all()
    job = db.get(Job, "job")
    # The requeue keeps the pre-retry failure count and reason as audit evidence.
    assert job.status == "pending"
    assert job.attempt_count == 3
    assert job.last_error == "HTTP_500"
    assert job.dedup_key == "collect:source"

    second = client.post("/api/v1/jobs/job/retry", headers=HEADERS)
    assert second.status_code == 200
    assert second.json()["queued"] is False
    assert second.json()["job_id"] == "job"
    assert db.scalar(select(func.count()).select_from(Job)) == 1


@pytest.mark.parametrize("status", ["completed", "running"])
def test_retry_never_resets_completed_or_running_jobs(db, client, status):
    db.add(failed_job("job", status=status, dedup_key=None if status == "completed" else "collect:source"))
    db.commit()

    response = client.post("/api/v1/jobs/job/retry", headers=HEADERS)
    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == f"JOB_{status.upper()}"

    db.expire_all()
    job = db.get(Job, "job")
    assert job.status == status
    assert job.attempt_count == 3
    assert job.last_error == "HTTP_500"


def test_retry_does_not_duplicate_an_active_business_job(db, client):
    db.add_all(
        [
            failed_job("failed"),
            Job(
                id="active",
                job_type="collect",
                source_id="source",
                dedup_key="collect:source",
                payload={},
                status="retry_wait",
            ),
        ]
    )
    db.commit()

    response = client.post("/api/v1/jobs/failed/retry", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["queued"] is False
    assert response.json()["job_id"] == "active"
    assert response.json()["deduplicated"] is True

    db.expire_all()
    assert db.get(Job, "failed").status == "failed"
    assert db.scalar(select(func.count()).select_from(Job)) == 2


def test_retry_supports_only_the_four_safe_job_types(db, client):
    article(db)
    db.add_all(
        [
            failed_job("collect", job_type="collect"),
            failed_job("analyze", job_type="analyze", payload={"article_id": "article"}),
            failed_job(
                "digest",
                job_type="daily_digest",
                payload={"start": "2026-10-19T00:00:00+00:00", "end": "2026-10-20T00:00:00+00:00"},
            ),
            failed_job("backup", job_type="backup", payload={}),
        ]
    )
    db.commit()

    expected = {
        "collect": "collect:source",
        "analyze": "analyze:article",
        "digest": "digest:2026-10-19T00:00:00+00:00",
        "backup": "maintenance:backup",
    }
    for job_id, key in expected.items():
        response = client.post(f"/api/v1/jobs/{job_id}/retry", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["queued"] is True
        db.expire_all()
        assert db.get(Job, job_id).dedup_key == key


def test_retry_rejects_disabled_source_missing_references_and_bad_payloads(db, client):
    db.add(Source(id="off", name="Off", url="https://example.com/off", enabled=False))
    db.flush()
    db.add_all(
        [
            failed_job("disabled", source_id="off"),
            failed_job("no-source", source_id=None),
            failed_job("no-article", job_type="analyze", payload={"article_id": "missing"}),
            failed_job(
                "bad-digest",
                job_type="daily_digest",
                payload={"start": "not-a-time", "end": now().isoformat()},
            ),
        ]
    )
    db.commit()

    cases = {
        "disabled": (409, "来源已停用"),
        "no-source": (422, "MISSING_SOURCE_ID"),
        "no-article": (409, "文章不存在"),
        "bad-digest": (422, "INVALID_DIGEST_WINDOW"),
    }
    for job_id, (code, detail) in cases.items():
        response = client.post(f"/api/v1/jobs/{job_id}/retry", headers=HEADERS)
        assert response.status_code == code
        assert detail in response.json()["detail"]

    db.expire_all()
    assert all(db.get(Job, job_id).status == "failed" for job_id in cases)


def test_notification_jobs_are_explicitly_not_retryable(db, client):
    db.add_all(
        [
            failed_job("notify", job_type="notify", payload={"notification_id": "note"}),
            failed_job(
                "batch",
                job_type="notify_batch",
                payload={"channel": "telegram", "cutoff": now().isoformat()},
            ),
        ]
    )
    db.commit()

    for job_id in ["notify", "batch"]:
        response = client.post(f"/api/v1/jobs/{job_id}/retry", headers=HEADERS)
        assert response.status_code == 409
        assert "通知任务" in response.json()["detail"]

    db.expire_all()
    assert all(db.get(Job, job_id).status == "failed" for job_id in ["notify", "batch"])


def test_retry_cas_does_not_overwrite_a_worker_claimed_running_job(db):
    db.add(failed_job("race"))
    db.commit()
    stale = db.get(Job, "race")

    # The Worker claims and commits the job after this ORM object was read.
    db.execute(
        update(Job)
        .where(Job.id == "race")
        .values(status="running", started_at=now())
        .execution_options(synchronize_session=False)
    )
    db.commit()
    assert stale.status == "failed"

    with pytest.raises(JobRetryRejected) as rejected:
        retry_failed_job(db, stale)
    assert rejected.value.code == "JOB_STATE_CHANGED"

    db.rollback()
    assert db.get(Job, "race").status == "running"


@pytest.mark.parametrize("article_id", [["article"], {"id": "article"}, 7])
def test_retry_rejects_malformed_analyze_article_id(db, client, article_id):
    article(db)
    db.add(failed_job("bad-article", job_type="analyze", payload={"article_id": article_id}))
    db.commit()

    # Truthy non-strings are not valid identifiers: reject with 422 instead of
    # letting a list/dict reach a query (409) or raise an unhandled 500.
    response = client.post("/api/v1/jobs/bad-article/retry", headers=HEADERS)
    assert response.status_code == 422
    assert response.headers["X-Error-Code"] == "INVALID_JOB_PAYLOAD"
    assert "INVALID_ARTICLE_ID" in response.json()["detail"]

    db.expire_all()
    job = db.get(Job, "bad-article")
    assert job.status == "failed"
    assert job.attempt_count == 3 and job.last_error == "HTTP_500"


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-10-20T00:00:00+00:00", "2026-10-19T00:00:00+00:00"),
        ("2026-10-20T00:00:00+00:00", "2026-10-20T00:00:00+00:00"),
    ],
)
def test_retry_rejects_inverted_or_empty_digest_window(db, client, start, end):
    db.add(failed_job("inverted", job_type="daily_digest", payload={"start": start, "end": end}))
    db.commit()

    response = client.post("/api/v1/jobs/inverted/retry", headers=HEADERS)
    assert response.status_code == 422
    assert response.headers["X-Error-Code"] == "INVALID_JOB_PAYLOAD"
    assert "INVALID_DIGEST_WINDOW" in response.json()["detail"]

    db.expire_all()
    assert db.get(Job, "inverted").status == "failed"


def test_retry_rejects_payload_that_is_not_an_object(db, client):
    db.add(failed_job("scalar-payload", job_type="backup", payload=["not", "a", "dict"]))
    db.commit()

    response = client.post("/api/v1/jobs/scalar-payload/retry", headers=HEADERS)
    assert response.status_code == 422
    assert response.headers["X-Error-Code"] == "INVALID_JOB_PAYLOAD"
    assert "INVALID_JOB_PAYLOAD" in response.json()["detail"]

    db.expire_all()
    assert db.get(Job, "scalar-payload").status == "failed"


def test_retry_reports_concurrent_removal_as_not_found(db):
    db.add(failed_job("removed"))
    db.commit()
    stale = db.get(Job, "removed")

    # Retention deletes the row from another session after this read. The CAS
    # then matches no row; rollback expires ``stale``, so the not-found branch
    # must re-read by stored id instead of touching the expired object.
    with sessionmaker(db.bind, expire_on_commit=False)() as concurrent:
        concurrent.execute(delete(Job).where(Job.id == "removed"))
        concurrent.commit()

    with pytest.raises(JobRetryRejected) as rejected:
        retry_failed_job(db, stale)
    assert rejected.value.code == "JOB_NOT_FOUND"
    assert rejected.value.status_code == 404

    db.rollback()
    assert db.get(Job, "removed") is None
