## 1. Purpose and high-level overview

Within the HealthArchive backend, the ``archive_tool`` package is the
crawler/orchestrator subpackage responsible for driving Zimit + Docker and
producing WARCs and final ZIMs. It started life as a standalone repository and
is now maintained in-tree as part of the ``healtharchive-backend`` repo.

This project is an **orchestrator around Zimit** (the `ghcr.io/openzim/zimit` Docker image). It turns a raw `zimit` crawl into a **resumable, monitored, multi-stage pipeline** that:

1. **Runs/Resumes crawls** inside Docker using Zimit.
2. **Monitors logs in real time**, tracks progress, and detects stalls / error storms.
3. **Applies adaptive strategies**:

   * Reduce `--workers` if things are unstable.
   * Rotate VPN / IP without stopping the container, if configured.
4. **Tracks temporary output directories** and **WARCs** across runs.
5. **Builds the final ZIM from WARCs** as a separate, synchronous stage.
6. Provides a **cleaned-up surface**: state file, temp dirs, and logs with predictable naming.

You invoke a single script with seeds + name + output dir, and it tries to:

* Decide whether to **do a fresh run**, **resume**, or **do a new crawl phase + consolidate existing WARCs**.
* **Automatically generate and run the correct `docker run zimit ...` commands** across multiple attempts.
* **End with `<name>.zim` in your chosen `--output-dir`**.

---

## 2. Entry points & process layout

### 2.1 Top-level executable

* **File:** `run_archive.py`

```python
from archive_tool.main import main

if __name__ == "__main__":
    main()
```

This is the main entrypoint. It:

* Assumes `archive_tool` is importable (either as a local package or installed).
* Delegates everything to `archive_tool.main.main()`.

You normally run:

```bash
./run_archive.py \
  --seeds https://example.org \
  --name example \
  --output-dir /path/to/output \
  [ARCHIVER OPTIONS...] \
  [-- zimit-options...]
```

### 2.2 Package structure

`archive_tool/` contains:

* `cli.py` – parses CLI arguments and splits “tool args” vs “zimit passthrough args”.
* `main.py` – orchestration, stages, run-mode detection, main crawl loop, final build, cleanup.
* `docker_runner.py` – builds zimit argument list; runs containers with `docker run`; tracks the `Popen` and container ID.
* `monitor.py` – background log monitor reading `docker logs -f`; parses Zimit logs and feeds progress/error info into a `CrawlState` and a message queue.
* `state.py` – `CrawlState` object + persistence to `.archive_state.json`.
* `utils.py` – Docker check, path mapping between host and container, log parsing, WARC discovery, cleanup, final-run arg filtering, generic external command execution.
* `strategies.py` – adaptive strategies: worker reduction and VPN rotation.
* `constants.py` – static configuration: default image name, error patterns, log formats, acceptable exit codes, regexes.

When used from the backend, the primary entrypoints are the ``archive_tool``
CLI (console script `archive-tool`) and a handful of helpers imported by
``ha_backend``:

* ``ha_backend.jobs.run_persistent_job`` constructs the CLI based on
  ``ArchiveJob.config["tool_options"]`` and ``archive_tool.cli``'s argument
  model.
* ``ha_backend.indexing.warc_discovery`` and ``ha_backend.cli.cmd_cleanup_job``
  reuse ``archive_tool.state.CrawlState`` and ``archive_tool.utils`` helpers
  for WARC discovery and cleanup.

If you change the CLI surface area or state layout here, update the backend
integration points and their docs accordingly.

### Backend contract summary

When called from the HealthArchive backend, the following expectations apply:

- **CLI arguments**:

  - ``ha_backend.jobs.run_persistent_job`` constructs the argv list using
    ``ArchiveJobConfig.tool_options``:

    - Core flags:

      - `--seeds`, `--name`, `--output-dir`, `--initial-workers`, `--log-level`.
      - `--cleanup` when `cleanup=True`.
      - `--overwrite` when `overwrite=True`.

    - Monitoring/adaptive flags:

      - `--enable-monitoring` when `enable_monitoring=True`.
      - Optional:
        - `monitor_interval_seconds` → `--monitor-interval-seconds`.
        - `stall_timeout_minutes` → `--stall-timeout-minutes`.
        - `error_threshold_timeout` → `--error-threshold-timeout`.
        - `error_threshold_http` → `--error-threshold-http`.
      - `--enable-adaptive-workers` when `enable_adaptive_workers=True` and
        monitoring is enabled.
      - Optional:
        - `min_workers` → `--min-workers`.
        - `max_worker_reductions` → `--max-worker-reductions`.

    - VPN/backoff flags:

      - `--enable-vpn-rotation` when `enable_vpn_rotation=True`,
        `enable_monitoring=True`, and `vpn_connect_command` is set.
      - `--vpn-connect-command "<command>"` from `vpn_connect_command`.
      - Optional:
        - `max_vpn_rotations` → `--max-vpn-rotations`.
        - `vpn_rotation_frequency_minutes`
          → `--vpn-rotation-frequency-minutes`.
      - `--backoff-delay-minutes` from `backoff_delay_minutes` when
        monitoring is enabled.

    - Other flags:

      - `--relax-perms` when `relax_perms=True`.
      - Zimit passthrough args from `zimit_passthrough_args` are appended
        after the tool flags and passed through unchanged.

- **State and directories**:

  - ``CrawlState`` persists to `<output-dir>/.archive_state.json` and tracks:
    host temp dir paths (`.tmp*`), current/initial workers, and adaptation
    counters.
  - Temporary crawl dirs live directly under `<output-dir>/.tmp*`.
  - WARC discovery uses:

    - ``utils.find_all_warc_files(temp_dir_paths)`` to locate WARCs under
      `collections/crawl-*/archive` inside each temp dir.

  - Backend indexing expects this layout for `discover_warcs_for_job(job)`.

