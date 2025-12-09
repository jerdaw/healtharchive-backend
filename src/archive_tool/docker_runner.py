# archive_tool/docker_runner.py
import logging
import subprocess
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
import os  # Import os for DEVNULL

from .constants import CONTAINER_OUTPUT_DIR

logger = logging.getLogger("website_archiver.docker")

# Globals for signal handling and potentially stopping from strategies
current_docker_process: Optional[subprocess.Popen] = None
current_container_id: Optional[str] = None


def build_zimit_args(
    base_zimit_args: List[str],
    required_args: Dict[str, Any],
    current_workers: int,
    is_final_build: bool,
    extra_args: List[str] = [],
) -> List[str]:
    """Constructs the argument list specifically for the zimit command."""
    # (Keep existing implementation)
    zimit_args = ["zimit"]
    if "seeds" in required_args and not is_final_build:
        for seed_url in required_args["seeds"]:
            zimit_args.extend(["--seeds", seed_url])
    if "name" in required_args:
        zimit_args.extend(["--name", required_args["name"]])

    temp_passthrough = []
    skip_next = False
    for i, arg in enumerate(base_zimit_args):
        if skip_next:
            skip_next = False
            continue
        if arg == "--workers":
            if i + 1 < len(base_zimit_args) and not base_zimit_args[i + 1].startswith(
                "-"
            ):
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
) -> Tuple[Optional[subprocess.Popen], Optional[str]]:
    """Starts the Docker container asynchronously using Popen and a unique label."""
    global current_docker_process, current_container_id
    current_docker_process = None
    current_container_id = None

    job_id = f"archive-{run_name}-{uuid.uuid4().hex[:8]}"

    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{host_output_dir.resolve()}:{CONTAINER_OUTPUT_DIR}",
        "--label",
        f"archive_job={job_id}",
        docker_image,
    ]
    docker_cmd.extend(zimit_args)

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
        for attempt in range(5):
            time.sleep(2)
            container_id = get_container_id_by_label(job_id)
            if container_id:
                break
            else:
                logger.debug(
                    f"Attempt {attempt+1}: Container ID not found yet for job {job_id}."
                )

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
                    stdout_quick, stderr_quick = process.communicate(timeout=1)
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
    """Tries to find the container ID based on the unique job label."""
    try:
        ps_cmd = ["docker", "ps", "-q", "--filter", f"label=archive_job={job_id}"]
        logger.debug(f"Running command to find container: {' '.join(ps_cmd)}")
        process = subprocess.run(
            ps_cmd, capture_output=True, text=True, check=True, timeout=10
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
        logger.debug(
            f"Error running 'docker ps' command (may be temporary): {e.stderr.strip()}"
        )
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
    """Attempts to gracefully stop a Docker container with increased timeout."""
    global current_container_id
    target_id = container_id or current_container_id
    if not target_id:
        logger.warning("No container ID available to stop.")
        return

    logger.info(
        f"Attempting to stop Docker container {target_id} (will wait up to 90s)..."
    )
    stop_timeout_seconds = "90"  # Increased grace period
    stop_command_timeout = 100  # Slightly longer than stop timeout
    try:
        # Use -t flag for stop timeout
        subprocess.run(
            ["docker", "stop", "-t", stop_timeout_seconds, target_id],
            check=True,
            capture_output=True,
            timeout=stop_command_timeout,
        )
        logger.info(f"Successfully stopped container {target_id}.")
    except subprocess.CalledProcessError as e:
        stderr_str = e.stderr.decode(errors="replace")
        if "No such container" in stderr_str:
            logger.warning(f"Container {target_id} not found. Assumed stopped.")
        else:
            logger.error(f"Failed to stop container {target_id}: {stderr_str}")
    except subprocess.TimeoutExpired:
        logger.error(
            f"Timed out waiting for container {target_id} to stop command to complete."
        )
    except FileNotFoundError:
        logger.error("Docker command not found.")
    except Exception as e:
        logger.error(f"Error stopping container {target_id}: {e}")
    finally:
        if target_id == current_container_id:
            current_container_id = None
