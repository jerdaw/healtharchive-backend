from __future__ import annotations

from pathlib import Path
from typing import List

from archive_tool.state import CrawlState
from archive_tool.utils import find_all_warc_files, find_latest_temp_dir_fallback

from ha_backend.models import ArchiveJob


def discover_warcs_for_job(
    job: ArchiveJob,
    *,
    allow_fallback: bool = True,
) -> List[Path]:
    """
    Discover all WARC files associated with a given ArchiveJob.

    This uses archive_tool's CrawlState and utility helpers so we respect the
    same layout and temp-dir tracking that the crawler uses.
    """
    host_output_dir = Path(job.output_dir).resolve()

    # Initial workers value is irrelevant here; we only need the persistent
    # state (temp dir paths and state file location).
    state = CrawlState(host_output_dir, initial_workers=1)
    temp_dirs = state.get_temp_dir_paths()

    if not temp_dirs and allow_fallback:
        latest = find_latest_temp_dir_fallback(host_output_dir)
        if latest is not None:
            temp_dirs = [latest]

    if not temp_dirs:
        return []

    return find_all_warc_files(temp_dirs)


__all__ = ["discover_warcs_for_job"]

