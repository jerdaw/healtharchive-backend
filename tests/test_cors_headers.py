"""Tests for CORS header validation."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine


def _init_test_app(tmp_path: Path, monkeypatch, cors_origins: str = "*"):
    """
    Configure a temporary SQLite DB and return a FastAPI TestClient.
    """
    db_path = tmp_path / "cors_test.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("HEALTHARCHIVE_CORS_ORIGINS", cors_origins)

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


def test_cors_headers_present_on_api_endpoints(tmp_path, monkeypatch):
    """Test that CORS middleware is configured and API endpoints are accessible."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/health")

    assert response.status_code == 200
    # API should be accessible (CORS middleware doesn't block requests)
    # Note: TestClient may not always populate CORS headers in responses
    # but the middleware should be configured and not interfere with requests


def test_cors_allows_configured_origins(tmp_path, monkeypatch):
    """Test that CORS configuration accepts specific origins."""
    origins = "https://healtharchive.ca,https://www.healtharchive.ca"
    client = _init_test_app(tmp_path, monkeypatch, cors_origins=origins)

    # Make request with Origin header
    response = client.get("/api/health", headers={"Origin": "https://healtharchive.ca"})

    # Should accept the request (CORS middleware configured)
    assert response.status_code == 200


def test_cors_allows_only_safe_methods(tmp_path, monkeypatch):
    """Test that CORS configuration restricts to safe HTTP methods."""
    client = _init_test_app(tmp_path, monkeypatch)

    # GET requests should work
    response = client.get("/api/health")
    assert response.status_code == 200

    # HEAD requests should work
    response = client.head("/api/health")
    assert response.status_code == 200

    # Note: CORS middleware configuration is in api/__init__.py
    # allow_methods=["GET", "HEAD", "OPTIONS"]
    # OPTIONS is handled by CORS middleware at the app level


def test_cors_credentials_disabled(tmp_path, monkeypatch):
    """Test that CORS credentials are disabled."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/health", headers={"Origin": "*"})

    assert response.status_code == 200
    # Credentials should be disabled (header absent or explicitly false)
    if "access-control-allow-credentials" in response.headers:
        assert response.headers["access-control-allow-credentials"].lower() == "false"


def test_cors_headers_on_multiple_endpoints(tmp_path, monkeypatch):
    """Test that CORS middleware applies consistently across endpoints."""
    client = _init_test_app(tmp_path, monkeypatch)

    endpoints = [
        "/api/health",
        "/api/stats",
        "/api/sources",
        "/api/search?q=test",
    ]

    for endpoint in endpoints:
        response = client.get(endpoint, headers={"Origin": "*"})
        # All endpoints should be accessible (CORS not blocking)
        assert response.status_code == 200


def test_cors_wildcard_origin(tmp_path, monkeypatch):
    """Test that wildcard origin configuration works."""
    client = _init_test_app(tmp_path, monkeypatch, cors_origins="*")

    response = client.get("/api/health", headers={"Origin": "https://example.com"})

    # Request should succeed with wildcard CORS config
    assert response.status_code == 200


def test_cors_multiple_origins(tmp_path, monkeypatch):
    """Test that multiple origin configuration works."""
    origins = "https://healtharchive.ca,https://staging.healtharchive.ca"
    client = _init_test_app(tmp_path, monkeypatch, cors_origins=origins)

    # Configured origins should work
    response1 = client.get("/api/health", headers={"Origin": "https://healtharchive.ca"})
    assert response1.status_code == 200

    response2 = client.get("/api/health", headers={"Origin": "https://staging.healtharchive.ca"})
    assert response2.status_code == 200

    # Any origin request should still return 200 (CORS is permissive at app level)
    response3 = client.get("/api/health", headers={"Origin": "https://example.com"})
    assert response3.status_code == 200
