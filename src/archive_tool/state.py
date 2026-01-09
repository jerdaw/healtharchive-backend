# archive_tool/state.py
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

# Use absolute imports
from archive_tool.constants import STATE_FILE_NAME

logger = logging.getLogger("website_archiver.state")


class CrawlState:
    """Holds the current state of the archiving process."""

    def __init__(self, host_output_dir: Path, initial_workers: int):
        self.host_output_dir = host_output_dir.resolve()
        self.state_file_path = self.host_output_dir / STATE_FILE_NAME

        # Runtime state (in-memory)
        self.status: str = "initializing"
        self.current_stage: str = "None"
        self.stage_start_time: Optional[float] = None
        self.last_crawled_count: int = -1
        self.last_total_count: int = -1
        self.last_pending_count: int = -1
        self.last_failed_count: int = -1
        self.last_progress_timestamp: Optional[float] = None
        self.last_stats_timestamp: Optional[float] = None
        self.error_counts: Dict[str, int] = {"timeout": 0, "http": 0, "other": 0}
        self.last_error_type: Optional[str] = None
        self.exit_code: Optional[int] = None
        self.previous_crawled_count: int = -1
        self.previous_stats_timestamp: Optional[float] = None
        self.progress_rate_ppm: float = 0.0
        self.last_vpn_rotation_timestamp: Optional[float] = None

        # Persistent state (loaded/saved)
        self.current_workers: int = initial_workers
        self.initial_workers: int = initial_workers
        self.temp_dirs_host_paths: List[str] = []
        self.vpn_rotations_done: int = 0
        self.worker_reductions_done: int = 0
        self.container_restarts_done: int = 0

        self.load_persistent_state()
        # --- ADD THIS LINE ---
        # Ensure state file exists even if starting fresh or load failed
        self.save_persistent_state()
        # --- END ADDED LINE ---

    def load_persistent_state(self):
        """Load state from JSON file if it exists."""
        if self.state_file_path.exists():
            try:
                with open(self.state_file_path, "r") as f:
                    data = json.load(f)
                loaded_workers = data.get("current_workers", self.initial_workers)
                self.current_workers = min(loaded_workers, self.initial_workers)
                self.temp_dirs_host_paths = data.get("temp_dirs_host_paths", [])
                self.vpn_rotations_done = data.get("vpn_rotations_done", 0)
                self.worker_reductions_done = data.get("worker_reductions_done", 0)
                self.container_restarts_done = data.get("container_restarts_done", 0)
                logger.info(
                    f"Loaded persistent state from {self.state_file_path}: "
                    f"Workers={self.current_workers}, Rotations={self.vpn_rotations_done}, "
                    f"Reductions={self.worker_reductions_done}, Restarts={self.container_restarts_done}, "
                    f"TempDirs={len(self.temp_dirs_host_paths)}"
                )
                self.temp_dirs_host_paths = [
                    p for p in self.temp_dirs_host_paths if Path(p).is_dir()
                ]
            except Exception as e:
                logger.warning(
                    f"Could not load or parse state file {self.state_file_path}: {e}. Resetting state."
                )
                self._reset_persistent_state_values()
                # No need to save here, __init__ will save after this returns
        else:
            logger.info("No previous state file found. Initializing fresh state.")
            self._reset_persistent_state_values()
            # No need to save here, __init__ will save after this returns

        # Ensure runtime-only timestamp is reset on load/start
        self.last_vpn_rotation_timestamp = None

    def save_persistent_state(self):
        """Save state to JSON file."""
        # Ensure all listed temp dirs actually exist before saving.
        #
        # Note: during storage incidents (e.g., sshfs stale mount), `is_dir()` /
        # `stat()` can raise. Treat those as "unknown" and keep the path rather
        # than crashing or silently dropping it.
        entries: list[tuple[float, str]] = []
        seen: set[str] = set()
        for raw in list(self.temp_dirs_host_paths):
            p = Path(raw)
            try:
                is_dir = p.is_dir()
            except OSError as exc:
                logger.warning("save_persistent_state: could not stat temp dir %s: %s", p, exc)
                is_dir = True

            if not is_dir:
                continue

            try:
                canonical = str(p.resolve())
            except Exception:
                canonical = str(p)

            if canonical in seen:
                continue
            seen.add(canonical)

            try:
                mtime = float(p.stat().st_mtime)
            except OSError:
                mtime = 0.0
            entries.append((mtime, canonical))

        # Keep oldest first so the newest temp dir is at the end of the list.
        entries.sort(key=lambda item: item[0])
        self.temp_dirs_host_paths = [p for _, p in entries]
        data = {
            "current_workers": self.current_workers,
            "initial_workers": self.initial_workers,
            "temp_dirs_host_paths": self.temp_dirs_host_paths,
            "vpn_rotations_done": self.vpn_rotations_done,
            "worker_reductions_done": self.worker_reductions_done,
            "container_restarts_done": self.container_restarts_done,
        }
        try:
            with open(self.state_file_path, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved persistent state to {self.state_file_path}")
        except Exception as e:
            logger.error(f"Could not save state file {self.state_file_path}: {e}")

    def add_temp_dir(self, temp_dir_path: Optional[Path]):
        """Adds a newly created temp dir path if valid."""
        if temp_dir_path and temp_dir_path.is_dir():
            path_str = str(temp_dir_path.resolve())
            if path_str not in self.temp_dirs_host_paths:
                logger.debug(f"Adding temp dir to state: {path_str}")
                self.temp_dirs_host_paths.append(path_str)
                self.save_persistent_state()  # Save state when adding a dir
        elif temp_dir_path:
            logger.warning(f"Attempted to add non-directory temp path: {temp_dir_path}")

    def get_temp_dir_paths(self) -> List[Path]:
        """Returns list of existing Path objects for temp dirs."""
        existing_paths = []
        changed = False
        current_paths = list(self.temp_dirs_host_paths)  # Copy list for safe iteration
        for p_str in current_paths:
            path = Path(p_str)
            if path.is_dir():
                existing_paths.append(path)
            else:
                logger.warning(
                    f"Temp dir path from state does not exist or is not a directory: {p_str}. Removing from state."
                )
                if p_str in self.temp_dirs_host_paths:
                    self.temp_dirs_host_paths.remove(p_str)
                    changed = True
        # Save state only if invalid paths were removed
        if changed:
            self.save_persistent_state()
        return existing_paths

    def _reset_persistent_state_values(self):
        """Resets state variables that are persisted."""
        self.current_workers = self.initial_workers
        self.temp_dirs_host_paths = []
        self.vpn_rotations_done = 0
        self.worker_reductions_done = 0
        self.container_restarts_done = 0
        self.last_vpn_rotation_timestamp = None

    def reset_adaptation_counts(self):
        """Resets counts for a fresh run if needed, keeping temp dirs."""
        self.vpn_rotations_done = 0
        self.worker_reductions_done = 0
        self.container_restarts_done = 0
        self.last_vpn_rotation_timestamp = None
        self.save_persistent_state()  # Save reset counts

    def reset_runtime_errors(self):
        """Resets error counts, e.g., after an adaptation."""
        self.error_counts = {"timeout": 0, "http": 0, "other": 0}
        self.last_error_type = None
        logger.debug("Runtime error counts reset.")  # Changed to debug

    def update_progress(self, stats: Dict[str, Any], timestamp: float):
        """Updates state based on parsed crawl statistics and calculates rate."""
        crawled = stats.get("crawled", self.last_crawled_count)
        total = stats.get("total", self.last_total_count)
        pending = stats.get("pending", self.last_pending_count)
        failed = stats.get("failed", self.last_failed_count)

        if self.previous_crawled_count < 0 and crawled >= 0:
            self.previous_crawled_count = crawled
            self.previous_stats_timestamp = timestamp

        progress_made = crawled > self.last_crawled_count
        stats_updated = (
            crawled != self.last_crawled_count
            or total != self.last_total_count
            or pending != self.last_pending_count
            or failed != self.last_failed_count
        )

        if (
            self.previous_stats_timestamp is not None
            and timestamp > self.previous_stats_timestamp
            and crawled >= self.previous_crawled_count
        ):
            time_delta_sec = timestamp - self.previous_stats_timestamp
            crawled_delta = crawled - self.previous_crawled_count
            if time_delta_sec > 1 and crawled_delta >= 0:
                rate_per_second = crawled_delta / time_delta_sec
                self.progress_rate_ppm = rate_per_second * 60
                self.previous_stats_timestamp = timestamp
                self.previous_crawled_count = crawled
            elif time_delta_sec > 60 and crawled_delta == 0:
                self.progress_rate_ppm = 0.0
                self.previous_stats_timestamp = timestamp
                self.previous_crawled_count = crawled

        if progress_made:
            self.last_progress_timestamp = timestamp
            if (
                self.error_counts["timeout"] > 0
                or self.error_counts["http"] > 0
                or self.error_counts["other"] > 0
            ):
                logger.info("Progress detected, resetting error counts.")
                self.reset_runtime_errors()

        self.last_crawled_count = crawled
        self.last_total_count = total
        self.last_pending_count = pending
        self.last_failed_count = failed
        self.last_stats_timestamp = timestamp

        if stats_updated:
            log_msg = f"Stats Update: Crawled={crawled}, Total={total}, Pending={pending}, Failed={failed}, Rate={self.progress_rate_ppm:.1f} ppm"
            logger.debug(log_msg)

    def record_error(self, error_type: str, timestamp: float):
        """Increments error counts."""
        if error_type in self.error_counts:
            self.error_counts[error_type] += 1
            self.last_error_type = error_type
