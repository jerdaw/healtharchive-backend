# archive_tool/docker_runner.py
import logging
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .constants import (
    CONTAINER_OUTPUT_DIR,
    DEFAULT_DOCKER_CPU_LIMIT,
    DEFAULT_DOCKER_MEMORY_LIMIT,
    DOCKER_COMMUNICATE_TIMEOUT_SEC,
    DOCKER_CONTAINER_ID_MAX_RETRIES,
    DOCKER_CONTAINER_ID_RETRY_DELAY_SEC,
    DOCKER_FORCE_KILL_TIMEOUT_SEC,
    DOCKER_PS_TIMEOUT_SEC,
    DOCKER_STOP_COMMAND_TIMEOUT_SEC,
    DOCKER_STOP_GRACE_PERIOD_SEC,
)

logger = logging.getLogger("website_archiver.docker")

# Globals for signal handling and potentially stopping from strategies
current_docker_process: Optional[subprocess.Popen] = None
current_container_id: Optional[str] = None


def build_docker_run_cmd(
    *,
    docker_image: str,
    host_output_dir: Path,
    zimit_args: List[str],
    label: str | None = None,
    docker_shm_size: str | None = None,
    user: str | None = None,
    memory_limit: str | None = None,
    cpu_limit: str | None = None,
) -> List[str]:
    """
    Build the `docker run` command for executing zimit in a container.

    Args:
        docker_image: Docker image name/tag to run (e.g., "ghcr.io/openzim/zimit")
        host_output_dir: Host directory to mount as /output in the container
        zimit_args: List of arguments to pass to the zimit command inside the container
        label: Optional Docker label for tracking the container (format: "key=value")
        docker_shm_size: Optional shared memory size (e.g., "512m", "1g") for browser stability
        user: Optional user:group to run as (e.g., "0:0" for root when relax_perms is needed)
        memory_limit: Optional memory limit (e.g., "4g") to prevent OOM-killing host
        cpu_limit: Optional CPU limit (e.g., "1.5") to prevent CPU saturation

    Returns:
        Complete command list ready for subprocess execution.

    Resource limits (when set):
        --memory: Hard memory limit
        --memory-swap: Set equal to memory to disable swap
        --memory-swappiness: Set to 10 to minimize I/O thrash
        --cpus: CPU quota limit
    """
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{host_output_dir.resolve()}:{CONTAINER_OUTPUT_DIR}",
    ]
    if label:
        cmd.extend(["--label", str(label)])
    if docker_shm_size:
        cmd.extend(["--shm-size", str(docker_shm_size)])
    if user:
        cmd.extend(["--user", str(user)])
    # Resource limits to prevent runaway crawlers from OOM-killing or CPU-saturating the host
    if memory_limit:
        cmd.extend(["--memory", str(memory_limit)])
        # Set swap equal to memory to prevent thrashing (effectively disabling swap)
        cmd.extend(["--memory-swap", str(memory_limit)])
        # Reduce swappiness to minimize I/O thrash
        cmd.extend(["--memory-swappiness", "10"])
    if cpu_limit:
        cmd.extend(["--cpus", str(cpu_limit)])
    cmd.append(docker_image)
    cmd.extend(zimit_args)
    return cmd


def build_zimit_args(
    base_zimit_args: List[str],
    required_args: Dict[str, Any],
    current_workers: int,
    is_final_build: bool,
    extra_args: List[str] = [],
) -> List[str]:
    """
    Construct the argument list for the zimit command inside the container.

    This function assembles zimit arguments from multiple sources while handling
    special cases like worker count management and final build mode.

    Args:
        base_zimit_args: User-provided passthrough arguments from CLI
        required_args: Dict with required args like "seeds" and "name"
        current_workers: Current worker count (may be reduced by adaptive strategies)
        is_final_build: True if building final ZIM from WARCs (skips seeds, workers)
        extra_args: Additional args like ["--config", path] for resume or ["--warcs", paths]

    Returns:
        Complete zimit command list starting with "zimit".

    Special handling:
        - Seeds: Joined with comma for zimit's --seeds format (skipped in final build)
        - Workers: Extracted from base_args and replaced with current_workers (skipped in final build)
        - --keep: Always added if not present (preserves temp artifacts for resume)
        - --output: Always set to CONTAINER_OUTPUT_DIR, removing any user-provided value
    """
    # (Keep existing implementation)
    zimit_args = ["zimit"]
    if "seeds" in required_args and not is_final_build:
        # zimit expects multiple seeds as a single comma-separated string.
        # Passing repeated --seeds flags results in only the last seed being used.
        seeds = required_args["seeds"]
        seeds_csv = ",".join(seeds) if isinstance(seeds, (list, tuple)) else str(seeds)
        zimit_args.extend(["--seeds", seeds_csv])
    if "name" in required_args:
        zimit_args.extend(["--name", required_args["name"]])

    temp_passthrough = []
    skip_next = False
    for i, arg in enumerate(base_zimit_args):
        if skip_next:
            skip_next = False
            continue
        if arg == "--workers":
            if i + 1 < len(base_zimit_args) and not base_zimit_args[i + 1].startswith("-"):
                skip_next = True
            continue
        if arg.startswith("--workers="):
            continue
        temp_passthrough.append(arg)

    if not is_final_build:
        zimit_args.extend(["--workers", str(current_workers)])

    zimit_args.extend(temp_passthrough)
    zimit_args.extend(extra_args)

    if "--keep" not in zimit_args:
        zimit_args.append("--keep")

    temp_args = [arg for arg in zimit_args if not arg.startswith("--output")]
    zimit_args = temp_args + ["--output", str(CONTAINER_OUTPUT_DIR)]
    return zimit_args


