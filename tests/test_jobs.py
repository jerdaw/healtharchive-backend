from __future__ import annotations

from pathlib import Path

from ha_backend.config import get_archive_tool_config
from ha_backend.jobs import ArchiveJob


def test_ensure_job_dir_creates_unique_dir(tmp_path) -> None:
    """
    ensure_job_dir should create a new timestamped subdirectory under archive root.
    """
    archive_root = tmp_path
    job = ArchiveJob(name="my-job", seeds=["https://example.org"])

    job_dir = job.ensure_job_dir(archive_root)

    assert job_dir.is_dir()
    assert job_dir.parent == archive_root

    prefix, sep, suffix = job_dir.name.partition("__")
    assert sep == "__"
    assert suffix == "my-job".replace(" ", "_")
    assert len(prefix) == 16  # e.g. 20251209T204530Z
    assert prefix.endswith("Z")


def test_build_command_includes_expected_args(monkeypatch, tmp_path) -> None:
    """
    build_command should construct a well-formed archive_tool CLI.
    """
    monkeypatch.setenv("HEALTHARCHIVE_TOOL_CMD", "archive-tool-test")
    job = ArchiveJob(
        name="example-job",
        seeds=["https://example.org/a", "https://example.org/b"],
    )
    output_dir = tmp_path / "out"

    cmd = job.build_command(
        output_dir=output_dir,
        initial_workers=2,
        cleanup=True,
        overwrite=True,
        log_level="DEBUG",
        extra_args=["--foo", "bar"],
    )

    assert cmd[0] == "archive-tool-test"

    # Seeds are passed after --seeds, in order.
    assert "--seeds" in cmd
    seeds_index = cmd.index("--seeds")
    assert cmd[seeds_index + 1 : seeds_index + 3] == job.seeds

    assert "--name" in cmd
    assert cmd[cmd.index("--name") + 1] == "example-job"

    assert "--output-dir" in cmd
    assert cmd[cmd.index("--output-dir") + 1] == str(output_dir)

    assert "--initial-workers" in cmd
    assert cmd[cmd.index("--initial-workers") + 1] == "2"

    assert "--cleanup" in cmd
    assert "--overwrite" in cmd
    assert cmd[-2:] == ["--foo", "bar"]


def test_run_job_uses_archive_root_and_tool(monkeypatch, tmp_path) -> None:
    """
    run() should honour HEALTHARCHIVE_ARCHIVE_ROOT and HEALTHARCHIVE_TOOL_CMD.

    We point the tool command at 'echo' so the subprocess always succeeds
    without invoking the real archive-tool or Docker.
    """
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(tmp_path))
    monkeypatch.setenv("HEALTHARCHIVE_TOOL_CMD", "echo")

    job = ArchiveJob(name="echo-job", seeds=["https://example.org"])

    rc = job.run(
        initial_workers=1,
        cleanup=False,
        overwrite=False,
        log_level="INFO",
        extra_args=["--foo", "bar"],
        stream_output=False,
    )

    assert rc == 0

    # A single job directory should have been created under the archive root.
    subdirs = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(subdirs) == 1
    job_dir = subdirs[0]

    assert job_dir.parent == Path(tmp_path)
    assert job_dir.name.endswith("__echo-job")
