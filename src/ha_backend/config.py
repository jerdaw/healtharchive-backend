from __future__ import annotations

import os
import shutil
import sys
import tempfile
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


def _detect_archive_tool_cmd() -> str:
    """
    Determine the effective archive_tool command.

    Precedence:
    1) HEALTHARCHIVE_TOOL_CMD (explicit override)
    2) If running from a venv and the sibling console script exists, use:
         <venv>/bin/archive-tool
    3) Fallback: "archive-tool" (PATH lookup)
    """
    explicit = os.environ.get("HEALTHARCHIVE_TOOL_CMD")
    if explicit is not None and explicit.strip():
        explicit = explicit.strip()
        # Explicit path: use as-is (caller owns correctness).
        if "/" in explicit:
            return explicit

        # For the default "archive-tool" name, try to resolve it. If it doesn't
        # resolve (common when running without an activated venv), fall back to
        # the venv-local console script if present.
        if explicit == DEFAULT_ARCHIVE_TOOL_CMD:
            resolved = shutil.which(explicit)
            if resolved:
                return resolved

            venv_candidate = Path(sys.executable).parent / "archive-tool"
            if venv_candidate.is_file() and os.access(venv_candidate, os.X_OK):
                return str(venv_candidate)
            repo_candidate = REPO_ROOT / ".venv" / "bin" / "archive-tool"
            if repo_candidate.is_file() and os.access(repo_candidate, os.X_OK):
                return str(repo_candidate)

        # For non-default names, respect the override even if it's not
        # resolvable in the current PATH.
        return explicit

    try:
        python_bin_dir = Path(sys.executable).parent
        candidate = python_bin_dir / "archive-tool"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    except Exception:
        return DEFAULT_ARCHIVE_TOOL_CMD

    try:
        repo_candidate = REPO_ROOT / ".venv" / "bin" / "archive-tool"
        if repo_candidate.is_file() and os.access(repo_candidate, os.X_OK):
            return str(repo_candidate)
    except Exception:
        return DEFAULT_ARCHIVE_TOOL_CMD

    resolved = shutil.which(DEFAULT_ARCHIVE_TOOL_CMD)
    if resolved:
        return resolved

    return DEFAULT_ARCHIVE_TOOL_CMD


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

# Precomputed change events and diff artifacts (change tracking pipeline).
DEFAULT_CHANGE_TRACKING_ENABLED = True

# === Compare-to-live ===

# Enable public compare-to-live diffing against the current URL.
DEFAULT_COMPARE_LIVE_ENABLED = True
DEFAULT_COMPARE_LIVE_TIMEOUT_SECONDS = 8
DEFAULT_COMPARE_LIVE_MAX_REDIRECTS = 4
DEFAULT_COMPARE_LIVE_MAX_BYTES = 2_000_000
DEFAULT_COMPARE_LIVE_MAX_ARCHIVE_BYTES = 2_000_000
DEFAULT_COMPARE_LIVE_MAX_RENDER_LINES = 5000
DEFAULT_COMPARE_LIVE_MAX_CONCURRENCY = 4
DEFAULT_COMPARE_LIVE_USER_AGENT = "HealthArchiveCompareLive/1.0 (+https://healtharchive.ca)"

# === Research exports ===

# Public, metadata-only exports for research.
DEFAULT_EXPORTS_ENABLED = True
DEFAULT_EXPORTS_DEFAULT_LIMIT = 1000
DEFAULT_EXPORTS_MAX_LIMIT = 10000

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
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.archive_root, prefix=".ha_write_test_", delete=True
            ) as file:
                file.write(b"ok")
                file.flush()
        except OSError as exc:
            raise RuntimeError(f"Archive root is not writable: {self.archive_root}") from exc


# Simple global accessor for now; later we can make this more flexible.
def get_archive_tool_config() -> ArchiveToolConfig:
    root_str = os.environ.get("HEALTHARCHIVE_ARCHIVE_ROOT", str(DEFAULT_ARCHIVE_ROOT))
    archive_root = Path(root_str)
    cmd = _detect_archive_tool_cmd()
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


def get_compare_live_enabled() -> bool:
    """
    Return whether public compare-to-live is enabled.
    """
    default = "1" if DEFAULT_COMPARE_LIVE_ENABLED else "0"
    raw = os.environ.get("HEALTHARCHIVE_COMPARE_LIVE_ENABLED", default).strip().lower()
    return raw not in ("0", "false", "no", "off")


