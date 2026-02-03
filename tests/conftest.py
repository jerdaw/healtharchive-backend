from __future__ import annotations

import argparse
import gzip
import io
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Generator

import pytest
from sqlalchemy.orm import Session, sessionmaker
from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

from archive_tool.state import CrawlState
from ha_backend.db import Base, get_engine
from ha_backend.models import ArchiveJob, Snapshot, Source


@pytest.fixture(autouse=True)
def _isolate_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Make tests deterministic even when run in a production-like shell.

    Some deployments export env vars (e.g. HEALTHARCHIVE_ENV=production,
    HEALTHARCHIVE_ADMIN_TOKEN, HEALTHARCHIVE_REPLAY_BASE_URL) that legitimately
    change API/CLI behavior. If those leak into pytest runs, tests can fail
    depending on the host environment.
    """
    # Force a non-production env so admin endpoints aren't "fail closed" when
    # HEALTHARCHIVE_ADMIN_TOKEN is intentionally unset in tests.
    monkeypatch.setenv("HEALTHARCHIVE_ENV", "test")

    # Clear deployment-sensitive toggles unless an individual test sets them.
    monkeypatch.delenv("HEALTHARCHIVE_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("HEALTHARCHIVE_REPLAY_BASE_URL", raising=False)
    monkeypatch.delenv("HA_SEARCH_RANKING_VERSION", raising=False)

    # Avoid cross-test/process contention on job locks when running tests in parallel.
    monkeypatch.setenv(
        "HEALTHARCHIVE_JOB_LOCK_DIR", f"/tmp/healtharchive-job-locks-tests-{os.getpid()}"
    )


@pytest.fixture(name="db_session")
def fixture_db_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Session, None, None]:
    """
    Creates a fresh SQLite database for each test and returns a SQLAlchemy session.
    """
    # Use a file-based SQLite DB per test to ensure isolation and compatibility with standard connection handling
    db_path = tmp_path / "test.db"
    db_url = f"sqlite:///{db_path}"

    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", db_url)

    # We need to recreate the engine because the env var changed
    from ha_backend import db

    db._engine = None
    db._SessionLocal = None

    engine = get_engine()
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()

    yield session

    session.close()
    # Explicitly dispose engine to release file locks on Windows/some setups
    engine.dispose()


@pytest.fixture(name="html_factory")
def fixture_html_factory() -> Callable[..., str]:
    """
    Returns a function that generates specific HTML structures for testing diffs.
    """

    def _create_html(
        title: str = "Test Page",
        headings: list[str] | None = None,
        content: str = "<p>Some content</p>",
        banner_text: str | None = None,
        lang: str = "en",
    ) -> str:
        headings_html = ""
        if headings:
            for i, h in enumerate(headings):
                level = min(i + 1, 6)
                headings_html += f"<h{level}>{h}</h{level}>\n"

        banner_html = ""
        if banner_text:
            # Typical structure for an archived banner
            banner_html = f"""
            <div id="archived-banner" class="transparency-banner">
                <p>{banner_text}</p>
            </div>
            """

        return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
    <title>{title}</title>
</head>
<body>
    {banner_html}
    <main>
        {headings_html}
        <div class="content">
            {content}
        </div>
    </main>
</body>
</html>"""

    return _create_html


@pytest.fixture(name="snapshot_factory")
def fixture_snapshot_factory(db_session: Session, tmp_path: Path) -> Callable[..., Snapshot]:
    """
    Factory to create Source, Job, and Snapshot records readily.
    """

    def _create_snapshot(
        url: str = "https://example.com/page",
        timestamp: datetime | None = None,
        title: str = "Example Page",
        content_hash: str = "filesha1",
        job_status: str = "completed",
        warc_record_id: str | None = None,
        mime_type: str = "text/html",
        status_code: int = 200,
    ) -> Snapshot:
        # Create or reuse a source
        source = db_session.query(Source).filter_by(code="test_src").first()
        if not source:
            source = Source(
                code="test_src", name="Test Source", base_url="https://example.com", enabled=True
            )
            db_session.add(source)
            db_session.flush()

        # Create a job
        job = ArchiveJob(
            source_id=source.id,
            name=f"job_{datetime.now().timestamp()}",
            status=job_status,
            output_dir=str(tmp_path / f"job_{datetime.now().timestamp()}"),
        )
        db_session.add(job)
        db_session.flush()

        # Create snapshot
        ts = timestamp or datetime.utcnow()
        snap = Snapshot(
            job_id=job.id,
            source_id=source.id,
            url=url,
            normalized_url_group=url,  # simplify for now
            capture_timestamp=ts,
            mime_type=mime_type,
            status_code=status_code,
            title=title,
            warc_record_id=warc_record_id or f"urn:uuid:{os.urandom(16).hex()}",
            content_hash=content_hash,
            warc_path=str(tmp_path / "dummy.warc.gz"),
            # Add other necessary fields with defaults
            language="en",
        )
        db_session.add(snap)
        db_session.commit()
        return snap

    return _create_snapshot


@pytest.fixture(name="mock_warc_generator")
def fixture_mock_warc_generator(tmp_path: Path) -> Callable[..., Path]:
    """
    Creates valid-enough WARC files for testing using warcio.
    """

    def _create_warc(
        filename: str,
        records: list[tuple[str, bytes]],  # list of (uri, content_bytes)
    ) -> Path:
        warc_path = tmp_path / filename

        with gzip.open(warc_path, "wb") as gz:
            writer = WARCWriter(gz, gzip=True)
            for uri, content in records:
                http_headers = StatusAndHeaders(
                    "200 OK",
                    [("Content-Type", "text/html; charset=utf-8")],
                    protocol="HTTP/1.1",
                )
                record = writer.create_warc_record(
                    uri,
                    "response",
                    payload=io.BytesIO(content),
                    http_headers=http_headers,
                )
                writer.write_record(record)

        return warc_path

    return _create_warc


@pytest.fixture(name="crawl_state_factory")
def fixture_crawl_state_factory(tmp_path: Path) -> Callable[..., CrawlState]:
    """
    Returns a function that creates a CrawlState instance for testing.
    """

    def _create_state(initial_workers: int = 5, output_dir: Path | None = None) -> CrawlState:
        od = output_dir or tmp_path / "crawl_output"
        od.mkdir(parents=True, exist_ok=True)
        return CrawlState(od, initial_workers)

    return _create_state


@pytest.fixture(name="mock_args_factory")
def fixture_mock_args_factory() -> Callable[..., argparse.Namespace]:
    """
    Returns a function that creates a mock argparse.Namespace for testing.
    """

    def _create_args(**kwargs) -> argparse.Namespace:
        return argparse.Namespace(**kwargs)

    return _create_args
