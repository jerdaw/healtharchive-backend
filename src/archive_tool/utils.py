# archive_tool/utils.py
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,  # Ensure necessary types are imported
    Optional,
)

# Use absolute imports
from . import constants  # Import constants module

# Import specific constants used here for clarity

logger = logging.getLogger("website_archiver.utils")


def check_docker() -> bool:
    """Checks if the docker command is available."""
    try:
        process = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        logger.info(f"Docker found: {process.stdout.strip()}")
        return True
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ) as e:
        logger.error(f"Docker command not found or failed to execute: {e}")
        logger.error("Please ensure Docker is installed, running, and accessible.")
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred while checking for Docker: {e}")
        return False


def container_to_host_path(container_path_str: str, host_output_dir: Path) -> Path | None:
    """Converts a container path within CONTAINER_OUTPUT_DIR to a host path."""
    try:
        container_path = Path(container_path_str)
        # Handle potential Windows paths in logs if needed, Path() usually works
        if not container_path.is_absolute():
            if container_path_str.startswith(
                str(constants.CONTAINER_OUTPUT_DIR)
            ) or container_path_str.startswith(constants.CONTAINER_OUTPUT_DIR.name):
                container_path = Path("/", container_path_str)
            else:
                logger.warning(f"Cannot convert relative container path: {container_path_str}")
                return None

        if not str(container_path).startswith(str(constants.CONTAINER_OUTPUT_DIR)):
            logger.warning(
                f"Path '{container_path}' doesn't start with '{constants.CONTAINER_OUTPUT_DIR}'. Attempting name-based."
            )
            relative_path_str: str = container_path.name
            host_path = host_output_dir.resolve() / relative_path_str
            return (
                host_path if host_path.exists() and host_path.is_dir() else None
            )  # Check is_dir too maybe

        relative_path: Path = container_path.relative_to(constants.CONTAINER_OUTPUT_DIR)
        host_path = host_output_dir.resolve() / relative_path
        return host_path
    except ValueError as e:
        logger.warning(f"Path conversion error for '{container_path_str}': {e}")
        return None
    except Exception as e:
        logger.error(f"Error converting container path '{container_path_str}': {e}")
        return None


def host_to_container_path(host_path: Path, host_output_dir: Path) -> str | None:
    """Converts a host path within the host_output_dir to a container path string."""
    try:
        resolved_host_path = host_path.resolve()
        resolved_host_output_dir = host_output_dir.resolve()
        # Check containment after resolving
        if (
            resolved_host_output_dir not in resolved_host_path.parents
            and resolved_host_path != resolved_host_output_dir
        ):
            raise ValueError("Path not within output directory")
        relative_path = resolved_host_path.relative_to(resolved_host_output_dir)
        container_path = constants.CONTAINER_OUTPUT_DIR / relative_path
        return str(container_path)
    except (ValueError, FileNotFoundError):
        logger.error(f"Path error for '{host_path}' within '{host_output_dir}'.")
        return None
    except Exception as e:
        logger.error(f"Error converting host path '{host_path}': {e}")
        return None


def parse_temp_dir_from_log_file(log_file_path: Path, host_output_dir: Path) -> Optional[Path]:
    """Parses zimit log file for the temp directory path."""
    temp_dir_regex = re.compile(
        r"Output to tempdir:\s*\"?([/\\]?output[/\\]\.tmp\w+)\"?", re.IGNORECASE
    )
    if not log_file_path or not log_file_path.is_file():
        logger.warning(f"Log file not found or invalid for parsing temp dir: {log_file_path}")
        return find_latest_temp_dir_fallback(host_output_dir)
    try:
        with open(log_file_path, "r", encoding="utf-8", errors="replace") as f:
            log_head = f.read(15 * 1024)
            log_tail = ""
            file_size = os.fstat(f.fileno()).st_size
            try:
                f.seek(max(0, file_size - (15 * 1024)))
                log_tail = f.read()
            except Exception:
                pass
            logs = log_head + "\n" + log_tail
        match = temp_dir_regex.search(logs)
        if match:
            container_temp_dir_str = match.group(1).strip().replace("\\", "/")
            logger.info(f"Found potential temp dir in log: {container_temp_dir_str}")
            host_path = container_to_host_path(container_temp_dir_str, host_output_dir)
            if host_path and host_path.is_dir():
                return host_path.resolve()
            elif host_path:
                logger.warning(f"Parsed host temp dir is not a directory: {host_path}")
            else:
                logger.warning(f"Could not convert parsed path '{container_temp_dir_str}'")
        else:
            logger.warning(f"Could not parse temp dir pattern from {log_file_path}.")
    except Exception as e:
        logger.error(f"Error reading/parsing log file {log_file_path}: {e}")
    logger.warning("Attempting fallback directory scan for temp dir.")
    return find_latest_temp_dir_fallback(host_output_dir)


