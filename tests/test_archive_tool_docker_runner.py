from __future__ import annotations

from pathlib import Path

from archive_tool.docker_runner import build_docker_run_cmd


def test_build_docker_run_cmd_includes_shm_size_before_image(tmp_path: Path) -> None:
    cmd = build_docker_run_cmd(
        docker_image="ghcr.io/openzim/zimit:stable",
        host_output_dir=tmp_path,
        zimit_args=["zimit", "--name", "example"],
        docker_shm_size="1g",
    )

    assert "--shm-size" in cmd
    assert cmd[cmd.index("--shm-size") + 1] == "1g"
    assert cmd.index("--shm-size") < cmd.index("ghcr.io/openzim/zimit:stable")


def test_build_docker_run_cmd_includes_label_when_set(tmp_path: Path) -> None:
    cmd = build_docker_run_cmd(
        docker_image="ghcr.io/openzim/zimit:stable",
        host_output_dir=tmp_path,
        zimit_args=["zimit", "--name", "example"],
        label="archive_job=abc123",
    )
    assert "--label" in cmd
    assert cmd[cmd.index("--label") + 1] == "archive_job=abc123"
