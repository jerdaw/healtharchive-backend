from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Snapshot, SnapshotChange, Source


def _init_test_app(tmp_path: Path, monkeypatch) -> TestClient:
    db_path = tmp_path / "api_exports.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    from ha_backend.api import app

    return TestClient(app)


def _seed_export_data() -> dict[str, int]:
    with get_session() as session:
        source = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="Health Canada",
            enabled=True,
        )
        session.add(source)
        session.flush()

        job = ArchiveJob(
            source_id=source.id,
            name="hc-20250101",
            output_dir="/tmp/jobs/hc-20250101",
            status="indexed",
        )
        session.add(job)
        session.flush()

        ts1 = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 4, 1, 12, 0, tzinfo=timezone.utc)

        snap_a = Snapshot(
            job_id=job.id,
            source_id=source.id,
            url="https://www.canada.ca/en/health-canada/covid19.html",
            normalized_url_group="https://www.canada.ca/en/health-canada/covid19.html",
            capture_timestamp=ts1,
            mime_type="text/html",
            status_code=200,
            title="COVID-19 guidance",
            snippet="Guidance for COVID-19.",
            language="en",
            warc_path="/warcs/hc-covid-a.warc.gz",
            warc_record_id="hc-covid-a",
        )
        snap_b = Snapshot(
            job_id=job.id,
            source_id=source.id,
            url="https://www.canada.ca/en/health-canada/covid19.html",
            normalized_url_group="https://www.canada.ca/en/health-canada/covid19.html",
            capture_timestamp=ts2,
            mime_type="text/html",
            status_code=200,
            title="COVID-19 guidance (updated)",
            snippet="Updated guidance for COVID-19.",
            language="en",
            warc_path="/warcs/hc-covid-b.warc.gz",
            warc_record_id="hc-covid-b",
        )
        session.add_all([snap_a, snap_b])
        session.flush()

        change = SnapshotChange(
            source_id=source.id,
            normalized_url_group=snap_b.normalized_url_group,
            from_snapshot_id=snap_a.id,
            to_snapshot_id=snap_b.id,
            from_job_id=job.id,
            to_job_id=job.id,
            from_capture_timestamp=ts1,
            to_capture_timestamp=ts2,
            change_type="updated",
            summary="1 sections changed; 1 added; 0 removed",
            diff_format="html",
            diff_html="<div>diff</div>",
            added_sections=1,
            removed_sections=0,
            changed_sections=1,
            added_lines=3,
            removed_lines=1,
            change_ratio=0.4,
            high_noise=False,
            computed_by="test",
        )
        session.add(change)
        session.flush()

        return {"snapshot_id": snap_b.id, "change_id": change.id}


def test_exports_manifest(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    resp = client.get("/api/exports")
    assert resp.status_code == 200
    data = resp.json()

    assert data["enabled"] is True
    assert "jsonl" in data["formats"]
    assert data["snapshots"]["path"] == "/api/exports/snapshots"
    assert data["changes"]["path"] == "/api/exports/changes"


def test_exports_manifest_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_EXPORTS_ENABLED", "0")
    client = _init_test_app(tmp_path, monkeypatch)

    resp = client.get("/api/exports")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False


def test_snapshot_exports_jsonl(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_export_data()

    resp = client.get(
        "/api/exports/snapshots",
        params={"format": "jsonl", "compressed": "false", "limit": 5},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert "content-encoding" not in resp.headers

    lines = [line for line in resp.text.strip().splitlines() if line.strip()]
    assert len(lines) >= 1
    row = json.loads(lines[0])

    assert "snapshot_id" in row
    assert "snapshot_url" in row
    assert row["snapshot_url"].startswith("https://healtharchive.ca/snapshot/")


def test_snapshot_exports_csv(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_export_data()

    resp = client.get(
        "/api/exports/snapshots",
        params={"format": "csv", "compressed": "false", "limit": 5},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")

    reader = csv.DictReader(resp.text.splitlines())
    rows = list(reader)
    assert rows
    assert "snapshot_id" in rows[0]
    assert "snapshot_url" in rows[0]


def test_change_exports_jsonl(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_export_data()

    resp = client.get(
        "/api/exports/changes",
        params={"format": "jsonl", "compressed": "false", "limit": 5},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")

    lines = [line for line in resp.text.strip().splitlines() if line.strip()]
    assert len(lines) >= 1
    row = json.loads(lines[0])

    assert "change_id" in row
    assert "compare_url" in row
    assert row["compare_url"].startswith("https://healtharchive.ca/compare?")


def test_exports_invalid_format(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_export_data()

    resp = client.get("/api/exports/snapshots", params={"format": "xlsx"})
    assert resp.status_code == 422


def test_snapshot_exports_head_returns_download_headers(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_export_data()

    resp = client.head(
        "/api/exports/snapshots",
        params={"format": "jsonl", "compressed": "false", "limit": 1},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert "content-encoding" not in resp.headers
    assert "attachment" in resp.headers.get("content-disposition", "").lower()
    assert resp.headers.get("content-disposition", "").endswith('.jsonl"')


def test_change_exports_head_returns_download_headers(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_export_data()

    resp = client.head(
        "/api/exports/changes",
        params={"format": "csv", "compressed": "true", "limit": 1},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers.get("content-encoding") == "gzip"
    assert "attachment" in resp.headers.get("content-disposition", "").lower()
    assert resp.headers.get("content-disposition", "").endswith('.csv.gz"')
