"""Tests for request ID middleware and correlation logging."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine


def _init_test_app(tmp_path: Path, monkeypatch):
    """
    Configure a temporary SQLite DB and return a FastAPI TestClient.
    """
    db_path = tmp_path / "request_id_test.db"
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


def test_request_id_auto_generated(tmp_path, monkeypatch):
    """Test that response includes auto-generated X-Request-Id header (UUID)."""
    client = _init_test_app(tmp_path, monkeypatch)
    response = client.get("/api/health")
    assert response.status_code == 200

    request_id = response.headers.get("X-Request-Id")
    assert request_id is not None

    # Validate it's a UUIDv4 format
    uuid_pattern = re.compile(
        r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$"
    )
    assert uuid_pattern.match(request_id), f"Invalid UUID format: {request_id}"

    # API version header should also be present
    assert response.headers.get("X-API-Version") == "1"


def test_request_id_passthrough(tmp_path, monkeypatch):
    """Test that client-provided X-Request-Id is honored (pass-through)."""
    client = _init_test_app(tmp_path, monkeypatch)
    custom_request_id = "test-custom-request-id-12345"

    response = client.get("/api/health", headers={"X-Request-Id": custom_request_id})
    assert response.status_code == 200

    returned_request_id = response.headers.get("X-Request-Id")
    assert returned_request_id == custom_request_id


def test_request_id_in_logs(tmp_path, monkeypatch):
    """Test that request ID filter is properly configured in logging."""
    import logging

    from ha_backend.logging_config import RequestIdFilter

    client = _init_test_app(tmp_path, monkeypatch)

    # Verify that RequestIdFilter is installed on root logger handlers
    root_logger = logging.getLogger()
    assert len(root_logger.handlers) > 0, "No handlers configured on root logger"

    filter_found = False
    for handler in root_logger.handlers:
        for filter_obj in handler.filters:
            if isinstance(filter_obj, RequestIdFilter):
                filter_found = True
                break

    assert filter_found, "RequestIdFilter not found in any handler"

    # Verify request ID is set in context during request
    custom_request_id = "test-log-request-id-67890"
    response = client.get("/api/health", headers={"X-Request-Id": custom_request_id})
    assert response.status_code == 200
    assert response.headers.get("X-Request-Id") == custom_request_id
