"""
Tests for the `ha-backend patch-job-config` CLI command.
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

import pytest

from ha_backend import cli as cli_module
from ha_backend import db as db_module
from ha_backend.archive_contract import ArchiveJobConfig
from ha_backend.db import Base, get_engine, get_session
from ha_backend.job_registry import create_job_for_source
from ha_backend.models import ArchiveJob
from ha_backend.seeds import seed_sources


def _init_test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Point the ORM at a throwaway SQLite database and create all tables.
    """
    db_path = tmp_path / "cli_patch_job.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


class TestPatchJobConfigDryRun:
    """Tests for dry-run mode (default behavior)."""

    def test_dry_run_shows_diff_without_changing_db(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dry-run shows the planned changes but does not modify the database."""
        _init_test_db(tmp_path, monkeypatch)

        archive_root = tmp_path / "jobs"
        monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

        with get_session() as session:
            seed_sources(session)

        with get_session() as session:
            job_row = create_job_for_source("hc", session=session)
            job_id = job_row.id
            original_config = dict(job_row.config or {})

        parser = cli_module.build_parser()
        # Note: job registry defaults have skip_final_build=True and initial_workers=2,
        # so we change initial_workers to 4 and overwrite (defaults to False) to True
        args = parser.parse_args(
            [
                "patch-job-config",
                "--id",
                str(job_id),
                "--set-tool-option",
                "overwrite=true",
                "--set-tool-option",
                "initial_workers=4",
            ]
        )

        stdout = StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = stdout
            args.func(args)
        finally:
            sys.stdout = old_stdout

        out = stdout.getvalue()

        # Should show the diff (overwrite: false -> true, initial_workers: 2 -> 4)
        assert "overwrite:" in out
        assert "initial_workers:" in out
        assert "[DRY RUN]" in out

        # Database should NOT be modified
        with get_session() as session:
            stored = session.get(ArchiveJob, job_id)
            assert stored is not None
            assert stored.config == original_config

    def test_dry_run_no_changes_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dry-run with a value that matches the current shows 'no changes'."""
        _init_test_db(tmp_path, monkeypatch)

        archive_root = tmp_path / "jobs"
        monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

        with get_session() as session:
            seed_sources(session)

        with get_session() as session:
            job_row = create_job_for_source("hc", session=session)
            job_id = job_row.id
            # Get the current initial_workers value
            cfg = ArchiveJobConfig.from_dict(job_row.config or {})
            current_workers = cfg.tool_options.initial_workers

        parser = cli_module.build_parser()
        args = parser.parse_args(
            [
                "patch-job-config",
                "--id",
                str(job_id),
                "--set-tool-option",
                f"initial_workers={current_workers}",
            ]
        )

        stdout = StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = stdout
            args.func(args)
        finally:
            sys.stdout = old_stdout

        out = stdout.getvalue()
        assert "(no changes)" in out


class TestPatchJobConfigApply:
    """Tests for apply mode (--apply flag)."""

    def test_apply_updates_config_in_db(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Apply mode updates the job config in the database."""
        _init_test_db(tmp_path, monkeypatch)

        archive_root = tmp_path / "jobs"
        monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

        with get_session() as session:
            seed_sources(session)

        with get_session() as session:
            job_row = create_job_for_source("hc", session=session)
            job_id = job_row.id

        parser = cli_module.build_parser()
        args = parser.parse_args(
            [
                "patch-job-config",
                "--id",
                str(job_id),
                "--set-tool-option",
                "skip_final_build=true",
                "--set-tool-option",
                "docker_shm_size=2g",
                "--apply",
            ]
        )

        stdout = StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = stdout
            args.func(args)
        finally:
            sys.stdout = old_stdout

        out = stdout.getvalue()
        assert "[APPLIED]" in out

        # Verify the changes were persisted
        with get_session() as session:
            stored = session.get(ArchiveJob, job_id)
            assert stored is not None
            cfg = ArchiveJobConfig.from_dict(stored.config or {})
            assert cfg.tool_options.skip_final_build is True
            assert cfg.tool_options.docker_shm_size == "2g"


class TestPatchJobConfigValidation:
    """Tests for validation and error handling."""

    def test_rejects_invalid_status_running(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Patching a job with status 'running' should fail."""
        _init_test_db(tmp_path, monkeypatch)

        archive_root = tmp_path / "jobs"
        monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

        with get_session() as session:
            seed_sources(session)

        with get_session() as session:
            job_row = create_job_for_source("hc", session=session)
            job_id = job_row.id
            # Manually set status to 'running'
            job_row.status = "running"
            session.commit()

        parser = cli_module.build_parser()
        args = parser.parse_args(
            [
                "patch-job-config",
                "--id",
                str(job_id),
                "--set-tool-option",
                "skip_final_build=true",
            ]
        )

        stderr = StringIO()
        old_stderr = sys.stderr
        try:
            sys.stderr = stderr
            with pytest.raises(SystemExit) as exc_info:
                args.func(args)
            assert exc_info.value.code == 1
        finally:
            sys.stderr = old_stderr

        err = stderr.getvalue()
        assert "Cannot patch job in status 'running'" in err

    def test_rejects_invalid_status_indexed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Patching a job with status 'indexed' should fail."""
        _init_test_db(tmp_path, monkeypatch)

        archive_root = tmp_path / "jobs"
        monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

        with get_session() as session:
            seed_sources(session)

        with get_session() as session:
            job_row = create_job_for_source("hc", session=session)
            job_id = job_row.id
            job_row.status = "indexed"
            session.commit()

        parser = cli_module.build_parser()
        args = parser.parse_args(
            [
                "patch-job-config",
                "--id",
                str(job_id),
                "--set-tool-option",
                "skip_final_build=true",
            ]
        )

        stderr = StringIO()
        old_stderr = sys.stderr
        try:
            sys.stderr = stderr
            with pytest.raises(SystemExit) as exc_info:
                args.func(args)
            assert exc_info.value.code == 1
        finally:
            sys.stderr = old_stderr

        err = stderr.getvalue()
        assert "Cannot patch job in status 'indexed'" in err

    def test_allows_patching_failed_job(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Patching a job with status 'failed' should succeed."""
        _init_test_db(tmp_path, monkeypatch)

        archive_root = tmp_path / "jobs"
        monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

        with get_session() as session:
            seed_sources(session)

        with get_session() as session:
            job_row = create_job_for_source("hc", session=session)
            job_id = job_row.id
            job_row.status = "failed"
            session.commit()

        parser = cli_module.build_parser()
        args = parser.parse_args(
            [
                "patch-job-config",
                "--id",
                str(job_id),
                "--set-tool-option",
                "initial_workers=2",
                "--apply",
            ]
        )

        stdout = StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = stdout
            args.func(args)
        finally:
            sys.stdout = old_stdout

        out = stdout.getvalue()
        assert "[APPLIED]" in out

    def test_rejects_unknown_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Using an unknown key should fail with an error message."""
        _init_test_db(tmp_path, monkeypatch)

        archive_root = tmp_path / "jobs"
        monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

        with get_session() as session:
            seed_sources(session)

        with get_session() as session:
            job_row = create_job_for_source("hc", session=session)
            job_id = job_row.id

        parser = cli_module.build_parser()
        args = parser.parse_args(
            [
                "patch-job-config",
                "--id",
                str(job_id),
                "--set-tool-option",
                "nonexistent_key=value",
            ]
        )

        stderr = StringIO()
        old_stderr = sys.stderr
        try:
            sys.stderr = stderr
            with pytest.raises(SystemExit) as exc_info:
                args.func(args)
            assert exc_info.value.code == 1
        finally:
            sys.stderr = old_stderr

        err = stderr.getvalue()
        assert "Unknown tool_option key: 'nonexistent_key'" in err

    def test_rejects_invalid_format(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Using invalid format (no =) should fail with an error message."""
        _init_test_db(tmp_path, monkeypatch)

        archive_root = tmp_path / "jobs"
        monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

        with get_session() as session:
            seed_sources(session)

        with get_session() as session:
            job_row = create_job_for_source("hc", session=session)
            job_id = job_row.id

        parser = cli_module.build_parser()
        args = parser.parse_args(
            [
                "patch-job-config",
                "--id",
                str(job_id),
                "--set-tool-option",
                "skip_final_build",  # Missing =value
            ]
        )

        stderr = StringIO()
        old_stderr = sys.stderr
        try:
            sys.stderr = stderr
            with pytest.raises(SystemExit) as exc_info:
                args.func(args)
            assert exc_info.value.code == 1
        finally:
            sys.stderr = old_stderr

        err = stderr.getvalue()
        assert "Invalid format" in err
        assert "Expected KEY=VALUE" in err

    def test_validation_error_on_adaptive_workers_without_monitoring(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Enabling adaptive_workers without monitoring should fail validation."""
        _init_test_db(tmp_path, monkeypatch)

        archive_root = tmp_path / "jobs"
        monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

        with get_session() as session:
            seed_sources(session)

        with get_session() as session:
            job_row = create_job_for_source("hc", session=session)
            job_id = job_row.id
            # The job registry defaults have enable_monitoring=True and
            # enable_adaptive_workers=True. We need to manually disable both
            # to test that enabling adaptive_workers without monitoring fails.
            cfg = ArchiveJobConfig.from_dict(job_row.config or {})
            cfg.tool_options.enable_monitoring = False
            cfg.tool_options.enable_adaptive_workers = False
            cfg.tool_options.enable_adaptive_restart = False
            job_row.config = cfg.to_dict()
            session.commit()

        parser = cli_module.build_parser()
        args = parser.parse_args(
            [
                "patch-job-config",
                "--id",
                str(job_id),
                "--set-tool-option",
                "enable_adaptive_workers=true",
                # Note: enable_monitoring is still False
            ]
        )

        stderr = StringIO()
        old_stderr = sys.stderr
        try:
            sys.stderr = stderr
            with pytest.raises(SystemExit) as exc_info:
                args.func(args)
            assert exc_info.value.code == 1
        finally:
            sys.stderr = old_stderr

        err = stderr.getvalue()
        assert "Validation failed" in err
        assert "enable_monitoring" in err

    def test_job_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Patching a non-existent job should fail."""
        _init_test_db(tmp_path, monkeypatch)

        archive_root = tmp_path / "jobs"
        monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

        parser = cli_module.build_parser()
        args = parser.parse_args(
            [
                "patch-job-config",
                "--id",
                "99999",
                "--set-tool-option",
                "skip_final_build=true",
            ]
        )

        stderr = StringIO()
        old_stderr = sys.stderr
        try:
            sys.stderr = stderr
            with pytest.raises(SystemExit) as exc_info:
                args.func(args)
            assert exc_info.value.code == 1
        finally:
            sys.stderr = old_stderr

        err = stderr.getvalue()
        assert "Job 99999 not found" in err

    def test_no_patches_provided(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Running without any --set-tool-option should fail."""
        _init_test_db(tmp_path, monkeypatch)

        archive_root = tmp_path / "jobs"
        monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

        with get_session() as session:
            seed_sources(session)

        with get_session() as session:
            job_row = create_job_for_source("hc", session=session)
            job_id = job_row.id

        parser = cli_module.build_parser()
        args = parser.parse_args(
            [
                "patch-job-config",
                "--id",
                str(job_id),
            ]
        )

        stderr = StringIO()
        old_stderr = sys.stderr
        try:
            sys.stderr = stderr
            with pytest.raises(SystemExit) as exc_info:
                args.func(args)
            assert exc_info.value.code == 1
        finally:
            sys.stderr = old_stderr

        err = stderr.getvalue()
        assert "At least one --set-tool-option" in err


class TestPatchJobConfigTypeCoercion:
    """Tests for type coercion of option values."""

    def test_boolean_true_coercion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """'true' string should be coerced to boolean True."""
        _init_test_db(tmp_path, monkeypatch)

        archive_root = tmp_path / "jobs"
        monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

        with get_session() as session:
            seed_sources(session)

        with get_session() as session:
            job_row = create_job_for_source("hc", session=session)
            job_id = job_row.id

        parser = cli_module.build_parser()
        args = parser.parse_args(
            [
                "patch-job-config",
                "--id",
                str(job_id),
                "--set-tool-option",
                "skip_final_build=TRUE",  # uppercase
                "--apply",
            ]
        )

        stdout = StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = stdout
            args.func(args)
        finally:
            sys.stdout = old_stdout

        with get_session() as session:
            stored = session.get(ArchiveJob, job_id)
            assert stored is not None
            cfg = ArchiveJobConfig.from_dict(stored.config or {})
            assert cfg.tool_options.skip_final_build is True

    def test_boolean_false_coercion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """'false' string should be coerced to boolean False."""
        _init_test_db(tmp_path, monkeypatch)

        archive_root = tmp_path / "jobs"
        monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

        with get_session() as session:
            seed_sources(session)

        with get_session() as session:
            job_row = create_job_for_source("hc", session=session)
            job_id = job_row.id
            # First set it to true
            cfg = ArchiveJobConfig.from_dict(job_row.config or {})
            cfg.tool_options.skip_final_build = True
            job_row.config = cfg.to_dict()
            session.commit()

        parser = cli_module.build_parser()
        args = parser.parse_args(
            [
                "patch-job-config",
                "--id",
                str(job_id),
                "--set-tool-option",
                "skip_final_build=False",  # mixed case
                "--apply",
            ]
        )

        stdout = StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = stdout
            args.func(args)
        finally:
            sys.stdout = old_stdout

        with get_session() as session:
            stored = session.get(ArchiveJob, job_id)
            assert stored is not None
            cfg = ArchiveJobConfig.from_dict(stored.config or {})
            assert cfg.tool_options.skip_final_build is False

    def test_integer_coercion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Numeric strings should be coerced to integers."""
        _init_test_db(tmp_path, monkeypatch)

        archive_root = tmp_path / "jobs"
        monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

        with get_session() as session:
            seed_sources(session)

        with get_session() as session:
            job_row = create_job_for_source("hc", session=session)
            job_id = job_row.id

        parser = cli_module.build_parser()
        args = parser.parse_args(
            [
                "patch-job-config",
                "--id",
                str(job_id),
                "--set-tool-option",
                "initial_workers=8",
                "--set-tool-option",
                "stall_timeout_minutes=45",
                "--apply",
            ]
        )

        stdout = StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = stdout
            args.func(args)
        finally:
            sys.stdout = old_stdout

        with get_session() as session:
            stored = session.get(ArchiveJob, job_id)
            assert stored is not None
            cfg = ArchiveJobConfig.from_dict(stored.config or {})
            assert cfg.tool_options.initial_workers == 8
            assert cfg.tool_options.stall_timeout_minutes == 45

    def test_string_coercion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-numeric, non-boolean strings should remain strings."""
        _init_test_db(tmp_path, monkeypatch)

        archive_root = tmp_path / "jobs"
        monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

        with get_session() as session:
            seed_sources(session)

        with get_session() as session:
            job_row = create_job_for_source("hc", session=session)
            job_id = job_row.id

        parser = cli_module.build_parser()
        args = parser.parse_args(
            [
                "patch-job-config",
                "--id",
                str(job_id),
                "--set-tool-option",
                "docker_shm_size=4g",
                "--set-tool-option",
                "log_level=DEBUG",
                "--apply",
            ]
        )

        stdout = StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = stdout
            args.func(args)
        finally:
            sys.stdout = old_stdout

        with get_session() as session:
            stored = session.get(ArchiveJob, job_id)
            assert stored is not None
            cfg = ArchiveJobConfig.from_dict(stored.config or {})
            assert cfg.tool_options.docker_shm_size == "4g"
            assert cfg.tool_options.log_level == "DEBUG"
