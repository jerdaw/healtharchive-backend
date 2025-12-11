from __future__ import annotations

import sys
from pathlib import Path

import archive_tool.main as archive_main
import archive_tool.state as state_mod
import archive_tool.utils as utils_mod


def test_archive_tool_dry_run_exits_before_crawl(tmp_path, monkeypatch, capsys) -> None:
    """
    Invoking archive_tool.main with --dry-run should validate configuration
    and return without instantiating CrawlState or starting any crawl stages.
    """
    # Ensure Docker check passes but record that it was called.
    called = {"check_docker": False, "crawl_state": False}

    def fake_check_docker() -> bool:
        called["check_docker"] = True
        return True

    def fake_crawl_state(*args, **kwargs):
        called["crawl_state"] = True
        raise AssertionError("CrawlState should not be created in dry-run mode")

    monkeypatch.setattr(utils_mod, "check_docker", fake_check_docker)
    monkeypatch.setattr(state_mod, "CrawlState", fake_crawl_state)

    out_dir = tmp_path / "out"
    out_dir.mkdir()

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

    # Should not raise, and should not instantiate CrawlState.
    archive_main.main()

    assert called["check_docker"] is True
    assert called["crawl_state"] is False

    captured = capsys.readouterr()
    # We don't assert exact wording, just that "Dry run" appears somewhere.
    assert "Dry run" in captured.out or "Dry run" in captured.err

