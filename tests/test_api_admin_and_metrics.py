from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Snapshot, Source


def _init_test_app(tmp_path: Path, monkeypatch) -> TestClient:
    """
    Configure a temporary SQLite DB and return a FastAPI TestClient.
    """
    db_path = tmp_path / "api_admin_test.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    # Reset cached engine/session so we pick up the new URL.
    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    from ha_backend.api import app

    return TestClient(app)


def _seed_basic_data() -> None:
    """
    Seed a single source, a couple of jobs, and some snapshots.
    """
    with get_session() as session:
        src = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="HC",
            enabled=True,
        )
        session.add(src)
        session.flush()

        now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)

        job1 = ArchiveJob(
            source_id=src.id,
            name="job1",
            output_dir="/tmp/job1",
            status="queued",
        )
        job2 = ArchiveJob(
            source_id=src.id,
            name="job2",
            output_dir="/tmp/job2",
            status="completed",
            pages_crawled=10,
            pages_total=20,
            pages_failed=1,
            warc_file_count=2,
            indexed_page_count=5,
        )
        session.add_all([job1, job2])
        session.flush()

        snap = Snapshot(
            job_id=job2.id,
            source_id=src.id,
            url="https://www.canada.ca/en/health-canada.html",
            normalized_url_group="https://www.canada.ca/en/health-canada.html",
            capture_timestamp=now,
            mime_type="text/html",
            status_code=200,
            title="HC Home",
            snippet="Health Canada home",
            language="en",
            warc_path="/warcs/hc1.warc.gz",
            warc_record_id="hc-1",
        )
        session.add(snap)


def test_admin_jobs_open_when_no_token(tmp_path, monkeypatch) -> None:
    # Ensure admin token is not set.
    monkeypatch.delenv("HEALTHARCHIVE_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("HEALTHARCHIVE_ENV", raising=False)
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_basic_data()

    resp = client.get("/api/admin/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 2
    assert len(body["items"]) >= 2


def test_admin_jobs_require_token_when_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_ADMIN_TOKEN", "secret-token")
    monkeypatch.delenv("HEALTHARCHIVE_ENV", raising=False)
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_basic_data()

    # Missing token -> forbidden
    resp = client.get("/api/admin/jobs")
    assert resp.status_code == 403

    # Wrong token -> forbidden
    resp = client.get(
        "/api/admin/jobs",
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 403

    # Correct token -> allowed
    resp = client.get(
        "/api/admin/jobs",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert resp.status_code == 200


def test_admin_job_detail_and_status_counts(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HEALTHARCHIVE_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("HEALTHARCHIVE_ENV", raising=False)
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_basic_data()

    resp = client.get("/api/admin/jobs")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items
    job_id = items[0]["id"]

    # Job detail
    detail_resp = client.get(f"/api/admin/jobs/{job_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["id"] == job_id
    assert "outputDir" in detail
    assert "status" in detail

    # Status counts
    counts_resp = client.get("/api/admin/jobs/status-counts")
    assert counts_resp.status_code == 200
    counts = counts_resp.json()["counts"]
    assert "queued" in counts or "completed" in counts


def test_admin_job_snapshots_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HEALTHARCHIVE_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("HEALTHARCHIVE_ENV", raising=False)
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_basic_data()

    resp = client.get("/api/admin/jobs")
    job_id = resp.json()["items"][0]["id"]

    snaps_resp = client.get(f"/api/admin/jobs/{job_id}/snapshots")
    assert snaps_resp.status_code == 200
    snapshots = snaps_resp.json()
    assert isinstance(snapshots, list)


def test_metrics_require_token_when_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_ADMIN_TOKEN", "secret-token")
    monkeypatch.delenv("HEALTHARCHIVE_ENV", raising=False)
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_basic_data()

    # Missing token -> forbidden
    resp = client.get("/metrics")
    assert resp.status_code == 403

    # Correct token -> allowed
    resp = client.get("/metrics", headers={"Authorization": "Bearer secret-token"})
    assert resp.status_code == 200


def test_metrics_content_includes_basic_counters(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HEALTHARCHIVE_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("HEALTHARCHIVE_ENV", raising=False)
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_basic_data()

    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text

    # We expect at least the job, snapshot, and page metrics headers.
    assert "healtharchive_jobs_total" in body
    assert "healtharchive_snapshots_total" in body
    assert "healtharchive_jobs_pages_crawled_total" in body
    assert "healtharchive_jobs_pages_failed_total" in body


def test_metrics_include_cleanup_status_labels(tmp_path, monkeypatch) -> None:
    """
    /metrics should emit cleanup_status breakdown when jobs exist with
    different cleanup_status values.
    """
    monkeypatch.delenv("HEALTHARCHIVE_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("HEALTHARCHIVE_ENV", raising=False)
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_basic_data()

    # Mark one job as temp_cleaned to exercise the label.
    with get_session() as session:
        job = session.query(ArchiveJob).first()
        job.cleanup_status = "temp_cleaned"

    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text

    assert 'healtharchive_jobs_cleanup_status_total{cleanup_status="none"}' in body
    assert (
        'healtharchive_jobs_cleanup_status_total{cleanup_status="temp_cleaned"}' in body
    )


def test_metrics_include_page_totals_and_per_source(tmp_path, monkeypatch) -> None:
    """
    /metrics should emit global and per-source page counters based on
    ArchiveJob.pages_* fields.
    """
    monkeypatch.delenv("HEALTHARCHIVE_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("HEALTHARCHIVE_ENV", raising=False)
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_basic_data()

    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text

    # From _seed_basic_data we have one job with pages_crawled=10 and pages_failed=1.
    assert "healtharchive_jobs_pages_crawled_total" in body
    assert "healtharchive_jobs_pages_failed_total" in body
    assert 'healtharchive_jobs_pages_crawled_total{source="hc"}' in body
    assert 'healtharchive_jobs_pages_failed_total{source="hc"}' in body


def test_admin_requires_token_when_env_is_production(tmp_path, monkeypatch) -> None:
    """
    In production/staging environments, admin endpoints should fail closed if
    HEALTHARCHIVE_ADMIN_TOKEN is not configured.
    """
    monkeypatch.setenv("HEALTHARCHIVE_ENV", "production")
    monkeypatch.delenv("HEALTHARCHIVE_ADMIN_TOKEN", raising=False)
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_basic_data()

    resp = client.get("/api/admin/jobs")
    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == "Admin token not configured for this environment"
