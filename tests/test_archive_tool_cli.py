from __future__ import annotations

import pytest

from archive_tool.cli import parse_arguments


def test_parse_arguments_accepts_skip_final_build_and_docker_shm_size(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "archive-tool",
            "--seeds",
            "https://example.org",
            "--name",
            "example",
            "--output-dir",
            "/tmp/example",
            "--skip-final-build",
            "--docker-shm-size",
            "1g",
            "--scopeType",
            "host",
        ],
    )

    script_args, zimit_passthrough = parse_arguments()
    assert script_args.skip_final_build is True
    assert script_args.docker_shm_size == "1g"
    assert zimit_passthrough == ["--scopeType", "host"]


def test_parse_arguments_rejects_adaptive_without_monitoring(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "archive-tool",
            "--seeds",
            "https://example.org",
            "--name",
            "example",
            "--output-dir",
            "/tmp/example",
            "--enable-adaptive-restart",
        ],
    )

    with pytest.raises(SystemExit):
        parse_arguments()