- **Logs and stats**:

  - For final build stages, ``archive_tool.main`` writes log triples:

    - `archive_<stage_name>_*.stdout.log`
    - `archive_<stage_name>_*.stderr.log`
    - `archive_<stage_name>_*.combined.log`

    under the job's `--output-dir`.

  - Crawl statistics are emitted as JSON in logs and parsed by
    ``utils.parse_last_stats_from_log(combined_log_path)``, which the backend
    calls via ``ha_backend.crawl_stats.update_job_stats_from_logs``.

If you change CLI flags, log formats, or directory layout, you should update
both this document and the backend integration modules (`ha_backend` contract
helpers and associated tests) to keep behaviour coherent.

---

## 3. CLI contract and argument model

### 3.1 Argument parsing

* **Module:** `archive_tool/cli.py`
* **Function:** `parse_arguments() -> (argparse.Namespace, List[str])`

It uses `parse_known_args()` to split:

* **`script_args`** – options that belong to this tool.
* **`zimit_passthrough_args`** – everything else, passed directly to `zimit` inside the container, with some small edits (`--workers`, `--output`).

#### 3.1.1 “Core Arguments” (tool’s required inputs)

* `--seeds` `SEED1 SEED2 ...` (required)

  * List of seed URLs for the crawl.
  * Tool also injects the **first seed** into the final WARCs build workaround stage.

* `--name` (required)

  * Base name for the ZIM and the “job”. Final ZIM will be `<name>.zim` in `--output-dir`.

* `--output-dir` (required)

  * **Host directory** where:

    * ZIM is stored.
    * Logs are written.
    * Temp dirs (mirror of `/output` inside container) live.
    * `.archive_state.json` lives.

* `--initial-workers` (default `1`)

  * Default worker count for zimit (if no `--workers` appears in the zimit args).
  * May be overridden by `--workers` in zimit passthrough.

#### 3.1.2 “Tool Options”

* `--cleanup`

  * If set, **delete** temp dirs and `.archive_state.json` on successful completion.

* `--overwrite`

  * If final ZIM already exists and this flag is **not** given → hard error, exit.
  * If given:

    * Allows recreating an existing ZIM.
    * Resets persistent state (workers, VPN counters, temp dir list) to behave as a fresh crawl from the tool’s POV.

* `--docker-image` (default = `DOCKER_IMAGE` from constants = `"ghcr.io/openzim/zimit"`)

  * Lets you override which Zimit image to run.

* `--log-level` (`DEBUG`, `INFO` (default), `WARNING`, `ERROR`, `CRITICAL`)

  * Controls verbosity for all `website_archiver.*` loggers.

* `--dry-run`

  * If set, perform all configuration and environment validation steps, print
    a summary of the planned crawl (seeds, name, output directory, effective
    workers, monitoring/VPN flags, passthrough args), and then **exit without
    starting any Docker containers or crawl stages**. This is primarily
    useful for debugging job configs from the backend (`ha-backend
    validate-job-config`) or from the CLI.

#### 3.1.3 Monitoring configuration

These control `CrawlMonitor` behaviour:

* `--enable-monitoring`

  * If set, spawns a monitor thread reading `docker logs -f` and drive stall/error detection.

* `--monitor-interval-seconds` (default `30`)

  * Frequency for:

    * Checking stall/error conditions.
    * Emitting periodic `status='progress'` events.

* `--stall-timeout-minutes` (default `30`)

  * If **no progress** (crawled count not increasing) for this long **while `pending > 0`**, monitor triggers a `status='stalled'` event.

* `--error-threshold-timeout` (default `10`)

  * If `timeout` error count in `CrawlState.error_counts` reaches this, monitor emits `status='error', reason='timeout_threshold'`.

* `--error-threshold-http` (default `10`)

  * Same but for `http` errors (non-200 responses, network errors).

#### 3.1.4 Adaptive strategies

* `--enable-adaptive-workers`

  * Allows automatic **reductions of `--workers`** when conditions trigger.

* `--min-workers` (default `1`)

  * Lower bound for worker reductions.

* `--max-worker-reductions` (default `2`)

  * Maximum number of successful worker reductions per run (tracked in `CrawlState`).

* `--enable-vpn-rotation`

  * Allows network/IP rotation using a command like `nordvpn connect us`.

* `--vpn-connect-command` (string, required if `--enable-vpn-rotation`)

  * Shell command invoked when an adaptation requires VPN rotation.
  * Must resolve in `$PATH` (checked via `shutil.which`).

* `--vpn-disconnect-command` (string, optional, but currently **ignored**)

  * Accepted but explicitly **not used**. Rotation logic assumes reconnection is handled by the connect command.

* `--max-vpn-rotations` (default `3`)

  * Max number of VPN rotation attempts per run (persisted across stages in `CrawlState`).

* `--vpn-rotation-frequency-minutes` (default `60`)

  * Minimum time between rotations. If last rotation < this interval ago, rotation is skipped.

* `--backoff-delay-minutes` (default `15`)

  * Used for **time-based backoff** when:

    * A stall/error is detected but no adaptive strategy could be applied.
    * After worker-reduction or failure before retrying/resuming.

#### 3.1.5 Validation rules

* If `--enable-adaptive-workers` or `--enable-vpn-rotation` is set but `--enable-monitoring` is not, parsing fails.
* If `--enable-vpn-rotation` without `--vpn-connect-command`, parsing fails.
* `--min-workers` must be ≥ 1.
* `--vpn-rotation-frequency-minutes` cannot be negative.

---

## 4. `CrawlState` – persistent and runtime state

**Module:** `archive_tool/state.py`
**Class:** `CrawlState`

