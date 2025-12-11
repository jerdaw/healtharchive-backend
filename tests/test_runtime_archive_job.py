from __future__ import annotations

from pathlib import Path

from ha_backend.jobs import RuntimeArchiveJob


def test_build_command_includes_core_args_and_cleanup(monkeypatch) -> None:
    """
    RuntimeArchiveJob.build_command should construct a CLI with the configured
    archive_tool cmd and core flags, plus any extra args.
    """

    class DummyCfg:
        archive_tool_cmd = "dummy-archive-tool"
        archive_root = Path("/tmp/archive-root")

    monkeypatch.setattr(
        "ha_backend.jobs.get_archive_tool_config", lambda: DummyCfg()
    )

    job = RuntimeArchiveJob(
        name="testjob",
        seeds=["https://example.org", "https://example.com"],
    )
    output_dir = Path("/tmp/output-dir")

    cmd = job.build_command(
        output_dir=output_dir,
        initial_workers=2,
        cleanup=True,
        overwrite=False,
        log_level="DEBUG",
        extra_args=["--some-flag", "value"],
    )

    # Core structure and values.
    assert cmd[0] == "dummy-archive-tool"
    assert "--seeds" in cmd
    assert "https://example.org" in cmd
    assert "https://example.com" in cmd
    assert "--name" in cmd
    assert "testjob" in cmd
    assert "--output-dir" in cmd
    assert str(output_dir) in cmd
    assert "--initial-workers" in cmd
    assert "2" in cmd
    assert "--log-level" in cmd
    assert "DEBUG" in cmd
    assert "--cleanup" in cmd
    assert "--overwrite" not in cmd

    # Extra args should be appended.
    assert "--some-flag" in cmd
    assert "value" in cmd


def test_build_command_includes_overwrite_flag_when_set(monkeypatch) -> None:
    """
    When overwrite=True, the '--overwrite' flag should be present and
    '--cleanup' should be omitted if cleanup=False.
    """

    class DummyCfg:
        archive_tool_cmd = "dummy-archive-tool"
        archive_root = Path("/tmp/archive-root")

    monkeypatch.setattr(
        "ha_backend.jobs.get_archive_tool_config", lambda: DummyCfg()
    )

    job = RuntimeArchiveJob(
        name="overwrite-job",
        seeds=["https://example.org"],
    )
    output_dir = Path("/tmp/output-dir")

    cmd = job.build_command(
        output_dir=output_dir,
        initial_workers=1,
        cleanup=False,
        overwrite=True,
        log_level="INFO",
        extra_args=None,
    )

    assert cmd[0] == "dummy-archive-tool"
    assert "--cleanup" not in cmd
    assert "--overwrite" in cmd

