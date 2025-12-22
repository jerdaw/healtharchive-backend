from __future__ import annotations

from pathlib import Path
from typing import List

from archive_tool.state import CrawlState
from archive_tool.utils import find_all_warc_files, find_latest_temp_dir_fallback
from ha_backend.archive_storage import get_job_warcs_dir
from ha_backend.models import ArchiveJob


def discover_temp_warcs_for_job(
    job: ArchiveJob,
    *,
    allow_fallback: bool = True,
) -> List[Path]:
    """
    Discover WARCs under archive_tool's `.tmp*` crawl directories for a job.

    This is the legacy discovery method and intentionally ignores the stable
    `warcs/` directory that may be present after consolidation.
    """
    host_output_dir = Path(job.output_dir).resolve()

    state = CrawlState(host_output_dir, initial_workers=1)
    temp_dirs = state.get_temp_dir_paths()

    if not temp_dirs and allow_fallback:
        latest = find_latest_temp_dir_fallback(host_output_dir)
        if latest is not None:
            temp_dirs = [latest]

    if not temp_dirs:
        return []

    return find_all_warc_files(temp_dirs)


def discover_warcs_for_job(
    job: ArchiveJob,
    *,
    allow_fallback: bool = True,
) -> List[Path]:
    """
    Discover all WARC files associated with a given ArchiveJob.

    This uses archive_tool's CrawlState and utility helpers so we respect the
    same layout and temp-dir tracking that the crawler uses. These helpers
    live in the in-repo ``archive_tool`` package and are expected to evolve
    in tandem with this indexing code.
    """
    host_output_dir = Path(job.output_dir).resolve()

    # Prefer stable per-job WARCs when present. This decouples long-lived WARC
    # artifacts from `.tmp*` crawl directories so operators can safely clean up
    # temp state without breaking replay.
    stable_dir = get_job_warcs_dir(host_output_dir)
    if stable_dir.is_dir():
        stable_warcs: set[Path] = set()
        for ext in (".warc.gz", ".warc"):
            for warc_file in stable_dir.rglob(f"*{ext}"):
                try:
                    if warc_file.is_file() and warc_file.stat().st_size > 0:
                        stable_warcs.add(warc_file.resolve())
                except OSError:
                    continue
        if stable_warcs:
            return sorted(stable_warcs)

    return discover_temp_warcs_for_job(job, allow_fallback=allow_fallback)


__all__ = ["discover_temp_warcs_for_job", "discover_warcs_for_job"]
