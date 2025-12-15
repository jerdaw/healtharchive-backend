from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ha_backend import db as db_module
from ha_backend.authority import recompute_page_signals
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import Snapshot, SnapshotOutlink, Source


def _init_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "authority.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_recompute_page_signals_populates_outlinks_and_pagerank(tmp_path, monkeypatch) -> None:
    _init_db(tmp_path, monkeypatch)

    with get_session() as session:
        src = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="Health Canada",
            enabled=True,
        )
        session.add(src)
        session.flush()

        ts = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)

        a = Snapshot(
            job_id=None,
            source_id=src.id,
            url="https://example.org/a",
            normalized_url_group="https://example.org/a",
            capture_timestamp=ts,
            mime_type="text/html",
            status_code=200,
            title="A",
            snippet="A",
            language="en",
            warc_path="/warcs/a.warc.gz",
            warc_record_id="a",
        )
        b = Snapshot(
            job_id=None,
            source_id=src.id,
            url="https://example.org/b",
            normalized_url_group="https://example.org/b",
            capture_timestamp=ts,
            mime_type="text/html",
            status_code=200,
            title="B",
            snippet="B",
            language="en",
            warc_path="/warcs/b.warc.gz",
            warc_record_id="b",
        )
        c = Snapshot(
            job_id=None,
            source_id=src.id,
            url="https://example.org/c",
            normalized_url_group="https://example.org/c",
            capture_timestamp=ts,
            mime_type="text/html",
            status_code=200,
            title="C",
            snippet="C",
            language="en",
            warc_path="/warcs/c.warc.gz",
            warc_record_id="c",
        )
        session.add_all([a, b, c])
        session.flush()

        # Graph:
        # a -> b, c
        # b -> a
        session.add_all(
            [
                SnapshotOutlink(snapshot_id=a.id, to_normalized_url_group="https://example.org/b"),
                SnapshotOutlink(snapshot_id=a.id, to_normalized_url_group="https://example.org/c"),
                SnapshotOutlink(snapshot_id=b.id, to_normalized_url_group="https://example.org/a"),
            ]
        )
        session.flush()

        changed = recompute_page_signals(session, groups=None)
        assert changed > 0

        from ha_backend.models import PageSignal

        rows = {r.normalized_url_group: r for r in session.query(PageSignal).all()}
        assert set(rows) == {
            "https://example.org/a",
            "https://example.org/b",
            "https://example.org/c",
        }

        assert rows["https://example.org/a"].inlink_count == 1
        assert rows["https://example.org/b"].inlink_count == 1
        assert rows["https://example.org/c"].inlink_count == 1

        assert rows["https://example.org/a"].outlink_count == 2
        assert rows["https://example.org/b"].outlink_count == 1
        assert rows["https://example.org/c"].outlink_count == 0

        pr_a = rows["https://example.org/a"].pagerank
        pr_b = rows["https://example.org/b"].pagerank
        pr_c = rows["https://example.org/c"].pagerank
        assert pr_a > 0
        assert pr_b > 0
        assert pr_c > 0
        # A participates in a small cycle (a <-> b) and should outrank the dangling node.
        assert pr_a > pr_b
        assert pr_a > pr_c
