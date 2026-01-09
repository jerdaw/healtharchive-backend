# archive_tool/main.py
import argparse
import datetime
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback  # Import traceback for detailed exception logging
from pathlib import Path
from queue import Empty, Queue
from typing import IO, Any, Dict, List, Optional, Tuple

# Use absolute imports within the package
from archive_tool import cli, constants, docker_runner, monitor, state, strategies, utils

# Setup logger for this module
# Note: Root logger is configured in main(), this just gets the specific logger
logger = logging.getLogger("website_archiver.main")

# --- Define stop_event at module level ---
stop_event = threading.Event()


# --- Global Signal Handling Setup ---
def signal_handler(signum, frame):
    """Handles SIGINT/SIGTERM for graceful shutdown attempt."""
    signal_name = signal.Signals(signum).name
    logger.warning(f"Signal {signal_name} received. Initiating shutdown sequence...")
    stop_event.set()  # Signal other threads to stop

    # Attempt to stop the current Docker container gracefully first
    if docker_runner.current_container_id:
        logger.info(
            f"Attempting graceful stop of container {docker_runner.current_container_id}..."
        )
        docker_runner.stop_docker_container(docker_runner.current_container_id)
    else:
        logger.info("No active container ID known to stop gracefully.")

    # Check the Popen process object for the docker run command
    if docker_runner.current_docker_process and docker_runner.current_docker_process.poll() is None:
        logger.info(
            f"Terminating main Docker process (PID: {docker_runner.current_docker_process.pid})..."
        )
        try:
            docker_runner.current_docker_process.terminate()
            # Wait a short time for termination
            docker_runner.current_docker_process.wait(timeout=10)
            logger.info("Docker process terminated.")
        except subprocess.TimeoutExpired:
            logger.warning("Docker process did not terminate gracefully, attempting kill...")
            try:
                docker_runner.current_docker_process.kill()
                docker_runner.current_docker_process.wait(timeout=5)  # Wait for kill
                logger.info("Docker process killed.")
            except Exception as e:
                logger.error(f"Failed to kill Docker process: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Error during Docker process termination: {e}", exc_info=True)
    else:
        logger.info("Main Docker process was not running or already terminated.")

    logger.warning("Shutdown sequence complete. Exiting application.")
    sys.exit(1)  # Ensure exit happens


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def _ensure_docker_process_exits(process: subprocess.Popen, *, reason: str) -> None:
    """
    Best-effort guard to avoid overlapping `docker run` processes when an
    adaptive strategy requests a restart.
    """
    if process.poll() is not None:
        return

    wait_seconds = 15
    logger.info(
        "Waiting up to %ss for docker process to exit (%s)...",
        wait_seconds,
        reason,
    )
    try:
        process.wait(timeout=wait_seconds)
        logger.info("Docker process exited.")
        return
    except subprocess.TimeoutExpired:
        logger.warning(
            "Docker process did not exit within %ss (%s); terminating...",
            wait_seconds,
            reason,
        )

    try:
        process.terminate()
        process.wait(timeout=10)
        logger.info("Docker process terminated.")
    except subprocess.TimeoutExpired:
        logger.warning("Docker process did not terminate gracefully; attempting kill...")
        try:
            process.kill()
            process.wait(timeout=5)
            logger.info("Docker process killed.")
        except Exception as e:
            logger.error(f"Failed to kill Docker process: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error during Docker process termination: {e}", exc_info=True)


def format_duration(seconds: float) -> str:
    """Formats duration in seconds into H:MM:SS or M:SS format."""
    try:
        secs = int(seconds)
        mins, secs = divmod(secs, 60)
        hours, mins = divmod(mins, 60)
        if hours > 0:
            return f"{hours}:{mins:02d}:{secs:02d}"
        else:
            return f"{mins}:{secs:02d}"
    except Exception:  # Catch potential errors with input
        return "??:??"


def _start_stage_log_drain(
    *,
    stage_name_with_attempt: str,
    host_output_dir: Path,
    stream: IO[str] | None,
    tee_to_stdout: bool,
) -> tuple[threading.Thread | None, Path | None]:
    """
    Drain docker-run stdout so the subprocess cannot block on a full pipe.

    This also writes crawl-stage logs under the job output directory so other
    components (stats parsing, temp-dir discovery) have stable artifacts.
    """
    if stream is None:
        return None, None

    timestamp = datetime.datetime.now().strftime(constants.TIMESTAMP_FORMAT)
    slug = stage_name_with_attempt.replace(" ", "_").lower()
    log_base = host_output_dir / f"archive_{slug}_{timestamp}"
    stdout_log_path = log_base.with_suffix(".stdout.log")
    combined_log_path = log_base.with_suffix(".combined.log")

    def _drain() -> None:
        try:
            host_output_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        stdout_file = None
        combined_file = None
        try:
            stdout_file = open(stdout_log_path, "a", encoding="utf-8")
            combined_file = open(combined_log_path, "a", encoding="utf-8")

            for line in stream:
                if tee_to_stdout:
                    try:
                        sys.stdout.write(line)
                        sys.stdout.flush()
                    except Exception:
                        pass
                try:
                    stdout_file.write(line)
                    stdout_file.flush()
                except Exception:
                    pass
                try:
                    combined_file.write(line)
                    combined_file.flush()
                except Exception:
                    pass
        finally:
            try:
                if stdout_file is not None:
                    stdout_file.close()
            except Exception:
                pass
            try:
                if combined_file is not None:
                    combined_file.close()
            except Exception:
                pass

    t = threading.Thread(
        target=_drain,
        name=f"StageLogDrain[{slug}]",
        daemon=True,
    )
    t.start()
    return t, combined_log_path


