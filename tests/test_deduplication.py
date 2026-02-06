"""
Tests for same-day snapshot deduplication.

Verifies the deduplication logic correctly identifies duplicates,
applies deduplication with audit logging, and supports restoration.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ha_backend.db import Base, get_session
from ha_backend.indexing.deduplication import (
    deduplicate_snapshots,
    find_same_day_duplicates,
    restore_deduped_snapshots,
)
from ha_backend.models import Snapshot, SnapshotDeduplication, Source


@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    """Create an in-memory test database with schema."""
    db_url = f"sqlite:///{tmp_path / 'test_dedupe.db'}"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", db_url)

    # Force re-creation of the engine for this test
    import ha_backend.db as db_mod

    db_mod._engine = None

    with get_session() as session:
        Base.metadata.drop_all(session.get_bind())
        Base.metadata.create_all(session.get_bind())

        # Seed a source
        source = Source(code="hc", name="Health Canada")
        session.add(source)
        session.flush()
        yield session


def _make_snapshot(
    session,
    *,
    source_id: int,
    url: str,
    capture_timestamp: datetime,
    content_hash: str | None = None,
    normalized_url_group: str | None = None,
    job_id: int | None = None,
    warc_path: str = "/fake/warc.warc.gz",
) -> Snapshot:
    snap = Snapshot(
        source_id=source_id,
        url=url,
        capture_timestamp=capture_timestamp,
        content_hash=content_hash,
        normalized_url_group=normalized_url_group,
        warc_path=warc_path,
        job_id=job_id,
    )
    session.add(snap)
    session.flush()
    return snap


class TestFindSameDayDuplicates:
    """Tests for find_same_day_duplicates."""

    def test_finds_duplicates_same_day_same_hash(self, db_session):
        """Identifies snapshots with same URL, same day, same hash as duplicates."""
        source_id = db_session.query(Source).first().id

        ts1 = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)

        snap1 = _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts1,
            content_hash="abc123",
            normalized_url_group="example.com/page",
        )
        snap2 = _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts2,
            content_hash="abc123",
            normalized_url_group="example.com/page",
        )

        candidates = find_same_day_duplicates(db_session)

        assert len(candidates) == 1
        assert candidates[0].duplicate_id == snap2.id
        assert candidates[0].canonical_id == snap1.id

    def test_no_duplicates_different_days(self, db_session):
        """Does not identify snapshots on different days as duplicates."""
        source_id = db_session.query(Source).first().id

        ts1 = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 1, 16, 10, 0, tzinfo=timezone.utc)

        _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts1,
            content_hash="abc123",
            normalized_url_group="example.com/page",
        )
        _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts2,
            content_hash="abc123",
            normalized_url_group="example.com/page",
        )

        candidates = find_same_day_duplicates(db_session)
        assert len(candidates) == 0

    def test_no_duplicates_different_hashes(self, db_session):
        """Does not identify snapshots with different content_hash as duplicates."""
        source_id = db_session.query(Source).first().id

        ts1 = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)

        _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts1,
            content_hash="abc123",
            normalized_url_group="example.com/page",
        )
        _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts2,
            content_hash="def456",
            normalized_url_group="example.com/page",
        )

        candidates = find_same_day_duplicates(db_session)
        assert len(candidates) == 0

    def test_ignores_null_content_hash(self, db_session):
        """Skips snapshots with NULL content_hash."""
        source_id = db_session.query(Source).first().id

        ts1 = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)

        _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts1,
            content_hash=None,
            normalized_url_group="example.com/page",
        )
        _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts2,
            content_hash=None,
            normalized_url_group="example.com/page",
        )

        candidates = find_same_day_duplicates(db_session)
        assert len(candidates) == 0

    def test_filters_by_job_id(self, db_session):
        """Filters candidates to a specific job_id."""
        source_id = db_session.query(Source).first().id

        ts1 = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)

        _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts1,
            content_hash="abc123",
            normalized_url_group="example.com/page",
            job_id=1,
        )
        _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts2,
            content_hash="abc123",
            normalized_url_group="example.com/page",
            job_id=1,
        )
        _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/other",
            capture_timestamp=ts1,
            content_hash="xyz789",
            normalized_url_group="example.com/other",
            job_id=2,
        )
        _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/other",
            capture_timestamp=ts2,
            content_hash="xyz789",
            normalized_url_group="example.com/other",
            job_id=2,
        )

        # Should only find duplicates from job 1
        candidates = find_same_day_duplicates(db_session, job_id=1)
        assert len(candidates) == 1
        assert all(c.url == "https://example.com/page" for c in candidates)

    def test_multiple_duplicates_same_group(self, db_session):
        """Finds multiple duplicates when 3+ snapshots exist for same group/day/hash."""
        source_id = db_session.query(Source).first().id
        ts_base = datetime(2026, 1, 15, tzinfo=timezone.utc)

        snap1 = _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts_base.replace(hour=8),
            content_hash="abc123",
            normalized_url_group="example.com/page",
        )
        _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts_base.replace(hour=12),
            content_hash="abc123",
            normalized_url_group="example.com/page",
        )
        _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts_base.replace(hour=16),
            content_hash="abc123",
            normalized_url_group="example.com/page",
        )

        candidates = find_same_day_duplicates(db_session)
        assert len(candidates) == 2
        # All candidates should point to the first snapshot as canonical
        assert all(c.canonical_id == snap1.id for c in candidates)


class TestDeduplicateSnapshots:
    """Tests for deduplicate_snapshots."""

    def test_dry_run_does_not_modify(self, db_session):
        """Dry-run mode returns candidates but does not modify the database."""
        source_id = db_session.query(Source).first().id

        ts1 = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)

        _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts1,
            content_hash="abc123",
            normalized_url_group="example.com/page",
        )
        snap2 = _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts2,
            content_hash="abc123",
            normalized_url_group="example.com/page",
        )

        candidates = find_same_day_duplicates(db_session)
        result = deduplicate_snapshots(db_session, candidates, dry_run=True)

        assert result.applied is False
        assert result.deduped_count == 1

        # Database should not be modified
        db_session.refresh(snap2)
        assert snap2.deduplicated is False

    def test_apply_marks_duplicates(self, db_session):
        """Apply mode sets deduplicated=True and creates audit records."""
        source_id = db_session.query(Source).first().id

        ts1 = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)

        snap1 = _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts1,
            content_hash="abc123",
            normalized_url_group="example.com/page",
        )
        snap2 = _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts2,
            content_hash="abc123",
            normalized_url_group="example.com/page",
        )

        candidates = find_same_day_duplicates(db_session)
        result = deduplicate_snapshots(db_session, candidates, dry_run=False)

        assert result.applied is True
        assert result.deduped_count == 1

        # Verify snapshot is marked
        db_session.refresh(snap2)
        assert snap2.deduplicated is True

        # Verify canonical snapshot is NOT marked
        db_session.refresh(snap1)
        assert snap1.deduplicated is False

        # Verify audit record exists
        audit = (
            db_session.query(SnapshotDeduplication)
            .filter(SnapshotDeduplication.snapshot_id == snap2.id)
            .first()
        )
        assert audit is not None
        assert audit.canonical_snapshot_id == snap1.id
        assert audit.reason == "same_day_same_hash"


class TestRestoreDeduped:
    """Tests for restore_deduped_snapshots."""

    def test_restore_clears_flag_and_audit(self, db_session):
        """Restoring deduped snapshots clears deduplicated flag and audit records."""
        source_id = db_session.query(Source).first().id

        ts1 = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)

        _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts1,
            content_hash="abc123",
            normalized_url_group="example.com/page",
        )
        snap2 = _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts2,
            content_hash="abc123",
            normalized_url_group="example.com/page",
        )

        # First, deduplicate
        candidates = find_same_day_duplicates(db_session)
        deduplicate_snapshots(db_session, candidates, dry_run=False)

        # Verify it's deduplicated
        db_session.refresh(snap2)
        assert snap2.deduplicated is True

        # Now restore
        restored = restore_deduped_snapshots(db_session)

        assert restored == 1

        # Verify flag cleared
        db_session.refresh(snap2)
        assert snap2.deduplicated is False

        # Verify audit records deleted
        audit_count = (
            db_session.query(SnapshotDeduplication)
            .filter(SnapshotDeduplication.snapshot_id == snap2.id)
            .count()
        )
        assert audit_count == 0

    def test_restore_filters_by_job_id(self, db_session):
        """Restore only affects snapshots from the specified job."""
        source_id = db_session.query(Source).first().id

        ts1 = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)

        # Job 1 snapshots
        _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts1,
            content_hash="abc123",
            normalized_url_group="example.com/page",
            job_id=1,
        )
        snap_j1_dup = _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/page",
            capture_timestamp=ts2,
            content_hash="abc123",
            normalized_url_group="example.com/page",
            job_id=1,
        )

        # Job 2 snapshots
        _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/other",
            capture_timestamp=ts1,
            content_hash="xyz789",
            normalized_url_group="example.com/other",
            job_id=2,
        )
        snap_j2_dup = _make_snapshot(
            db_session,
            source_id=source_id,
            url="https://example.com/other",
            capture_timestamp=ts2,
            content_hash="xyz789",
            normalized_url_group="example.com/other",
            job_id=2,
        )

        # Deduplicate all
        candidates = find_same_day_duplicates(db_session)
        deduplicate_snapshots(db_session, candidates, dry_run=False)

        # Restore only job 1
        restored = restore_deduped_snapshots(db_session, job_id=1)
        assert restored == 1

        db_session.refresh(snap_j1_dup)
        assert snap_j1_dup.deduplicated is False

        db_session.refresh(snap_j2_dup)
        assert snap_j2_dup.deduplicated is True