def get_compare_live_timeout_seconds() -> float:
    """
    Return the total timeout for live fetches (seconds).
    """
    raw = os.environ.get(
        "HEALTHARCHIVE_COMPARE_LIVE_TIMEOUT_SECONDS",
        str(DEFAULT_COMPARE_LIVE_TIMEOUT_SECONDS),
    ).strip()
    try:
        value = float(raw)
    except ValueError:
        value = float(DEFAULT_COMPARE_LIVE_TIMEOUT_SECONDS)
    return max(1.0, min(value, 30.0))


def get_compare_live_max_redirects() -> int:
    """
    Return the maximum number of live redirects to follow.
    """
    raw = os.environ.get(
        "HEALTHARCHIVE_COMPARE_LIVE_MAX_REDIRECTS",
        str(DEFAULT_COMPARE_LIVE_MAX_REDIRECTS),
    ).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_COMPARE_LIVE_MAX_REDIRECTS
    return max(0, min(value, 10))


def get_compare_live_max_bytes() -> int:
    """
    Return the maximum number of bytes read from the live URL.
    """
    raw = os.environ.get(
        "HEALTHARCHIVE_COMPARE_LIVE_MAX_BYTES",
        str(DEFAULT_COMPARE_LIVE_MAX_BYTES),
    ).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_COMPARE_LIVE_MAX_BYTES
    return max(100_000, min(value, 20_000_000))


def get_compare_live_max_archive_bytes() -> int:
    """
    Return the maximum number of bytes read from archived HTML.
    """
    raw = os.environ.get(
        "HEALTHARCHIVE_COMPARE_LIVE_MAX_ARCHIVE_BYTES",
        str(DEFAULT_COMPARE_LIVE_MAX_ARCHIVE_BYTES),
    ).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_COMPARE_LIVE_MAX_ARCHIVE_BYTES
    return max(100_000, min(value, 20_000_000))


def get_compare_live_max_render_lines() -> int:
    """
    Return the maximum number of lines included in compare-live render payloads.
    """
    raw = os.environ.get(
        "HEALTHARCHIVE_COMPARE_LIVE_MAX_RENDER_LINES",
        str(DEFAULT_COMPARE_LIVE_MAX_RENDER_LINES),
    ).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_COMPARE_LIVE_MAX_RENDER_LINES
    return max(500, min(value, 20_000))


def get_compare_live_max_concurrency() -> int:
    """
    Return the per-process max concurrent compare-live requests.
    """
    raw = os.environ.get(
        "HEALTHARCHIVE_COMPARE_LIVE_MAX_CONCURRENCY",
        str(DEFAULT_COMPARE_LIVE_MAX_CONCURRENCY),
    ).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_COMPARE_LIVE_MAX_CONCURRENCY
    return max(1, min(value, 50))


def get_compare_live_user_agent() -> str:
    """
    Return the User-Agent used for compare-live fetches.
    """
    raw = os.environ.get(
        "HEALTHARCHIVE_COMPARE_LIVE_USER_AGENT",
        DEFAULT_COMPARE_LIVE_USER_AGENT,
    )
    return raw.strip() or DEFAULT_COMPARE_LIVE_USER_AGENT


def get_exports_enabled() -> bool:
    """
    Return whether public export endpoints are enabled.
    """
    default = "1" if DEFAULT_EXPORTS_ENABLED else "0"
    raw = os.environ.get("HEALTHARCHIVE_EXPORTS_ENABLED", default).strip().lower()
    return raw not in ("0", "false", "no", "off")


def get_exports_default_limit() -> int:
    """
    Default maximum rows returned by export endpoints.
    """
    raw = os.environ.get(
        "HEALTHARCHIVE_EXPORTS_DEFAULT_LIMIT",
        str(DEFAULT_EXPORTS_DEFAULT_LIMIT),
    ).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_EXPORTS_DEFAULT_LIMIT
    max_limit = get_exports_max_limit()
    return max(1, min(value, max_limit))


def get_exports_max_limit() -> int:
    """
    Hard cap on rows returned by export endpoints.
    """
    raw = os.environ.get(
        "HEALTHARCHIVE_EXPORTS_MAX_LIMIT",
        str(DEFAULT_EXPORTS_MAX_LIMIT),
    ).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_EXPORTS_MAX_LIMIT
    return max(1, value)


def get_public_site_base_url() -> str:
    """
    Return the public site base URL for building absolute links.
    """
    raw = os.environ.get(
        "HEALTHARCHIVE_PUBLIC_SITE_URL",
        DEFAULT_PUBLIC_SITE_BASE_URL,
    ).strip()
    return raw.rstrip("/") if raw else DEFAULT_PUBLIC_SITE_BASE_URL
