from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Snapshot, Source


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

    hc_payload = next(s for s in sources if s["sourceCode"] == "hc")
    assert hc_payload["baseUrl"] == "https://www.canada.ca/en/health-canada.html"
    assert hc_payload["description"] == "Health Canada"
    assert hc_payload["entryRecordId"] == hc_payload["latestRecordId"]

    phac_payload = next(s for s in sources if s["sourceCode"] == "phac")
    assert phac_payload["baseUrl"] == "https://www.canada.ca/en/public-health.html"
    assert phac_payload["description"] == "PHAC"
    assert phac_payload["entryRecordId"] == phac_payload["latestRecordId"]


def test_sources_entry_record_prefers_base_url_over_latest_snapshot(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(
        "HEALTHARCHIVE_REPLAY_BASE_URL", "https://replay.healtharchive.ca/"
    )
    client = _init_test_app(tmp_path, monkeypatch)

    with get_session() as session:
        hc = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="Health Canada",
            enabled=True,
        )
        session.add(hc)
        session.flush()

        job = ArchiveJob(
            source_id=hc.id,
            name="job-1",
            output_dir="/srv/healtharchive/jobs/imports/job-1",
            status="indexed",
        )
        session.add(job)
        session.flush()

        entry_ts = datetime(2025, 4, 1, 12, 0, tzinfo=timezone.utc)
        latest_ts = datetime(2025, 4, 1, 12, 5, tzinfo=timezone.utc)

        entry_snapshot = Snapshot(
            job_id=job.id,
            source_id=hc.id,
            url="https://www.canada.ca/en/health-canada.html",
            normalized_url_group="https://www.canada.ca/en/health-canada.html",
            capture_timestamp=entry_ts,
            mime_type="text/html",
            status_code=200,
            title="Health Canada",
            snippet="HC home",
            language="en",
            warc_path="/warcs/hc-home.warc.gz",
            warc_record_id="hc-home",
        )

        latest_snapshot = Snapshot(
            job_id=job.id,
            source_id=hc.id,
            url="https://canada.demdex.net/dest5.html?d_nsid=0",
            normalized_url_group="https://canada.demdex.net/dest5.html",
            capture_timestamp=latest_ts,
            mime_type="text/html",
            status_code=200,
            title="Adobe DTM destination",
            snippet="Analytics placeholder page",
            language="en",
            warc_path="/warcs/hc-analytics.warc.gz",
            warc_record_id="hc-analytics",
        )

        session.add_all([entry_snapshot, latest_snapshot])
        session.flush()

        entry_id = entry_snapshot.id
        latest_id = latest_snapshot.id
        job_id = job.id

    resp = client.get("/api/sources")
    assert resp.status_code == 200
    sources = resp.json()
    hc_payload = next(s for s in sources if s["sourceCode"] == "hc")

    assert hc_payload["latestRecordId"] == latest_id
    assert hc_payload["entryRecordId"] == entry_id
    assert (
        hc_payload["entryBrowseUrl"]
        == f"https://replay.healtharchive.ca/job-{job_id}/20250401120000/https://www.canada.ca/en/health-canada.html"
    )


def test_sources_entry_record_falls_back_to_first_party_host_when_base_url_missing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_REPLAY_BASE_URL", "https://replay.healtharchive.ca")
    client = _init_test_app(tmp_path, monkeypatch)

    with get_session() as session:
        cihr = Source(
            code="cihr",
            name="Canadian Institutes of Health Research",
            base_url="https://cihr-irsc.gc.ca/",
            description="CIHR",
            enabled=True,
        )
        session.add(cihr)
        session.flush()

        job = ArchiveJob(
            source_id=cihr.id,
            name="legacy-cihr-2025-04",
            output_dir="/srv/healtharchive/jobs/imports/legacy-cihr-2025-04",
            status="indexed",
        )
        session.add(job)
        session.flush()

        entry_ts = datetime(2025, 4, 10, 12, 34, 56, tzinfo=timezone.utc)
        latest_ts = datetime(2025, 4, 10, 12, 40, 0, tzinfo=timezone.utc)

        entry_snapshot = Snapshot(
            job_id=job.id,
            source_id=cihr.id,
            url="https://cihr-irsc.gc.ca/e/193.html",
            normalized_url_group="https://cihr-irsc.gc.ca/e/193.html",
            capture_timestamp=entry_ts,
            mime_type="text/html",
            status_code=200,
            title="CIHR",
            snippet="CIHR home",
            language="en",
            warc_path="/warcs/cihr-home.warc.gz",
            warc_record_id="cihr-home",
        )

        # A newer third-party capture should not be treated as the source entry.
        latest_snapshot = Snapshot(
            job_id=job.id,
            source_id=cihr.id,
            url="https://cihr.tt.omtrdc.net/rest/v1/delivery?client=cihr",
            normalized_url_group="https://cihr.tt.omtrdc.net/rest/v1/delivery",
            capture_timestamp=latest_ts,
            mime_type="text/html",
            status_code=200,
            title="Analytics",
            snippet="Third-party analytics endpoint",
            language="en",
            warc_path="/warcs/cihr-analytics.warc.gz",
            warc_record_id="cihr-analytics",
        )

        session.add_all([entry_snapshot, latest_snapshot])
        session.flush()

        entry_id = entry_snapshot.id
        latest_id = latest_snapshot.id
        job_id = job.id

    resp = client.get("/api/sources")
    assert resp.status_code == 200
    sources = resp.json()
    cihr_payload = next(s for s in sources if s["sourceCode"] == "cihr")

    assert cihr_payload["latestRecordId"] == latest_id
    assert cihr_payload["entryRecordId"] == entry_id
    assert (
        cihr_payload["entryBrowseUrl"]
        == f"https://replay.healtharchive.ca/job-{job_id}/20250410123456/https://cihr-irsc.gc.ca/e/193.html"
    )


