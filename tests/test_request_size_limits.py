"""Tests for request size limit middleware."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine


def _init_test_app(tmp_path: Path, monkeypatch):
    """
    Configure a temporary SQLite DB and return a FastAPI TestClient.
    """
    db_path = tmp_path / "request_size_test.db"
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


def test_request_body_size_limit(tmp_path, monkeypatch):
    """Test that oversized request bodies are rejected with 413."""
    # Set a small limit for testing (10KB)
    monkeypatch.setenv("HEALTHARCHIVE_MAX_REQUEST_BODY_SIZE", "10240")

    client = _init_test_app(tmp_path, monkeypatch)

    # Create a payload that exceeds the limit
    large_payload = {"data": "x" * 20000}  # 20KB of data

    response = client.post(
        "/api/reports",
        json=large_payload,
        headers={"Content-Length": str(len(str(large_payload)))},
    )

    assert response.status_code == 413
    body = response.json()
    assert body["error"] == "Payload Too Large"
    assert "exceeds maximum size" in body["detail"]


def test_request_body_within_limit(tmp_path, monkeypatch):
    """Test that requests within size limits are processed normally."""
    # Set a reasonable limit
    monkeypatch.setenv("HEALTHARCHIVE_MAX_REQUEST_BODY_SIZE", "1048576")  # 1MB

    client = _init_test_app(tmp_path, monkeypatch)

    # Create a small payload
    small_payload = {
        "url": "https://example.com",
        "issueType": "broken_link",
        "description": "Test report",
    }

    response = client.post("/api/reports", json=small_payload)

    # Should not be rejected for size (may fail validation, but not 413)
    assert response.status_code != 413


def test_query_string_length_limit(tmp_path, monkeypatch):
    """Test that oversized query strings are rejected with 414."""
    # Set a small limit for testing (1KB)
    monkeypatch.setenv("HEALTHARCHIVE_MAX_QUERY_STRING_LENGTH", "1024")

    client = _init_test_app(tmp_path, monkeypatch)

    # Create a very long query string
    long_query = "q=" + ("x" * 2000)

    response = client.get(f"/api/search?{long_query}")

    assert response.status_code == 414
    body = response.json()
    assert body["error"] == "URI Too Long"
    assert "exceeds maximum length" in body["detail"]


def test_query_string_within_limit(tmp_path, monkeypatch):
    """Test that queries within size limits are processed normally."""
    # Set a reasonable limit
    monkeypatch.setenv("HEALTHARCHIVE_MAX_QUERY_STRING_LENGTH", "8192")

    client = _init_test_app(tmp_path, monkeypatch)

    # Normal query
    response = client.get("/api/search?q=test&source=hc&page=1")

    # Should not be rejected for size
    assert response.status_code != 414


def test_health_endpoint_unaffected_by_limits(tmp_path, monkeypatch):
    """Test that endpoints without query strings or bodies work normally."""
    monkeypatch.setenv("HEALTHARCHIVE_MAX_REQUEST_BODY_SIZE", "1024")
    monkeypatch.setenv("HEALTHARCHIVE_MAX_QUERY_STRING_LENGTH", "1024")

    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