### 4.1 Where state lives

* File path: `<output-dir>/.archive_state.json`
* Created/updated automatically whenever `CrawlState` is created and when:

  * Temp dirs are added.
  * Adaptation counts change.
  * Worker count changes.

### 4.2 Persistent fields (survive across processes)

* `current_workers: int`

  * Effective worker count used in the latest/next stage.

* `initial_workers: int`

  * The **original** worker count used when the state file was first created (or last reset).

* `temp_dirs_host_paths: List[str]`

  * List of **host absolute paths** to recognized temp dirs (`.tmp*`) created in past stages.
  * `get_temp_dir_paths()` validates each on load; removes non-existent entries and saves trimmed list.

* `vpn_rotations_done: int`

  * How many VPN rotations have been successfully performed so far.

* `worker_reductions_done: int`

  * How many times workers have been reduced via `attempt_worker_reduction`.

These fields are written as:

```json
{
  "current_workers": 4,
  "initial_workers": 4,
  "temp_dirs_host_paths": ["/some/output/.tmp123", ...],
  "vpn_rotations_done": 1,
  "worker_reductions_done": 1
}
```

### 4.3 Runtime-only fields (in-memory during a run)

Used for monitoring/printing but **not persisted**:

* Stage tracking:

  * `status` (`"initializing"`, `"running"`, etc.)
  * `current_stage` (e.g. `"Initial Crawl - Attempt 1"`)
  * `stage_start_time: float | None` (monotonic seconds).

* Progress metrics:

  * `last_crawled_count`, `last_total_count`, `last_pending_count`, `last_failed_count`
  * `last_stats_timestamp`
  * `last_progress_timestamp`
  * `previous_crawled_count`
  * `previous_stats_timestamp`
  * `progress_rate_ppm` (pages/minute, derived from deltas).

* Error tracking:

  * `error_counts: {"timeout": int, "http": int, "other": int}`
  * `last_error_type`
  * `exit_code` (last docker exit code).

* VPN runtime:

  * `last_vpn_rotation_timestamp: float | None` (monotonic seconds when last rotation happened).

### 4.4 Key methods

* `load_persistent_state()`

  * If `.archive_state.json` exists:

    * Load JSON.
    * Clamp `current_workers` to <= `initial_workers`.
    * Filter `temp_dirs_host_paths` down to existing directories.
  * Else, initialize everything fresh.

* `save_persistent_state()`

  * Deduplicates & sorts `temp_dirs_host_paths`.
  * Writes JSON as above.

* `add_temp_dir(Path)`

  * Adds a new temp dir if it exists and is a directory, then saves state.

* `get_temp_dir_paths() -> List[Path]`

  * Validates each stored path, keeps only existing dirs, and re-saves state if any were removed.

* `reset_adaptation_counts()`

  * Resets `vpn_rotations_done` and `worker_reductions_done` (for truly fresh runs).

* `reset_runtime_errors()`

  * Resets `error_counts` dict and `last_error_type`.

* `update_progress(stats: Dict[str, Any], timestamp: float)`

  * Accepts parsed stats object like `{"crawled": N, "total": T, "pending": P, "failed": F}`.
  * Updates `last_*` counters, timestamps, and `progress_rate_ppm`.
  * If crawled increased, sets `last_progress_timestamp`, and resets error counts if any were nonzero.

* `record_error(error_type: str, timestamp: float)`

  * Increments `error_counts[error_type]` and updates `last_error_type`.

---

## 5. Utility layer

**Module:** `archive_tool/utils.py`

### 5.1 Docker availability check

* `check_docker() -> bool`

  * Runs `docker --version` and logs output.
  * Returns `True` if the command succeeds; `False` with detailed logs otherwise.

### 5.2 Path mapping between host and container

Zimit runs in a container with `/output` mapped to the host `--output-dir`.

Constants:

* `CONTAINER_OUTPUT_DIR = Path("/output")`
* `TEMP_DIR_PREFIX = ".tmp"` (temp dirs are `.tmp...` inside `/output`)

Functions:

* `container_to_host_path(container_path_str: str, host_output_dir: Path) -> Path | None`

  * Handles:

    * Absolute `/output/...` paths.
    * Possibly paths starting with `output` relative style.
  * Validates path is within `/output`, then converts:

    * `Path("/output/...")` → `host_output_dir / "..."`.

* `host_to_container_path(host_path: Path, host_output_dir: Path) -> str | None`

  * Checks that `host_path` is inside `host_output_dir`.
  * Returns `"/output/relative/path"`.

These are used for:

* Mapping log-reported temp dirs into host paths.
* Mapping WARC files from host into container `--warcs` arguments.

### 5.3 Temp dir discovery

1. **From logs:**

   * `parse_temp_dir_from_log_file(log_file_path, host_output_dir) -> Optional[Path]`

     * Reads the **head and tail** of the log file (~30KB total).
     * Looks for lines like:

       ```
       Output to tempdir: "output/.tmpXXXX"
       ```
     * Converts the `output/.tmpXXXX` to a host path via `container_to_host_path`.
     * If fails, logs warnings and calls `find_latest_temp_dir_fallback`.

2. **Fallback scanning:**

   * `find_latest_temp_dir_fallback(host_output_dir) -> Optional[Path]`

     * Scans `host_output_dir.glob(".tmp*")`.
     * Picks directory with **most recent mtime**.

### 5.4 YAML config discovery (for resuming)

* `find_latest_config_yaml(temp_dir_path: Path) -> Optional[Path]`

Looks for:

```text
collections/crawl-*/crawls/crawl-*.yaml
```

under a temp dir and returns the newest file. This is the config YAML used to **resume** a previous crawl queue.

### 5.5 WARC discovery

* `find_all_warc_files(temp_dir_paths: List[Path]) -> List[Path]`