def test_sources_endpoint_excludes_test_source(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    with get_session() as session:
        test_source = Source(code="test", name="Test Source", enabled=True)
        hc = Source(code="hc", name="Health Canada", enabled=True)
        session.add_all([test_source, hc])
        session.flush()

        ts = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
        session.add_all(
            [
                Snapshot(
                    job_id=None,
                    source_id=test_source.id,
                    url="https://example.test/test",
                    normalized_url_group="https://example.test/test",
                    capture_timestamp=ts,
                    mime_type="text/html",
                    status_code=200,
                    title="Synthetic test snapshot",
                    snippet="Test snapshot",
                    language="en",
                    warc_path="/warcs/test.warc.gz",
                    warc_record_id="test-1",
                ),
                Snapshot(
                    job_id=None,
                    source_id=hc.id,
                    url="https://www.canada.ca/en/health-canada.html",
                    normalized_url_group="https://www.canada.ca/en/health-canada.html",
                    capture_timestamp=ts,
                    mime_type="text/html",
                    status_code=200,
                    title="HC Home",
                    snippet="Health Canada home",
                    language="en",
                    warc_path="/warcs/hc1.warc.gz",
                    warc_record_id="hc-1",
                ),
            ]
        )

    resp = client.get("/api/sources")
    assert resp.status_code == 200
    sources = resp.json()
    codes = {s["sourceCode"] for s in sources}
    assert "test" not in codes
    assert "hc" in codes


def test_sources_advertises_preview_url_when_cached_preview_exists(
    tmp_path, monkeypatch
) -> None:
    preview_dir = tmp_path / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HEALTHARCHIVE_REPLAY_PREVIEW_DIR", str(preview_dir))

    client = _init_test_app(tmp_path, monkeypatch)

    with get_session() as session:
        hc = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="Health Canada",
            enabled=True,
        )
        session.add(hc)
        session.flush()

        job = ArchiveJob(
            source_id=hc.id,
            name="legacy-hc-2025-04",
            output_dir="/srv/healtharchive/jobs/imports/legacy-hc-2025-04",
            status="indexed",
        )
        session.add(job)
        session.flush()

        ts = datetime(2025, 4, 18, 12, 0, tzinfo=timezone.utc)
        snapshot = Snapshot(
            job_id=job.id,
            source_id=hc.id,
            url="https://www.canada.ca/en/health-canada.html",
            normalized_url_group="https://www.canada.ca/en/health-canada.html",
            capture_timestamp=ts,
            mime_type="text/html",
            status_code=200,
            title="Health Canada",
            snippet="HC home",
            language="en",
            warc_path="/warcs/hc-home.warc.gz",
            warc_record_id="hc-home",
        )
        session.add(snapshot)
        session.flush()

        preview_path = preview_dir / f"source-hc-job-{job.id}.png"
        preview_path.write_bytes(b"\x89PNG\r\n\x1a\n")

        job_id = job.id

    resp = client.get("/api/sources")
    assert resp.status_code == 200
    sources = resp.json()
    hc_payload = next(s for s in sources if s["sourceCode"] == "hc")

    assert (
        hc_payload["entryPreviewUrl"]
        == f"/api/sources/hc/preview?jobId={job_id}"
    )


