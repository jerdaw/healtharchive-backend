# archive_tool/state.py
import json
import logging
import os
import threading
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
        # Thread lock for state file operations to prevent races between
        # monitor thread and main thread
        self._state_lock = threading.Lock()

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
        """
        Load persistent crawl state from the JSON state file.

        Restores worker count, temp directory list, and adaptation counters from
        a previous run. This enables resuming crawls after container restarts or
        script interruptions.

        Loaded fields:
        - current_workers: Capped at initial_workers to prevent invalid states
        - temp_dirs_host_paths: List of temp directories, filtered to existing dirs only
        - vpn_rotations_done, worker_reductions_done, container_restarts_done: Counters

        Type validation is performed on all loaded values to prevent JSON
        corruption from causing runtime errors. Invalid or missing values reset
        to defaults.

        Thread safety: Acquires _state_lock during file read and state mutation.

        Side effects:
        - Resets last_vpn_rotation_timestamp to None (runtime-only field)
        - If load fails or file doesn't exist, initializes fresh state
        """
        with self._state_lock:
            if self.state_file_path.exists():
                try:
                    with open(self.state_file_path, "r") as f:
                        data = json.load(f)
                    # === Type Validation for Loaded State ===
                    # JSON can represent values with wrong types (e.g., "5" instead of 5)
                    # or the file could be manually edited. Validate each field to prevent
                    # runtime TypeErrors downstream.
                    #
                    # Pattern: get with default -> type check -> cap/reset if invalid
                    loaded_workers = data.get("current_workers", self.initial_workers)
                    # Validate types from JSON to prevent type confusion
                    if not isinstance(loaded_workers, int):
                        loaded_workers = self.initial_workers
                    # Cap workers at initial value - can only decrease via adaptation, never increase
                    self.current_workers = min(loaded_workers, self.initial_workers)
                    self.temp_dirs_host_paths = data.get("temp_dirs_host_paths", [])
                    if not isinstance(self.temp_dirs_host_paths, list):
                        self.temp_dirs_host_paths = []
                    # Counter fields: must be non-negative integers
                    self.vpn_rotations_done = data.get("vpn_rotations_done", 0)
                    if not isinstance(self.vpn_rotations_done, int):
                        self.vpn_rotations_done = 0
                    self.worker_reductions_done = data.get("worker_reductions_done", 0)
                    if not isinstance(self.worker_reductions_done, int):
                        self.worker_reductions_done = 0
                    self.container_restarts_done = data.get("container_restarts_done", 0)
                    if not isinstance(self.container_restarts_done, int):
                        self.container_restarts_done = 0
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
        """
        Persist current crawl state to the JSON state file with durability guarantees.

        Writes worker count, temp directory list, adaptation counters, and current
        error counts to disk. Uses fsync() to ensure data reaches disk before
        returning, preventing data loss on power failure.

        Temp directory cleanup:
        - Validates each path still exists via is_dir()
        - Handles OSError (e.g., stale NFS mount) by skipping inaccessible paths
        - Deduplicates via canonical path resolution
        - Sorts by mtime so newest temp dir is last (used for resume discovery)

        Thread safety: Acquires _state_lock during the entire save operation.

        Error handling:
        - OSError on path stat: Logs warning, skips path
        - Write/fsync failure: Logs error, state file may be stale
        """
        with self._state_lock:
            # === Temp Directory Cleanup Pipeline ===
            # This block filters, deduplicates, and orders temp dirs before saving.
            # Purpose: Maintain a clean, ordered list for resume/WARC discovery.
            #
            # On OSError (e.g., Errno 107 stale mount): skip the path rather than
            # keeping stale references. If the mount recovers, temp dirs will be
            # re-discovered from logs/filesystem. Keeping inaccessible paths
            # indefinitely causes state bloat over long crawls.
            entries: list[tuple[float, str]] = []
            seen: set[str] = set()
            for raw in list(self.temp_dirs_host_paths):
                p = Path(raw)
                # Step 1: Validate path exists and is accessible
                try:
                    is_dir = p.is_dir()
                except OSError as exc:
                    logger.warning(
                        "save_persistent_state: could not stat temp dir %s: %s (skipping)", p, exc
                    )
                    is_dir = False  # Skip inaccessible paths; re-discover when mount recovers

                if not is_dir:
                    continue

                # Step 2: Resolve to canonical path for deduplication
                # (handles symlinks and relative paths)
                try:
                    canonical = str(p.resolve())
                except Exception:
                    canonical = str(p)

                # Step 3: Deduplicate using canonical paths
                if canonical in seen:
                    continue
                seen.add(canonical)

                # Step 4: Get mtime for chronological ordering
                try:
                    mtime = float(p.stat().st_mtime)
                except OSError:
                    mtime = 0.0
                entries.append((mtime, canonical))

            # Step 5: Sort by mtime (oldest first, newest last)
            # The newest temp dir (last in list) is used for resume config discovery.
            entries.sort(key=lambda item: item[0])
            self.temp_dirs_host_paths = [p for _, p in entries]
            # === Build State Snapshot for Persistence ===
            data = {
                "current_workers": self.current_workers,
                "initial_workers": self.initial_workers,
                "temp_dirs_host_paths": self.temp_dirs_host_paths,
                "vpn_rotations_done": self.vpn_rotations_done,
                "worker_reductions_done": self.worker_reductions_done,
                "container_restarts_done": self.container_restarts_done,
                # Include error counts for metrics visibility (these are runtime counts
                # that get reset on progress, but persisting them allows monitoring to
                # see current error situation)
                "error_counts": dict(self.error_counts),
            }
            # === Durable Write with fsync ===
            # Critical: Use fsync() to guarantee data reaches disk before returning.
            # Without fsync, data may sit in OS buffers and be lost on power failure.
            # This is essential for resume reliability - losing state means losing
            # the ability to resume a multi-hour crawl.
            try:
                with open(self.state_file_path, "w") as f:
                    json.dump(data, f, indent=2)
                    f.flush()  # Flush Python buffers to OS
                    os.fsync(f.fileno())  # Force OS to write to disk
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
        """
        Get validated list of existing temp directory paths.

        Filters temp_dirs_host_paths to only paths that currently exist and
        are directories. Removes invalid paths from internal state and triggers
        a save if any were removed.

        Returns:
            List of Path objects for existing temp directories.

        Thread safety:
            Acquires _state_lock during iteration and modification of the path
            list. Releases lock before calling save_persistent_state() to avoid
            deadlock (save also acquires the lock).

        Error handling:
            OSError during is_dir() check (e.g., stale mount) treats path as
            non-existent and removes it from state.
        """
        with self._state_lock:
            existing_paths = []
            changed = False
            current_paths = list(self.temp_dirs_host_paths)  # Copy list for safe iteration
            for p_str in current_paths:
                path = Path(p_str)
                try:
                    is_dir = path.is_dir()
                except OSError:
                    is_dir = False
                if is_dir:
                    existing_paths.append(path)
                else:
                    logger.warning(
                        f"Temp dir path from state does not exist or is not a directory: {p_str}. Removing from state."
                    )
                    if p_str in self.temp_dirs_host_paths:
                        self.temp_dirs_host_paths.remove(p_str)
                        changed = True
            # Save state only if invalid paths were removed (save_persistent_state acquires lock, so release first)
            paths_to_return = list(existing_paths)
        # Save outside lock to avoid deadlock (save_persistent_state also acquires lock)
        if changed:
            self.save_persistent_state()
        return paths_to_return

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
        """
        Update crawl progress metrics from parsed zimit statistics.

        Called by the monitor thread each time a "Crawl statistics" log entry
        is parsed. Tracks progress, calculates crawl rate, and resets error
        counts when forward progress is detected.

        Args:
            stats: Dict with crawl metrics from zimit logs. Expected keys:
                   "crawled", "total", "pending", "failed" (all optional)
            timestamp: Monotonic timestamp when stats were observed

        Rate calculation (progress_rate_ppm):
            Computes pages-per-minute rate based on delta between current and
            previous stats timestamps. Only updates if time delta > 1 second
            and crawled count increased. Resets to 0 if no progress for > 60s.

        Progress detection:
            Progress is detected when crawled count increases. On progress:
            - Updates last_progress_timestamp (used for stall detection)
            - Resets error counts (gives crawler fresh error budget)
        """
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
