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

# We prefer using the console script 'archive-tool', provided by the in-repo
# archive_tool package (archive_tool.main:main via pyproject.toml).
DEFAULT_ARCHIVE_TOOL_CMD = "archive-tool"

# === Replay (pywb) integration ===

# Base URL for the replay service (pywb), used to construct public browse URLs
# for snapshots when the replay service is deployed.
#
# Example:
#   HEALTHARCHIVE_REPLAY_BASE_URL=https://replay.healtharchive.ca
#
# If unset, the API will omit browse URLs and clients should fall back to the
# raw snapshot HTML endpoint.
DEFAULT_REPLAY_BASE_URL = ""

# Directory where pre-rendered replay preview images are stored.
#
# These images are used by the frontend to show lightweight “homepage preview”
# tiles without embedding iframes. They are intentionally generated offline (or
# on-demand by an operator script) and served as static files by the API.
#
# Example (prod):
#   HEALTHARCHIVE_REPLAY_PREVIEW_DIR=/srv/healtharchive/replay/previews
DEFAULT_REPLAY_PREVIEW_DIR = ""

# === Search/browse behavior toggles ===

# When enabled, /api/search with view=pages and no query/date-range can use the
# materialized "pages" table for faster browsing.
DEFAULT_PAGES_FASTPATH_ENABLED = True

# === Usage metrics ===

# Aggregate-only usage metrics (daily counts). Disable if you want a strictly
# metrics-free deployment.
DEFAULT_USAGE_METRICS_ENABLED = True
DEFAULT_USAGE_METRICS_WINDOW_DAYS = 30

# === Change tracking ===

# Precomputed change events and diff artifacts (Phase 3).
DEFAULT_CHANGE_TRACKING_ENABLED = True

# Public site base URL for building absolute links (RSS feeds, etc.).
DEFAULT_PUBLIC_SITE_BASE_URL = "https://healtharchive.ca"


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


def get_replay_base_url() -> str | None:
    """
    Return the configured replay base URL for generating public browse links.

    Reads HEALTHARCHIVE_REPLAY_BASE_URL and normalizes it by:
    - trimming whitespace
    - stripping any trailing slashes
    - defaulting to https:// if the scheme is omitted
    """
    raw = os.environ.get("HEALTHARCHIVE_REPLAY_BASE_URL", DEFAULT_REPLAY_BASE_URL)
    raw = raw.strip()
    if not raw:
        return None

    if not (raw.startswith("http://") or raw.startswith("https://")):
        raw = f"https://{raw}"

    return raw.rstrip("/")


def get_replay_preview_dir() -> Path | None:
    """
    Return the configured directory containing replay preview images.

    If unset, the API will not advertise preview image URLs.
    """
    raw = os.environ.get("HEALTHARCHIVE_REPLAY_PREVIEW_DIR", DEFAULT_REPLAY_PREVIEW_DIR)
    raw = raw.strip()
    if not raw:
        return None
    return Path(raw)


def get_pages_fastpath_enabled() -> bool:
    """
    Return whether the API should use the pages-table fast path for browse.

    Controlled via HA_PAGES_FASTPATH (truthy/falsey). Defaults to enabled.
    """
    default = "1" if DEFAULT_PAGES_FASTPATH_ENABLED else "0"
    raw = os.environ.get("HA_PAGES_FASTPATH", default).strip().lower()
    return raw not in ("0", "false", "no", "off")


def get_usage_metrics_enabled() -> bool:
    """
    Return whether aggregated usage metrics should be recorded.

    Controlled via HEALTHARCHIVE_USAGE_METRICS_ENABLED (truthy/falsey).
    Defaults to enabled.
    """
    default = "1" if DEFAULT_USAGE_METRICS_ENABLED else "0"
    raw = os.environ.get("HEALTHARCHIVE_USAGE_METRICS_ENABLED", default).strip().lower()
    return raw not in ("0", "false", "no", "off")


def get_usage_metrics_window_days() -> int:
    """
    Return the rolling window size (in days) for usage metrics summaries.
    """
    raw = os.environ.get(
        "HEALTHARCHIVE_USAGE_METRICS_WINDOW_DAYS",
        str(DEFAULT_USAGE_METRICS_WINDOW_DAYS),
    ).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_USAGE_METRICS_WINDOW_DAYS
    return max(1, min(value, 365))


def get_change_tracking_enabled() -> bool:
    """
    Return whether change tracking (diff computation + feeds) is enabled.
    """
    default = "1" if DEFAULT_CHANGE_TRACKING_ENABLED else "0"
    raw = os.environ.get("HEALTHARCHIVE_CHANGE_TRACKING_ENABLED", default).strip().lower()
    return raw not in ("0", "false", "no", "off")


def get_public_site_base_url() -> str:
    """
    Return the public site base URL for building absolute links.
    """
    raw = os.environ.get(
        "HEALTHARCHIVE_PUBLIC_SITE_URL",
        DEFAULT_PUBLIC_SITE_BASE_URL,
    ).strip()
    return raw.rstrip("/") if raw else DEFAULT_PUBLIC_SITE_BASE_URL
