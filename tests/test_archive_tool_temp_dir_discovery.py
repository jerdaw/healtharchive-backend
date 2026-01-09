from __future__ import annotations

import os
import pathlib
import time
from pathlib import Path

from archive_tool.state import CrawlState
from archive_tool.utils import (
    discover_temp_dirs,
    find_latest_config_yaml_in_temp_dirs,
    find_stable_resume_config,
    parse_temp_dir_from_log_file,
    persist_resume_config,
)


def test_discover_temp_dirs_orders_by_mtime(tmp_path: Path) -> None:
    first = tmp_path / ".tmpA"
    second = tmp_path / ".tmpB"
    first.mkdir()
    second.mkdir()

    now = time.time()
    os.utime(first, (now - 10, now - 10))
    os.utime(second, (now - 5, now - 5))

    found = discover_temp_dirs(tmp_path)
    assert found == [first.resolve(), second.resolve()]


def test_parse_temp_dir_from_log_file_handles_oserror_on_is_file(
    tmp_path: Path, monkeypatch
) -> None:
    """
    When the log path is on a stale mount (Errno 107), temp-dir parsing should
    fall back to scanning `.tmp*` dirs instead of raising.
    """
    temp_dir = tmp_path / ".tmpA"
    temp_dir.mkdir()

    log_path = tmp_path / "archive_stage.combined.log"

    orig_is_file = pathlib.Path.is_file

    def raising_is_file(self: pathlib.Path) -> bool:
        if Path(self) == log_path:
            raise OSError(107, "Transport endpoint is not connected", str(self))
        return orig_is_file(self)

    monkeypatch.setattr(pathlib.Path, "is_file", raising_is_file)

    found = parse_temp_dir_from_log_file(log_path, tmp_path)
    assert found == temp_dir.resolve()


def test_crawl_state_sorts_temp_dirs_by_mtime(tmp_path: Path) -> None:
    output_dir = tmp_path / "job-output"
    output_dir.mkdir(parents=True, exist_ok=True)

    older = output_dir / ".tmpA"
    newer = output_dir / ".tmpB"
    older.mkdir()
    newer.mkdir()

    now = time.time()
    os.utime(older, (now - 10, now - 10))
    os.utime(newer, (now - 5, now - 5))

    state = CrawlState(output_dir, initial_workers=1)
    # Add in reverse order to ensure mtime ordering is applied.
    state.add_temp_dir(newer)
    state.add_temp_dir(older)

    ordered = state.get_temp_dir_paths()
    assert ordered == [older.resolve(), newer.resolve()]

    # A fresh reload should preserve the same ordering.
    state2 = CrawlState(output_dir, initial_workers=1)
    assert state2.get_temp_dir_paths() == [older.resolve(), newer.resolve()]


def test_resume_config_discovery_prefers_newest_and_persists(tmp_path: Path) -> None:
    output_dir = tmp_path / "job-output"
    output_dir.mkdir(parents=True, exist_ok=True)

    tmp_a = output_dir / ".tmpA"
    tmp_b = output_dir / ".tmpB"
    tmp_a.mkdir()
    tmp_b.mkdir()

    yaml_a = tmp_a / "collections" / "crawl-a" / "crawls" / "crawl-a.yaml"
    yaml_b = tmp_b / "collections" / "crawl-b" / "crawls" / "crawl-b.yaml"
    yaml_a.parent.mkdir(parents=True, exist_ok=True)
    yaml_b.parent.mkdir(parents=True, exist_ok=True)
    yaml_a.write_text("a: 1\n", encoding="utf-8")
    yaml_b.write_text("b: 2\n", encoding="utf-8")

    now = time.time()
    os.utime(yaml_a, (now - 10, now - 10))
    os.utime(yaml_b, (now - 5, now - 5))

    found = find_latest_config_yaml_in_temp_dirs([tmp_a, tmp_b])
    assert found is not None
    assert found == yaml_b.resolve()

    persisted = persist_resume_config(found, output_dir)
    assert persisted is not None
    stable = find_stable_resume_config(output_dir)
    assert stable == persisted
