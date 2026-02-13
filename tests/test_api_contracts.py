"""API contract tests - verify response schemas match Pydantic models."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.api.schemas import (
    ArchiveStatsSchema,
    ChangeEventSchema,
    ExportManifestSchema,
    SearchResponseSchema,
    SnapshotSummarySchema,
    SourceSummarySchema,
)
from ha_backend.db import Base, get_engine
from ha_backend.models import Source


def _init_test_app(tmp_path: Path, monkeypatch):
    """
    Configure a temporary SQLite DB and return a FastAPI TestClient.
    """
    db_path = tmp_path / "api_contracts_test.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    # Reset cached engine/session so we pick up the new URL.
    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    # Seed sources
    from ha_backend.db import get_session

    with get_session() as session:
        session.add(Source(code="hc", name="Health Canada", enabled=True))
        session.add(Source(code="phac", name="Public Health Agency of Canada", enabled=True))
        session.commit()

    from ha_backend.api import app

    try:
        import uvloop  # noqa: F401
    except Exception:
        return TestClient(app)
    return TestClient(app, backend_options={"use_uvloop": True})


def test_health_endpoint_schema(tmp_path, monkeypatch):
    """Test that /api/health returns expected structure."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/health")

    assert response.status_code == 200
    data = response.json()

    # Verify structure
    assert "status" in data
    assert "checks" in data
    assert isinstance(data["status"], str)
    assert isinstance(data["checks"], dict)

    # Verify checks structure
    checks = data["checks"]
    assert "db" in checks
    assert "snapshots" in checks
    assert checks["db"] in ("ok", "error")
    assert isinstance(checks["snapshots"], dict)
    assert "total" in checks["snapshots"]
    assert isinstance(checks["snapshots"]["total"], int)


def test_stats_endpoint_schema(tmp_path, monkeypatch):
    """Test that /api/stats conforms to ArchiveStatsSchema."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/stats")

    assert response.status_code == 200
    data = response.json()

    # Validate against Pydantic schema
    stats = ArchiveStatsSchema(**data)
    assert isinstance(stats.snapshotsTotal, int)
    assert isinstance(stats.sourcesTotal, int)
    assert isinstance(stats.pagesTotal, int)


def test_sources_endpoint_schema(tmp_path, monkeypatch):
    """Test that /api/sources returns list of SourceSummarySchema."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/sources")

    assert response.status_code == 200
    data = response.json()

    # Should be a list (may be empty if no snapshots indexed)
    assert isinstance(data, list)

    # Validate each source against Pydantic schema (if any returned)
    for source_data in data:
        source = SourceSummarySchema(**source_data)
        assert isinstance(source.sourceCode, str)
        assert isinstance(source.sourceName, str)
        assert isinstance(source.recordCount, int)


def test_search_endpoint_schema(tmp_path, monkeypatch):
    """Test that /api/search conforms to SearchResponseSchema."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/search?q=test&pageSize=10&page=1")

    assert response.status_code == 200
    data = response.json()

    # Validate against Pydantic schema
    search_result = SearchResponseSchema(**data)
    assert isinstance(search_result.results, list)
    assert isinstance(search_result.total, int)
    assert isinstance(search_result.page, int)
    assert isinstance(search_result.pageSize, int)

    # Each result should conform to SnapshotSummarySchema
    for result in search_result.results:
        assert isinstance(result, SnapshotSummarySchema)


def test_search_pagination_parameters(tmp_path, monkeypatch):
    """Test that search pagination parameters are validated."""
    client = _init_test_app(tmp_path, monkeypatch)

    # Valid pagination
    response = client.get("/api/search?q=test&page=1&pageSize=20")
    assert response.status_code == 200

    # Page must be >= 1
    response = client.get("/api/search?q=test&page=0")
    assert response.status_code == 422  # Validation error

    # PageSize must be within bounds
    response = client.get("/api/search?q=test&pageSize=1000")
    assert response.status_code == 422  # Validation error


def test_exports_manifest_schema(tmp_path, monkeypatch):
    """Test that /api/exports conforms to ExportManifestSchema."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/exports")

    assert response.status_code == 200
    data = response.json()

    # Validate against Pydantic schema
    manifest = ExportManifestSchema(**data)
    assert isinstance(manifest.enabled, bool)
    assert isinstance(manifest.formats, list)
    assert isinstance(manifest.defaultLimit, int)
    assert isinstance(manifest.maxLimit, int)


