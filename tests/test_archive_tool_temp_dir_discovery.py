from __future__ import annotations

import os
import time
from pathlib import Path

from archive_tool.utils import discover_temp_dirs


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
