#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: run a small "crawl rehearsal" (capped crawl + indexing).

Purpose:
  Exercise the end-to-end path (create-job -> validate-job-config -> run-db-job -> index-job)
  without touching the production database or archive root.

How it stays safe:
  - Uses a temporary SQLite DB under the report directory (default).
  - Uses a temporary archive root under the report directory (default).
  - Defaults to DRY-RUN (prints commands); pass --apply to actually run a crawl.

Usage (on the VPS):
  cd /opt/healtharchive-backend

  # Dry-run:
  ./scripts/vps-smoke-crawl-rehearsal.sh --source cihr

  # Apply (recommended small cap):
  ./scripts/vps-smoke-crawl-rehearsal.sh --apply --source cihr --page-limit 25 --depth 1

Options:
  --apply                 Actually run the crawl and indexing (default: dry-run only)
  --source CODE           Source code: hc|phac|cihr (default: cihr)
  --page-limit N          Zimit --pageLimit (default: 25)
  --depth N               Zimit --depth (default: 1)
  --out-root DIR          Root dir for artifacts (default: /srv/healtharchive/ops/rehearsal)
  --keep-sandbox          Do not remove sandbox files on success (default: keep)
  --cleanup-on-success    Remove sandbox files on success (DB + archive-root) (default: keep)
  --no-resource-monitor   Do not record resource peaks during crawl/index
  -h, --help              Show help

Notes:
  - This will make real outbound requests to the target site, but should remain low-impact
    due to the page/depth caps.
EOF
}

APPLY="false"
SOURCE_CODE="cihr"
PAGE_LIMIT="25"
DEPTH="1"
OUT_ROOT="/srv/healtharchive/ops/rehearsal"
KEEP_SANDBOX="true"
CLEANUP_ON_SUCCESS="false"
RESOURCE_MONITOR="true"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY="true"
      shift 1
      ;;
    --source)
      SOURCE_CODE="$2"
      shift 2
      ;;
    --page-limit)
      PAGE_LIMIT="$2"
      shift 2
      ;;
    --depth)
      DEPTH="$2"
      shift 2
      ;;
    --out-root)
      OUT_ROOT="$2"
      shift 2
      ;;
    --keep-sandbox)
      KEEP_SANDBOX="true"
      CLEANUP_ON_SUCCESS="false"
      shift 1
      ;;
    --cleanup-on-success)
      CLEANUP_ON_SUCCESS="true"
      KEEP_SANDBOX="false"
      shift 1
      ;;
    --no-resource-monitor)
      RESOURCE_MONITOR="false"
      shift 1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "${SOURCE_CODE}" in
  hc|phac|cihr) ;;
  *)
    echo "ERROR: --source must be one of: hc, phac, cihr (got: ${SOURCE_CODE})" >&2
    exit 2
    ;;
esac

if ! [[ "${PAGE_LIMIT}" =~ ^[0-9]+$ ]] || [[ "${PAGE_LIMIT}" -le 0 ]]; then
  echo "ERROR: --page-limit must be a positive integer (got: ${PAGE_LIMIT})" >&2
  exit 2
fi

if ! [[ "${DEPTH}" =~ ^[0-9]+$ ]] || [[ "${DEPTH}" -le 0 ]]; then
  echo "ERROR: --depth must be a positive integer (got: ${DEPTH})" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

VENV_BIN="${REPO_DIR}/.venv/bin"
if [[ ! -x "${VENV_BIN}/python3" || ! -x "${VENV_BIN}/ha-backend" || ! -x "${VENV_BIN}/alembic" ]]; then
  echo "ERROR: missing venv executables under ${VENV_BIN} (expected python3, ha-backend, alembic)" >&2
  exit 1
fi

timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
RUN_DIR="${OUT_ROOT%/}/${timestamp}"
SANDBOX_DIR="${RUN_DIR}/sandbox"
ARCHIVE_ROOT="${SANDBOX_DIR}/archive-root"
DB_PATH="${SANDBOX_DIR}/rehearsal.db"
DATABASE_URL="sqlite:///${DB_PATH}"

run() {
  if [[ "${APPLY}" != "true" ]]; then
    echo "+ $*"
    return 0
  fi
  "$@"
}

run_capture() {
  local label="$1"
  local outfile="$2"
  shift 2
  local cmd=("$@")

  if [[ "${APPLY}" != "true" ]]; then
    echo "+ (${label}) ${cmd[*]}"
    return 0
  fi

  echo "Running: ${label} (capturing to ${RUN_DIR}/${outfile})"

  set +e
  {
    echo "== ${label} =="
    echo "timestamp_utc=${timestamp}"
    echo "cmd=${cmd[*]}"
    echo ""
    "${cmd[@]}"
  } >"${RUN_DIR}/${outfile}" 2>&1
  local rc=$?
  set -e

  if [[ "${rc}" -ne 0 ]]; then
    echo "ERROR: ${label} failed (rc=${rc})" >&2
    echo "  See: ${RUN_DIR}/${outfile}" >&2
    tail -n 30 "${RUN_DIR}/${outfile}" 2>/dev/null | sed 's/^/  | /' >&2 || true
    return "${rc}"
  fi
  return 0
}