def find_latest_temp_dir_fallback(host_output_dir: Path) -> Optional[Path]:
    """Fallback: Finds the most recently modified .tmp* directory."""
    latest_mod_time: float = 0.0  # Correct type
    latest_temp_dir = None
    try:
        for item in host_output_dir.glob(f"{constants.TEMP_DIR_PREFIX}*"):
            if item.is_dir():
                try:
                    mod_time = item.stat().st_mtime
                    if mod_time > latest_mod_time:
                        latest_mod_time = mod_time
                        latest_temp_dir = item
                except OSError as stat_e:
                    logger.warning(f"Could not stat dir {item}: {stat_e}")
    except Exception as e:
        logger.error(f"Error scanning for temp dirs: {e}")
        return None
    if latest_temp_dir:
        logger.info(f"Fallback found latest temp dir: {latest_temp_dir.resolve()}")
        return latest_temp_dir.resolve()
    else:
        logger.info(f"Fallback did not find any '{constants.TEMP_DIR_PREFIX}*' directories.")
        return None


def find_latest_config_yaml(temp_dir_path: Path) -> Optional[Path]:
    """Finds the most recently modified 'crawl-*.yaml' file in the temp directory's expected location."""
    latest_mod_time: float = 0.0  # Correct type
    latest_yaml = None
    search_pattern_str = "collections/crawl-*/crawls/crawl-*.yaml"
    logger.debug(f"Searching for pattern '{search_pattern_str}' in directory: {temp_dir_path}")
    if not temp_dir_path or not temp_dir_path.is_dir():
        logger.warning(f"Cannot search for YAML, invalid temp dir: {temp_dir_path}")
        return None
    try:
        config_files = list(temp_dir_path.glob(search_pattern_str))
        if not config_files:
            logger.info(f"No config YAML files found in subdirs of {temp_dir_path}.")
            return None
        for yaml_file in config_files:
            if yaml_file.is_file():
                try:
                    mod_time = yaml_file.stat().st_mtime
                    if mod_time > latest_mod_time:
                        latest_mod_time = mod_time
                        latest_yaml = yaml_file
                except OSError as stat_e:
                    logger.warning(f"Could not stat config file {yaml_file}: {stat_e}")
    except Exception as e:
        logger.error(f"Error searching config YAML in {temp_dir_path}: {e}")
        return None
    if latest_yaml:
        logger.info(f"Found latest config YAML: {latest_yaml.resolve()}")
        return latest_yaml.resolve()
    else:
        logger.warning(f"Found files matching pattern but none were valid in {temp_dir_path}.")
        return None


def find_all_warc_files(temp_dir_paths: List[Path]) -> List[Path]:
    """Finds all unique *.warc.gz files within the 'archive' subdirs of the given temp dirs."""
    all_warcs = set()
    if not temp_dir_paths:
        return []
    logger.info(f"Searching for WARC files in {len(temp_dir_paths)} temp dir path(s)...")
    for temp_dir in temp_dir_paths:
        if not temp_dir.is_dir():
            logger.warning(f"Skipping WARC search in non-existent dir: {temp_dir}")
            continue
        try:
            archive_dirs = list(temp_dir.glob("collections/crawl-*/archive"))
            if not archive_dirs:
                logger.info(f"  No 'archive' subdirectory found within collections in {temp_dir}")
                continue
            found_in_temp_dir = 0
            for archive_dir in archive_dirs:
                if archive_dir.is_dir():
                    for warc_file in archive_dir.rglob("*.warc.gz"):
                        try:
                            if warc_file.is_file() and warc_file.stat().st_size > 0:
                                all_warcs.add(warc_file.resolve())
                                found_in_temp_dir += 1
                        except OSError as stat_e:
                            logger.warning(f"Could not stat WARC file {warc_file}: {stat_e}")
                else:
                    logger.warning(f"  Path is not a directory: {archive_dir}")
            logger.info(f"  Found {found_in_temp_dir} WARC file(s) in subdirs of {temp_dir}")
        except Exception as e:
            logger.error(f"Error searching WARCs in {temp_dir}: {e}")
    unique_warc_list = sorted(list(all_warcs))
    logger.info(f"Total unique WARC files found: {len(unique_warc_list)}")
    return unique_warc_list


def parse_last_stats_from_log(log_file_path: Path) -> Optional[Dict[str, Any]]:
    """Parses the last 'Crawl statistics' JSON blob from a log file."""
    if not log_file_path or not log_file_path.is_file():
        logger.warning(f"Cannot parse stats, invalid log file path: {log_file_path}")
        return None
    last_stats_json_str = None
    try:
        file_size = log_file_path.stat().st_size
        read_size = min(file_size, 1024 * 1024)
        offset = file_size - read_size
        with open(log_file_path, "r", encoding="utf-8", errors="replace") as f:
            if offset > 0:
                f.seek(offset)
            log_content = f.read()
        # Use regex from constants
        matches = list(constants.STATS_REGEX.finditer(log_content))
        if matches:
            last_stats_json_str = matches[-1].group(1)
        else:
            logger.info(f"No 'Crawl statistics' message found in the end of {log_file_path.name}.")
            return None
        stats_details = json.loads(last_stats_json_str)
        extracted_stats = {
            "crawled": stats_details.get("crawled"),
            "total": stats_details.get("total"),
            "pending": stats_details.get("pending"),
            "failed": stats_details.get("failed"),
        }
        if extracted_stats["crawled"] is None or extracted_stats["total"] is None:
            logger.warning(f"Last stats message missing data: {last_stats_json_str}")
            return None
        logger.info(f"Parsed last stats from {log_file_path.name}: {extracted_stats}")
        return extracted_stats
    except json.JSONDecodeError as json_e:
        logger.error(f"Failed parsing stats JSON: {json_e}")
        logger.debug(f"Invalid JSON: {last_stats_json_str}")
        return None
    except Exception as e:
        logger.error(f"Error parsing stats log {log_file_path}: {e}")
        return None


