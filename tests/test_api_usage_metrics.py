from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import UsageMetric
from ha_backend.usage_metrics import (
    EVENT_CHANGES_LIST,
    EVENT_EXPORTS_DOWNLOAD_SNAPSHOTS,
)


def _init_test_app(tmp_path: Path, monkeypatch) -> TestClient:
    db_path = tmp_path / "api_usage_metrics.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

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


def test_usage_metrics_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_USAGE_METRICS_ENABLED", "0")
    client = _init_test_app(tmp_path, monkeypatch)

    resp = client.get("/api/usage")
    assert resp.status_code == 200
    body = resp.json()

    assert body["enabled"] is False
    assert body["totals"]["reportSubmissions"] == 0


def test_usage_metrics_counts_reports(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_USAGE_METRICS_ENABLED", "1")
    client = _init_test_app(tmp_path, monkeypatch)

    payload = {
        "category": "general_feedback",
        "description": "Reporting a general issue for usage metrics test.",
    }

    resp = client.post("/api/reports", json=payload)
    assert resp.status_code == 201

    metrics = client.get("/api/usage")
    assert metrics.status_code == 200
    body = metrics.json()

    assert body["enabled"] is True
    assert body["totals"]["reportSubmissions"] == 1


def test_usage_metrics_records_private_events_without_leaking(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_USAGE_METRICS_ENABLED", "1")
    client = _init_test_app(tmp_path, monkeypatch)

    resp = client.get("/api/exports/snapshots?format=jsonl&compressed=false&limit=1")
    assert resp.status_code == 200

    resp = client.get("/api/changes")
    assert resp.status_code == 200

    today = datetime.now(timezone.utc).date()
    with get_session() as session:
        rows = session.query(UsageMetric).filter(UsageMetric.metric_date == today).all()
        counts = {row.event: int(row.count or 0) for row in rows}

    assert counts.get(EVENT_EXPORTS_DOWNLOAD_SNAPSHOTS) == 1
    assert counts.get(EVENT_CHANGES_LIST) == 1

    public_usage = client.get("/api/usage")
    assert public_usage.status_code == 200
    body = public_usage.json()
    assert body["totals"]["searchRequests"] == 0
    assert body["totals"]["snapshotDetailViews"] == 0
    assert body["totals"]["rawSnapshotViews"] == 0
    assert body["totals"]["reportSubmissions"] == 0
