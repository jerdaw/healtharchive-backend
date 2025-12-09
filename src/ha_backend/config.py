from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass

# === Core paths ===

# Base directory where all job output dirs will live.
# Adjust to your actual NAS mount if you like.
DEFAULT_ARCHIVE_ROOT = Path("/mnt/nasd/nobak/healtharchive/jobs")

# Path to this repo root (computed from this file)
REPO_ROOT = Path(__file__).resolve().parents[2]  # src/ha_backend -> src -> repo root

# === Archive tool invocation ===

# We prefer using the console script 'archive-tool' which points to
# archive_tool.main:main via pyproject.toml.
DEFAULT_ARCHIVE_TOOL_CMD = "archive-tool"


@dataclass
class ArchiveToolConfig:
    """
    Configuration for calling the archive_tool CLI.
    """

    archive_root: Path = DEFAULT_ARCHIVE_ROOT
    archive_tool_cmd: str = DEFAULT_ARCHIVE_TOOL_CMD

    def ensure_archive_root(self) -> None:
        """
        Ensure the archive root directory exists and is writable.
        """
        self.archive_root.mkdir(parents=True, exist_ok=True)
        # Optionally: add a simple writability check here later.


# Simple global accessor for now; later we can make this more flexible.
def get_archive_tool_config() -> ArchiveToolConfig:
    root_str = os.environ.get("HEALTHARCHIVE_ARCHIVE_ROOT", str(DEFAULT_ARCHIVE_ROOT))
    archive_root = Path(root_str)
    cmd = os.environ.get("HEALTHARCHIVE_TOOL_CMD", DEFAULT_ARCHIVE_TOOL_CMD)
    return ArchiveToolConfig(archive_root=archive_root, archive_tool_cmd=cmd)


