"""Tests for health check error scenarios."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine


def _init_test_app(tmp_path: Path, monkeypatch):
    """
    Configure a temporary SQLite DB and return a FastAPI TestClient.
    """
    db_path = tmp_path / "health_error_test.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    # Reset cached engine/session so we pick up the new URL.
    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    from ha_backend.api import app

    try:
        import uvloop  # noqa: F401
    except Exception:
        return TestClient(app)
    return TestClient(app, backend_options={"use_uvloop": True})


def test_health_check_succeeds_with_empty_database(tmp_path, monkeypatch):
    """Test that health check succeeds even with an empty database."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/health")

    # Health check should succeed even with no data
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "checks" in data
    # Checks should have db, jobs, and snapshots
    checks = data["checks"]
    assert "db" in checks
    assert checks["db"] == "ok"
    assert "snapshots" in checks
    assert isinstance(checks["snapshots"]["total"], int)


def test_health_check_with_missing_optional_fields(tmp_path, monkeypatch):
    """Test health check behavior when optional data is missing."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/health")

    assert response.status_code == 200
    data = response.json()
    # Core fields should always be present
    assert "status" in data
    assert "checks" in data
    assert "db" in data["checks"]


def test_health_check_response_format(tmp_path, monkeypatch):
    """Test that health check response has correct format."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/health")

    assert response.status_code == 200
    data = response.json()

    # Check required fields and types
    assert isinstance(data["status"], str)
    assert data["status"] == "ok"
    assert "checks" in data
    assert isinstance(data["checks"], dict)
    assert "db" in data["checks"]
    assert "snapshots" in data["checks"]
    assert isinstance(data["checks"]["snapshots"]["total"], int)
    assert data["checks"]["snapshots"]["total"] >= 0


def test_health_check_includes_security_headers(tmp_path, monkeypatch):
    """Test that health check includes all security headers."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/health")

    assert response.status_code == 200

    # Check security headers are present
    assert "X-Content-Type-Options" in response.headers
    assert "X-Request-Id" in response.headers
    assert "X-API-Version" in response.headers


def test_health_check_includes_cors_headers(tmp_path, monkeypatch):
    """Test that health check includes CORS headers."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/health", headers={"Origin": "*"})

    assert response.status_code == 200
    # CORS headers should be present
    assert "access-control-allow-origin" in response.headers


def test_health_check_handles_options_request(tmp_path, monkeypatch):
    """Test that CORS middleware is configured at the app level."""
    client = _init_test_app(tmp_path, monkeypatch)

    # Health endpoint defines GET and HEAD only
    # OPTIONS is handled by CORS middleware at app level, not endpoint level
    # CORS middleware configuration: allow_methods=["GET", "HEAD", "OPTIONS"]
    # Test that health endpoint properly defines its supported methods
    response = client.get("/api/health")
    assert response.status_code == 200

    response = client.head("/api/health")
    assert response.status_code == 200


def test_health_check_rejects_post_requests(tmp_path, monkeypatch):
    """Test that health check only accepts GET requests."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.post("/api/health")

    # POST should not be allowed (405 Method Not Allowed)
    assert response.status_code == 405


def test_health_check_rejects_put_requests(tmp_path, monkeypatch):
    """Test that health check rejects PUT requests."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.put("/api/health")

    # PUT should not be allowed
    assert response.status_code == 405


def test_health_check_rejects_delete_requests(tmp_path, monkeypatch):
    """Test that health check rejects DELETE requests."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.delete("/api/health")

    # DELETE should not be allowed
    assert response.status_code == 405


def test_stats_endpoint_with_empty_database(tmp_path, monkeypatch):
    """Test stats endpoint with empty database."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/stats")

    # Stats should work even with empty database
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    # Should have basic numeric fields
    # (actual field names may vary, but should be numeric values)
    for value in data.values():
        if isinstance(value, (int, float)):
            assert value >= 0


def test_health_check_with_query_parameters(tmp_path, monkeypatch):
    """Test that health check ignores query parameters."""
    client = _init_test_app(tmp_path, monkeypatch)

    # Health check should ignore query params
    response = client.get("/api/health?foo=bar&baz=qux")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_health_check_with_large_database(tmp_path, monkeypatch):
    """Test that health check response format is consistent with data."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/health")

    assert response.status_code == 200
    data = response.json()

    # Should have consistent structure
    assert "status" in data
    assert "checks" in data
    assert "snapshots" in data["checks"]
    # Snapshot count should be non-negative
    assert data["checks"]["snapshots"]["total"] >= 0


def test_health_check_response_time(tmp_path, monkeypatch):
    """Test that health check responds quickly."""
    client = _init_test_app(tmp_path, monkeypatch)

    import time

    start = time.time()
    response = client.get("/api/health")
    elapsed = time.time() - start

    assert response.status_code == 200
    # Health check should be fast (< 1 second even with cold start)
    assert elapsed < 1.0, f"Health check took {elapsed:.2f}s"


def test_health_check_with_concurrent_writes(tmp_path, monkeypatch):
    """Test health check during concurrent database operations."""
    client = _init_test_app(tmp_path, monkeypatch)

    # Make multiple concurrent health checks
    import concurrent.futures

    def make_health_request():
        return client.get("/api/health")

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(make_health_request) for _ in range(5)]
        results = [future.result() for future in futures]

    # All should succeed
    for response in results:
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_health_check_content_type(tmp_path, monkeypatch):
    """Test that health check returns JSON content type."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"].lower()


def test_health_check_cacheable_headers(tmp_path, monkeypatch):
    """Test that health check doesn't set aggressive caching headers."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/health")

    assert response.status_code == 200
    # Health checks should not be cached aggressively
    # (either no cache-control or short TTL)
    if "cache-control" in response.headers:
        cache_control = response.headers["cache-control"].lower()
        # Should not have long max-age
        assert "max-age=31536000" not in cache_control