def cleanup_temp_dirs(temp_dir_paths: List[Path], state_file_path: Path):
    """Deletes temporary directories and the state file."""
    import shutil

    logger.info("--- Starting Cleanup ---")
    deleted_count = 0
    for temp_dir in temp_dir_paths:
        if temp_dir and temp_dir.is_dir() and temp_dir.name.startswith(constants.TEMP_DIR_PREFIX):
            try:
                shutil.rmtree(temp_dir)
                logger.info(f"Deleted: {temp_dir}")
                deleted_count += 1
            except Exception as e:
                logger.error(f"Failed to delete {temp_dir}: {e}")
        else:
            logger.warning(f"Skipping cleanup: {temp_dir}")
    if state_file_path.exists():
        try:
            state_file_path.unlink()
            logger.info(f"Deleted state file: {state_file_path}")
        except Exception as e:
            logger.error(f"Failed to delete state file {state_file_path}: {e}")
    logger.info(f"Cleanup finished. Deleted {deleted_count} director(y/ies).")


def relax_permissions(host_output_dir: Path, temp_dirs: List[Path]) -> None:
    """
    Make crawl artifacts world-readable so host users can index WARCs without sudo.
    Runs chmod inside a root container to avoid host-side sudo.
    """
    if not temp_dirs:
        logger.info("relax_permissions: no temp dirs to adjust.")
        return

    if not host_output_dir.exists():
        logger.warning(f"relax_permissions: output dir {host_output_dir} does not exist; skipping.")
        return

    try:
        logger.info("relax_permissions: ensuring WARCs are readable (chmod a+rX) ...")
        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{host_output_dir.resolve()}:/output",
            "alpine",
            "sh",
            "-c",
            "chmod -R a+rX /output/.tmp* 2>/dev/null || true",
        ]
        subprocess.run(cmd, check=False, capture_output=True, text=True)
    except FileNotFoundError:
        logger.warning("relax_permissions: docker not available; cannot adjust permissions.")
    except Exception as exc:
        logger.warning(f"relax_permissions: unexpected error: {exc}")


def filter_args_for_final_run(passthrough_args: List[str]) -> List[str]:
    """Removes args not needed for the final --warcs build."""
    # --- Initialize filtered ---
    filtered: List[str] = []
    # --- End Initialization ---
    i = 0  # Ensure i is initialized
    while i < len(passthrough_args):
        arg = passthrough_args[i]
        keep_arg = False
        # Check against constants.REQUIRED_FINAL_ARGS_PREFIXES
        for prefix in constants.REQUIRED_FINAL_ARGS_PREFIXES:
            if arg.startswith(prefix):
                keep_arg = True
                break
        if keep_arg:
            filtered.append(arg)
            if not arg.startswith("--") or "=" in arg:
                i += 1
            elif i + 1 < len(passthrough_args) and not passthrough_args[i + 1].startswith("-"):
                filtered.append(passthrough_args[i + 1])
                i += 2
            else:
                i += 1
        else:  # Skip arg if not required for final run
            if not arg.startswith("--") or "=" in arg:
                i += 1
            elif i + 1 < len(passthrough_args) and not passthrough_args[i + 1].startswith("-"):
                i += 2
            else:
                i += 1
    logger.debug(f"Filtered arguments for final run: {filtered}")
    return filtered


def execute_external_command(command: str, description: str) -> bool:
    """Executes an external command, logs output, returns success."""
    import shlex

    logger.info(f"Executing {description} command: {command}")
    try:
        args = shlex.split(command)
        process = subprocess.run(args, capture_output=True, text=True, check=True, timeout=120)
        logger.info(f"{description} STDOUT:\n{process.stdout.strip()}")
        if process.stderr.strip():
            logger.warning(f"{description} STDERR:\n{process.stderr.strip()}")
        logger.info(f"{description} command completed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"{description} failed (RC {e.returncode}):\n{e.stderr.strip()}")
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"{description} timed out.")
        return False
    except FileNotFoundError:
        cmd_base = args[0] if args else command
        logger.error(f"{description} not found ('{cmd_base}').")
        return False
    except Exception as e:
        logger.error(f"Error executing {description}: {e}")
        return False