def test_usage_endpoint_schema(tmp_path, monkeypatch):
    """Test that /api/usage returns expected structure."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/usage")

    assert response.status_code == 200
    data = response.json()

    # Verify structure
    assert "enabled" in data
    assert isinstance(data["enabled"], bool)

    if data["enabled"]:
        assert "windowDays" in data
        assert "totals" in data
        assert "daily" in data
        assert isinstance(data["windowDays"], int)
        assert isinstance(data["totals"], dict)
        assert isinstance(data["daily"], list)


def test_changes_endpoint_schema(tmp_path, monkeypatch):
    """Test that /api/changes returns list of ChangeEventSchema."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/changes?pageSize=10&page=1")

    assert response.status_code == 200
    data = response.json()

    # Verify structure
    assert "enabled" in data
    assert "results" in data
    assert "total" in data
    assert "page" in data
    assert "pageSize" in data

    assert isinstance(data["enabled"], bool)
    assert isinstance(data["results"], list)
    assert isinstance(data["total"], int)
    assert isinstance(data["page"], int)
    assert isinstance(data["pageSize"], int)

    # Each event should conform to ChangeEventSchema (if any exist)
    for event_data in data["results"]:
        event = ChangeEventSchema(**event_data)
        assert isinstance(event.changeId, int)


def test_api_version_header(tmp_path, monkeypatch):
    """Test that all API endpoints return X-API-Version header."""
    client = _init_test_app(tmp_path, monkeypatch)

    endpoints = [
        "/api/health",
        "/api/stats",
        "/api/sources",
        "/api/search?q=test",
        "/api/exports",
        "/api/usage",
        "/api/changes",
    ]

    for endpoint in endpoints:
        response = client.get(endpoint)
        assert response.status_code == 200
        assert "X-API-Version" in response.headers
        assert response.headers["X-API-Version"] == "1"


def test_request_id_header(tmp_path, monkeypatch):
    """Test that all API endpoints return X-Request-Id header."""
    client = _init_test_app(tmp_path, monkeypatch)

    endpoints = [
        "/api/health",
        "/api/stats",
        "/api/sources",
        "/api/search?q=test",
    ]

    for endpoint in endpoints:
        response = client.get(endpoint)
        assert response.status_code == 200
        assert "X-Request-Id" in response.headers
        # Request ID should be a UUID
        request_id = response.headers["X-Request-Id"]
        assert len(request_id) == 36  # UUID format
        assert request_id.count("-") == 4  # UUID has 4 hyphens


def test_content_type_headers(tmp_path, monkeypatch):
    """Test that JSON endpoints return correct Content-Type."""
    client = _init_test_app(tmp_path, monkeypatch)

    json_endpoints = [
        "/api/health",
        "/api/stats",
        "/api/sources",
        "/api/search?q=test",
        "/api/exports",
    ]

    for endpoint in json_endpoints:
        response = client.get(endpoint)
        assert response.status_code == 200
        assert "application/json" in response.headers["Content-Type"].lower()


def test_rss_feed_content_type(tmp_path, monkeypatch):
    """Test that RSS feed returns correct Content-Type when available."""
    client = _init_test_app(tmp_path, monkeypatch)

    response = client.get("/api/changes/rss")

    # RSS endpoint should either work (200) or be disabled (404)
    assert response.status_code in (200, 404)

    if response.status_code == 200:
        content_type = response.headers.get("Content-Type", "").lower()
        # Should be RSS or XML
        assert "xml" in content_type or "rss" in content_type


def test_error_responses_are_json(tmp_path, monkeypatch):
    """Test that error responses are also JSON formatted."""
    client = _init_test_app(tmp_path, monkeypatch)

    # 404 error
    response = client.get("/api/snapshot/999999")
    assert response.status_code == 404
    assert "application/json" in response.headers.get("Content-Type", "").lower()
    data = response.json()
    assert "detail" in data or "error" in data

    # 422 validation error
    response = client.get("/api/search?page=0")
    assert response.status_code == 422
    assert "application/json" in response.headers.get("Content-Type", "").lower()
    data = response.json()
    assert "detail" in data
