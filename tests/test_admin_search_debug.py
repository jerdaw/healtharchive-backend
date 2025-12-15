from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine


def _init_test_app(tmp_path: Path, monkeypatch) -> TestClient:
    db_path = tmp_path / "admin_search_debug.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    from ha_backend.api import app

    return TestClient(app)


def test_admin_search_debug_endpoint_shape(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    # With an empty DB, endpoint should still succeed and return stable metadata.
    resp = client.get("/api/admin/search-debug", params={"q": "covid", "ranking": "v2"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["dialect"] == "sqlite"
    assert body["rankingVersion"] == "v2"
    assert body["view"] in {"snapshots", "pages"}
    assert body["sort"] in {"relevance", "newest"}
    assert isinstance(body["results"], list)