For each temp dir:

* Looks under `collections/crawl-*/archive`.
* Recursively gathers all `*.warc.gz` with size > 0.
* Deduplicates and returns sorted list of absolute host paths.

Used both to:

* Decide if there are **preexisting warcs** when choosing run mode.
* Provide warc list to the final `--warcs` build.

### 5.6 Parsing stats from logs

* `parse_last_stats_from_log(log_file_path: Path) -> Optional[Dict[str, Any]]`

Uses `constants.STATS_REGEX` to find the **last** `"Crawl statistics"` log entry in the tail of the log file and returns:

```python
{
  "crawled": ...,
  "total": ...,
  "pending": ...,
  "failed": ...
}
```

Used in:

* Initial run-mode summary (“Last known status from logs”).

### 5.7 Cleanup

* `cleanup_temp_dirs(temp_dir_paths: List[Path], state_file_path: Path)`

If `--cleanup` was set and the run is successful:

* Deletes each temp dir whose name starts with `.tmp`.
* Deletes the `.archive_state.json`.

### 5.8 Final-build argument filtering

* `filter_args_for_final_run(passthrough_args: List[str]) -> List[str]`

Keeps only zimit args that match `REQUIRED_FINAL_ARGS_PREFIXES`, e.g.:

* `--name`
* `--title`
* `--description`
* `--long-description`
* `--zim-lang`
* `--custom-css`
* `--adminEmail`
* `--favicon`
* `--warcPrefix`
* `--lang`

Drops other flags like `--workers`. It also cooperates with `build_zimit_args` to reconstruct final zimit command.

### 5.9 External command wrapper

* `execute_external_command(command: str, description: str) -> bool`

  * `shlex.split` → `subprocess.run`.
  * Logs STDOUT and STDERR.
  * Times out after 120s.
  * Used by VPN rotation strategy.

---

## 6. Docker runner and Zimit command construction

**Module:** `archive_tool/docker_runner.py`

### 6.1 Globals

* `current_docker_process: Optional[subprocess.Popen]`

  * Current `docker run` process for the active crawl stage.

* `current_container_id: Optional[str]`

  * Actual Docker container ID for Zimit container, located using a label.

These are essential for:

* Monitoring (CrawlMonitor uses the Popen).
* Signal handler (stops the process/container).
* Worker-reduction strategy (stops container via ID).

### 6.2 Building the zimit argument list

**Function:** `build_zimit_args(base_zimit_args, required_args, current_workers, is_final_build, extra_args=[])`

Steps:

1. Start with `["zimit"]`.
2. If not final build:

   * Add seeds: for each `seed_url` in `required_args["seeds"]`, add `--seeds seed_url`.
3. Always ensure `--name` is present from `required_args`.
4. Process `base_zimit_args` to:

   * Remove any `--workers` and `--workers=...` entries.
   * Keep the rest in order.
5. If not final build:

   * Append `["--workers", str(current_workers)]`.
6. Append `extra_args` (e.g., `["--config", "/output/...yaml"]` or `["--warcs", "..."]`).
7. Ensure `--keep` is present (prevents zimit from deleting temp output).
8. Ensure `--output /output` is the last output directive, overriding any existing `--output`.

This ensures that worker count is **controlled centrally** by `CrawlState.current_workers` and that all output ends up under `/output` → `--output-dir` on the host.

### 6.3 Starting the Docker container

**Function:** `start_docker_container(docker_image, host_output_dir, zimit_args, run_name)`

* Compose:

  ```bash
  docker run --rm \
    -v /host/output:/output \
    --label archive_job=archive-<run_name>-<uuid8> \
    <docker_image> \
    zimit ...
  ```

* Uses `subprocess.Popen` with:

  * `stdin=subprocess.DEVNULL` (no interactive input)
  * `stdout=PIPE`, `stderr=STDOUT` (for logging).

* After process start, it repeatedly calls `get_container_id_by_label(job_id)` (up to 5 times, 2s apart) to get the container ID; if:

  * Found → logs ID and sets `current_container_id`.
  * Not found and process exits quickly → logs RC and attempts to dump quick-stdout/stderr.

Returns `(process, container_id)` (container_id may be `None` if detection failed but process is still running).

### 6.4 Getting and stopping containers

* `get_container_id_by_label(job_id) -> Optional[str]`

  * Runs `docker ps -q --filter label=archive_job=<job_id>` and returns the first ID.

* `stop_docker_container(container_id: Optional[str])`

  * Uses `docker stop -t 90 <id>` to gracefully stop.
  * Handles “No such container” gracefully.
  * On success, logs and clears `current_container_id`.

---

## 7. Monitoring & adaptive strategies

### 7.1 CrawlMonitor – log-based monitor thread

**Module:** `archive_tool/monitor.py`
**Class:** `CrawlMonitor(threading.Thread)`

Constructor:

```python
CrawlMonitor(
    container_id: str,
    process_handle: subprocess.Popen,  # docker run process
    state: CrawlState,
    args: argparse.Namespace,          # script_args
    output_queue: Queue,               # back to main loop
    stop_event: threading.Event        # shared global stop flag
)
```

Responsibilities:

1. **Attach to logs:**

   * Runs: `docker logs -f --tail 50 <container_id>`.
   * Reads line by line.

2. **Parse each log line:**

   * Try `json.loads(line)`:

     * If `context="crawlStatus"` & `message="Crawl statistics"` & `details` dict:

       * Call `state.update_progress(details, timestamp)`.
     * If `context="pageStatus"` & `message="Page Load Failed: will retry"`:

       * Inspect `details["msg"]` for timeout/HTTP patterns; call `state.record_error`.
     * For logs with `level in ["error", "warn"]`, search for timeout / HTTP patterns and record accordingly.

   * If not JSON:

     * Still scan for timeout / HTTP patterns and record errors.

