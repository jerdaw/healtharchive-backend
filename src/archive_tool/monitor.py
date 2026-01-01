# archive_tool/monitor.py
import argparse  # Keep for type hint in __init__
import json
import logging
import os
import re
import signal  # For killpg
import subprocess
import threading
import time
from queue import Queue

from .constants import HTTP_ERROR_PATTERNS, TIMEOUT_PATTERNS

# Use absolute imports within the package
from .state import CrawlState

# DO NOT import current_docker_process - use passed handle instead

logger = logging.getLogger("website_archiver.monitor")


class CrawlMonitor(threading.Thread):
    """Monitors a running zimit container via docker logs."""

    # Add process_handle: subprocess.Popen to __init__
    def __init__(
        self,
        container_id: str,
        process_handle: subprocess.Popen,
        state: CrawlState,
        args: argparse.Namespace,
        output_queue: Queue,
        stop_event: threading.Event,
    ):
        super().__init__(name="CrawlMonitorThread", daemon=True)
        self.container_id = container_id
        self.process_handle = process_handle  # Store the Popen object from main thread
        self.state = state
        self.args = args  # Keep args for thresholds etc.
        self.output_queue = output_queue
        self.stop_event = stop_event  # Use the shared stop event

        # Compile regex patterns once
        self.stats_pattern = re.compile(r'"context":"crawlStatus".*"details":({.*?})}')
        self.timeout_pattern = re.compile("|".join(TIMEOUT_PATTERNS), re.IGNORECASE)
        self.http_error_pattern = re.compile("|".join(HTTP_ERROR_PATTERNS))

    def run(self):
        """Fetches logs, parses them, updates state, checks for stalls/errors, reports progress."""
        logger.info(f"Starting monitoring for container {self.container_id}...")
        last_check_time = time.monotonic()
        last_progress_report_time = time.monotonic()
        processed_lines = 0
        log_process = None
        preexec_fn = os.setsid if hasattr(os, "setsid") else None  # For killing process group later

        # Add a small delay before the very first check
        time.sleep(2)

        try:
            log_cmd = ["docker", "logs", "-f", "--tail", "50", self.container_id]
            log_process = subprocess.Popen(
                log_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Combine streams
                text=True,
                encoding="utf-8",
                errors="replace",
                preexec_fn=preexec_fn,
            )

            if log_process.stdout is None:
                logger.error("Failed to get stdout stream from docker logs.")
                self.output_queue.put(
                    {"status": "error", "message": "Failed to start log monitoring"}
                )
                return

            while not self.stop_event.is_set():
                # Check the Popen object passed during initialization
                main_process_poll_result = self.process_handle.poll()  # Use self.process_handle

                # --- Modified Check ---
                if main_process_poll_result is not None:
                    # Popen object indicates the 'docker run' command might have exited
                    logger.info(
                        f"Monitor check: main process poll() returned {main_process_poll_result}."
                    )
                    # Double-check with `docker ps` using the known container ID
                    container_still_running = False
                    try:
                        ps_check_cmd = [
                            "docker",
                            "ps",
                            "-q",
                            "--filter",
                            f"id={self.container_id}",
                        ]
                        logger.debug(f"Running docker ps check: {' '.join(ps_check_cmd)}")
                        ps_output = subprocess.check_output(
                            ps_check_cmd, text=True, timeout=5
                        ).strip()
                        if ps_output and self.container_id in ps_output:
                            container_still_running = True
                            # Log less aggressively, maybe only if persists? Or just debug.
                            logger.debug(
                                f"Container {self.container_id} IS still running via 'docker ps' despite poll()={main_process_poll_result}."
                            )
                            # Don't break; continue monitoring logs assuming poll() was misleading early on.
                        else:
                            logger.info(
                                f"Confirmed via 'docker ps': Container {self.container_id} is NOT running (Output: '{ps_output}')."
                            )
                    except Exception as ps_e:
                        logger.error(
                            f"Error running 'docker ps' check: {ps_e}. Assuming container has exited."
                        )
                        # Assume container gone if check fails
                        container_still_running = False

                    # Only break the monitoring loop if docker ps confirms container is gone
                    if not container_still_running:
                        logger.info("Main Docker process appears to have exited. Stopping monitor.")
                        break  # Break outer while loop

                # If poll() was None, or if we decided not to break above, read logs
                try:
                    # Use non-blocking read if possible? No, readline() should block until data or EOF
                    line = log_process.stdout.readline() if log_process.stdout else None
                except Exception as read_e:
                    logger.warning(f"Error reading log stream: {read_e}. Assuming logs ended.")
                    line = None

                if not line:
                    # Check if the log process itself exited
                    if log_process.poll() is not None:
                        logger.info("Docker logs stream ended.")
                        break
                    else:
                        # If main process is still running but no log line, wait briefly
                        if self.process_handle.poll() is None:  # Check main process again
                            time.sleep(0.1)
                            continue
                        else:  # Main process poll showed exit, but log stream was slow? Break.
                            logger.info(
                                "Log stream empty and main process poll() was non-None. Exiting monitor."
                            )
                            break

                processed_lines += 1
                now = time.monotonic()
                self._parse_log_line(line.strip(), now)  # Update state based on line

                # Check stall/error conditions periodically
                # Use configured interval, default 30s
                if now - last_check_time > self.args.monitor_interval_seconds:
                    if self._check_stall_and_error_conditions(
                        now
                    ):  # Check returns True if intervention needed
                        # Signal sent, reset check time
                        last_check_time = now
                        last_progress_report_time = (
                            now  # Also reset progress report to avoid immediate duplicate message
                        )
                        # Let main loop handle intervention based on queue message
                    else:
                        # No condition met, just update check time
                        last_check_time = now

                # Report progress periodically (independent of stall check)
                # Use configured interval, default 30s
                if now - last_progress_report_time > self.args.monitor_interval_seconds:
                    # Only put progress update if no stall/error signal was just sent
                    # Check queue size? No, just rely on timestamp reset above.
                    self.output_queue.put({"status": "progress"})  # Signal main thread
                    last_progress_report_time = now

        except FileNotFoundError:
            logger.error("Docker command not found for log monitoring.")
            self.output_queue.put({"status": "error", "message": "Docker not found for logs"})
        except Exception as e:
            # Only report error if stop wasn't requested
            if not self.stop_event.is_set():
                logger.exception(f"Error in monitoring thread: {e}")
                self.output_queue.put({"status": "error", "message": f"Monitoring failed: {e}"})
        finally:
            # Clean up the 'docker logs -f' process
            if log_process and log_process.poll() is None:
                logger.debug("Terminating docker logs process...")
                try:
                    # Kill process group on Unix-like systems, just terminate on others
                    use_pg = hasattr(os, "killpg") and hasattr(log_process, "pid") and preexec_fn
                    if use_pg:
                        os.killpg(os.getpgid(log_process.pid), signal.SIGTERM)
                        logger.debug(f"Sent SIGTERM to process group {os.getpgid(log_process.pid)}")
                    else:
                        log_process.terminate()
                        logger.debug(f"Sent SIGTERM to process {log_process.pid}")

                    log_process.wait(timeout=5)
                    logger.debug("Docker logs process terminated.")
                except Exception as e_kill:
                    logger.warning(f"Could not cleanly terminate docker logs process: {e_kill}")
                    # Ensure it's killed if terminate failed
                    if log_process.poll() is None:
                        try:
                            log_process.kill()
                            logger.warning("Force-killed docker logs process.")
                        except Exception as e_force_kill:
                            logger.error(f"Force kill of log process failed: {e_force_kill}")
            logger.info("Monitoring thread stopped.")

    def _parse_log_line(self, line: str, timestamp: float):
        """Parses a single log line for stats or errors."""
        if not line:
            return
        try:
            log_data = json.loads(line)
            message = log_data.get("message", "")
            context = log_data.get("context", "")
            details = log_data.get("details", {})
            level = log_data.get("logLevel", "info")

            if (
                context == "crawlStatus"
                and message == "Crawl statistics"
                and isinstance(details, dict)
            ):
                self.state.update_progress(details, timestamp)
                return

            if context == "pageStatus" and message == "Page Load Failed: will retry":
                error_msg = details.get("msg", "")
                if self.timeout_pattern.search(error_msg):
                    self.state.record_error("timeout", timestamp)
                    logger.warning(f"Timeout reported by pageStatus: {line[:200]}...")
                elif self.http_error_pattern.search(error_msg):
                    self.state.record_error("http", timestamp)
                    logger.warning(f"HTTP/Network error reported by pageStatus: {line[:200]}...")
                else:
                    self.state.record_error("other", timestamp)
                    logger.warning(f"Unknown page load failure: {line[:200]}...")
                return

            if level in ["error", "warn"]:
                full_log_check = line
                if self.timeout_pattern.search(message) or self.timeout_pattern.search(
                    full_log_check
                ):
                    self.state.record_error("timeout", timestamp)
                    logger.warning(f"Timeout detected in logs: {line[:200]}...")
                elif self.http_error_pattern.search(message) or self.http_error_pattern.search(
                    full_log_check
                ):
                    self.state.record_error("http", timestamp)
                    logger.warning(f"HTTP/Network error detected in logs: {line[:200]}...")
                elif level == "error":
                    self.state.record_error("other", timestamp)
                    logger.error(f"Generic error detected in logs: {line[:200]}...")
        except json.JSONDecodeError:
            if self.timeout_pattern.search(line):
                self.state.record_error("timeout", timestamp)
                logger.warning(f"Timeout detected in non-JSON log: {line[:200]}...")
            elif self.http_error_pattern.search(line):
                self.state.record_error("http", timestamp)
                logger.warning(f"HTTP/Network error detected in non-JSON log: {line[:200]}...")
        except Exception as e:
            logger.error(f"Error parsing log line: '{line[:100]}...' - {e}")

    def _check_stall_and_error_conditions(self, now: float) -> bool:
        """
        Checks conditions and signals main thread via output_queue.
        Returns True if an intervention signal was sent, False otherwise.
        """
        if not self.args.enable_monitoring:
            return False
        signaled = False
        # Stall Check
        #
        # Prefer the "pending > 0" signal when present, but treat negative
        # values as "unknown" so we still detect stalls if Zimit log schema
        # changes or omits pending counts.
        pending = self.state.last_pending_count
        pending_unknown = pending is None or pending < 0
        if (
            self.state.last_progress_timestamp is not None
            and self.state.last_crawled_count >= 0
            and (pending_unknown or pending > 0)
        ):
            time_since_progress = now - self.state.last_progress_timestamp
            if time_since_progress > self.args.stall_timeout_minutes * 60:
                logger.warning(
                    f"Stall Condition Met: No progress for {time_since_progress:.1f} seconds."
                )
                self.output_queue.put({"status": "stalled", "reason": "timeout"})
                self.state.last_progress_timestamp = now  # Reset timestamp
                self.state.reset_runtime_errors()  # Reset errors
                signaled = True

        # Error Threshold Checks
        if not signaled and self.state.error_counts["timeout"] >= self.args.error_threshold_timeout:
            logger.warning(f"Error Condition Met: {self.state.error_counts['timeout']} timeouts.")
            self.output_queue.put({"status": "error", "reason": "timeout_threshold"})
            self.state.reset_runtime_errors()
            signaled = True
        if not signaled and self.state.error_counts["http"] >= self.args.error_threshold_http:
            logger.warning(
                f"Error Condition Met: {self.state.error_counts['http']} HTTP/network errors."
            )
            self.output_queue.put({"status": "error", "reason": "http_threshold"})
            self.state.reset_runtime_errors()
            signaled = True
        return signaled
