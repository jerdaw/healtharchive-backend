from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_DOCKER_IMAGE = "ghcr.io/openzim/zimit"


def _default_docker_image() -> str:
    raw = os.environ.get("HEALTHARCHIVE_ZIMIT_DOCKER_IMAGE", "")
    docker_image = raw.strip()
    return docker_image or DEFAULT_DOCKER_IMAGE


DOCKER_IMAGE = _default_docker_image()


def _default_docker_memory_limit() -> str | None:
    """Get Docker memory limit from environment or use default."""
    raw = os.environ.get("HEALTHARCHIVE_DOCKER_MEMORY_LIMIT", "")
    value = raw.strip()
    return value if value else "4g"


def _default_docker_cpu_limit() -> str | None:
    """Get Docker CPU limit from environment or use default."""
    raw = os.environ.get("HEALTHARCHIVE_DOCKER_CPU_LIMIT", "")
    value = raw.strip()
    return value if value else "1.5"


DEFAULT_DOCKER_MEMORY_LIMIT = _default_docker_memory_limit()
DEFAULT_DOCKER_CPU_LIMIT = _default_docker_cpu_limit()
CONTAINER_OUTPUT_DIR = Path("/output")
TEMP_DIR_PREFIX = ".tmp"
LOG_FORMAT = "%(asctime)s - %(levelname)s - [%(threadName)s] %(message)s"
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
STATE_FILE_NAME = ".archive_state.json"
RESUME_CONFIG_FILE_NAME = ".zimit_resume.yaml"

# ---------------------------------------------------------------------------
# Timeout and delay constants (seconds)
# ---------------------------------------------------------------------------
# Docker process management
DOCKER_PROCESS_WAIT_TIMEOUT_SEC = 15  # Wait for Docker process to exit gracefully
DOCKER_PROCESS_TERM_TIMEOUT_SEC = 10  # Timeout for SIGTERM to take effect
DOCKER_PROCESS_KILL_TIMEOUT_SEC = 5  # Timeout for SIGKILL to take effect
DOCKER_STOP_GRACE_PERIOD_SEC = 90  # Docker stop --time grace period
DOCKER_STOP_COMMAND_TIMEOUT_SEC = 100  # Timeout for docker stop command
DOCKER_PS_TIMEOUT_SEC = 10  # Timeout for docker ps command
DOCKER_COMMUNICATE_TIMEOUT_SEC = 1  # Timeout for process.communicate()
DOCKER_FORCE_KILL_TIMEOUT_SEC = 15  # Timeout for force kill operations
DOCKER_CONTAINER_ID_RETRY_DELAY_SEC = 2  # Sleep between container ID lookup retries
DOCKER_CONTAINER_ID_MAX_RETRIES = 5  # Max retries for container ID lookup

# Monitor thread
MONITOR_STARTUP_DELAY_SEC = 2  # Delay before monitor starts reading logs
MONITOR_LOG_PROCESS_TERM_TIMEOUT_SEC = 5  # Timeout for log process termination

# Strategy adaptation
CONTAINER_STOP_SETTLE_DELAY_SEC = 5  # Wait after stopping container for full shutdown

# Logging and truncation
LOG_LINE_TRUNCATE_LENGTH = 200  # Truncate log lines to this length for display
LOG_LINE_ERROR_TRUNCATE_LENGTH = 100  # Truncate error log lines to this shorter length

# Utility command timeouts
DOCKER_VERSION_CHECK_TIMEOUT_SEC = 10  # Timeout for docker --version check
EXTERNAL_COMMAND_TIMEOUT_SEC = 120  # Timeout for external command execution

# Main loop
PROGRESS_PRINT_INTERVAL_SEC = 60.0  # How often to print progress to console
QUEUE_CHECK_TIMEOUT_SEC = 1.0  # How long to wait for monitor queue message
THREAD_JOIN_TIMEOUT_SEC = 5.0  # Timeout for thread.join() operations
MIN_RELAX_PERMS_INTERVAL_SEC = 10  # Minimum interval for relax_permissions calls

# ---------------------------------------------------------------------------
# CLI default values
# ---------------------------------------------------------------------------
DEFAULT_INITIAL_WORKERS = 1
DEFAULT_MONITOR_INTERVAL_SEC = 30
DEFAULT_STALL_TIMEOUT_MINUTES = 30
DEFAULT_ERROR_THRESHOLD_TIMEOUT = 10
DEFAULT_ERROR_THRESHOLD_HTTP = 10
DEFAULT_BACKOFF_DELAY_MINUTES = 5
DEFAULT_MAX_CONTAINER_RESTARTS = 5
DEFAULT_MAX_WORKER_REDUCTIONS = 3
DEFAULT_MAX_VPN_ROTATIONS = 3
DEFAULT_MIN_WORKERS = 1

# Args needed for final build
REQUIRED_FINAL_ARGS_PREFIXES = [
    "--name",
    "--title",
    "--description",
    "--long-description",
    "--zim-lang",
    "--custom-css",
    "--adminEmail",
    "--favicon",
    "--warcPrefix",
    "--lang",
]

# Error patterns for monitoring
TIMEOUT_PATTERNS = [
    r"Navigation timeout",
    r"net::ERR_TIMED_OUT",
    r"net::ERR_NAME_NOT_RESOLVED",  # DNS lookup failures
    r"net::ERR_DNS_TIMED_OUT",  # DNS timeout
    r"net::ERR_CONNECTION_TIMED_OUT",  # Connection timeout
]
HTTP_ERROR_PATTERNS = [
    r"net::ERR_",
    r'status":([45]\d{2})',
    r"net::ERR_CONNECTION_REFUSED",  # Server not accepting connections
    r"net::ERR_CONNECTION_RESET",  # Connection reset by peer
    r"net::ERR_NETWORK_CHANGED",  # Network configuration changed mid-request
]  # Generic net errors or 4xx/5xx status

# Exit codes potentially indicating non-fatal completion (e.g., soft limits)
# These might allow skipping resume/retries if encountered
ACCEPTABLE_CRAWLER_EXIT_CODES = [
    16,  # EXIT_CODE_DISK_UTILIZATION_EXCEEDED - prevents restart loops on disk full
    32,  # Zimit/Browsertrix constant EXIT_CODE_CRAWLER_SIZE_LIMIT_HIT
    33,  # Zimit/Browsertrix constant EXIT_CODE_CRAWLER_TIME_LIMIT_HIT
]

# --- NEW CONSTANT ---
# Regex to find the JSON details block of the last crawl statistics message
# Ensures it captures the full JSON object, even if complex
STATS_REGEX = re.compile(
    r'"context":"crawlStatus".*"message":"Crawl statistics".*"details":({.*?})\s*}\s*$',
    re.MULTILINE | re.DOTALL,
)
# Explanation:
# "context":"crawlStatus" - Match context
# "message":"Crawl statistics" - Match message
# "details":({.*?}) - Capture the JSON details block non-greedily ({...})
# \s*}\s*$ - Match the closing brace of the outer JSON log entry, optional whitespace, end of line ($) in MULTILINE mode. DOTALL allows '.' to match newlines within the details block.
# --- END NEW CONSTANT ---
