from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine


def _init_test_app(tmp_path: Path, monkeypatch) -> TestClient:
    db_path = tmp_path / "api_usage_metrics.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    from ha_backend.api import app

    return TestClient(app)


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
