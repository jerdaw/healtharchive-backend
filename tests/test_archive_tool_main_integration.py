"""
Integration tests for archive_tool/main.py orchestration.

These tests verify:
- Run mode detection (existing ZIM handling, overwrite behavior)
- Early exit conditions (missing Docker, Docker start failures)
- Dry-run mode behavior

Note: Full stage loop tests with mocked containers are complex due to
threading in the log drain. Tests that exercise the complete stage loop
are marked with pytest.mark.slow and may timeout in CI.

Docker operations are mocked to allow testing without real containers.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import archive_tool.docker_runner as docker_runner_mod
import archive_tool.main as archive_main
import archive_tool.utils as utils_mod
from archive_tool.state import CrawlState


@pytest.fixture
def mock_docker_check(monkeypatch):
    """Ensure Docker check always passes."""
    monkeypatch.setattr(utils_mod, "check_docker", lambda: True)


@pytest.fixture
def mock_container_stop(monkeypatch):
    """Mock container stop operations."""
    mock = MagicMock()
    monkeypatch.setattr(docker_runner_mod, "stop_docker_container", mock)
    # Reset global state
    docker_runner_mod.current_container_id = None
    docker_runner_mod.current_docker_process = None
    return mock


@pytest.fixture
def clean_stop_event():
    """Ensure stop_event is cleared before and after tests."""
    archive_main.stop_event.clear()
    yield
    archive_main.stop_event.clear()


class TestExistingZIMHandling:
    """Tests for behavior when a ZIM file already exists."""

    def test_without_overwrite_exits_when_zim_exists(
        self,
        tmp_path: Path,
        monkeypatch,
        mock_docker_check,
        mock_container_stop,
        clean_stop_event,
    ):
        """Without --skip-final-build or --overwrite, existing ZIM causes exit."""
        out_dir = tmp_path / "existing_zim"
        out_dir.mkdir()

        # Create an existing ZIM file
        zim_file = out_dir / "test-job.zim"
        zim_file.write_bytes(b"fake zim")

        # Mock container start (shouldn't be called)
        container_start_called = {"value": False}

        def fake_start(*args, **kwargs):
            container_start_called["value"] = True
            return MagicMock(), "container-id"

        monkeypatch.setattr(docker_runner_mod, "start_docker_container", fake_start)

        argv = [
            "archive-tool",
            "--seeds",
            "https://example.org",
            "--name",
            "test-job",
            "--output-dir",
            str(out_dir),
        ]
        monkeypatch.setattr(sys, "argv", argv)

        # Should exit with error
        with pytest.raises(SystemExit) as exc_info:
            archive_main.main()

        assert exc_info.value.code != 0
        # Container should NOT have been started
        assert container_start_called["value"] is False


class TestDockerStartFailures:
    """Tests for Docker container start failure scenarios."""

    def test_docker_start_exception_exits_with_error(
        self,
        tmp_path: Path,
        monkeypatch,
        mock_docker_check,
        mock_container_stop,
        clean_stop_event,
    ):
        """Docker start raising exception should cause exit."""
        out_dir = tmp_path / "docker_fail"
        out_dir.mkdir()

        def fake_start(*args, **kwargs):
            raise RuntimeError("Docker start failed")

        monkeypatch.setattr(docker_runner_mod, "start_docker_container", fake_start)

        argv = [
            "archive-tool",
            "--seeds",
            "https://example.org",
            "--name",
            "test-job",
            "--output-dir",
            str(out_dir),
            "--skip-final-build",
        ]
        monkeypatch.setattr(sys, "argv", argv)

        # Should exit with error
        with pytest.raises(SystemExit) as exc_info:
            archive_main.main()

        assert exc_info.value.code != 0

    def test_docker_start_returns_none_exits_with_error(
        self,
        tmp_path: Path,
        monkeypatch,
        mock_docker_check,
        mock_container_stop,
        clean_stop_event,
    ):
        """Docker start returning None should cause exit."""
        out_dir = tmp_path / "docker_none"
        out_dir.mkdir()

        def fake_start(*args, **kwargs):
            return None, None

        monkeypatch.setattr(docker_runner_mod, "start_docker_container", fake_start)

        argv = [
            "archive-tool",
            "--seeds",
            "https://example.org",
            "--name",
            "test-job",
            "--output-dir",
            str(out_dir),
            "--skip-final-build",
        ]
        monkeypatch.setattr(sys, "argv", argv)

        # Should exit with error
        with pytest.raises(SystemExit) as exc_info:
            archive_main.main()

        assert exc_info.value.code != 0


class TestDockerCheck:
    """Tests for Docker availability check."""

    def test_docker_unavailable_exits_with_error(
        self,
        tmp_path: Path,
        monkeypatch,
        clean_stop_event,
    ):
        """Docker check failing should cause immediate exit."""
        out_dir = tmp_path / "no_docker"
        out_dir.mkdir()

        # Mock Docker check to fail
        monkeypatch.setattr(utils_mod, "check_docker", lambda: False)

        argv = [
            "archive-tool",
            "--seeds",
            "https://example.org",
            "--name",
            "test-job",
            "--output-dir",
            str(out_dir),
        ]
        monkeypatch.setattr(sys, "argv", argv)

        # Should exit with error
        with pytest.raises(SystemExit) as exc_info:
            archive_main.main()

        assert exc_info.value.code != 0


class TestDryRunMode:
    """Tests for --dry-run behavior (already tested in test_archive_tool_dry_run.py)."""

    def test_dry_run_skips_docker_start(
        self,
        tmp_path: Path,
        monkeypatch,
        mock_docker_check,
        clean_stop_event,
    ):
        """Dry-run mode should not start any Docker containers."""
        out_dir = tmp_path / "dry_run"
        out_dir.mkdir()

        container_started = {"value": False}

        def fake_start(*args, **kwargs):
            container_started["value"] = True
            return MagicMock(), "container-id"

        monkeypatch.setattr(docker_runner_mod, "start_docker_container", fake_start)

        argv = [
            "archive-tool",
            "--seeds",
            "https://example.org",
            "--name",
            "test-job",
            "--output-dir",
            str(out_dir),
            "--dry-run",
        ]
        monkeypatch.setattr(sys, "argv", argv)

        # Should complete without error
        archive_main.main()

        # Container should NOT have been started
        assert container_started["value"] is False


class TestOutputDirectoryHandling:
    """Tests for output directory validation."""

    def test_output_dir_created_if_missing(
        self,
        tmp_path: Path,
        monkeypatch,
        mock_docker_check,
        clean_stop_event,
    ):
        """Output directory should be created if it doesn't exist (dry-run)."""
        out_dir = tmp_path / "new_output_dir"
        # Don't create it - let main() create it

        argv = [
            "archive-tool",
            "--seeds",
            "https://example.org",
            "--name",
            "test-job",
            "--output-dir",
            str(out_dir),
            "--dry-run",
        ]
        monkeypatch.setattr(sys, "argv", argv)

        archive_main.main()

        # Directory should have been created
        assert out_dir.exists()


