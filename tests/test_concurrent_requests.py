"""Tests for concurrent request handling."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine


def _init_test_app(tmp_path: Path, monkeypatch):
    """
    Configure a temporary SQLite DB and return a FastAPI TestClient.
    """
    db_path = tmp_path / "concurrent_test.db"
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


def test_concurrent_health_checks(tmp_path, monkeypatch):
    """Test that concurrent health check requests are handled correctly."""
    client = _init_test_app(tmp_path, monkeypatch)

    def make_health_request():
        response = client.get("/api/health")
        return response.status_code, response.json()

    # Make 10 concurrent requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(make_health_request) for _ in range(10)]
        results = [future.result() for future in futures]

    # All requests should succeed
    for status_code, data in results:
        assert status_code == 200
        assert "status" in data
        assert data["status"] == "ok"


def test_concurrent_stats_requests(tmp_path, monkeypatch):
    """Test that concurrent stats requests don't cause database deadlocks."""
    client = _init_test_app(tmp_path, monkeypatch)

    def make_stats_request():
        response = client.get("/api/stats")
        return response.status_code, response.json()

    # Make 10 concurrent requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(make_stats_request) for _ in range(10)]
        results = [future.result() for future in futures]

    # All requests should succeed
    for status_code, data in results:
        assert status_code == 200
        assert isinstance(data, dict)


def test_concurrent_source_requests(tmp_path, monkeypatch):
    """Test that concurrent source list requests are handled correctly."""
    client = _init_test_app(tmp_path, monkeypatch)

    def make_sources_request():
        response = client.get("/api/sources")
        return response.status_code, response.json()

    # Make 10 concurrent requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(make_sources_request) for _ in range(10)]
        results = [future.result() for future in futures]

    # All requests should succeed
    for status_code, data in results:
        assert status_code == 200
        # Sources response is a list of source objects
        assert isinstance(data, list)


def test_concurrent_search_requests(tmp_path, monkeypatch):
    """Test that concurrent search requests are handled correctly."""
    client = _init_test_app(tmp_path, monkeypatch)

    def make_search_request(query):
        response = client.get(f"/api/search?q={query}")
        return response.status_code, response.json()

    # Make 10 concurrent requests with different queries
    queries = [f"test{i}" for i in range(10)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(make_search_request, q) for q in queries]
        results = [future.result() for future in futures]

    # All requests should succeed
    for status_code, data in results:
        assert status_code == 200
        assert "results" in data
        assert isinstance(data["results"], list)


def test_concurrent_mixed_requests(tmp_path, monkeypatch):
    """Test that concurrent requests to different endpoints don't interfere."""
    client = _init_test_app(tmp_path, monkeypatch)

    def make_health_request():
        return client.get("/api/health")

    def make_stats_request():
        return client.get("/api/stats")

    def make_sources_request():
        return client.get("/api/sources")

    def make_search_request():
        return client.get("/api/search?q=test")

    # Make 20 concurrent requests (5 of each type)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = []
        for _ in range(5):
            futures.append(executor.submit(make_health_request))
            futures.append(executor.submit(make_stats_request))
            futures.append(executor.submit(make_sources_request))
            futures.append(executor.submit(make_search_request))

        results = [future.result() for future in futures]

    # All requests should succeed
    for response in results:
        assert response.status_code == 200
        data = response.json()
        # Response should be valid JSON (dict or list)
        assert isinstance(data, (dict, list))


def test_concurrent_requests_same_session(tmp_path, monkeypatch):
    """Test that concurrent requests using the same client are handled correctly."""
    client = _init_test_app(tmp_path, monkeypatch)

    def make_request(endpoint):
        response = client.get(endpoint)
        return response.status_code

    endpoints = [
        "/api/health",
        "/api/stats",
        "/api/sources",
        "/api/search?q=test1",
        "/api/search?q=test2",
        "/api/search?q=test3",
    ]

    # Make concurrent requests using the same client
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(make_request, ep) for ep in endpoints]
        results = [future.result() for future in futures]

    # All requests should succeed
    for status_code in results:
        assert status_code == 200


def test_concurrent_requests_with_unique_request_ids(tmp_path, monkeypatch):
    """Test that concurrent requests get unique request IDs."""
    client = _init_test_app(tmp_path, monkeypatch)

    def make_request_and_get_id():
        response = client.get("/api/health")
        return response.headers.get("X-Request-Id")

    # Make 20 concurrent requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(make_request_and_get_id) for _ in range(20)]
        request_ids = [future.result() for future in futures]

    # All request IDs should be present
    assert all(request_ids)

    # All request IDs should be unique
    assert len(request_ids) == len(set(request_ids))


def test_concurrent_requests_under_load(tmp_path, monkeypatch):
    """Test API behavior under concurrent load."""
    client = _init_test_app(tmp_path, monkeypatch)

    def make_request(i):
        # Mix of different endpoints - use more non-rate-limited endpoints
        # to avoid hitting search rate limit (60/min)
        endpoints = [
            "/api/health",
            "/api/stats",
            "/api/sources",
            "/api/health",
            "/api/stats",
            "/api/sources",
            "/api/health",
            f"/api/search?q=query{i}",  # Only 1 in 8 is search
        ]
        endpoint = endpoints[i % len(endpoints)]
        try:
            response = client.get(endpoint)
            # Accept both 200 and 429 (rate limit) as non-error responses
            return response.status_code in (200, 429)
        except Exception:
            return False

    # Make 50 concurrent requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(make_request, i) for i in range(50)]
        results = [future.result() for future in futures]

    # At least 95% of requests should get a response (200 or 429)
    success_rate = sum(results) / len(results)
    assert success_rate >= 0.95, f"Only {success_rate * 100}% of requests succeeded"