def start_docker_container(
    docker_image: str,
    host_output_dir: Path,
    zimit_args: List[str],
    run_name: str,  # Add run_name for label
    *,
    relax_perms: bool = False,
    docker_shm_size: str | None = None,
    docker_memory_limit: str | None = DEFAULT_DOCKER_MEMORY_LIMIT,
    docker_cpu_limit: str | None = DEFAULT_DOCKER_CPU_LIMIT,
) -> Tuple[Optional[subprocess.Popen], Optional[str]]:
    """
    Start a Docker container running zimit asynchronously.

    Creates a unique job label for container identification, starts the container
    with Popen for non-blocking operation, and retrieves the container ID via
    docker ps (with retries).

    Args:
        docker_image: Docker image to run
        host_output_dir: Host directory to mount as /output
        zimit_args: Complete zimit command arguments
        run_name: Base name for the job label (combined with UUID for uniqueness)
        relax_perms: If True, run container as root (0:0) for permission relaxation
        docker_shm_size: Optional shared memory size for browser stability
        docker_memory_limit: Memory limit (default from env or "4g")
        docker_cpu_limit: CPU limit (default from env or "1.5")

    Returns:
        Tuple of (Popen process, container_id or None).
        If container start fails completely, returns (None, None).
        If container starts but ID cannot be determined, returns (process, None).

    Side effects:
        Sets module-level current_docker_process and current_container_id globals
        for signal handler access during graceful shutdown.
    """
    global current_docker_process, current_container_id
    current_docker_process = None
    current_container_id = None

    job_id = f"archive-{run_name}-{uuid.uuid4().hex[:8]}"
    docker_cmd = build_docker_run_cmd(
        docker_image=docker_image,
        host_output_dir=host_output_dir,
        zimit_args=zimit_args,
        label=f"archive_job={job_id}",
        docker_shm_size=docker_shm_size,
        user="0:0" if relax_perms else None,
        memory_limit=docker_memory_limit,
        cpu_limit=docker_cpu_limit,
    )

    logger.info(f"Executing Docker command (Job ID: {job_id}):\n{' '.join(docker_cmd)}")
    try:
        process = subprocess.Popen(
            docker_cmd,
            stdin=subprocess.DEVNULL,  # <-- ADD THIS LINE
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        current_docker_process = process

        # Retry getting container ID
        container_id = None
        for attempt in range(DOCKER_CONTAINER_ID_MAX_RETRIES):
            time.sleep(DOCKER_CONTAINER_ID_RETRY_DELAY_SEC)
            container_id = get_container_id_by_label(job_id)
            if container_id:
                break
            else:
                logger.debug(f"Attempt {attempt + 1}: Container ID not found yet for job {job_id}.")

        if container_id:
            current_container_id = container_id
            logger.info(f"Identified running container ID: {container_id}")
        else:
            logger.warning(
                f"Could not identify running container ID using label {job_id} after multiple attempts."
            )
            if process.poll() is not None:
                logger.error(
                    f"Docker process exited prematurely with code {process.returncode}. Check Docker setup or image."
                )
                try:  # Try to get output if it exited fast
                    stdout_quick, stderr_quick = process.communicate(
                        timeout=DOCKER_COMMUNICATE_TIMEOUT_SEC
                    )
                    logger.error(f"Quick Exit STDOUT: {stdout_quick}")
                    logger.error(
                        f"Quick Exit STDERR: {stderr_quick}"
                    )  # Will be empty due to redirect
                except Exception:
                    pass
        return process, container_id

    except FileNotFoundError:
        logger.error("Docker command failed. Is Docker installed and in your PATH?")
        return None, None
    except Exception as e:
        logger.exception(f"Failed to start Docker container: {e}")
        return None, None


# --- Functions get_container_id_by_label and stop_docker_container remain unchanged ---


def get_container_id_by_label(job_id: str) -> Optional[str]:
    """
    Find a running container's ID using its unique job label.

    Uses `docker ps -q --filter label=archive_job={job_id}` to locate the
    container. This is more reliable than parsing Popen output since docker run
    with --rm doesn't print the container ID.

    Args:
        job_id: The unique job identifier set as the archive_job label value

    Returns:
        Container ID string if found, None otherwise.

    Note:
        Returns only the first container ID if multiple match (shouldn't happen
        with UUID-based job IDs). Handles CalledProcessError gracefully since
        the container may not exist yet during startup.
    """
    try:
        ps_cmd = ["docker", "ps", "-q", "--filter", f"label=archive_job={job_id}"]
        logger.debug(f"Running command to find container: {' '.join(ps_cmd)}")
        process = subprocess.run(
            ps_cmd, capture_output=True, text=True, check=True, timeout=DOCKER_PS_TIMEOUT_SEC
        )
        container_id = process.stdout.strip()
        if container_id:
            first_id = container_id.split("\n")[0].strip()
            if first_id:
                logger.debug(f"Found container ID {first_id} for job {job_id}")
                return first_id
        return None
    except subprocess.CalledProcessError as e:
        # Expected if container not found yet or command fails slightly
        logger.debug(f"Error running 'docker ps' command (may be temporary): {e.stderr.strip()}")
        return None
    except subprocess.TimeoutExpired:
        logger.error("Timed out running 'docker ps' command.")
        return None
    except FileNotFoundError:
        logger.error("Docker command not found while trying to get container ID.")
        return None
    except Exception as e:
        logger.error(f"Unexpected error getting container ID by label {job_id}: {e}")
        return None


def stop_docker_container(container_id: Optional[str]):
    """
    Gracefully stop a Docker container, with fallback to force kill.

    Issues `docker stop -t {grace_period}` and waits for clean shutdown.
    If stop fails or times out, escalates to _force_kill_container().

    Args:
        container_id: Container ID to stop. If None, uses current_container_id global.

    The grace period (DOCKER_STOP_GRACE_PERIOD_SEC, default 90s) allows zimit
    to complete in-progress page captures and flush WARC data before SIGKILL.

    Side effects:
        Clears current_container_id global if the stopped container matches it.
    """
    global current_container_id
    target_id = container_id or current_container_id
    if not target_id:
        logger.warning("No container ID available to stop.")
        return

    logger.info(
        f"Attempting to stop Docker container {target_id} (will wait up to {DOCKER_STOP_GRACE_PERIOD_SEC}s)..."
    )
    try:
        # Use -t flag for stop timeout
        subprocess.run(
            ["docker", "stop", "-t", str(DOCKER_STOP_GRACE_PERIOD_SEC), target_id],
            check=True,
            capture_output=True,
            timeout=DOCKER_STOP_COMMAND_TIMEOUT_SEC,
        )
        logger.info(f"Successfully stopped container {target_id}.")
    except subprocess.CalledProcessError as e:
        stderr_str = e.stderr.decode(errors="replace")
        if "No such container" in stderr_str:
            logger.warning(f"Container {target_id} not found. Assumed stopped.")
        else:
            logger.error(f"Failed to stop container {target_id}: {stderr_str}")
            _force_kill_container(target_id)
    except subprocess.TimeoutExpired:
        logger.error(f"Timed out waiting for container {target_id} to stop command to complete.")
        _force_kill_container(target_id)
    except FileNotFoundError:
        logger.error("Docker command not found.")
    except Exception as e:
        logger.error(f"Error stopping container {target_id}: {e}")
        _force_kill_container(target_id)
    finally:
        if target_id == current_container_id:
            current_container_id = None


def _force_kill_container(container_id: str) -> None:
    """
    Best-effort fallback when `docker stop` fails or hangs.

    Note: `docker run --rm` sets AutoRemove; once killed/stopped, the daemon
    will remove the container automatically.
    """
    try:
        logger.warning(f"Attempting to force-kill container {container_id}...")
        subprocess.run(
            ["docker", "kill", container_id],
            check=True,
            capture_output=True,
            timeout=DOCKER_FORCE_KILL_TIMEOUT_SEC,
        )
        logger.warning(f"Force-killed container {container_id}.")
    except subprocess.CalledProcessError as e:
        stderr_str = e.stderr.decode(errors="replace")
        if "No such container" in stderr_str:
            logger.warning(f"Container {container_id} not found during kill. Assumed stopped.")
        else:
            logger.error(f"Failed to kill container {container_id}: {stderr_str}")
    except subprocess.TimeoutExpired:
        logger.error(f"Timed out waiting for container {container_id} to be killed.")
    except FileNotFoundError:
        logger.error("Docker command not found while attempting docker kill.")
    except Exception as exc:
        logger.error(f"Unexpected error force-killing container {container_id}: {exc}")