3. **Stall & error checks (periodically):**

   * On each iteration, after enough time since last check:

     * Runs `_check_stall_and_error_conditions(now)`, which:

       * If monitoring disabled, returns False.
       * If:

         * We have valid `last_progress_timestamp`,
         * `last_pending_count > 0`,
         * No progress for > `stall_timeout_minutes`,
         * Then pushes `{"status": "stalled", "reason": "timeout"}` and resets error counters.
       * If `error_counts["timeout"] >= error_threshold_timeout`:

         * Pushes `{"status": "error", "reason": "timeout_threshold"}` and resets errors.
       * If `error_counts["http"] >= error_threshold_http`:

         * Pushes `{"status": "error", "reason": "http_threshold"}` and resets errors.

4. **Progress signaling:**

   * On another timer, sends `{"status": "progress"}` periodically so main can print progress even if stats change slowly.

5. **Stop condition:**

   * Exits when:

     * `stop_event` set.
     * `docker logs` process exits.
     * It detects that `docker run` process finished and `docker ps` shows the container is gone.

6. **Cleanup:**

   * Ensures `docker logs -f` process is properly terminated (kills process group if possible).

### 7.2 Adaptive workers

**Module:** `archive_tool/strategies.py`
**Function:** `attempt_worker_reduction(state: CrawlState, args: argparse.Namespace) -> bool`

Triggered when main sees a `stalled` or `error` event and `--enable-adaptive-workers` is on.

Steps:

1. Check:

   * `enable_adaptive_workers` is True.
   * `worker_reductions_done < max_worker_reductions`.
   * `current_workers > min_workers`.

2. **Stop the container:**

   * Calls `stop_docker_container(current_container_id)`.
   * Waits a short period for full stop.

3. **Adjust state:**

   * `state.current_workers = max(min_workers, current_workers - 1)`.
   * `state.worker_reductions_done += 1`.
   * `state.reset_runtime_errors()`.
   * `state.save_persistent_state()`.

4. Return `True` to main loop, which then:

   * Marks stage as `stopped_for_adaptation`.
   * Goes into a resume stage in the next outer loop iteration with new worker count.

### 7.3 VPN rotation (live, without stopping container)

**Function:** `attempt_vpn_rotation(state: CrawlState, args: argparse.Namespace, stop_event) -> bool`

Triggered when:

* `--enable-vpn-rotation` is set, and
* A stall/error occurred, and
* Worker reduction either wasn't enabled or didn't apply.

Steps:

1. Validate:

   * `enable_vpn_rotation` is True.
   * `vpn_rotations_done < max_vpn_rotations`.
   * `vpn_connect_command` is defined and resolves (via `shutil.which`).
   * Enough time has passed since `last_vpn_rotation_timestamp` based on `vpn_rotation_frequency_minutes`.

2. Run the command:

   * Calls `execute_external_command(vpn_connect_command, "VPN Connect/Rotate")`.

3. On success:

   * Logs a delay of 15 seconds and uses `stop_event.wait(post_vpn_delay)` so a global stop can interrupt.
   * If stop_event not set:

     * `state.vpn_rotations_done += 1`.
     * `state.last_vpn_rotation_timestamp = now` (time recorded before command).
     * `state.reset_runtime_errors()`.
     * `state.save_persistent_state()`.
   * Returns `True`.

4. On failure:

   * Logs and returns `False`.

This strategy does **not** stop or restart the container; it assumes the network/route change will begin to affect subsequent page loads.

### 7.4 Backoff logic

If monitor events occur and **neither worker reduction nor VPN rotation were applied successfully**, main:

* Applies a **backoff**:

  * If `backoff_delay_minutes > 0`:

    * Logs and calls `stop_event.wait(backoff_delay_minutes * 60)`.

      * If stop_event is set during wait → stage_status becomes `stopped`, break out.
      * Else → resets runtime error counts and continue monitoring.
  * If `backoff_delay_minutes == 0`:

    * Just resets runtime error counts and continues.

---

## 8. Main orchestration (`main.py`)

### 8.1 Signal handling

At import time:

* Registers `signal_handler` for `SIGINT` and `SIGTERM`.

`signal_handler`:

1. Logs that a signal was received.
2. Sets a **global** `stop_event` (shared with monitor & strategies).
3. If there is an active `current_container_id`:

   * Calls `stop_docker_container`.
4. If `current_docker_process` is still running:

   * Tries `terminate()` then waits up to 10s.
   * If still alive, `kill()` with 5s wait.
5. Exits process with code `1`.

### 8.2 Startup steps

`main()`:

1. **Parse CLI:**

   * `script_args, zimit_passthrough_args = cli.parse_arguments()`.

2. **Configure logging:**

   * `logging.basicConfig(..., force=True)` with `LOG_FORMAT` and chosen log level.
   * Sets up loggers for:

     * `website_archiver.main`
     * `website_archiver.docker`
     * `website_archiver.monitor`
     * `website_archiver.state`
     * `website_archiver.strategies`
     * `website_archiver.utils`

3. **Check Docker:**

   * `utils.check_docker()`, exit with RC 1 on failure.

4. **Prepare output directory:**

   * Resolves `script_args.output_dir`.
   * `mkdir(parents=True, exist_ok=True)`.
   * Tests writability by touching & deleting a `.writable_test_<pid>` file.

5. **Determine effective initial workers:**

   * Start from `script_args.initial_workers`.
   * Scan `zimit_passthrough_args` for `--workers N` / `--workers=N`:

     * If found and value is int, override initial workers.
   * Clamp to ≥ 1 → `effective_initial_workers`.

