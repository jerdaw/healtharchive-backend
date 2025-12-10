from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

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


# === Database configuration ===

# By default we keep things simple and use a SQLite database file in the
# repository root. This can be overridden via HEALTHARCHIVE_DATABASE_URL.
DEFAULT_DATABASE_URL = f"sqlite:///{REPO_ROOT / 'healtharchive.db'}"

# === CORS / frontend integration ===

# Default origins for the public API. This covers local dev and the
# production/staging frontend domains. Override via
# HEALTHARCHIVE_CORS_ORIGINS (comma-separated).
DEFAULT_CORS_ORIGINS: List[str] = [
    "http://localhost:3000",
    "http://localhost:5173",
    "https://healtharchive.ca",
    "https://www.healtharchive.ca",
]


@dataclass
class DatabaseConfig:
    """
    Database connection settings.

    For now this is a very small wrapper around a single DATABASE_URL string,
    but it gives us a stable place to grow later (pool settings, echo flags,
    etc.).
    """

    database_url: str = DEFAULT_DATABASE_URL


def get_database_config() -> DatabaseConfig:
    """
    Return the current database configuration, honouring environment overrides.
    """
    url = os.environ.get("HEALTHARCHIVE_DATABASE_URL", DEFAULT_DATABASE_URL)
    return DatabaseConfig(database_url=url)


def get_cors_origins() -> List[str]:
    """
    Return the list of allowed CORS origins for the public API.

    Controlled via HEALTHARCHIVE_CORS_ORIGINS (comma-separated). Falls back to
    a sensible set covering local dev and production domains.
    """
    raw = os.environ.get("HEALTHARCHIVE_CORS_ORIGINS")
    if raw is not None:
        origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
        if origins:
            return origins
    return DEFAULT_CORS_ORIGINS
