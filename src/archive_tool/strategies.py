# archive_tool/strategies.py
import argparse
import logging
import shlex
import shutil  # For which()
import threading
import time

# Use absolute imports
from archive_tool.constants import CONTAINER_STOP_SETTLE_DELAY_SEC
from archive_tool.state import CrawlState
from archive_tool.utils import execute_external_command

# We no longer need stop_docker_container or current_container_id for VPN rotation
# They are imported conditionally for worker reduction

logger = logging.getLogger("website_archiver.strategies")


def attempt_worker_reduction(state: CrawlState, args: argparse.Namespace) -> bool:
    """
    Attempts to reduce worker count if enabled and possible.
    This strategy *DOES* involve stopping and restarting the container.
    Returns True if adaptation requiring restart was successfully initiated.
    """
    # --- Import needed here since it's conditionally used ---
    from archive_tool.docker_runner import current_container_id, stop_docker_container

    # --- End Import ---

    if not args.enable_adaptive_workers:
        logger.debug("Adaptive workers strategy disabled.")
        return False
    if state.worker_reductions_done >= args.max_worker_reductions:
        logger.warning(
            f"Adaptive workers: Max reductions ({args.max_worker_reductions}) already performed."
        )
        return False
    if state.current_workers <= args.min_workers:
        logger.info(
            f"Adaptive workers: Already at minimum workers ({args.min_workers}). Cannot reduce further."
        )
        return False

    logger.warning("Attempting adaptive worker reduction...")
    # --- Action: Stop the container ---
    logger.info("Stopping Docker container for worker reduction...")
    stop_docker_container(current_container_id)  # Requires container ID from docker_runner
    logger.info("Waiting briefly after container stop...")
    time.sleep(CONTAINER_STOP_SETTLE_DELAY_SEC)

    # --- Update State ---
    new_worker_count = max(args.min_workers, state.current_workers - 1)
    state.current_workers = new_worker_count
    state.worker_reductions_done += 1
    state.reset_runtime_errors()  # Reset errors after adaptation
    state.save_persistent_state()  # Save updated state

    logger.info(f"Reduced worker count to {state.current_workers}. Main loop will handle restart.")
    # Return True signifies an action requiring restart was taken
    return True


def attempt_container_restart(state: CrawlState, args: argparse.Namespace) -> bool:
    """
    Attempts to restart the current container without changing worker count.

    Intended as a last-resort self-healing strategy when a crawl appears stalled and
    worker reduction / VPN rotation are not applicable.

    Returns True if a restart requiring the main loop to re-run the stage was initiated.
    """
    from archive_tool.docker_runner import current_container_id, stop_docker_container

    if not getattr(args, "enable_adaptive_restart", False):
        logger.debug("Adaptive restart strategy disabled.")
        return False

    max_restarts = int(getattr(args, "max_container_restarts", 0) or 0)
    if max_restarts <= 0:
        logger.info("Adaptive restart: max container restarts is 0; restart skipped.")
        return False

    if state.container_restarts_done >= max_restarts:
        logger.warning(
            f"Adaptive restart: Max restarts ({max_restarts}) already performed for this run."
        )
        return False

    logger.warning("Attempting adaptive container restart...")
    logger.info("Stopping Docker container for restart...")
    stop_docker_container(current_container_id)
    logger.info("Waiting briefly after container stop...")
    time.sleep(CONTAINER_STOP_SETTLE_DELAY_SEC)

    state.container_restarts_done += 1
    state.reset_runtime_errors()
    state.save_persistent_state()

    logger.info(
        "Container restart requested (Count: %s/%s). Main loop will handle restart.",
        state.container_restarts_done,
        max_restarts,
    )
    return True