# Define synchronous runner here for final build stage
def run_final_build_stage_sync(
    stage_name: str,
    docker_image: str,
    host_output_dir: Path,
    crawl_state: state.CrawlState,
    script_args: argparse.Namespace,
    passthrough_args: List[str],
    required_args: Dict[str, Any],
    extra_args: List[str] = [],
) -> Tuple[str, Optional[Path]]:
    """Runs the final --warcs build stage using subprocess.run."""
    logger.info(f"--- Starting Stage: {stage_name} ---")
    timestamp = datetime.datetime.now().strftime(constants.TIMESTAMP_FORMAT)
    crawl_state.current_stage = stage_name
    stage_start_time = time.monotonic()

    final_build_args = list(passthrough_args)  # Start with filtered passthrough
    logger.debug(f"Base arguments for final build: {final_build_args}")

    # Add required --name back if not present (needed by build_zimit_args)
    name_present = any(a.startswith("--name") for a in final_build_args)
    if not name_present and "name" in required_args:
        final_build_args.extend(["--name", required_args["name"]])
        logger.debug(f"Added missing --name='{required_args['name']}' for build_zimit_args.")

    # Add the first seed URL as a workaround (as observed)
    if script_args.seeds:
        seed_arg_present = any(a == "--seeds" for a in final_build_args)
        if not seed_arg_present:
            final_build_args.extend(["--seeds", script_args.seeds[0]])
            logger.debug(f"Added first seed '{script_args.seeds[0]}' for final build workaround.")
    else:
        logger.error("Cannot determine seed URL needed for final build! Aborting.")
        return "failed", None

    # Add the --warcs argument via extra_args
    logger.debug(f"Extra arguments for final build (WARCs): {extra_args}")

    # Build the final zimit command list (is_final_build=True)
    zimit_args = docker_runner.build_zimit_args(
        final_build_args, required_args, crawl_state.current_workers, True, extra_args
    )
    logger.debug(f"Final zimit arguments list: {zimit_args}")

    # Build the full docker command list
    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{host_output_dir.resolve()}:{constants.CONTAINER_OUTPUT_DIR}",
        docker_image,
    ]
    docker_cmd.extend(zimit_args)  # Add zimit command and its args

    logger.info(f"Executing Final Build Docker command:\n{' '.join(docker_cmd)}")

    # Prepare log file paths
    log_base = host_output_dir / f"archive_{stage_name.replace(' ', '_').lower()}_{timestamp}"
    stdout_log_path = log_base.with_suffix(".stdout.log")
    stderr_log_path = log_base.with_suffix(".stderr.log")
    combined_log_path = log_base.with_suffix(".combined.log")
    logger.info(f"Final build logs will be saved to: {log_base}.*")

    stage_status = "failed"  # Default status
    temp_dir_host_path = None
    try:
        logger.info(f"Running {stage_name} synchronously...")
        process = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            check=False,  # Don't raise exception on non-zero exit
            encoding="utf-8",
            errors="replace",  # Handle potential encoding errors in output
        )
        run_duration = time.monotonic() - stage_start_time
        logger.info(
            f"{stage_name} subprocess finished with RC: {process.returncode} after {format_duration(run_duration)}"
        )

        # Capture and save logs regardless of success/failure
        stdout_content = process.stdout or ""
        stderr_content = process.stderr or ""
        combined_content = (
            f"--- STDOUT ---\n{stdout_content}\n"
            f"--- STDERR ---\n{stderr_content}\n"
            f"--- Return Code: {process.returncode} ---"
        )
        try:
            logger.debug(f"Attempting to write logs for {stage_name}...")
            with (
                open(stdout_log_path, "w", encoding="utf-8") as f1,
                open(stderr_log_path, "w", encoding="utf-8") as f2,
                open(combined_log_path, "w", encoding="utf-8") as f3,
            ):
                f1.write(stdout_content)
                f2.write(stderr_content)
                f3.write(combined_content)
            logger.info(f"Logs for {stage_name} saved successfully.")
        except IOError as e:
            logger.error(f"Failed to write log files for {stage_name}: {e}", exc_info=True)

        # Log snippets
        logger.info(f"--- {stage_name} STDOUT Snippet (Last 10 lines) ---")
        print("\n".join(stdout_content.strip().split("\n")[-10:]))
        logger.info(f"--- END {stage_name} STDOUT Snippet ---")
        if stderr_content.strip():
            logger.warning(f"--- {stage_name} STDERR Snippet (Last 10 lines) ---")
            print("\n".join(stderr_content.strip().split("\n")[-10:]))
            logger.warning(f"--- END {stage_name} STDERR Snippet ---")

        # Try to find the temp dir from the logs (may not exist for final build)
        logger.debug(f"Parsing combined log for temp dir: {combined_log_path}")
        temp_dir_host_path = utils.parse_temp_dir_from_log_file(combined_log_path, host_output_dir)
        if temp_dir_host_path:
            logger.info(f"Found temp dir path from final build logs: {temp_dir_host_path}")
        else:
            logger.info(
                "No specific temp dir path found in final build logs (this might be normal)."
            )

        # Determine status based on return code
        if process.returncode == 0:
            logger.info(f"Stage '{stage_name}' completed successfully (RC: 0).")
            stage_status = "success"
            # Double-check if the ZIM file was actually created
            final_zim_path = host_output_dir / f"{required_args['name']}.zim"
            if final_zim_path.exists():
                logger.info(f"Verified final ZIM file exists: {final_zim_path}")
            else:
                # This shouldn't happen if RC is 0, but good to check
                logger.warning(
                    f"Stage '{stage_name}' finished with RC 0, but final ZIM file not found at {final_zim_path}!"
                )
                # Keep status as success for now, but log warning.
        else:
            logger.error(
                f"Stage '{stage_name}' failed (RC: {process.returncode}). Check logs {log_base}.*"
            )
            stage_status = "failed"

    except FileNotFoundError:
        logger.error(
            f"Docker command not found during {stage_name}. Is Docker installed and in PATH?",
            exc_info=True,
        )
        stage_status = "failed"
    except Exception as e:
        logger.error(f"An unexpected exception occurred during {stage_name}: {e}", exc_info=True)
        logger.error(traceback.format_exc())  # Log full traceback
        stage_status = "failed"

    logger.info(f"--- Finished Stage: {stage_name} (Determined Status: {stage_status}) ---")
    return stage_status, temp_dir_host_path