6. **Initialize CrawlState:**

   * `crawl_state = CrawlState(host_output_dir, initial_workers=effective_initial_workers)`.
   * This loads any prior `.archive_state.json` and then immediately saves a fresh snapshot.

### 8.3 Run-mode detection

Goal: decide **how to interpret existing state + files**.

Inputs:

* `crawl_state` (with temp dirs and adaptation counts).
* Existing temp dirs (via `crawl_state.get_temp_dir_paths()`).
* Any previous logs: `archive_*.combined.log` in the output dir.
* Existing ZIM file `<output-dir>/<name>.zim`.

Steps:

1. Get existing temp dirs; choose `latest_temp_dir` if any.

2. Try to find resume config YAML:

   * `config_yaml_path = utils.find_latest_config_yaml(latest_temp_dir)` if `latest_temp_dir` exists.
   * If found → `can_resume_crawl = True`.

3. Search for WARCs across all temp dirs:

   * `warc_files = utils.find_all_warc_files(existing_temp_dirs)`.
   * If non-empty → `has_prior_warcs = True` and `warc_file_count = len(warc_files)`.

4. Parse last known stats for logging:

   * Look for `archive_*.combined.log` files.
   * Use `parse_last_stats_from_log` on the most recently modified file (if any).
   * If stats available, log a summary: e.g. `Crawled=..., Total=..., Failed=...`.

5. Check for existing ZIM:

   ```python
   final_zim_path = host_output_dir / f"{script_args.name}.zim"
   final_zim_exists = final_zim_path.exists()
   ```

6. Decide initial run mode:

   * If `final_zim_exists` and **no** `--overwrite`:

     * Log critical and exit (RC 1).

   * If `final_zim_exists` and `--overwrite`:

     * Log that we’re overwriting.
     * Reset **persistent** state values via `crawl_state._reset_persistent_state_values()` and save.
     * **Ignore** any previous config/warcs/stats; set:

       * `can_resume_crawl = False`
       * `has_prior_warcs = False`
       * `warc_file_count = 0`
       * `last_stats = None`
     * Set `initial_run_mode = "Fresh Crawl (Overwrite)"`.

   * Else if can_resume_crawl:

     * `initial_run_mode = "Resume Crawl"`.

   * Else if has_prior_warcs:

     * `initial_run_mode = "New Crawl (with Consolidation)"`.

   * Else:

     * `initial_run_mode = "Fresh Crawl"`.
     * `crawl_state.reset_adaptation_counts()` to clear rotations/reductions.

The run mode only affects initial stage naming and logic in the outer loop.

### 8.4 Main crawl/resume loop

Core persistent loop:

```python
current_stage_name = "Initial Crawl" / "Resume Crawl" / "New Crawl Phase"
stage_attempt = 1
max_crawl_stages = 100
final_status = "failed"
monitor_queue = Queue()
required_run_args = {"seeds": script_args.seeds, "name": script_args.name}
```

Outer loop condition: `while stage_attempt <= max_crawl_stages and not stop_event.is_set():`

Each iteration is a **logical stage** (Initial, Resume, or New Crawl Phase) + attempt number.

#### 8.4.1 Stage prep

* `stage_name_with_attempt = f"{current_stage_name} - Attempt {stage_attempt}"`
* Set `crawl_state.current_stage` and `crawl_state.stage_start_time`.
* Determine `extra_run_args`:

  * For "Resume Crawl":

    * Re-evaluate current temp dirs and `latest_temp_dir`.
    * `config_yaml_path = utils.find_latest_config_yaml(current_latest_temp_dir)`.
    * Convert to container path: `container_yaml = utils.host_to_container_path(config_yaml_path, host_output_dir)`.
    * Set `extra_run_args = ["--config", container_yaml]`.
    * If config absent or conversion fails:

      * Switch `current_stage_name` to `"New Crawl Phase"` and continue.

#### 8.4.2 Build zimit args & start container

* `zimit_args = docker_runner.build_zimit_args(zimit_passthrough_args, required_run_args, crawl_state.current_workers, is_final_build=False, extra_args=extra_run_args)`.

* Call:

  ```python
  docker_process, container_id = docker_runner.start_docker_container(
      script_args.docker_image,
      host_output_dir,
      zimit_args,
      script_args.name
  )
  ```

* If `docker_process` is `None` → fatal: `final_status = "docker_start_failed"` → break.

* If `container_id` is missing but process alive:

  * `stage_status = "running_no_monitor"`.

* Else:

  * `stage_status = "running"`.

#### 8.4.3 Reset runtime state for the new stage

* Set:

  ```python
  crawl_state.status = "running"
  crawl_state.exit_code = None
  crawl_state.last_crawled_count = -1
  crawl_state.last_total_count = -1
  crawl_state.last_pending_count = -1
  crawl_state.last_failed_count = -1
  crawl_state.last_progress_timestamp = None
  crawl_state.last_stats_timestamp = None
  crawl_state.previous_crawled_count = -1
  crawl_state.previous_stats_timestamp = None
  crawl_state.progress_rate_ppm = 0.0
  crawl_state.reset_runtime_errors()
  ```

#### 8.4.4 Start monitor thread (if possible)

If `container_id` and `--enable-monitoring`:

* Instantiate `CrawlMonitor` with:

  * `container_id`
  * `docker_process` (Popen)
  * `crawl_state`
  * `script_args`
  * `monitor_queue`
  * global `stop_event`
* `active_monitor.start()`.

If monitor can’t start, continue without monitoring (but with warnings).

#### 8.4.5 Inner monitoring loop

Loop:

```python
while docker_process.poll() is None:
    ...
```

Within this:

1. Respect `stop_event`:

   * If set, `stage_status = "stopped"`, `break`.

