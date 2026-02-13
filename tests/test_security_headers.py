"""Tests for security headers middleware (CSP, HSTS, etc.)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine


def _init_test_app(
    tmp_path: Path, monkeypatch, csp_enabled: bool = True, hsts_enabled: bool = True
):
    """
    Configure a temporary SQLite DB and return a FastAPI TestClient.
    """
    db_path = tmp_path / "security_headers_test.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("HEALTHARCHIVE_CSP_ENABLED", "1" if csp_enabled else "0")
    monkeypatch.setenv("HEALTHARCHIVE_HSTS_ENABLED", "1" if hsts_enabled else "0")

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


def test_csp_header_on_json_endpoints(tmp_path, monkeypatch):
    """Test that restrictive CSP is applied to JSON API endpoints."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert "Content-Security-Policy" in response.headers
    csp = response.headers["Content-Security-Policy"]
    # Should have restrictive policy for JSON endpoints
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_csp_header_on_raw_snapshot_endpoint(tmp_path, monkeypatch):
    """Test that permissive CSP is applied to raw snapshot HTML replay."""
    client = _init_test_app(tmp_path, monkeypatch)

    # Raw snapshot endpoint needs a snapshot ID, but we can test with a 404
    # to verify the CSP header is set correctly
    response = client.get("/api/snapshots/raw/999999")

    # May be 404 (snapshot not found) but CSP should still be present
    assert "Content-Security-Policy" in response.headers
    csp = response.headers["Content-Security-Policy"]
    # Should have permissive policy for archived HTML
    assert "script-src 'unsafe-inline'" in csp
    assert "style-src 'unsafe-inline'" in csp
    assert "img-src * data:" in csp


def test_hsts_header_present(tmp_path, monkeypatch):
    """Test that HSTS header is present when enabled."""
    client = _init_test_app(tmp_path, monkeypatch, hsts_enabled=True)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert "Strict-Transport-Security" in response.headers
    hsts = response.headers["Strict-Transport-Security"]
    # Should include max-age and includeSubDomains
    assert "max-age=" in hsts
    assert "includeSubDomains" in hsts


def test_hsts_can_be_disabled(tmp_path, monkeypatch):
    """Test that HSTS header can be disabled via environment variable."""
    client = _init_test_app(tmp_path, monkeypatch, hsts_enabled=False)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert "Strict-Transport-Security" not in response.headers


def test_csp_can_be_disabled(tmp_path, monkeypatch):
    """Test that CSP header can be disabled via environment variable."""
    client = _init_test_app(tmp_path, monkeypatch, csp_enabled=False)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert "Content-Security-Policy" not in response.headers


def test_all_security_headers_present(tmp_path, monkeypatch):
    """Test that all expected security headers are present."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/health")

    assert response.status_code == 200

    # Check all security headers
    assert "X-Content-Type-Options" in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"

    assert "Referrer-Policy" in response.headers
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    assert "X-Frame-Options" in response.headers
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"

    assert "Permissions-Policy" in response.headers
    assert "geolocation=()" in response.headers["Permissions-Policy"]

    assert "Content-Security-Policy" in response.headers
    assert "Strict-Transport-Security" in response.headers


def test_raw_snapshot_no_x_frame_options(tmp_path, monkeypatch):
    """Test that raw snapshot endpoint does not have X-Frame-Options."""
    client = _init_test_app(tmp_path, monkeypatch)

    # Test with non-existent snapshot ID (will 404 but headers will be set)
    response = client.get("/api/snapshots/raw/999999")

    # X-Frame-Options should NOT be present for raw snapshot endpoint
    assert "X-Frame-Options" not in response.headers


def test_security_headers_on_different_endpoints(tmp_path, monkeypatch):
    """Test that security headers are consistently applied across endpoints."""
    client = _init_test_app(tmp_path, monkeypatch)

    endpoints = [
        "/api/health",
        "/api/stats",
        "/api/sources",
        "/api/search?q=test",
    ]

    for endpoint in endpoints:
        response = client.get(endpoint)
        # All endpoints should have security headers
        assert "X-Content-Type-Options" in response.headers
        assert "Content-Security-Policy" in response.headers
        assert "Strict-Transport-Security" in response.headers


def test_hsts_max_age_configurable(tmp_path, monkeypatch):
    """Test that HSTS max-age is configurable via environment variable."""
    custom_max_age = 7776000  # 90 days
    monkeypatch.setenv("HEALTHARCHIVE_HSTS_MAX_AGE", str(custom_max_age))

    client = _init_test_app(tmp_path, monkeypatch, hsts_enabled=True)

    response = client.get("/api/health")

    assert "Strict-Transport-Security" in response.headers
    hsts = response.headers["Strict-Transport-Security"]
    assert f"max-age={custom_max_age}" in hsts
