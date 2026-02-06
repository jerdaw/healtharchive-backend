"""
Tests for annual job tiering guardrails.

These tests verify that the worker refuses to start annual crawls
when the output directory is still on the root device after tiering.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ha_backend.worker.main import (
    _get_filesystem_device,
    _is_on_root_device,
    _tier_annual_job_if_needed,
)


class TestFilesystemDeviceDetection:
    """Tests for filesystem device detection helpers."""

    def test_get_filesystem_device_success(self):
        """Successfully gets device from df output."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Filesystem\n/dev/sdb1\n")

            device = _get_filesystem_device(Path("/srv/healtharchive/jobs"))

            assert device == "/dev/sdb1"
            mock_run.assert_called_once()

    def test_get_filesystem_device_root(self):
        """Correctly identifies root device."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Filesystem\n/dev/sda1\n")

            device = _get_filesystem_device(Path("/srv/healtharchive/jobs"))

            assert device == "/dev/sda1"

    def test_get_filesystem_device_command_failure(self):
        """Returns None when df command fails."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("Command failed")

            device = _get_filesystem_device(Path("/nonexistent"))

            assert device is None

    def test_is_on_root_device_true(self):
        """Correctly detects path on root device."""
        with patch("ha_backend.worker.main._get_filesystem_device", return_value="/dev/sda1"):
            assert _is_on_root_device(Path("/srv/healtharchive/jobs")) is True

    def test_is_on_root_device_false(self):
        """Correctly detects path NOT on root device."""
        with patch("ha_backend.worker.main._get_filesystem_device", return_value="/dev/sdb1"):
            assert _is_on_root_device(Path("/srv/healtharchive/storagebox")) is False

    def test_is_on_root_device_unknown_failsafe(self):
        """Assumes root device when detection fails (fail-safe)."""
        with patch("ha_backend.worker.main._get_filesystem_device", return_value=None):
            # Should return True (fail-safe: assume root if unknown)
            assert _is_on_root_device(Path("/unknown")) is True


class TestTierAnnualJobGuardrail:
    """Tests for annual job tiering guardrail logic."""

    def _create_job_mock(self, *, campaign_kind="annual", campaign_year=2025):
        """Create a mock ArchiveJob for testing."""
        job = MagicMock()
        job.id = 42
        job.output_dir = "/srv/healtharchive/jobs/2025_annual_hc"
        job.config = {
            "campaign_kind": campaign_kind,
            "campaign_year": campaign_year,
        }
        return job

    def test_skip_tiering_for_non_annual_job(self):
        """Skips tiering for jobs that are not annual campaigns."""
        job = self._create_job_mock(campaign_kind="monthly")

        # Should return without error (no-op)
        _tier_annual_job_if_needed(job)

    def test_skip_tiering_for_nonexistent_output_dir(self, tmp_path: Path):
        """Skips tiering when output_dir does not exist yet."""
        job = self._create_job_mock()
        job.output_dir = str(tmp_path / "nonexistent")

        # Should return without error (no-op)
        _tier_annual_job_if_needed(job)

    def test_skip_tiering_when_already_tiered(self, tmp_path: Path):
        """Skips tiering when output_dir is already a mountpoint."""
        output_dir = tmp_path / "annual_job"
        output_dir.mkdir()

        job = self._create_job_mock()
        job.output_dir = str(output_dir)

        with patch("ha_backend.worker.main._is_mountpoint", return_value=True):
            # Should return without error (already tiered)
            _tier_annual_job_if_needed(job)

    def test_guardrail_raises_when_tiering_fails(self, tmp_path: Path):
        """Raises RuntimeError when tiering script fails."""
        output_dir = tmp_path / "annual_job"
        output_dir.mkdir()

        job = self._create_job_mock()
        job.output_dir = str(output_dir)

        with patch("ha_backend.worker.main._is_mountpoint", return_value=False):
            with patch("subprocess.run") as mock_run:
                # Simulate tiering script failure
                mock_run.return_value = MagicMock(
                    returncode=1,
                    stderr="Storage Box not mounted",
                )

                with pytest.raises(RuntimeError, match="auto-tiering failed.*Cannot proceed"):
                    _tier_annual_job_if_needed(job)

    def test_guardrail_raises_when_still_on_root_after_tiering(self, tmp_path: Path):
        """Raises RuntimeError when output_dir is still on root device after tiering."""
        output_dir = tmp_path / "annual_job"
        output_dir.mkdir()

        job = self._create_job_mock()
        job.output_dir = str(output_dir)

        with patch("ha_backend.worker.main._is_mountpoint", return_value=False):
            with patch("subprocess.run") as mock_run:
                # Simulate tiering script success
                mock_run.return_value = MagicMock(returncode=0, stderr="")

                # But output_dir is still on root device
                with patch("ha_backend.worker.main._is_on_root_device", return_value=True):
                    with pytest.raises(RuntimeError, match="still on /dev/sda1 after auto-tiering"):
                        _tier_annual_job_if_needed(job)

    def test_successful_tiering_verification(self, tmp_path: Path):
        """Successfully verifies tiering when output_dir is on non-root device."""
        output_dir = tmp_path / "annual_job"
        output_dir.mkdir()

        job = self._create_job_mock()
        job.output_dir = str(output_dir)

        with patch("ha_backend.worker.main._is_mountpoint", return_value=False):
            with patch("subprocess.run") as mock_run:
                # Simulate tiering script success
                mock_run.return_value = MagicMock(returncode=0, stderr="")

                # Output_dir is now on non-root device
                with patch("ha_backend.worker.main._is_on_root_device", return_value=False):
                    # Should complete without error
                    _tier_annual_job_if_needed(job)

    def test_logs_warning_for_missing_campaign_year(self, tmp_path: Path):
        """Logs warning and returns when campaign_year is missing."""
        output_dir = tmp_path / "annual_job"
        output_dir.mkdir()

        job = self._create_job_mock(campaign_year=None)
        job.config = {"campaign_kind": "annual"}  # No campaign_year

        with patch("ha_backend.worker.main._is_mountpoint", return_value=False):
            # Should return without error (logs warning internally)
            _tier_annual_job_if_needed(job)