2. If there is an `active_monitor`:

   * Attempt `monitor_queue.get(timeout=1.0)`.
   * If message arrives:

     * `event_status = message["status"]`.
     * `event_reason = message.get("reason")`.

   Process events:

   * `status == "progress"`:

     * No immediate action; progress lines are printed by timed block below.

   * `status in ["stalled", "error"]`:

     * Log “Intervention Triggered”.

     * `adaptation_performed_type = None`.

     * If `enable_adaptive_workers`:

       * If `strategies.attempt_worker_reduction(...)` returns `True`:

         * Set `adaptation_performed_type = "worker_reduction"`.
         * `stage_status = "stopped_for_adaptation"`.
         * `break` inner loop (container has been stopped).

     * If still no adaptation and `enable_vpn_rotation`:

       * If `strategies.attempt_vpn_rotation(...)` returns `True`:

         * `adaptation_performed_type = "vpn_rotation"`.
         * **Do not break**; we continue monitoring with same container.

     * If `adaptation_performed_type is None`:

       * No adaptation executed; apply backoff if configured:

         * `script_args.backoff_delay_minutes > 0`:

           * Wait (with `stop_event.wait`) for that many seconds.
           * If stop_event set during wait → `stage_status = "stopped"`, `break`.
           * Else reset runtime errors.
         * Else:

           * Reset runtime errors immediately.

3. Without monitor:

   * Sleep 1s to avoid spinning.

4. Periodic progress printing:

   * Every `print_interval_seconds` (default ~60s):

     * If we have valid stats (`last_crawled_count >= 0`):

       * Compute elapsed: `format_duration(now - stage_start_time)`.

       * Compute percent if `total > 0`.

       * Build a status line:

         ```
         [StageName - Attempt N | 0:13:42] Crawled: C/T (P%) | Rate: R ppm | Pending: P | Failed: F | Workers: W | VRot: vRot/max | WRed: wRed/max | Errs(T/H/O): t/h/o
         ```

       * Print padded to terminal width with `end="\r"` to overwrite.

#### 8.4.6 End of stage: cleanup & status resolution

When:

* Docker process exits, or
* Inner loop breaks for adaptation/stop,

then:

1. Print newline to flush last carriage-return status line.

2. Stop monitor thread:

   * If `active_monitor` alive:

     * Set `active_monitor.stop_event` and `join(timeout=5)`.

3. Determine `final_rc = docker_process.returncode`. If `None`, double-check via `.poll()`.

4. Record `crawl_state.exit_code = final_rc`.

5. Identify temp dir for this stage:

   * Search `host_output_dir` for `archive_<current_stage_name>_*.combined.log` sorted by mtime.
   * If a log present:

     * Try `utils.parse_temp_dir_from_log_file(latest_stage_log, host_output_dir)`.
   * If no path found:

     * Fallback `utils.find_latest_temp_dir_fallback(host_output_dir)`.
   * If a temp dir is found:

     * `crawl_state.add_temp_dir(temp_dir_host_path)`.
   * Else:

     * Log critical that temp dir couldn’t be determined.

6. Resolve final `stage_status`:

   * If `stage_status` in `["running", "running_no_monitor"]` (i.e., we’re here only because process exited):

     * If `final_rc == 0`:

       * `stage_status = "success"`.
     * Else if `final_rc` in `ACCEPTABLE_CRAWLER_EXIT_CODES` (32 or 33 – size/time limit hits):

       * Treat as `success` (non-fatal).
     * Else:

       * `stage_status = "failed"`.

   * If `stage_status == "stopped"`:

     * Keep as is (interrupt/signal case).

   * If `stage_status == "stopped_for_adaptation"`:

     * Keep as is (intentional stop for worker reductions).

7. Decide what to do next:

   * `stage_status == "success"`:

     * `final_status = "pending_final_build"`.
     * `break` outer loop to advance to final WARCs build.

   * `stage_status == "stopped_for_adaptation"`:

     * Set `current_stage_name = "Resume Crawl"`.
     * Do **not** increment `stage_attempt`.
     * Optionally apply post-adaptation backoff.
     * Continue outer loop.

   * `stage_status == "stopped"`:

     * `final_status = "stopped"`.
     * `break` outer loop.

   * `stage_status == "failed"`:

     * If `stage_attempt >= max_crawl_stages`:

       * `final_status = "failed_max_attempts"`.
       * `break`.
     * Else:

       * Set `current_stage_name = "Resume Crawl"`.
       * `stage_attempt += 1`.
       * Apply post-failure backoff.
       * Continue outer loop.

### 8.5 Final WARCs consolidation

**Only executed if** `final_status == "pending_final_build"` and `stop_event` is not set.

Steps:

1. Compute `warc_host_paths = utils.find_all_warc_files(crawl_state.get_temp_dir_paths())`.

   * If empty → `final_status = "failed_no_warcs"`.

2. Convert each WARC to container path with `host_to_container_path`.

   * If any conversion fails → `final_status = "failed_warc_path_conversion"`.

3. Build comma-separated WARCs string: `container_warc_paths_str`.

4. Filter zimit args for final build:

   * `final_build_base_args = utils.filter_args_for_final_run(zimit_passthrough_args)`.

5. Compose extra args for final build:

   * `extra_args_final = ["--warcs", container_warc_paths_str]`.

6. Required args: `{"name": script_args.name}`.

7. Call `run_final_build_stage_sync(...)` with:

   * `stage_name="Final Build from WARCs"`
   * `docker_image=script_args.docker_image`
   * `host_output_dir`
   * `crawl_state`
   * `script_args`
   * `passthrough_args=final_build_base_args`
   * `required_args={"name": ...}`
   * `extra_args=extra_args_final`

