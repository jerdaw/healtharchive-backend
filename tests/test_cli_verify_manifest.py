"""
Tests for ha-backend verify-warc-manifest CLI command.

Tests manifest verification including:
- Valid manifest with all files present
- Missing WARC files
- Size mismatches
- Hash mismatches (with --level hash)
- JSON output format
- Jobs without manifest (pre-consolidation)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ha_backend import cli as cli_module
from ha_backend import db as db_module
from ha_backend.archive_storage import verify_warc_manifest
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Source


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """Set up a test database."""
    db_path = tmp_path / "verify_manifest.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    yield

    db_module._engine = None
    db_module._SessionLocal = None


def _create_warc(path: Path, content: bytes = b"warc content") -> tuple[int, str]:
    """Create a WARC file and return its size and SHA256 hash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    sha256 = hashlib.sha256(content).hexdigest()
    return len(content), sha256


def _create_manifest(warcs_dir: Path, entries: list[dict]) -> Path:
    """Create a manifest.json file."""
    manifest_path = warcs_dir / "manifest.json"
    manifest_data = {
        "version": 1,
        "output_dir": str(warcs_dir.parent),
        "warcs_dir": str(warcs_dir),
        "created_at": "2026-01-29T00:00:00+00:00",
        "updated_at": "2026-01-29T00:00:00+00:00",
        "entries": entries,
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
    return manifest_path


class TestVerifyWarcManifestFunction:
    """Tests for the verify_warc_manifest function in archive_storage."""

    def test_valid_manifest_all_files_present(self, tmp_path: Path):
        """Verify a valid manifest with all files present and matching."""
        output_dir = tmp_path / "job-out"
        warcs_dir = output_dir / "warcs"
        warcs_dir.mkdir(parents=True)

        # Create WARCs
        size1, hash1 = _create_warc(warcs_dir / "warc-000001.warc.gz", b"content1")
        size2, hash2 = _create_warc(warcs_dir / "warc-000002.warc.gz", b"content2")

        # Create manifest
        _create_manifest(
            warcs_dir,
            [
                {
                    "source_path": "/tmp/src1.warc.gz",
                    "stable_name": "warc-000001.warc.gz",
                    "size_bytes": size1,
                    "sha256": hash1,
                },
                {
                    "source_path": "/tmp/src2.warc.gz",
                    "stable_name": "warc-000002.warc.gz",
                    "size_bytes": size2,
                    "sha256": hash2,
                },
            ],
        )

        result = verify_warc_manifest(output_dir, check_size=True, check_hash=False)

        assert result.valid is True
        assert result.entries_total == 2
        assert result.entries_verified == 2
        assert result.missing == []
        assert result.size_mismatches == []
        assert result.hash_mismatches == []
        assert result.orphaned == []
        assert result.errors == []

    def test_missing_manifest(self, tmp_path: Path):
        """Verify behavior when manifest doesn't exist."""
        output_dir = tmp_path / "job-out"
        output_dir.mkdir(parents=True)

        result = verify_warc_manifest(output_dir)

        assert result.valid is False
        assert result.entries_total == 0
        assert len(result.errors) == 1
        assert "Manifest not found" in result.errors[0]

    def test_empty_manifest(self, tmp_path: Path):
        """Verify behavior when manifest is empty/invalid."""
        output_dir = tmp_path / "job-out"
        warcs_dir = output_dir / "warcs"
        warcs_dir.mkdir(parents=True)

        manifest_path = warcs_dir / "manifest.json"
        manifest_path.write_text("{}", encoding="utf-8")

        result = verify_warc_manifest(output_dir)

        # Empty manifest should be valid but with 0 entries
        assert result.entries_total == 0
        assert result.entries_verified == 0

    def test_missing_warc_file(self, tmp_path: Path):
        """Verify detection of missing WARC files."""
        output_dir = tmp_path / "job-out"
        warcs_dir = output_dir / "warcs"
        warcs_dir.mkdir(parents=True)

        # Create one WARC but not the other
        size1, hash1 = _create_warc(warcs_dir / "warc-000001.warc.gz", b"content1")

        # Manifest references two files
        _create_manifest(
            warcs_dir,
            [
                {
                    "source_path": "/tmp/src1.warc.gz",
                    "stable_name": "warc-000001.warc.gz",
                    "size_bytes": size1,
                    "sha256": hash1,
                },
                {
                    "source_path": "/tmp/src2.warc.gz",
                    "stable_name": "warc-000002.warc.gz",
                    "size_bytes": 100,
                    "sha256": "abc123",
                },
            ],
        )

        result = verify_warc_manifest(output_dir)

        assert result.valid is False
        assert result.entries_total == 2
        assert result.entries_verified == 1
        assert result.missing == ["warc-000002.warc.gz"]

    def test_size_mismatch(self, tmp_path: Path):
        """Verify detection of size mismatches."""
        output_dir = tmp_path / "job-out"
        warcs_dir = output_dir / "warcs"
        warcs_dir.mkdir(parents=True)

        # Create WARC with specific content
        _create_warc(warcs_dir / "warc-000001.warc.gz", b"actual content")

        # Manifest claims a different size
        _create_manifest(
            warcs_dir,
            [
                {
                    "source_path": "/tmp/src1.warc.gz",
                    "stable_name": "warc-000001.warc.gz",
                    "size_bytes": 9999,  # Wrong size
                    "sha256": "doesntmatter",
                },
            ],
        )

        result = verify_warc_manifest(output_dir, check_size=True, check_hash=False)

        assert result.valid is False
        assert len(result.size_mismatches) == 1
        name, expected, actual = result.size_mismatches[0]
        assert name == "warc-000001.warc.gz"
        assert expected == 9999
        assert actual == len(b"actual content")

    def test_hash_mismatch(self, tmp_path: Path):
        """Verify detection of hash mismatches (with --level hash)."""
        output_dir = tmp_path / "job-out"
        warcs_dir = output_dir / "warcs"
        warcs_dir.mkdir(parents=True)

        # Create WARC with specific content
        content = b"actual content"
        actual_size = len(content)
        _create_warc(warcs_dir / "warc-000001.warc.gz", content)

        # Manifest claims correct size but wrong hash
        _create_manifest(
            warcs_dir,
            [
                {
                    "source_path": "/tmp/src1.warc.gz",
                    "stable_name": "warc-000001.warc.gz",
                    "size_bytes": actual_size,
                    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
                },
            ],
        )

        result = verify_warc_manifest(output_dir, check_size=True, check_hash=True)

        assert result.valid is False
        assert len(result.hash_mismatches) == 1
        name, expected, actual = result.hash_mismatches[0]
        assert name == "warc-000001.warc.gz"
        assert expected == "0" * 64
        assert actual == hashlib.sha256(content).hexdigest()

    def test_orphaned_warc(self, tmp_path: Path):
        """Verify detection of orphaned WARCs not in manifest."""
        output_dir = tmp_path / "job-out"
        warcs_dir = output_dir / "warcs"
        warcs_dir.mkdir(parents=True)

        # Create WARCs
        size1, hash1 = _create_warc(warcs_dir / "warc-000001.warc.gz", b"content1")
        _create_warc(warcs_dir / "warc-000002.warc.gz", b"content2")  # Orphan

        # Manifest only references one file
        _create_manifest(
            warcs_dir,
            [
                {
                    "source_path": "/tmp/src1.warc.gz",
                    "stable_name": "warc-000001.warc.gz",
                    "size_bytes": size1,
                    "sha256": hash1,
                },
            ],
        )

        result = verify_warc_manifest(output_dir)

        # Orphaned files don't fail verification, just a warning
        assert result.valid is True
        assert result.orphaned == ["warc-000002.warc.gz"]

    def test_presence_level_skips_size_check(self, tmp_path: Path):
        """Verify that presence level doesn't check sizes."""
        output_dir = tmp_path / "job-out"
        warcs_dir = output_dir / "warcs"
        warcs_dir.mkdir(parents=True)

        # Create WARC with different size than manifest claims
        _create_warc(warcs_dir / "warc-000001.warc.gz", b"actual content")

        _create_manifest(
            warcs_dir,
            [
                {
                    "source_path": "/tmp/src1.warc.gz",
                    "stable_name": "warc-000001.warc.gz",
                    "size_bytes": 9999,  # Wrong size
                    "sha256": "wrong",
                },
            ],
        )

        # With check_size=False, should pass
        result = verify_warc_manifest(output_dir, check_size=False, check_hash=False)

        assert result.valid is True
        assert result.size_mismatches == []


class TestVerifyWarcManifestCLI:
    """Tests for the verify-warc-manifest CLI command."""

    def test_cli_valid_manifest(self, test_db, tmp_path: Path, monkeypatch, capsys):
        """CLI verifies a valid manifest successfully."""
        output_dir = tmp_path / "job-out"
        warcs_dir = output_dir / "warcs"
        warcs_dir.mkdir(parents=True)

        size1, hash1 = _create_warc(warcs_dir / "warc-000001.warc.gz", b"content1")
        _create_manifest(
            warcs_dir,
            [
                {
                    "source_path": "/tmp/src.warc.gz",
                    "stable_name": "warc-000001.warc.gz",
                    "size_bytes": size1,
                    "sha256": hash1,
                },
            ],
        )

        with get_session() as session:
            source = Source(code="hc", name="Health Canada", enabled=True)
            session.add(source)
            session.flush()

            job = ArchiveJob(
                source_id=source.id,
                name="test-job",
                output_dir=str(output_dir),
                status="indexed",
            )
            session.add(job)
            session.flush()
            job_id = job.id

        parser = cli_module.build_parser()
        args = parser.parse_args(["verify-warc-manifest", "--id", str(job_id)])

        # Should not raise
        args.func(args)

        captured = capsys.readouterr()
        assert "Status: OK" in captured.out

    def test_cli_missing_file_fails(self, test_db, tmp_path: Path, monkeypatch, capsys):
        """CLI exits with error when files are missing."""
        output_dir = tmp_path / "job-out"
        warcs_dir = output_dir / "warcs"
        warcs_dir.mkdir(parents=True)

        # Manifest references file that doesn't exist
        _create_manifest(
            warcs_dir,
            [
                {
                    "source_path": "/tmp/src.warc.gz",
                    "stable_name": "warc-000001.warc.gz",
                    "size_bytes": 100,
                    "sha256": "abc",
                },
            ],
        )

        with get_session() as session:
            source = Source(code="hc", name="Health Canada", enabled=True)
            session.add(source)
            session.flush()

            job = ArchiveJob(
                source_id=source.id,
                name="test-job",
                output_dir=str(output_dir),
                status="indexed",
            )
            session.add(job)
            session.flush()
            job_id = job.id

        parser = cli_module.build_parser()
        args = parser.parse_args(["verify-warc-manifest", "--id", str(job_id)])

        with pytest.raises(SystemExit) as exc_info:
            args.func(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "MISSING: warc-000001.warc.gz" in captured.out
        assert "Status: FAILED" in captured.out

    def test_cli_json_output(self, test_db, tmp_path: Path, monkeypatch, capsys):
        """CLI produces valid JSON output with --json flag."""
        output_dir = tmp_path / "job-out"
        warcs_dir = output_dir / "warcs"
        warcs_dir.mkdir(parents=True)

        size1, hash1 = _create_warc(warcs_dir / "warc-000001.warc.gz", b"content1")
        _create_manifest(
            warcs_dir,
            [
                {
                    "source_path": "/tmp/src.warc.gz",
                    "stable_name": "warc-000001.warc.gz",
                    "size_bytes": size1,
                    "sha256": hash1,
                },
            ],
        )

        with get_session() as session:
            source = Source(code="hc", name="Health Canada", enabled=True)
            session.add(source)
            session.flush()

            job = ArchiveJob(
                source_id=source.id,
                name="test-job",
                output_dir=str(output_dir),
                status="indexed",
            )
            session.add(job)
            session.flush()
            job_id = job.id

        parser = cli_module.build_parser()
        args = parser.parse_args(["verify-warc-manifest", "--id", str(job_id), "--json"])

        args.func(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["valid"] is True
        assert output["entries_total"] == 1
        assert output["entries_verified"] == 1
        assert output["level"] == "size"

    def test_cli_no_manifest(self, test_db, tmp_path: Path, monkeypatch, capsys):
        """CLI handles job without manifest (pre-consolidation)."""
        output_dir = tmp_path / "job-out"
        output_dir.mkdir(parents=True)
        # No warcs/ directory or manifest

        with get_session() as session:
            source = Source(code="hc", name="Health Canada", enabled=True)
            session.add(source)
            session.flush()

            job = ArchiveJob(
                source_id=source.id,
                name="test-job",
                output_dir=str(output_dir),
                status="running",  # Pre-consolidation
            )
            session.add(job)
            session.flush()
            job_id = job.id

        parser = cli_module.build_parser()
        args = parser.parse_args(["verify-warc-manifest", "--id", str(job_id)])

        with pytest.raises(SystemExit) as exc_info:
            args.func(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Manifest not found" in captured.out or "FAILED" in captured.out

    def test_cli_level_hash(self, test_db, tmp_path: Path, monkeypatch, capsys):
        """CLI respects --level hash and detects hash mismatches."""
        output_dir = tmp_path / "job-out"
        warcs_dir = output_dir / "warcs"
        warcs_dir.mkdir(parents=True)

        content = b"actual content"
        actual_size = len(content)
        _create_warc(warcs_dir / "warc-000001.warc.gz", content)

        # Correct size but wrong hash
        _create_manifest(
            warcs_dir,
            [
                {
                    "source_path": "/tmp/src.warc.gz",
                    "stable_name": "warc-000001.warc.gz",
                    "size_bytes": actual_size,
                    "sha256": "0" * 64,
                },
            ],
        )

        with get_session() as session:
            source = Source(code="hc", name="Health Canada", enabled=True)
            session.add(source)
            session.flush()

            job = ArchiveJob(
                source_id=source.id,
                name="test-job",
                output_dir=str(output_dir),
                status="indexed",
            )
            session.add(job)
            session.flush()
            job_id = job.id

        parser = cli_module.build_parser()
        args = parser.parse_args(["verify-warc-manifest", "--id", str(job_id), "--level", "hash"])

        with pytest.raises(SystemExit) as exc_info:
            args.func(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "HASH_MISMATCH" in captured.out

    def test_cli_job_not_found(self, test_db, tmp_path: Path, monkeypatch, capsys):
        """CLI handles non-existent job ID."""
        parser = cli_module.build_parser()
        args = parser.parse_args(["verify-warc-manifest", "--id", "99999"])

        with pytest.raises(SystemExit) as exc_info:
            args.func(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ERROR: Job 99999 not found" in captured.err
