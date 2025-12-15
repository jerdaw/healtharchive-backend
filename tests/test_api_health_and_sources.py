from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import Snapshot, Source


def _init_test_app(tmp_path: Path, monkeypatch):
    """
    Configure a temporary SQLite DB and return a FastAPI TestClient.
    """
    db_path = tmp_path / "api_test.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    # Reset cached engine/session so we pick up the new URL.
    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    from ha_backend.api import app

    return TestClient(app)


def test_health_endpoint(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "ok"

    # Basic security headers should be present on the health response.
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert (
        resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    )
    assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"

    head_resp = client.head("/api/health")
    assert head_resp.status_code == 200


def test_health_endpoint_includes_checks(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "ok"

    checks = body.get("checks") or {}
    # At minimum we expect a database check entry.
    assert "db" in checks


def test_sources_endpoint_with_data(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    # Insert minimal data: two sources and a handful of snapshots.
    with get_session() as session:
        hc = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="Health Canada",
            enabled=True,
        )
        phac = Source(
            code="phac",
            name="Public Health Agency of Canada",
            base_url="https://www.canada.ca/en/public-health.html",
            description="PHAC",
            enabled=True,
        )
        session.add_all([hc, phac])
        session.flush()

        ts1 = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 2, 1, 12, 0, tzinfo=timezone.utc)

        session.add_all(
            [
                Snapshot(
                    job_id=None,
                    source_id=hc.id,
                    url="https://www.canada.ca/en/health-canada.html",
                    normalized_url_group="https://www.canada.ca/en/health-canada.html",
                    capture_timestamp=ts1,
                    mime_type="text/html",
                    status_code=200,
                    title="HC Home",
                    snippet="Health Canada home",
                    language="en",
                    warc_path="/warcs/hc1.warc.gz",
                    warc_record_id="hc-1",
                ),
                Snapshot(
                    job_id=None,
                    source_id=phac.id,
                    url="https://www.canada.ca/en/public-health.html",
                    normalized_url_group="https://www.canada.ca/en/public-health.html",
                    capture_timestamp=ts2,
                    mime_type="text/html",
                    status_code=200,
                    title="PHAC Home",
                    snippet="PHAC home",
                    language="en",
                    warc_path="/warcs/phac1.warc.gz",
                    warc_record_id="phac-1",
                ),
            ]
        )

    resp = client.get("/api/sources")
    assert resp.status_code == 200
    sources = resp.json()

    # Expect two entries, one for each source with snapshots.
    codes = {s["sourceCode"] for s in sources}
    assert "hc" in codes
    assert "phac" in codes


def test_stats_endpoint_with_no_data(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    resp = client.get("/api/stats")
    assert resp.status_code == 200
    body = resp.json()

    assert body["snapshotsTotal"] == 0
    assert body["pagesTotal"] == 0
    assert body["sourcesTotal"] == 0
    assert body["latestCaptureDate"] is None

    assert resp.headers.get("Cache-Control") is not None


def test_stats_endpoint_with_data(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    with get_session() as session:
        hc = Source(code="hc", name="Health Canada", enabled=True)
        phac = Source(code="phac", name="Public Health Agency of Canada", enabled=True)
        session.add_all([hc, phac])
        session.flush()

        ts1 = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 2, 1, 12, 0, tzinfo=timezone.utc)

        session.add_all(
            [
                Snapshot(
                    job_id=None,
                    source_id=hc.id,
                    url="https://www.canada.ca/en/health-canada.html",
                    normalized_url_group="https://www.canada.ca/en/health-canada.html",
                    capture_timestamp=ts1,
                    mime_type="text/html",
                    status_code=200,
                    title="HC Home",
                    snippet="Health Canada home",
                    language="en",
                    warc_path="/warcs/hc1.warc.gz",
                    warc_record_id="hc-1",
                ),
                Snapshot(
                    job_id=None,
                    source_id=hc.id,
                    url="https://www.canada.ca/en/health-canada.html?foo=bar",
                    normalized_url_group="https://www.canada.ca/en/health-canada.html",
                    capture_timestamp=ts2,
                    mime_type="text/html",
                    status_code=200,
                    title="HC Home Updated",
                    snippet="Health Canada home updated",
                    language="en",
                    warc_path="/warcs/hc2.warc.gz",
                    warc_record_id="hc-2",
                ),
                Snapshot(
                    job_id=None,
                    source_id=phac.id,
                    url="https://www.canada.ca/en/public-health.html",
                    normalized_url_group=None,
                    capture_timestamp=ts2,
                    mime_type="text/html",
                    status_code=200,
                    title="PHAC Home",
                    snippet="PHAC home",
                    language="en",
                    warc_path="/warcs/phac1.warc.gz",
                    warc_record_id="phac-1",
                ),
            ]
        )

    resp = client.get("/api/stats")
    assert resp.status_code == 200
    body = resp.json()

    assert body["snapshotsTotal"] == 3
    assert body["pagesTotal"] == 2
    assert body["sourcesTotal"] == 2
    assert body["latestCaptureDate"] == "2025-02-01"
