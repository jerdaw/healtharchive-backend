# archive_tool/constants.py
import re  # Add import re
from pathlib import Path

DOCKER_IMAGE = "ghcr.io/openzim/zimit"
CONTAINER_OUTPUT_DIR = Path("/output")
TEMP_DIR_PREFIX = ".tmp"
LOG_FORMAT = "%(asctime)s - %(levelname)s - [%(threadName)s] %(message)s"
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
STATE_FILE_NAME = ".archive_state.json"
RESUME_CONFIG_FILE_NAME = ".zimit_resume.yaml"

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
TIMEOUT_PATTERNS = [r"Navigation timeout", r"net::ERR_TIMED_OUT"]
HTTP_ERROR_PATTERNS = [
    r"net::ERR_",
    r'status":([45]\d{2})',
]  # Generic net errors or 4xx/5xx status

# Exit codes potentially indicating non-fatal completion (e.g., soft limits)
# These might allow skipping resume/retries if encountered
ACCEPTABLE_CRAWLER_EXIT_CODES = [
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