def attempt_vpn_rotation(
    state: CrawlState, args: argparse.Namespace, stop_event: threading.Event
) -> bool:
    """
    Attempts IP rotation via VPN **without stopping the container**.
    Checks limits, frequency, and command existence.
    Returns True if VPN command executed successfully.
    """
    if not args.enable_vpn_rotation:
        logger.debug("VPN rotation strategy disabled.")
        return False
    if state.vpn_rotations_done >= args.max_vpn_rotations:
        logger.warning(f"VPN Rotation: Max rotations ({args.max_vpn_rotations}) already performed.")
        return False
    # Ensure connect command is provided
    if not args.vpn_connect_command:
        logger.error("VPN rotation strategy enabled, but --vpn-connect-command is not specified.")
        return False
    # Explicitly ignore disconnect command argument based on new requirement
    if args.vpn_disconnect_command:
        logger.warning(
            "Ignoring --vpn-disconnect-command as strategy now relies solely on connect command."
        )

    # --- Check Connect Command Existence ---
    logger.debug("Checking VPN connect command existence...")
    try:
        connect_cmd_parts = shlex.split(args.vpn_connect_command)
        if not connect_cmd_parts:
            raise ValueError("Connect command is empty after parsing.")
        connect_cmd_base = connect_cmd_parts[0]
        if not shutil.which(connect_cmd_base):
            logger.error(
                f"VPN connect command '{connect_cmd_base}' not found in PATH. Cannot rotate VPN."
            )
            return False
        logger.debug(f"VPN connect command '{connect_cmd_base}' found.")
    except Exception as e:
        logger.error(f"Error parsing or checking VPN connect command: {e}", exc_info=True)
        return False

    # --- Frequency Check ---
    now = time.monotonic()
    required_interval_sec = args.vpn_rotation_frequency_minutes * 60
    logger.debug(f"Checking VPN rotation frequency (min interval: {required_interval_sec}s)")
    if state.last_vpn_rotation_timestamp is not None and required_interval_sec > 0:
        time_since_last = now - state.last_vpn_rotation_timestamp
        if time_since_last < required_interval_sec:
            wait_more_sec = required_interval_sec - time_since_last
            logger.info(
                f"VPN rotation frequency limit not met. Last rotation was {time_since_last:.0f}s ago. Need to wait {wait_more_sec:.0f} more seconds."
            )
            return False  # Indicate adaptation cannot proceed yet
        logger.debug("VPN rotation frequency requirement met.")
    else:
        logger.debug("VPN rotation frequency check skipped (first rotation or interval is 0).")

    # --- Attempt Rotation (Without Stopping Container) ---
    logger.warning("Attempting VPN rotation while container remains running...")
    vpn_command_success = False

    # --- Step 1: Execute VPN Connect Command ---
    # Assumes 'nordvpn connect <region>' handles disconnect/reconnect implicitly.
    logger.info(f"Executing VPN connect/rotate command: {args.vpn_connect_command}")
    vpn_command_success = execute_external_command(args.vpn_connect_command, "VPN Connect/Rotate")

    # --- Step 2: Update State if Command Succeeded ---
    if vpn_command_success:
        logger.info("VPN connect/rotate command executed successfully.")
        # Add a delay to allow network changes to potentially propagate
        # This might help the running container pick up the new route
        post_vpn_delay = 15  # Tunable delay in seconds
        logger.info(
            f"Waiting {post_vpn_delay} seconds for network to potentially stabilize after VPN change..."
        )
        # Use stop_event.wait for the delay so shutdown is still responsive
        stop_event.wait(timeout=post_vpn_delay)
        if stop_event.is_set():
            logger.warning(
                "Stop event triggered during post-VPN delay. Aborting further state updates."
            )
            return False  # Don't signal success if stopped

        logger.info("Post-VPN delay complete. Updating state.")
        # Update state
        state.vpn_rotations_done += 1
        state.last_vpn_rotation_timestamp = now  # Record time *before* command execution
        state.reset_runtime_errors()  # Reset errors, hoping the new IP helps
        state.save_persistent_state()  # Save updated state

        logger.info(
            f"VPN rotation attempt finished (Count: {state.vpn_rotations_done}/{args.max_vpn_rotations}). Container continues running."
        )
        return True  # Signal that adaptation was performed
    else:
        logger.error("VPN connect/rotate command failed. See command output above for details.")
        # Do not update counts or timestamp on failure
        return False  # Indicate adaptation failed