cleanup_sandbox() {
  if [[ "${APPLY}" != "true" ]]; then
    return 0
  fi
  if [[ "${CLEANUP_ON_SUCCESS}" != "true" ]]; then
    return 0
  fi
  rm -rf "${SANDBOX_DIR}"
}

mkdir -p "${RUN_DIR}"
mkdir -p "${SANDBOX_DIR}"
mkdir -p "${ARCHIVE_ROOT}"

{
  echo "timestamp_utc=${timestamp}"
  echo "repo_dir=${REPO_DIR}"
  if command -v git >/dev/null 2>&1 && [[ -d "${REPO_DIR}/.git" ]]; then
    echo "git_head=$(git -C "${REPO_DIR}" rev-parse HEAD 2>/dev/null || echo unknown)"
  fi
  echo "apply=${APPLY}"
  echo "source=${SOURCE_CODE}"
  echo "page_limit=${PAGE_LIMIT}"
  echo "depth=${DEPTH}"
  echo "run_dir=${RUN_DIR}"
  echo "sandbox_dir=${SANDBOX_DIR}"
  echo "database_url=${DATABASE_URL}"
  echo "archive_root=${ARCHIVE_ROOT}"
} > "${RUN_DIR}/00-meta.txt"

echo "HealthArchive smoke crawl rehearsal"
echo "----------------------------------"
echo "Mode:      $([[ "${APPLY}" == "true" ]] && echo APPLY || echo DRY-RUN)"
echo "Source:    ${SOURCE_CODE}"
echo "Caps:      --pageLimit ${PAGE_LIMIT} --depth ${DEPTH}"
echo "Run dir:   ${RUN_DIR}"
echo "DB:        ${DATABASE_URL}"
echo "Archive:   ${ARCHIVE_ROOT}"
echo ""

# Keep this run isolated from production by overriding the two core env vars.
export HEALTHARCHIVE_DATABASE_URL="${DATABASE_URL}"
export HEALTHARCHIVE_ARCHIVE_ROOT="${ARCHIVE_ROOT}"

run_capture "Alembic upgrade" "01-alembic-upgrade.txt" "${VENV_BIN}/alembic" upgrade head
run_capture "Seed sources" "02-seed-sources.txt" "${VENV_BIN}/ha-backend" seed-sources

create_out="${RUN_DIR}/03-create-job.txt"
if [[ "${APPLY}" != "true" ]]; then
  echo "+ (${create_out}) ${VENV_BIN}/ha-backend create-job --source ${SOURCE_CODE} --page-limit ${PAGE_LIMIT} --depth ${DEPTH}"
  echo ""
  echo "Dry-run complete. Re-run with --apply to execute."
  exit 0
fi

run_capture "Create job" "03-create-job.txt" "${VENV_BIN}/ha-backend" create-job --source "${SOURCE_CODE}" --page-limit "${PAGE_LIMIT}" --depth "${DEPTH}"

job_id="$(awk -F':' '/^[[:space:]]*ID:[[:space:]]*/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2; exit}' "${RUN_DIR}/03-create-job.txt" || true)"
if ! [[ "${job_id}" =~ ^[0-9]+$ ]]; then
  echo "ERROR: could not parse job id from ${RUN_DIR}/03-create-job.txt" >&2
  echo "Hint: inspect the file for the 'ID:' line." >&2
  exit 1
fi
echo "Job ID: ${job_id}"

run_capture "Validate job config (archive-tool dry-run)" "04-validate-job-config.txt" "${VENV_BIN}/ha-backend" validate-job-config --id "${job_id}"

monitor_pid=""
monitor_file="${RUN_DIR}/98-resource-monitor.jsonl"

cleanup_on_exit() {
  stop_monitor
  write_resource_summary || true
}
trap cleanup_on_exit EXIT