class TestCrawlStateInitialization:
    """Tests for CrawlState behavior during startup."""

    def test_state_file_path(
        self,
        tmp_path: Path,
    ):
        """CrawlState should use correct state file path."""
        out_dir = tmp_path / "state_test"
        out_dir.mkdir()

        state = CrawlState(out_dir, initial_workers=2)
        state.save_persistent_state()

        state_file = out_dir / ".archive_state.json"
        assert state_file.exists()

    def test_state_preserves_adaptation_counts(
        self,
        tmp_path: Path,
    ):
        """CrawlState should preserve adaptation counts across loads."""
        out_dir = tmp_path / "persist_test"
        out_dir.mkdir()

        # Create state with some adaptation history
        state1 = CrawlState(out_dir, initial_workers=4)
        state1.worker_reductions_done = 2
        state1.vpn_rotations_done = 1
        state1.current_workers = 2
        state1.save_persistent_state()

        # Load state again
        state2 = CrawlState(out_dir, initial_workers=10)  # Different initial value

        # Should preserve the saved values
        assert state2.worker_reductions_done == 2
        assert state2.vpn_rotations_done == 1
        assert state2.current_workers == 2

    def test_state_tracks_temp_dirs(
        self,
        tmp_path: Path,
    ):
        """CrawlState should track temp directories."""
        out_dir = tmp_path / "temp_dir_test"
        out_dir.mkdir()

        temp_dir = out_dir / ".tmp12345"
        temp_dir.mkdir()

        state = CrawlState(out_dir, initial_workers=2)
        state.add_temp_dir(temp_dir)
        state.save_persistent_state()

        # Reload
        state2 = CrawlState(out_dir, initial_workers=2)
        temp_dirs = state2.get_temp_dir_paths()

        assert temp_dir in temp_dirs