def test_source_preview_endpoint_serves_cached_image(tmp_path, monkeypatch) -> None:
    preview_dir = tmp_path / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HEALTHARCHIVE_REPLAY_PREVIEW_DIR", str(preview_dir))

    client = _init_test_app(tmp_path, monkeypatch)

    with get_session() as session:
        hc = Source(code="hc", name="Health Canada", enabled=True)
        session.add(hc)
        session.flush()

    file_path = preview_dir / "source-hc-job-1.png"
    file_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    resp = client.get("/api/sources/hc/preview", params={"jobId": 1})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.headers.get("cache-control") is not None


def test_source_editions_endpoint_lists_indexed_jobs_sorted_by_recency(
    tmp_path, monkeypatch
) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    with get_session() as session:
        hc = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="Health Canada",
            enabled=True,
        )
        session.add(hc)
        session.flush()

        older_job = ArchiveJob(
            source_id=hc.id,
            name="legacy-hc-older",
            output_dir="/srv/healtharchive/jobs/imports/legacy-hc-older",
            status="indexed",
        )
        newer_job = ArchiveJob(
            source_id=hc.id,
            name="legacy-hc-newer",
            output_dir="/srv/healtharchive/jobs/imports/legacy-hc-newer",
            status="indexed",
        )
        not_indexed_job = ArchiveJob(
            source_id=hc.id,
            name="legacy-hc-not-indexed",
            output_dir="/srv/healtharchive/jobs/imports/legacy-hc-not-indexed",
            status="completed",
        )
        session.add_all([older_job, newer_job, not_indexed_job])
        session.flush()

        older_job_id = older_job.id
        newer_job_id = newer_job.id

        older_ts = datetime(2025, 1, 10, 12, 0, tzinfo=timezone.utc)
        newer_ts = datetime(2025, 2, 10, 12, 0, tzinfo=timezone.utc)
        ignored_ts = datetime(2025, 3, 10, 12, 0, tzinfo=timezone.utc)

        session.add_all(
            [
                Snapshot(
                    job_id=older_job.id,
                    source_id=hc.id,
                    url="https://www.canada.ca/en/health-canada.html",
                    normalized_url_group="https://www.canada.ca/en/health-canada.html",
                    capture_timestamp=older_ts,
                    mime_type="text/html",
                    status_code=200,
                    title="HC Home (older)",
                    snippet="HC home older",
                    language="en",
                    warc_path="/warcs/hc-older.warc.gz",
                    warc_record_id="hc-older",
                ),
                Snapshot(
                    job_id=newer_job.id,
                    source_id=hc.id,
                    url="https://www.canada.ca/en/health-canada.html",
                    normalized_url_group="https://www.canada.ca/en/health-canada.html",
                    capture_timestamp=newer_ts,
                    mime_type="text/html",
                    status_code=200,
                    title="HC Home (newer)",
                    snippet="HC home newer",
                    language="en",
                    warc_path="/warcs/hc-newer.warc.gz",
                    warc_record_id="hc-newer",
                ),
                Snapshot(
                    job_id=not_indexed_job.id,
                    source_id=hc.id,
                    url="https://www.canada.ca/en/health-canada.html",
                    normalized_url_group="https://www.canada.ca/en/health-canada.html",
                    capture_timestamp=ignored_ts,
                    mime_type="text/html",
                    status_code=200,
                    title="HC Home (ignored)",
                    snippet="HC home ignored",
                    language="en",
                    warc_path="/warcs/hc-ignored.warc.gz",
                    warc_record_id="hc-ignored",
                ),
            ]
        )

    resp = client.get("/api/sources/hc/editions")
    assert resp.status_code == 200
    editions = resp.json()

    assert [e["jobId"] for e in editions] == [newer_job_id, older_job_id]
    assert editions[0]["jobName"] == "legacy-hc-newer"
    assert editions[0]["recordCount"] == 1
    assert editions[0]["firstCapture"] == "2025-02-10"
    assert editions[0]["lastCapture"] == "2025-02-10"


def test_source_editions_endpoint_returns_404_for_missing_source(
    tmp_path, monkeypatch
) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    resp = client.get("/api/sources/does-not-exist/editions")
    assert resp.status_code == 404


def test_stats_endpoint_with_no_data(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    resp = client.get("/api/stats")
    assert resp.status_code == 200
    body = resp.json()

    assert body["snapshotsTotal"] == 0
    assert body["pagesTotal"] == 0
    assert body["sourcesTotal"] == 0
    assert body["latestCaptureDate"] is None
    assert body["latestCaptureAgeDays"] is None

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
    assert body["latestCaptureAgeDays"] == max(
        0,
        (
            datetime.now(timezone.utc).date()
            - datetime(2025, 2, 1, tzinfo=timezone.utc).date()
        ).days,
    )
