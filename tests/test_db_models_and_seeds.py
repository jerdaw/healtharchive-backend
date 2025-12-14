from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Snapshot, Source, Topic
from ha_backend.seeds import seed_sources


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    """
    Point the ORM at a throwaway SQLite database and create all tables.
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    # Reset cached engine/session so we pick up the new URL.
    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_seed_sources_idempotent(tmp_path, monkeypatch) -> None:
    """
    seed_sources should insert hc/phac/cihr once and be idempotent on repeated calls.
    """
    _init_test_db(tmp_path, monkeypatch)

    with get_session() as session:
        created_first = seed_sources(session)
        created_second = seed_sources(session)

        codes = {code for (code,) in session.query(Source.code).all()}

    assert created_first == 3
    assert created_second == 0
    assert {"hc", "phac", "cihr"}.issubset(codes)


def test_model_roundtrip_relationships(tmp_path, monkeypatch) -> None:
    """
    Basic round-trip test for Source, ArchiveJob, Snapshot, Topic relationships.
    """
    _init_test_db(tmp_path, monkeypatch)

    captured_at = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)

    with get_session() as session:
        source = Source(
            code="test-source",
            name="Test Source",
            base_url="https://example.org",
            description="Test description",
            enabled=True,
        )
        session.add(source)
        session.flush()

        job = ArchiveJob(
            source=source,
            name="test-job",
            output_dir="/tmp/test-job",
            status="queued",
        )
        session.add(job)
        session.flush()

        snapshot = Snapshot(
            job=job,
            source=source,
            url="https://example.org/page",
            normalized_url_group="https://example.org/page",
            capture_timestamp=captured_at,
            mime_type="text/html",
            status_code=200,
            title="Example Page",
            snippet="Example snippet",
            language="en",
            warc_path="/warcs/test.warc.gz",
            warc_record_id="record-1",
        )

        topic = Topic(slug="example-topic", label="Example Topic")
        snapshot.topics.append(topic)

        session.add_all([snapshot, topic])

    # New session to exercise relationship loading.
    with get_session() as session:
        loaded_source = session.query(Source).filter_by(code="test-source").one()
        assert loaded_source.jobs
        assert loaded_source.snapshots

        loaded_job = loaded_source.jobs[0]
        assert loaded_job.name == "test-job"
        assert loaded_job.source is loaded_source
        assert loaded_job.snapshots

        loaded_snapshot = loaded_job.snapshots[0]
        assert loaded_snapshot.url == "https://example.org/page"
        # SQLite stores timezone-aware datetimes as naive; normalise before comparing.
        assert (
            loaded_snapshot.capture_timestamp.replace(tzinfo=timezone.utc)
            == captured_at
        )
        assert loaded_snapshot.source is loaded_source
        assert loaded_snapshot.topics
        assert loaded_snapshot.topics[0].slug == "example-topic"
