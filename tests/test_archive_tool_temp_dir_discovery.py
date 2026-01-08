from __future__ import annotations

import os
import pathlib
import time
from pathlib import Path

from archive_tool.utils import discover_temp_dirs, parse_temp_dir_from_log_file


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
