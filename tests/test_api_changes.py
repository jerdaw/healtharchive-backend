from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Snapshot, SnapshotChange, Source


def _init_test_app(tmp_path: Path, monkeypatch) -> TestClient:
    db_path = tmp_path / "api_changes.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    from ha_backend.api import app

    return TestClient(app)


def _seed_change_data() -> dict[str, int]:
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
            source_id=hc.id,
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
            source_id=hc.id,
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
            source_id=hc.id,
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

        return {"source_id": hc.id, "job_id": job.id, "snap_a": snap_a.id, "snap_b": snap_b.id}


def test_changes_feed_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_CHANGE_TRACKING_ENABLED", "0")
    client = _init_test_app(tmp_path, monkeypatch)

    resp = client.get("/api/changes")
    assert resp.status_code == 200
    body = resp.json()

    assert body["enabled"] is False
    assert body["total"] == 0


def test_changes_feed_and_compare(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_CHANGE_TRACKING_ENABLED", "1")
    client = _init_test_app(tmp_path, monkeypatch)
    ids = _seed_change_data()

    resp = client.get(f"/api/changes?source=hc&jobId={ids['job_id']}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["enabled"] is True
    assert body["total"] == 1
    assert body["results"][0]["changeType"] == "updated"

    compare = client.get(
        f"/api/changes/compare?toSnapshotId={ids['snap_b']}&fromSnapshotId={ids['snap_a']}"
    )
    assert compare.status_code == 200
    compare_body = compare.json()
    assert compare_body["diffHtml"] == "<div>diff</div>"

    timeline = client.get(f"/api/snapshots/{ids['snap_b']}/timeline")
    assert timeline.status_code == 200
    timeline_body = timeline.json()
    assert len(timeline_body["snapshots"]) == 2
    assert timeline_body["snapshots"][1]["compareFromSnapshotId"] == ids["snap_a"]