# --- Main Orchestration Logic ---
def main():
    """Main function to orchestrate the website archiving."""
    script_args, zimit_passthrough_args = cli.parse_arguments()

    # Configure logging
    log_level_name = script_args.log_level.upper()
    log_level_int = getattr(logging, log_level_name, logging.INFO)
    logging.basicConfig(
        level=log_level_int,
        format=constants.LOG_FORMAT,
        # force=True might suppress warnings if logging is already configured elsewhere
        force=True,  # Overwrite any existing root logger config
    )
    # Ensure our main logger respects the level
    logger.setLevel(log_level_int)
    # Optionally configure log levels for other modules if needed
    logging.getLogger("website_archiver.docker").setLevel(log_level_int)
    logging.getLogger("website_archiver.monitor").setLevel(log_level_int)
    logging.getLogger("website_archiver.state").setLevel(log_level_int)
    logging.getLogger("website_archiver.strategies").setLevel(log_level_int)
    logging.getLogger("website_archiver.utils").setLevel(log_level_int)

    start_time_dt = datetime.datetime.now()
    logger.info(f"--- Enhanced Website Archiver Started: {start_time_dt:%Y-%m-%d %H:%M:%S} ---")
    logger.info(f"Log Level Set To: {log_level_name}")
    logger.debug(f"Parsed Script Arguments: {script_args}")
    logger.debug(f"Raw Passthrough Zimit Arguments: {zimit_passthrough_args}")

    logger.info("Step 1: Initial Checks and Setup")
    logger.debug("Checking Docker availability...")
    if not utils.check_docker():
        logger.critical(
            "Docker check failed. Please ensure Docker is installed, running, and accessible. Exiting."
        )
        sys.exit(1)
    logger.debug("Docker check successful.")

    logger.debug("Resolving and verifying output directory...")
    try:
        host_output_dir = Path(script_args.output_dir).resolve()
        logger.info(f"Using resolved output directory: {host_output_dir}")
        host_output_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("Output directory exists or was created.")
        # Test writability
        test_file = host_output_dir / f".writable_test_{os.getpid()}"
        logger.debug(f"Testing writability with temporary file: {test_file}")
        test_file.touch()
        test_file.unlink()
        logger.debug("Output directory is writable.")
    except Exception as e:
        logger.critical(
            f"Output directory '{script_args.output_dir}' is invalid or not writable: {e}",
            exc_info=True,
        )
        sys.exit(1)

    # Determine initial worker count, potentially overridden by passthrough args
    logger.debug(f"Script argument --initial-workers: {script_args.initial_workers}")
    initial_workers_arg = script_args.initial_workers
    for i, arg in enumerate(zimit_passthrough_args):
        if arg == "--workers":
            if i + 1 < len(zimit_passthrough_args) and zimit_passthrough_args[i + 1].isdigit():
                try:
                    passthrough_workers = int(zimit_passthrough_args[i + 1])
                    logger.info(
                        f"Found passthrough '--workers {passthrough_workers}', overriding initial workers setting."
                    )
                    initial_workers_arg = passthrough_workers
                    break
                except ValueError:
                    logger.warning(
                        f"Found '--workers' but value '{zimit_passthrough_args[i + 1]}' is not an integer. Ignoring."
                    )
        elif arg.startswith("--workers="):
            try:
                passthrough_workers = int(arg.split("=", 1)[1])
                logger.info(f"Found passthrough '{arg}', overriding initial workers setting.")
                initial_workers_arg = passthrough_workers
                break
            except (ValueError, IndexError):
                logger.warning(f"Found '{arg}' but could not parse integer value. Ignoring.")
    effective_initial_workers = max(1, initial_workers_arg)  # Ensure at least 1 worker
    logger.info(f"Effective initial worker count set to: {effective_initial_workers}")

    # In dry-run mode we stop after validation and a configuration summary,
    # without starting any Docker containers.
    if getattr(script_args, "dry_run", False):
        logger.info("Dry run requested; not starting crawl or Docker containers.")
        logger.info("Configuration summary:")
        logger.info("  Seeds: %s", ", ".join(script_args.seeds))
        logger.info("  Name: %s", script_args.name)
        logger.info("  Output directory: %s", host_output_dir)
        logger.info("  Effective initial workers: %s", effective_initial_workers)
        logger.info("  Monitoring enabled: %s", script_args.enable_monitoring)
        logger.info("  Adaptive workers enabled: %s", script_args.enable_adaptive_workers)
        logger.info("  VPN rotation enabled: %s", script_args.enable_vpn_rotation)
        if zimit_passthrough_args:
            logger.info(
                "  Zimit passthrough args: %s",
                " ".join(zimit_passthrough_args),
            )
        return

    logger.info("Step 2: Loading or Initializing Crawl State")
    try:
        crawl_state = state.CrawlState(host_output_dir, initial_workers=effective_initial_workers)
        logger.debug("CrawlState object initialized.")
    except Exception as e:
        logger.critical(f"Failed to initialize CrawlState: {e}", exc_info=True)
        sys.exit(1)

    logger.info("Step 3: Determining Run Mode (Fresh/Resume/Consolidate)")
    logger.debug("Scanning output directory for potentially relevant existing files...")
    # Note: CrawlState loading already happened, this finds files for resume decision
    can_resume_crawl = False
    has_prior_warcs = False
    warc_file_count = 0
    last_stats = None
    config_yaml_path: Optional[Path] = None  # Ensure type hint
    warc_files: List[Path] = []  # Initialize

    existing_temp_dirs = crawl_state.get_temp_dir_paths()  # Get validated paths from state
    discovered_temp_dirs = utils.discover_temp_dirs(host_output_dir)
    if discovered_temp_dirs:
        known = {p.resolve() for p in existing_temp_dirs}
        missing = [p for p in discovered_temp_dirs if p.resolve() not in known]
        for p in missing:
            crawl_state.add_temp_dir(p)
        if missing:
            existing_temp_dirs = crawl_state.get_temp_dir_paths()

    logger.debug(f"Temp directories managed by state: {existing_temp_dirs}")
    latest_temp_dir = existing_temp_dirs[-1] if existing_temp_dirs else None
    logger.debug(f"Latest temp directory from state (if any): {latest_temp_dir}")

    stable_resume = utils.find_stable_resume_config(host_output_dir)
    if stable_resume is not None:
        config_yaml_path = stable_resume
        logger.info("Found stable resume config YAML: %s", config_yaml_path)
        can_resume_crawl = True
    elif existing_temp_dirs:
        logger.debug(
            "Searching for newest resume config YAML across %d temp dir(s).",
            len(existing_temp_dirs),
        )
        config_yaml_path = utils.find_latest_config_yaml_in_temp_dirs(existing_temp_dirs)
        if config_yaml_path:
            logger.info(f"Found potential resume config YAML: {config_yaml_path}")
            can_resume_crawl = True
            persisted = utils.persist_resume_config(config_yaml_path, host_output_dir)
            if persisted is not None:
                config_yaml_path = persisted
                logger.info("Persisted resume config YAML to stable path: %s", persisted)
        else:
            logger.info("No resume config YAML found in tracked temp dirs.")
    else:
        logger.info("No existing temp directories tracked, cannot resume from YAML.")

    if existing_temp_dirs:
        logger.debug(
            f"Searching for WARC files in {len(existing_temp_dirs)} tracked temp directories..."
        )
        warc_files = utils.find_all_warc_files(existing_temp_dirs)
        if warc_files:
            has_prior_warcs = True
            warc_file_count = len(warc_files)
            logger.info(f"Found {warc_file_count} existing WARC file(s).")
            logger.debug(f"WARC files found: {[str(p) for p in warc_files]}")
        else:
            logger.info("No existing WARC files found in tracked temp directories.")
    else:
        logger.info("No existing temp directories tracked, no prior WARCs to find.")

    # Try to get stats from the last run if we might be resuming/consolidating
    if can_resume_crawl or has_prior_warcs:
        logger.debug("Attempting to parse statistics from previous logs...")
        try:
            log_files = list(host_output_dir.glob("archive_*.combined.log"))
            if log_files:
                latest_log_file = max(log_files, key=lambda p: p.stat().st_mtime)
                logger.debug(f"Parsing last stats from log file: {latest_log_file}")
                last_stats = utils.parse_last_stats_from_log(latest_log_file)
                if last_stats:
                    logger.debug(f"Parsed last stats: {last_stats}")
                else:
                    logger.debug("Could not parse stats from the latest log file.")
            else:
                logger.debug("No 'archive_*.combined.log' files found to parse stats from.")
        except Exception as e:
            logger.warning(f"Error finding or parsing last log file: {e}", exc_info=True)

    # Check for existing ZIM file and --overwrite flag
    final_zim_path = host_output_dir / f"{script_args.name}.zim"
    final_zim_exists = final_zim_path.exists()
    logger.debug(f"Checking for final ZIM file: {final_zim_path} (Exists: {final_zim_exists})")

    logger.info("--- Initial Run Status Determination ---")
    last_status_str = ""
    if last_stats:
        c = last_stats.get("crawled", "-")
        t = last_stats.get("total", "-")
        f = last_stats.get("failed", "-")
        last_status_str = f"(Last known status from logs: Crawled={c}/{t}, Failed={f})"
        logger.info(last_status_str)
    else:
        logger.info("No stats parsed from previous runs.")

    initial_run_mode = "Fresh Crawl"  # Default assumption

    if final_zim_exists:
        if not script_args.overwrite:
            logger.critical(
                f"Target ZIM file already exists: {final_zim_path}. Use --overwrite to allow replacing it. Exiting."
            )
            sys.exit(1)
        else:
            logger.warning(f"Target ZIM file exists and --overwrite specified: {final_zim_path}")
            logger.warning(
                "Resetting persistent state values (temp dirs, adaptation counts) for a completely fresh crawl due to --overwrite."
            )
            crawl_state._reset_persistent_state_values()  # Reset counts AND temp dir list
            crawl_state.save_persistent_state()  # Save the reset state
            # Invalidate previous findings
            can_resume_crawl = False
            has_prior_warcs = False
            warc_file_count = 0
            last_stats = None
            initial_run_mode = "Fresh Crawl (Overwrite)"
    # Check resume/consolidate possibilities only if not overwriting
    elif can_resume_crawl:
        logger.info(f"Run Mode: RESUME crawl using configuration: {config_yaml_path.name}")
        logger.info(
            f"Will also use {warc_file_count} previously found WARC file(s) in final build."
        )
        logger.info(
            f"Current State: Workers={crawl_state.current_workers}, VPN Rotations={crawl_state.vpn_rotations_done}, Worker Reductions={crawl_state.worker_reductions_done}"
        )
        initial_run_mode = "Resume Crawl"
    elif has_prior_warcs:
        logger.info(
            f"Run Mode: NEW crawl phase, but will consolidate {warc_file_count} previous WARC file(s)."
        )
        logger.info("No valid resume configuration (.yaml) found to continue previous queue.")
        logger.info(
            f"Current State: Workers={crawl_state.current_workers}, VPN Rotations={crawl_state.vpn_rotations_done}, Worker Reductions={crawl_state.worker_reductions_done}"
        )
        initial_run_mode = "New Crawl (with Consolidation)"
    else:  # No ZIM, no resume, no prior WARCs
        logger.info("Run Mode: FRESH crawl.")
        logger.info("No existing ZIM, no resume config, and no prior WARCs found.")
        crawl_state.reset_adaptation_counts()  # Ensure counts are 0 for a truly fresh start
        logger.info(
            f"Current State: Workers={crawl_state.current_workers}, VPN Rotations={crawl_state.vpn_rotations_done}, Worker Reductions={crawl_state.worker_reductions_done}"
        )
        initial_run_mode = "Fresh Crawl"
    logger.info("---------------------------------------")

    # --- Main Crawl/Resume Loop ---
    logger.info("Step 4: Entering Main Crawl/Resume Loop")
    current_stage_name = "Initial Crawl"
    if initial_run_mode == "Resume Crawl":
        current_stage_name = "Resume Crawl"
    elif initial_run_mode == "New Crawl (with Consolidation)":
        current_stage_name = "New Crawl Phase"  # More descriptive name

    stage_attempt = 1
    max_crawl_stages = 100  # Safety limit to prevent infinite loops
    final_status = "failed"  # Overall status, assume failure until success
    monitor_queue = Queue()  # Queue for monitor thread communication
    active_monitor: Optional[monitor.CrawlMonitor] = None
    required_run_args = {"seeds": script_args.seeds, "name": script_args.name}

    while stage_attempt <= max_crawl_stages and not stop_event.is_set():
        stage_name_with_attempt = f"{current_stage_name} - Attempt {stage_attempt}"
        logger.info(f"--- Starting Loop Iteration: Stage '{stage_name_with_attempt}' ---")
        crawl_state.current_stage = stage_name_with_attempt
        crawl_state.stage_start_time = time.monotonic()  # Record stage start time
        extra_run_args = []  # Arguments specific to this run (e.g., --config)
        stage_log_drain_thread: threading.Thread | None = None
        stage_combined_log_path: Path | None = None

        # --- Prepare arguments for this stage attempt ---
        logger.debug(f"Preparing arguments for stage '{stage_name_with_attempt}'")
        if current_stage_name in ["Resume Crawl"]:
            logger.debug("This is a Resume attempt. Need to find config YAML.")
            # Re-check for the latest YAML file path right before the attempt
            current_temp_dirs = crawl_state.get_temp_dir_paths()
            stable_resume = utils.find_stable_resume_config(host_output_dir)
            if stable_resume is not None:
                config_yaml_path = stable_resume
                logger.info("Using stable resume config YAML: %s", config_yaml_path)
            else:
                config_yaml_path = utils.find_latest_config_yaml_in_temp_dirs(current_temp_dirs)
                if config_yaml_path is not None:
                    logger.info("Found resume config YAML for this attempt: %s", config_yaml_path)
                    persisted = utils.persist_resume_config(config_yaml_path, host_output_dir)
                    if persisted is not None:
                        config_yaml_path = persisted
                        logger.info("Persisted resume config YAML to stable path: %s", persisted)

            if config_yaml_path:
                container_yaml = utils.host_to_container_path(config_yaml_path, host_output_dir)
                if container_yaml:
                    extra_run_args = ["--config", container_yaml]
                    logger.info(f"Will use container config path: {container_yaml}")
                else:
                    logger.critical(
                        f"Failed to convert host YAML path '{config_yaml_path}' to container path. Cannot resume. Exiting loop."
                    )
                    final_status = "failed_state_error"
                    break  # Exit outer loop
            else:
                logger.error(
                    "Resume requested for this stage, but could not find a valid config YAML file. Switching to 'New Crawl Phase'."
                )
                current_stage_name = "New Crawl Phase"  # Fallback if YAML disappears
                # Continue loop iteration to start as a new crawl phase

        logger.debug(f"Current worker count for this stage: {crawl_state.current_workers}")
        logger.debug(f"Base passthrough args: {zimit_passthrough_args}")
        logger.debug(f"Extra args for this run (e.g., --config): {extra_run_args}")

        zimit_args = docker_runner.build_zimit_args(
            zimit_passthrough_args,
            required_run_args,
            crawl_state.current_workers,
            False,  # Not final build
            extra_run_args,
        )
        logger.debug(f"Constructed zimit command arguments: {zimit_args}")

        # --- Start Docker Container ---
        logger.info(
            f"Attempting to start Docker container for stage '{stage_name_with_attempt}'..."
        )
        start_container_time = time.monotonic()
        try:
            docker_process, container_id = docker_runner.start_docker_container(
                script_args.docker_image, host_output_dir, zimit_args, script_args.name
            )
            call_duration = time.monotonic() - start_container_time
            logger.info(f"Call to start_docker_container completed in {call_duration:.2f} seconds.")
        except Exception as e:
            logger.critical(
                f"Unhandled exception during start_docker_container call: {e}",
                exc_info=True,
            )
            logger.error(traceback.format_exc())
            docker_process = None
            container_id = None

        if not docker_process:
            logger.critical(
                f"Failed to start Docker container for stage '{stage_name_with_attempt}'. Check Docker setup and image."
            )
            final_status = "docker_start_failed"
            break  # Exit outer loop

        stage_log_drain_thread, stage_combined_log_path = _start_stage_log_drain(
            stage_name_with_attempt=stage_name_with_attempt,
            host_output_dir=host_output_dir,
            stream=docker_process.stdout,
            tee_to_stdout=not script_args.enable_monitoring,
        )
        if stage_combined_log_path is not None:
            logger.info("Crawl stage logs: %s", stage_combined_log_path)

        logger.info(
            f"Docker process started (PID: {docker_process.pid}). Waiting for container ID..."
        )
        if container_id:
            logger.info(f"Successfully identified container ID: {container_id}")
        else:
            logger.warning(
                "Could not identify container ID after starting process. Monitoring will be disabled. Process might have exited quickly."
            )
            # Check if process exited immediately
            quick_exit_code = docker_process.poll()
            if quick_exit_code is not None:
                logger.error(
                    f"Docker process exited immediately with RC: {quick_exit_code}. Check Docker command/image/permissions."
                )
                # Go directly to post-stage processing
                final_rc = quick_exit_code
                stage_status = "failed"  # Assume failure if exited immediately
                # Skip inner loop logic below
            else:
                logger.warning(
                    "Process still running but container ID unknown. Proceeding without monitoring."
                )
                stage_status = "running_no_monitor"

        # --- Reset state for the new stage ---
        logger.debug("Resetting runtime state variables for the new stage...")
        crawl_state.status = "running"  # Initial status for this stage attempt
        crawl_state.exit_code = None
        # Reset progress tracking
        crawl_state.last_crawled_count = -1
        crawl_state.last_total_count = -1
        crawl_state.last_pending_count = -1
        crawl_state.last_failed_count = -1
        crawl_state.last_progress_timestamp = None
        crawl_state.last_stats_timestamp = None
        crawl_state.previous_crawled_count = -1  # For rate calculation
        crawl_state.previous_stats_timestamp = None  # For rate calculation
        crawl_state.progress_rate_ppm = 0.0
        # Reset error counts for this specific attempt
        crawl_state.reset_runtime_errors()

        # --- Start Monitor Thread (if possible and enabled) ---
        active_monitor = None
        if container_id and script_args.enable_monitoring:
            logger.info(f"Starting CrawlMonitor thread for container {container_id}...")
            try:
                active_monitor = monitor.CrawlMonitor(
                    container_id=container_id,
                    process_handle=docker_process,  # Pass the Popen object
                    state=crawl_state,
                    args=script_args,
                    output_queue=monitor_queue,
                    stop_event=stop_event,  # Share the main stop event
                )
                active_monitor.start()
                logger.info("CrawlMonitor thread started.")
            except Exception as e:
                logger.error(f"Failed to start CrawlMonitor thread: {e}", exc_info=True)
                # Continue without monitor? Or fail? Let's continue but warn heavily.
                logger.warning("Proceeding without monitoring due to thread start failure.")
        elif not script_args.enable_monitoring:
            logger.info("Monitoring is disabled via script arguments.")
        elif not container_id:
            logger.warning("Cannot start monitor: Container ID was not identified.")

        # --- Inner Monitoring Loop (Runs while Docker process is alive) ---
        logger.debug(f"Entering inner monitoring loop for stage '{stage_name_with_attempt}'...")
        last_print_time = 0
        print_interval_seconds = 60.0  # How often to print progress to console
        queue_check_timeout = 1.0  # How long to wait for monitor message

        # Initialize stage_status assuming it's running until proven otherwise
        stage_status = "running" if container_id else "running_no_monitor"

        while docker_process.poll() is None:
            logger.log(5, "Inner loop: Docker process still running.")  # Trace level logging
            if stop_event.is_set():
                logger.warning("Inner loop: Stop event detected. Breaking monitor loop.")
                stage_status = "stopped"  # Mark status as stopped due to signal/event
                break  # Exit inner loop

            # Check for messages from the monitor thread (if active)
            monitor_message = None
            if active_monitor:
                try:
                    monitor_message = monitor_queue.get(timeout=queue_check_timeout)
                    logger.log(
                        5, f"Inner loop: Received monitor message: {monitor_message}"
                    )  # Trace level
                except Empty:
                    logger.log(5, "Inner loop: No message from monitor queue.")  # Trace level
                    pass  # No message, continue loop
                except Exception as e:
                    logger.error(
                        f"Inner loop: Error getting message from monitor queue: {e}",
                        exc_info=True,
                    )
                    # Continue loop, but something might be wrong with the queue/monitor
            else:  # No active monitor, sleep briefly to avoid busy-waiting
                logger.log(5, "Inner loop: No active monitor, sleeping.")  # Trace level
                time.sleep(queue_check_timeout)

            # Process monitor message if received
            if monitor_message:
                event_status = monitor_message.get("status")
                event_reason = monitor_message.get("reason", "N/A")
                logger.debug(
                    f"Processing monitor event: status='{event_status}', reason='{event_reason}'"
                )

                if event_status == "progress":
                    logger.log(5, "Progress event received (handled by timed print).")
                    pass  # Progress is printed periodically below

                elif event_status == "stalled" or event_status == "error":
                    print()  # Newline before logging intervention message
                    logger.warning(
                        f"Intervention Triggered! Condition: {event_status.upper()}, Reason: {event_reason}"
                    )

                    # --- Try Adaptive Strategies ---
                    logger.info("Attempting adaptive strategies...")
                    adaptation_performed_type = None  # Track *which* adaptation worked, if any

                    # 1. Try Worker Reduction (Requires Container Restart)
                    # Check this only if no adaptation has worked yet
                    if adaptation_performed_type is None and script_args.enable_adaptive_workers:
                        logger.info("Attempting strategy: Worker Reduction")
                        try:
                            # Pass crawl_state and script_args
                            if strategies.attempt_worker_reduction(crawl_state, script_args):
                                logger.info(
                                    "Worker reduction strategy SUCCESSFUL. Stopping current container to restart."
                                )
                                adaptation_performed_type = "worker_reduction"  # Mark which one
                                stage_status = "stopped_for_adaptation"
                                _ensure_docker_process_exits(
                                    docker_process, reason="worker reduction"
                                )
                                # Break inner loop ONLY for adaptations requiring restart
                                break  # EXIT INNER LOOP
                            else:
                                logger.info(
                                    "Worker reduction strategy skipped or not applicable (e.g., already at min workers)."
                                )
                        except Exception as e_adapt:
                            logger.error(
                                f"Error during worker reduction strategy: {e_adapt}",
                                exc_info=True,
                            )

                    # 2. Try VPN Rotation (Does NOT require Container Restart Anymore)
                    # Check this only if no adaptation has worked yet
                    if adaptation_performed_type is None and script_args.enable_vpn_rotation:
                        logger.info("Attempting strategy: VPN Rotation (Live)")
                        try:
                            # Pass crawl_state and script_args
                            if strategies.attempt_vpn_rotation(
                                crawl_state, script_args, stop_event
                            ):
                                logger.info(
                                    "VPN rotation (Live) strategy SUCCESSFUL. Container continues running."
                                )
                                adaptation_performed_type = "vpn_rotation"  # Mark which one
                                # === IMPORTANT: DO NOT BREAK ===
                                # Let the inner loop continue running
                            else:
                                logger.info(
                                    "VPN rotation (Live) strategy skipped or failed (e.g., limits, frequency, command failure)."
                                )
                        except Exception as e_adapt:
                            logger.error(
                                f"Error during VPN rotation (Live) strategy: {e_adapt}",
                                exc_info=True,
                            )

                    # 3. Container Restart (Requires Container Restart)
                    #
                    # Only apply when explicitly enabled, and prefer using the
                    # monitor's stall detection rather than reacting to transient error storms.
                    if (
                        adaptation_performed_type is None
                        and getattr(script_args, "enable_adaptive_restart", False)
                        and event_status == "stalled"
                    ):
                        logger.info("Attempting strategy: Container Restart")
                        try:
                            if strategies.attempt_container_restart(crawl_state, script_args):
                                logger.info(
                                    "Container restart strategy SUCCESSFUL. Stopping current container to restart."
                                )
                                adaptation_performed_type = "container_restart"
                                stage_status = "stopped_for_adaptation"
                                _ensure_docker_process_exits(
                                    docker_process, reason="container restart"
                                )
                                break  # EXIT INNER LOOP
                            else:
                                logger.info(
                                    "Container restart strategy skipped or not applicable (e.g., max restarts reached)."
                                )
                        except Exception as e_adapt:
                            logger.error(
                                f"Error during container restart strategy: {e_adapt}",
                                exc_info=True,
                            )

                    # --- Handle Outcome Based on Adaptation Type ---
                    if adaptation_performed_type == "worker_reduction":
                        # Action already taken (break above), log is just for clarity
                        logger.debug("Worker reduction requires restart, inner loop broken.")
                        pass  # Loop will exit due to break above

                    elif adaptation_performed_type == "vpn_rotation":
                        # Live VPN rotation was performed. Continue the loop.
                        logger.info(
                            "Live VPN rotation performed. Continuing monitoring of the running container."
                        )
                        # No backoff needed here as an adaptation was performed.
                        # Reset errors already happened in the strategy function.
                        # Just continue the while loop.
                        pass

                    elif adaptation_performed_type is None:
                        # This block runs if NEITHER worker reduction nor VPN rotation triggered/succeeded
                        logger.warning(
                            "No adaptation strategy successfully executed or applicable for this condition."
                        )
                        backoff_minutes = script_args.backoff_delay_minutes
                        if backoff_minutes > 0:
                            logger.warning(
                                f"Applying backoff delay of {backoff_minutes} minutes before potentially retrying..."
                            )
                            backoff_seconds = backoff_minutes * 60
                            # Wait for the duration OR until the stop event is set
                            stop_event_triggered_during_wait = stop_event.wait(
                                timeout=backoff_seconds
                            )
                            if stop_event_triggered_during_wait:
                                logger.warning(
                                    "Stop event received during backoff delay. Breaking inner loop."
                                )
                                stage_status = "stopped"
                                break  # Exit inner loop
                            else:
                                logger.info(
                                    "Backoff delay complete. Resetting runtime errors and continuing monitoring."
                                )
                                crawl_state.reset_runtime_errors()  # Give it another chance after delay
                        else:  # No backoff configured
                            logger.warning(
                                "No adaptation performed and no backoff delay configured. Continuing monitoring."
                            )
                            # Reset errors to potentially avoid immediate re-trigger if condition was transient
                            crawl_state.reset_runtime_errors()
                            # Let the loop continue; if the condition persists, it will trigger again.

                elif event_status == "error" and "message" in monitor_message:
                    logger.error(f"Monitor thread reported an error: {monitor_message['message']}")
                    # Consider if this should cause the stage to fail? For now, just log it.

            # --- Periodic Progress Printing ---
            now = time.monotonic()
            if script_args.enable_monitoring and (now - last_print_time > print_interval_seconds):
                if crawl_state.last_crawled_count >= 0:  # Only print if we have valid stats
                    try:
                        stage_elapsed_str = format_duration(
                            now - (crawl_state.stage_start_time or now)
                        )
                        percent_str = "-"
                        total = (
                            crawl_state.last_total_count
                            if crawl_state.last_total_count is not None
                            else "-"
                        )
                        if (
                            isinstance(total, int)
                            and total > 0
                            and isinstance(crawl_state.last_crawled_count, int)
                        ):
                            percent = (crawl_state.last_crawled_count / total) * 100
                            percent_str = f"{percent:.1f}%"

                        pending = (
                            crawl_state.last_pending_count
                            if crawl_state.last_pending_count is not None
                            else "-"
                        )
                        failed = (
                            crawl_state.last_failed_count
                            if crawl_state.last_failed_count is not None
                            else "-"
                        )
                        max_vpn = (
                            script_args.max_vpn_rotations
                            if script_args.enable_vpn_rotation
                            else "N/A"
                        )
                        max_wred = (
                            script_args.max_worker_reductions
                            if script_args.enable_adaptive_workers
                            else "N/A"
                        )

                        status_line = (
                            f"[{stage_name_with_attempt} | {stage_elapsed_str}] "
                            f"Crawled: {crawl_state.last_crawled_count}/{total} ({percent_str}) | "
                            f"Rate: {crawl_state.progress_rate_ppm:.1f} ppm | Pending: {pending} | Failed: {failed} | "
                            f"Workers: {crawl_state.current_workers} | VRot: {crawl_state.vpn_rotations_done}/{max_vpn} | "
                            f"WRed: {crawl_state.worker_reductions_done}/{max_wred} | "
                            f"Errs(T/H/O): {crawl_state.error_counts['timeout']}/{crawl_state.error_counts['http']}/{crawl_state.error_counts['other']}"
                        )
                        term_width = shutil.get_terminal_size((80, 24))[0]
                        # Print to console, ensuring it fits and clears previous line
                        print(f"{status_line:<{term_width}}", end="\r", flush=True)
                        last_print_time = now
                    except Exception as print_e:
                        logger.warning(
                            f"Error formatting/printing progress line: {print_e}",
                            exc_info=False,
                        )  # Avoid spamming logs
                else:
                    logger.debug("Skipping progress print: initial stats not yet received.")
                    last_print_time = now  # Update time anyway to avoid spamming this message

        # --- End Inner Monitoring Loop (Docker process exited or loop broken) ---
        logger.debug(f"Exited inner monitoring loop for stage '{stage_name_with_attempt}'.")

        print()  # Ensure newline after final progress print or if loop breaks suddenly
        logger.info(f"Processing end of stage '{stage_name_with_attempt}'...")

        if stage_log_drain_thread is not None:
            stage_log_drain_thread.join(timeout=5.0)

        # --- Stop Monitor Thread Gracefully ---
        if active_monitor and active_monitor.is_alive():
            logger.info("Stopping CrawlMonitor thread...")
            active_monitor.stop_event.set()  # Signal thread to stop
            try:
                active_monitor.join(timeout=5.0)  # Wait for thread to finish
                if active_monitor.is_alive():
                    logger.warning("CrawlMonitor thread did not stop gracefully within timeout.")
                else:
                    logger.info("CrawlMonitor thread stopped.")
            except Exception as e_join:
                logger.error(f"Error joining CrawlMonitor thread: {e_join}", exc_info=True)
        elif active_monitor:
            logger.debug("CrawlMonitor thread was already stopped.")
        else:
            logger.debug("No active monitor thread to stop for this stage.")

        # --- Get Final Status of the Docker Process ---
        final_rc = docker_process.returncode  # Get final RC after loop exit
        if final_rc is None:
            # This case should ideally be handled by the signal handler or if poll() was checked one last time
            logger.warning(
                "Docker process return code is None after loop exit, checking poll() again."
            )
            final_rc = docker_process.poll()
            if final_rc is None and stage_status not in [
                "stopped",
                "stopped_for_adaptation",
            ]:
                logger.error(
                    "Docker process still seems to be running unexpectedly after monitoring loop!? Forcing status to 'failed'."
                )
                stage_status = "failed"  # Override status if process didn't terminate as expected
                # Attempt to terminate it again forcefully? Handled by signal handler mostly.
        crawl_state.exit_code = final_rc
        logger.info(
            f"Docker process for '{stage_name_with_attempt}' ended. Final RC: {final_rc}. Loop Exit Status: {stage_status}"
        )

        # --- Add Temp Dir To State ---
        logger.debug("Attempting to find and record temp directory for this stage...")
        # Try finding from logs first, then fallback scan
        temp_dir_host_path = None
        stage_log_is_file = False
        if stage_combined_log_path is not None:
            try:
                stage_log_is_file = stage_combined_log_path.is_file()
            except OSError as exc:
                logger.warning(
                    "Failed to stat stage combined log path %s: %s", stage_combined_log_path, exc
                )
                stage_log_is_file = False

        if stage_combined_log_path is not None and stage_log_is_file:
            logger.debug(
                "Trying to parse temp dir from stage combined log: %s", stage_combined_log_path
            )
            temp_dir_host_path = utils.parse_temp_dir_from_log_file(
                stage_combined_log_path, host_output_dir
            )
        else:
            log_pattern = f"archive_{current_stage_name.replace(' ', '_').lower()}_*.combined.log"
            try:
                log_candidates = list(host_output_dir.glob(log_pattern))
            except OSError as exc:
                logger.warning(
                    "Failed to scan stage logs under %s (%s): %s", host_output_dir, log_pattern, exc
                )
                log_candidates = []

            def _safe_log_mtime(path: Path) -> float:
                try:
                    return path.stat().st_mtime
                except OSError as exc:
                    logger.warning("Failed to stat stage log %s: %s", path, exc)
                    return 0.0

            log_files = sorted(log_candidates, key=_safe_log_mtime, reverse=True)
            if log_files:
                latest_stage_log = log_files[0]
                logger.debug(f"Trying to parse temp dir from latest log: {latest_stage_log}")
                temp_dir_host_path = utils.parse_temp_dir_from_log_file(
                    latest_stage_log, host_output_dir
                )

        if not temp_dir_host_path:
            logger.warning("Could not parse temp dir from logs, falling back to directory scan.")
            temp_dir_host_path = utils.find_latest_temp_dir_fallback(host_output_dir)

        if temp_dir_host_path:
            logger.info(f"Identified temp directory for this stage: {temp_dir_host_path}")
            crawl_state.add_temp_dir(temp_dir_host_path)  # Adds to state and saves
        else:
            # This is problematic if the stage should have created one
            logger.error(
                f"CRITICAL: Could not determine temp directory created by stage '{stage_name_with_attempt}'. State might be incomplete."
            )

        # --- Determine Final Stage Status (if not already set by loop break) ---
        logger.debug(
            f"Determining final status for stage '{stage_name_with_attempt}' (Current loop status: {stage_status}, RC: {final_rc})"
        )
        if stage_status in [
            "running",
            "running_no_monitor",
        ]:  # If loop finished because process exited normally
            if final_rc == 0:
                logger.info("Docker process finished with RC 0. Marking stage as SUCCESS.")
                stage_status = "success"
            elif final_rc in constants.ACCEPTABLE_CRAWLER_EXIT_CODES:
                logger.warning(
                    f"Docker process finished with acceptable non-zero RC {final_rc} (e.g., limit hit). Marking stage as SUCCESS."
                )
                stage_status = "success"
            else:
                logger.error(
                    f"Docker process finished with unexpected RC {final_rc}. Marking stage as FAILED."
                )
                stage_status = "failed"
        elif stage_status == "stopped":
            logger.warning(
                f"Stage '{stage_name_with_attempt}' was stopped by external signal/event."
            )
        elif stage_status == "stopped_for_adaptation":
            logger.info(f"Stage '{stage_name_with_attempt}' was stopped internally for adaptation.")
        # else: stage_status already set to 'failed' if intervention failed etc.

        # --- Decide Next Action Based on Stage Status ---
        logger.info(f"Deciding next action based on stage status: '{stage_status}'")
        if stage_status == "success":
            logger.info(
                f"Stage '{stage_name_with_attempt}' completed successfully. Proceeding to final build."
            )
            final_status = "pending_final_build"  # Set overall status flag
            break  # Exit outer loop -> Go to final build stage

        elif stage_status == "stopped_for_adaptation":
            logger.info(
                f"Stage '{stage_name_with_attempt}' stopped for adaptation. Will attempt to resume."
            )
            current_stage_name = "Resume Crawl"  # Ensure next stage is explicitly resume
            # Do NOT increment stage_attempt, we are retrying the *same logical step* after adaptation
            logger.info(f"Next stage will be '{current_stage_name}' (Attempt {stage_attempt}).")
            # Optional: Apply backoff delay even after successful adaptation?
            backoff_minutes = script_args.backoff_delay_minutes
            if backoff_minutes > 0:
                logger.info(
                    f"Applying post-adaptation backoff delay of {backoff_minutes} minutes..."
                )
                if stop_event.wait(timeout=backoff_minutes * 60):
                    logger.warning(
                        "Stop event received during post-adaptation backoff. Exiting outer loop."
                    )
                    final_status = "stopped"
                    break  # Exit outer loop
            # Continue outer loop for the resume attempt

        elif stage_status == "stopped":
            logger.warning(
                "Process stopped by external signal. Cannot continue. Exiting outer loop."
            )
            final_status = "stopped"  # Mark overall status
            break  # Exit outer loop

        else:  # status == "failed" (or potentially other unexpected states)
            logger.error(f"Stage '{stage_name_with_attempt}' FAILED (RC: {final_rc}).")
            if stage_attempt >= max_crawl_stages:
                logger.critical(
                    f"Maximum number of stage attempts ({max_crawl_stages}) reached after failure. Aborting."
                )
                final_status = "failed_max_attempts"
                break  # Exit outer loop
            else:
                logger.info("Attempting to recover by trying a 'Resume Crawl' in the next stage.")
                current_stage_name = (
                    "Resume Crawl"  # Try resuming even if previous stage wasn't resume
                )
                stage_attempt += 1  # Increment attempt counter
                logger.info(f"Next stage will be '{current_stage_name}' (Attempt {stage_attempt}).")
                # Apply backoff delay after failure
                backoff_minutes = script_args.backoff_delay_minutes
                if backoff_minutes > 0:
                    logger.info(
                        f"Applying post-failure backoff delay of {backoff_minutes} minutes..."
                    )
                    if stop_event.wait(timeout=backoff_minutes * 60):
                        logger.warning(
                            "Stop event received during post-failure backoff. Exiting outer loop."
                        )
                        final_status = "stopped"
                        break  # Exit outer loop
                # Continue outer loop for the resume attempt

        logger.debug(
            f"--- End of Outer Loop Iteration {stage_attempt - 1 if stage_status == 'failed' else stage_attempt} ---"
        )
    # --- End Outer Loop ---
    logger.info(
        f"Exited main crawl/resume loop. Final determined status before consolidation: '{final_status}'"
    )

    # --- Stage 3: Final WARC Consolidation ---
    logger.info("Step 5: Checking if Final WARC Consolidation is needed")
    if final_status == "pending_final_build" and not stop_event.is_set():
        logger.info("Crawl/Resume phase successful. Proceeding to final WARC consolidation stage.")

        # If Docker/Zimit wrote temp dirs with root-only permissions, the host
        # process may not be able to discover WARCs for the final build. When
        # enabled, relax permissions before scanning so WARC discovery works.
        if script_args.relax_perms:
            try:
                utils.relax_permissions(host_output_dir, crawl_state.get_temp_dir_paths())
            except Exception as exc:
                logger.warning(f"Failed to relax permissions before WARC discovery: {exc}")

        logger.info("Finding all WARC files from all tracked temporary directories...")
        warc_host_paths = utils.find_all_warc_files(crawl_state.get_temp_dir_paths())
        if not warc_host_paths:
            logger.error(
                "CRITICAL: No WARC files found in any tracked temp directories. Cannot perform final build."
            )
            final_status = "failed_no_warcs"
        else:
            logger.info(f"Found {len(warc_host_paths)} WARC files to consolidate.")
            logger.debug(f"Host paths: {[str(p) for p in warc_host_paths]}")

            logger.info("Converting WARC host paths to container paths...")
            container_warc_paths = []
            conversion_failed = False
            for p in warc_host_paths:
                cp = utils.host_to_container_path(p, host_output_dir)
                if cp is None:
                    logger.error(
                        f"Failed to convert WARC host path '{p}' to container path. Aborting final build."
                    )
                    conversion_failed = True
                    break
                logger.debug(f"Converted '{p}' -> '{cp}'")
                container_warc_paths.append(cp)

            if conversion_failed:
                final_status = "failed_warc_path_conversion"
            else:
                container_warc_paths_str = ",".join(container_warc_paths)
                logger.debug(f"Comma-separated container WARC paths: {container_warc_paths_str}")

                logger.info("Filtering passthrough arguments for final build...")
                final_build_base_args = utils.filter_args_for_final_run(zimit_passthrough_args)
                logger.debug(f"Filtered base args for final build: {final_build_base_args}")

                extra_args_final = ["--warcs", container_warc_paths_str]
                # Required args for the build stage function are just 'name'
                final_required_args = {"name": script_args.name}

                # Run the synchronous final build stage
                build_status, final_build_temp_dir = run_final_build_stage_sync(
                    stage_name="Final Build from WARCs",
                    docker_image=script_args.docker_image,
                    host_output_dir=host_output_dir,
                    crawl_state=crawl_state,
                    script_args=script_args,  # Needed for seed workaround
                    passthrough_args=final_build_base_args,  # Pass filtered args
                    required_args=final_required_args,  # Pass just name
                    extra_args=extra_args_final,  # Pass --warcs list
                )
                final_status = build_status  # Update overall status based on build result

                # Add the temp dir from the final build stage to state for potential cleanup
                if final_build_temp_dir:
                    logger.info(
                        f"Adding temp directory from final build stage to state: {final_build_temp_dir}"
                    )
                    crawl_state.add_temp_dir(final_build_temp_dir)

    elif stop_event.is_set():
        logger.warning("Skipping final build stage because stop event was set.")
        if final_status == "pending_final_build":  # If we were going to build but got stopped
            final_status = "stopped"
    else:  # Crawl/Resume loop failed or was stopped before reaching success
        logger.error(
            f"Skipping final build stage because the main crawl/resume phase did not succeed (Status: '{final_status}')."
        )
        # Keep the existing failure/stopped status

    # --- Post-Processing & Cleanup ---
    logger.info("Step 6: Final Summary and Cleanup")
    logger.info("--- Archiving Process Summary ---")
    end_time_dt = datetime.datetime.now()
    total_duration = end_time_dt - start_time_dt
    logger.info(f"Archiver Started: {start_time_dt:%Y-%m-%d %H:%M:%S}")
    logger.info(f"Archiver Finished: {end_time_dt:%Y-%m-%d %H:%M:%S}")
    logger.info(f"Total Duration: {total_duration}")
    logger.info(f"Final Overall Status: {final_status.upper()}")

    temp_dir_paths = crawl_state.get_temp_dir_paths()  # Get final list of valid paths
    if script_args.relax_perms:
        try:
            utils.relax_permissions(host_output_dir, temp_dir_paths)
        except Exception as exc:
            logger.warning(f"Failed to relax permissions: {exc}")

    if final_status == "success":
        final_zim_path = host_output_dir / f"{script_args.name}.zim"
        logger.info("Overall process SUCCEEDED.")
        if final_zim_path.exists():
            logger.info(f"Final ZIM file should be available at: {final_zim_path}")
            try:
                logger.info(
                    f"ZIM File Size: {final_zim_path.stat().st_size / (1024 * 1024):.2f} MB"
                )
            except Exception:
                pass
        else:
            logger.warning("Success reported, but final ZIM file check failed post-build!")

        if script_args.cleanup:
            logger.info(
                "Cleanup enabled (--cleanup). Removing temporary directories and state file..."
            )
            utils.cleanup_temp_dirs(temp_dir_paths, crawl_state.state_file_path)
        else:
            logger.info("Cleanup disabled. Temporary files and state remain:")
            for p in temp_dir_paths:
                logger.info(f"  - Temp Dir: {p}")
            if crawl_state.state_file_path.exists():
                logger.info(f"  - State File: {crawl_state.state_file_path}")
        logger.info("--- Archiver Finished ---")
        sys.exit(0)  # Exit with success code
    else:
        logger.error(f"Overall process FAILED or was STOPPED (Final Status: {final_status}).")
        logger.info("Temporary files and state have been kept for debugging:")
        for p in temp_dir_paths:
            logger.info(f"  - Temp Dir: {p}")
        if crawl_state.state_file_path.exists():
            logger.info(f"  - State File: {crawl_state.state_file_path}")
        logger.error("Review logs carefully to diagnose the failure.")
        logger.info("--- Archiver Finished ---")
        sys.exit(1)  # Exit with error code


if __name__ == "__main__":
    # Setup logging basic config here temporarily for potential early errors
    # before main() fully configures it based on args
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    try:
        main()
    except Exception as e:
        logger.critical(f"A critical unhandled exception occurred in main: {e}", exc_info=True)
        logger.critical(traceback.format_exc())
        sys.exit(2)  # Different exit code for uncaught exception