8. `run_final_build_stage_sync`:

   * Ensures `--name` is present in args.
   * Injects **first seed** with `--seeds <script_args.seeds[0]>` as a workaround (zimit wants seeds even with `--warcs`).
   * Calls `build_zimit_args(..., is_final_build=True, extra_args=extra_args_final)`.
   * Runs `docker run` synchronously with `capture_output=True`.
   * Writes three logs:

     * `archive_final_build_from_warcs_<TS>.stdout.log`
     * `archive_final_build_from_warcs_<TS>.stderr.log`
     * `archive_final_build_from_warcs_<TS>.combined.log`
   * Attempts to parse a temp dir from logs (may or may not exist).
   * If `returncode == 0` → `stage_status="success"`, else `"failed"`.

9. `main()` sets `final_status = build_status` and, if temp dir discovered, adds it to state.

### 8.6 Summary & cleanup, exit codes

* Fetch final `temp_dir_paths = crawl_state.get_temp_dir_paths()`.

If `final_status == "success"`:

* Check `<output-dir>/<name>.zim` exists and log size.
* If `--cleanup`:

  * `utils.cleanup_temp_dirs(temp_dir_paths, crawl_state.state_file_path)`
* Else:

  * Log surviving temp dirs and state file path.
* `sys.exit(0)`.

Else:

* Log failure/stopped status.
* Temp dirs & state file are left intact for debugging/resume.
* `sys.exit(1)` (or `2` in the top-level `if __name__ == "__main__"` wrapper if an uncaught exception escapes main).

---

## 9. Filesystem layout and naming conventions

Given `--output-dir /data/archive` and `--name example`, you’ll see:

* State file:

  * `/data/archive/.archive_state.json`

* Crawl-stage logs (per stage & attempt):

  * `/data/archive/archive_initial_crawl_-_attempt_1_<TS>.stdout.log`
  * `/data/archive/archive_initial_crawl_-_attempt_1_<TS>.stderr.log`
  * `/data/archive/archive_initial_crawl_-_attempt_1_<TS>.combined.log`
  * Similarly for `"resume_crawl"` / `"new_crawl_phase"` etc.

* Final build logs:

  * `/data/archive/archive_final_build_from_warcs_<TS>.stdout.log`
  * `/data/archive/archive_final_build_from_warcs_<TS>.stderr.log`
  * `/data/archive/archive_final_build_from_warcs_<TS>.combined.log`

* ZIM file:

  * `/data/archive/example.zim`

* Temp directories:

  * `/data/archive/.tmpXXXXX/`

    * `collections/crawl-*/crawls/crawl-*.yaml`
    * `collections/crawl-*/archive/**/*.warc.gz`

---

## 10. Operational runbook (how to actually use it)

### 10.1 Fresh crawl from scratch

```bash
./run_archive.py \
  --seeds https://www.canada.ca/en/public-health.html \
  --name phac-2025 \
  --output-dir /mnt/nasd/archives/phac \
  --initial-workers 4 \
  --enable-monitoring \
  --enable-adaptive-workers \
  --max-worker-reductions 3 \
  --enable-vpn-rotation \
  --vpn-connect-command "nordvpn connect us" \
  --backoff-delay-minutes 10 \
  --cleanup \
  -- \
  --profile my-zimit-profile \
  --scope-page \
  --max-pages 500000
```

* First run: `initial_run_mode = "Fresh Crawl"`.
* If stable and finishes → final ZIM built and temp dirs cleaned.
* If unstable, it can:

  * Reduce `--workers` down (within limits).
  * Rotate VPN (within limits and frequency).
  * Resume as needed.

### 10.2 Resuming a previous failed run

If your prior run died / was interrupted:

* Make sure you still have:

  * `.archive_state.json`
  * `.tmp*` directories with `collections/...`.
  * `archive_*` log files.

Then simply re-run the **same command** (no special `--resume` flag needed):

* If a live config YAML is found:

  * `initial_run_mode = "Resume Crawl"`.
* If WARCs exist but no config:

  * `initial_run_mode = "New Crawl (with Consolidation)"` (new crawl + final consolidation from *all* discovered WARCs).

### 10.3 Redoing a completed run (overwrite)

If you already have `<name>.zim` and want to re-crawl from scratch (ignoring old temp dirs):

```bash
./run_archive.py \
  --seeds ... \
  --name phac-2025 \
  --output-dir /mnt/nasd/archives/phac \
  --overwrite \
  ...
```

* The tool will:

  * See existing `phac-2025.zim`.
  * Because of `--overwrite`, it **resets persistent state** to fresh values.
  * Start a fresh crawl.
  * Note: old `.tmp*` dirs are still on disk but no longer tracked.

### 10.4 Cleaning up failed runs

* If `--cleanup` was not set and a run failed:

  * `.archive_state.json` and `.tmp*` dirs remain.
  * You can:

    * Use them to resume.
    * Or manually delete them if you want to start over:

      * Remove `.archive_state.json` and `.tmp*` dirs, then re-run with `--overwrite` if you want to ensure a clean state.

---

## 11. Implementation notes / gotchas

* The **monitor and strategies are entirely log-driven**: if Zimit log shape changes (e.g., JSON schema or message names), some of the parsing (`crawlStatus`, `pageStatus`, etc.) may need to be updated.

* **Acceptable non-zero exit codes (32, 33)** are mapped to “success” because they represent Zimit’s size/time limits, not fatal crashes.

* The **final build** always injects a seed (`--seeds first_seed`) as a workaround because Zimit expects at least one seed, even for `--warcs` builds.

* `--vpn-disconnect-command` is **intentionally ignored**, so you’re expected to provide a `vpn_connect_command` that handles both disconnect & reconnect (if needed) or just reconnects to a new endpoint.

* For long runs, **logs can accumulate** (one set per stage attempt). Cleanup only removes temp dirs + state file; logs remain until you manually delete them.