start_monitor() {
  if [[ "${APPLY}" != "true" || "${RESOURCE_MONITOR}" != "true" ]]; then
    return 0
  fi
  if [[ -n "${monitor_pid}" ]]; then
    return 0
  fi
  : > "${monitor_file}"
  (
    set +e
    while true; do
      ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
      load="$(cat /proc/loadavg 2>/dev/null | awk '{print $1" "$2" "$3}' || echo '')"
      mem_kv="$(awk -F':' '/^(MemTotal|MemAvailable|SwapTotal|SwapFree):/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $1":"$2}' /proc/meminfo 2>/dev/null)"
      root_df="$(df -P / 2>/dev/null | tail -n 1 | awk '{print $5}' | tr -d '%' || echo '')"
      arc_df="$(df -P "${ARCHIVE_ROOT}" 2>/dev/null | tail -n 1 | awk '{print $5}' | tr -d '%' || echo '')"

      zimit_id="$(docker ps -q --filter 'ancestor=ghcr.io/openzim/zimit' 2>/dev/null | head -n 1 || true)"
      zimit_cpu=""
      zimit_mem=""
      zimit_mem_perc=""
      if [[ -n "${zimit_id}" ]]; then
        stats="$(docker stats --no-stream --format '{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' "${zimit_id}" 2>/dev/null || true)"
        zimit_cpu="$(printf '%s' "${stats}" | awk -F'\t' '{print $1}' || true)"
        zimit_mem="$(printf '%s' "${stats}" | awk -F'\t' '{print $2}' || true)"
        zimit_mem_perc="$(printf '%s' "${stats}" | awk -F'\t' '{print $3}' || true)"
      fi

      printf '{'
      printf '"ts":"%s",' "${ts}"
      printf '"load":"%s",' "${load}"
      printf '"meminfo":"%s",' "$(printf '%s' "${mem_kv}" | tr '\n' ';' | sed 's/;*$//')"
      printf '"rootUsedPct":"%s",' "${root_df}"
      printf '"archiveUsedPct":"%s",' "${arc_df}"
      printf '"zimitContainerId":"%s",' "${zimit_id}"
      printf '"zimitCpuPerc":"%s",' "${zimit_cpu}"
      printf '"zimitMemUsage":"%s",' "${zimit_mem}"
      printf '"zimitMemPerc":"%s"' "${zimit_mem_perc}"
      printf '}\n'

      sleep 5
    done
  ) >> "${monitor_file}" 2>/dev/null &
  monitor_pid=$!
}

stop_monitor() {
  if [[ -z "${monitor_pid}" ]]; then
    return 0
  fi
  kill "${monitor_pid}" >/dev/null 2>&1 || true
  wait "${monitor_pid}" >/dev/null 2>&1 || true
  monitor_pid=""
}

write_resource_summary() {
  if [[ "${APPLY}" != "true" || "${RESOURCE_MONITOR}" != "true" ]]; then
    return 0
  fi
  if [[ ! -s "${monitor_file}" ]]; then
    return 0
  fi
  "${VENV_BIN}/python3" - "${monitor_file}" "${RUN_DIR}/98-resource-summary.json" <<'PY'
import json
import re
import sys
from pathlib import Path

monitor_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])

def parse_meminfo(meminfo: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in (meminfo or "").split(";"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        m = re.search(r"([0-9]+)\s*kB", v)
        if not m:
            continue
        out[k.strip()] = int(m.group(1)) * 1024
    return out

def parse_percent(s: str) -> float | None:
    s = (s or "").strip().rstrip("%")
    try:
        return float(s)
    except ValueError:
        return None

def parse_load(s: str) -> tuple[float, float, float] | None:
    parts = (s or "").split()
    if len(parts) < 3:
        return None
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        return None

cpu_peak: float | None = None
mem_min: int | None = None
swap_peak: int | None = None
root_peak: float | None = None
arc_peak: float | None = None
load15_peak: float | None = None
samples = 0

for line in monitor_path.read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        continue
    samples += 1

    mi = parse_meminfo(row.get("meminfo", ""))
    mem_av = mi.get("MemAvailable")
    if mem_av is not None:
        mem_min = mem_av if mem_min is None else min(mem_min, mem_av)
    swap_total = mi.get("SwapTotal")
    swap_free = mi.get("SwapFree")
    if swap_total is not None and swap_free is not None:
        swap_used = max(0, swap_total - swap_free)
        swap_peak = swap_used if swap_peak is None else max(swap_peak, swap_used)

    root = parse_percent(row.get("rootUsedPct", ""))
    if root is not None:
        root_peak = root if root_peak is None else max(root_peak, root)
    arc = parse_percent(row.get("archiveUsedPct", ""))
    if arc is not None:
        arc_peak = arc if arc_peak is None else max(arc_peak, arc)

    load = parse_load(row.get("load", ""))
    if load is not None:
        load15_peak = load[2] if load15_peak is None else max(load15_peak, load[2])

    cpu = parse_percent(row.get("zimitCpuPerc", ""))
    if cpu is not None:
        cpu_peak = cpu if cpu_peak is None else max(cpu_peak, cpu)

out = {
    "samples": samples,
    "minMemAvailableBytes": mem_min,
    "maxSwapUsedBytes": swap_peak,
    "maxRootUsedPercent": root_peak,
    "maxArchiveUsedPercent": arc_peak,
    "maxZimitCpuPercent": cpu_peak,
    "maxLoad15": load15_peak,
}
out_path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

start_monitor
run_capture "Run crawl" "05-run-db-job.txt" "${VENV_BIN}/ha-backend" run-db-job --id "${job_id}"
run_capture "Index job" "06-index-job.txt" "${VENV_BIN}/ha-backend" index-job --id "${job_id}"
run_capture "Show job" "07-show-job.txt" "${VENV_BIN}/ha-backend" show-job --id "${job_id}"

cleanup_sandbox

echo ""
echo "OK: rehearsal completed."
echo "Artifacts: ${RUN_DIR}"
exit 0