class TestTempDirDiscovery:
    """Tests for temp directory discovery functionality."""

    def test_discover_temp_dirs_finds_tmp_dirs(
        self,
        tmp_path: Path,
    ):
        """discover_temp_dirs should find .tmp* directories."""
        out_dir = tmp_path / "discovery_test"
        out_dir.mkdir()

        # Create some temp dirs
        temp_dir1 = out_dir / ".tmp12345"
        temp_dir1.mkdir()
        temp_dir2 = out_dir / ".tmpABCDE"
        temp_dir2.mkdir()

        # Create a non-temp dir that shouldn't be found
        other_dir = out_dir / "collections"
        other_dir.mkdir()

        discovered = utils_mod.discover_temp_dirs(out_dir)

        assert len(discovered) == 2
        assert temp_dir1 in discovered
        assert temp_dir2 in discovered
        assert other_dir not in discovered

    def test_discover_temp_dirs_returns_empty_for_clean_dir(
        self,
        tmp_path: Path,
    ):
        """discover_temp_dirs should return empty list for directory with no temp dirs."""
        out_dir = tmp_path / "clean_dir"
        out_dir.mkdir()

        discovered = utils_mod.discover_temp_dirs(out_dir)

        assert discovered == []


class TestWorkerCountParsing:
    """Tests for --workers passthrough arg parsing."""

    def test_passthrough_workers_with_space(
        self,
        tmp_path: Path,
        monkeypatch,
        mock_docker_check,
        clean_stop_event,
        capsys,
    ):
        """Passthrough --workers N should be recognized."""
        out_dir = tmp_path / "workers_space"
        out_dir.mkdir()

        argv = [
            "archive-tool",
            "--seeds",
            "https://example.org",
            "--name",
            "test-job",
            "--output-dir",
            str(out_dir),
            "--initial-workers",
            "2",
            "--dry-run",
            "--",
            "--workers",
            "5",
        ]
        monkeypatch.setattr(sys, "argv", argv)

        archive_main.main()

        # Check logs for worker count (dry-run prints effective workers)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        # Should see "Effective initial worker count set to: 5"
        assert "5" in combined

    def test_passthrough_workers_with_equals(
        self,
        tmp_path: Path,
        monkeypatch,
        mock_docker_check,
        clean_stop_event,
        capsys,
    ):
        """Passthrough --workers=N should be recognized."""
        out_dir = tmp_path / "workers_equals"
        out_dir.mkdir()

        argv = [
            "archive-tool",
            "--seeds",
            "https://example.org",
            "--name",
            "test-job",
            "--output-dir",
            str(out_dir),
            "--initial-workers",
            "2",
            "--dry-run",
            "--",
            "--workers=7",
        ]
        monkeypatch.setattr(sys, "argv", argv)

        archive_main.main()

        # Check logs for worker count
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "7" in combined
