from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app


def test_auth_crud_and_filters(db, monkeypatch):
    app.dependency_overrides[get_session] = lambda: db
    monkeypatch.setattr("app.main.validate_url", lambda u: u)
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/health/live").status_code == 200
            assert client.get("/api/v1/sources").status_code == 401
            headers = {"X-API-Key": "test-admin"}
            assert client.get("/api/v1/sources", headers=headers).status_code == 200
            added = client.post(
                "/api/v1/industries", headers=headers, json={"name": "Custom", "slug": "custom"}
            )
            assert added.status_code == 201
            identity = added.json()["id"]
            assert (
                client.patch(
                    f"/api/v1/industries/{identity}", headers=headers, json={"keywords": ["hello"]}
                ).status_code
                == 200
            )
            invalid = client.post(
                "/api/v1/sources",
                headers=headers,
                json={"name": "x", "url": "https://example.com", "poll_interval_seconds": 1},
            )
            assert invalid.status_code == 422
            assert client.get("/api/v1/articles/search?q=AI", headers=headers).status_code == 200
            assert (
                client.get("/api/v1/articles?start=2026-01-01T00:00:00", headers=headers).status_code == 422
            )
            assert client.get("/api/v1/stats/overview", headers=headers).status_code == 200
            assert client.delete(f"/api/v1/industries/{identity}", headers=headers).status_code == 200
            assert "api_key" not in client.get("/api/v1/settings", headers=headers).text.lower()
            assert client.get("/openapi.json").status_code == 200
    finally:
        app.dependency_overrides.clear()
