#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: hot-path staleness Phase 2 drill (safe-by-default)

Purpose:
  - Capture a pre/post evidence bundle (read-only)
  - Optionally run the storage hot-path watchdog in dry-run simulation mode
  - Diff the bundles and append a small log line for later correlation

This script DOES NOT:
  - stop/start services
  - unmount mounts
  - write to node_exporter collector dirs
unless you explicitly add your own commands outside of this drill.

Usage (on the VPS):
  cd /opt/healtharchive-backend
  ./scripts/vps-hotpath-staleness-drill.sh --simulate-broken-path /srv/healtharchive/jobs/hc/<JOB_DIR>

Options:
  --simulate-broken-path PATH   Optional. If set, runs watchdog in dry-run simulation mode.
  --out-root DIR                Evidence out root (default: /srv/healtharchive/ops/observability/hotpath-staleness)
  --since-minutes N             Journal window for bundles (default: 240)
  --year YEAR                   Campaign year for `vps-crawl-status.sh` (default: current UTC year)
  --note TEXT                   Optional note for the TSV log (no secrets)
  --no-diff                     Skip running the diff helper

Output:
  - Prints pre/post bundle paths
  - Writes a TSV record to:
      <out-root>/investigation-log.tsv
    (falls back to /tmp if not writable)
EOF
}

SIMULATE_BROKEN_PATH=""
OUT_ROOT="/srv/healtharchive/ops/observability/hotpath-staleness"
SINCE_MINUTES="240"
YEAR="$(date -u +%Y)"
NOTE=""
NO_DIFF="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --simulate-broken-path)
      SIMULATE_BROKEN_PATH="${2:-}"
      shift 2
      ;;
    --out-root)
      OUT_ROOT="${2:-}"
      shift 2
      ;;
    --since-minutes)
      SINCE_MINUTES="${2:-}"
      shift 2
      ;;
    --year)
      YEAR="${2:-}"
      shift 2
      ;;
    --note)
      NOTE="${2:-}"
      shift 2
      ;;
    --no-diff)
      NO_DIFF="1"
      shift 1
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "${YEAR}" =~ ^[0-9]{4}$ ]]; then
  echo "ERROR: --year must be a 4-digit year (got: ${YEAR})" >&2
  exit 2
fi

if ! [[ "${SINCE_MINUTES}" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --since-minutes must be an integer (got: ${SINCE_MINUTES})" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "${script_dir}/.." && pwd)"

capture_bundle() {
  local tag="$1"
  local out
  out="$("${repo_dir}/scripts/vps-capture-hotpath-staleness-evidence.sh" \
    --out-root "${OUT_ROOT}" \
    --since-minutes "${SINCE_MINUTES}" \
    --year "${YEAR}" \
    --tag "${tag}" 2>&1 | tee /dev/stderr || true)"

  local bundle
  bundle="$(printf "%s\n" "${out}" | sed -n 's/^OK: evidence bundle complete: //p' | tail -n 1)"
  if [[ -z "${bundle}" ]]; then
    echo "ERROR: could not parse bundle dir from capture output (tag=${tag})" >&2
    return 2
  fi
  echo "${bundle}"
}

append_log_tsv() {
  local log_root="${OUT_ROOT}"
  if ! mkdir -p "${log_root}" 2>/dev/null; then
    log_root="/tmp/healtharchive-hotpath-staleness"
    mkdir -p "${log_root}" || true
  fi
  local log_file="${log_root%/}/investigation-log.tsv"

  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  # Keep it parseable and safe: strip newlines/tabs.
  local note_sanitized
  note_sanitized="$(printf "%s" "${NOTE}" | tr '\n\t' '  ')"
  local simulate_sanitized
  simulate_sanitized="$(printf "%s" "${SIMULATE_BROKEN_PATH}" | tr '\n\t' '  ')"

  if [[ ! -f "${log_file}" ]]; then
    printf "timestamp_utc\tevent\tpre_bundle\tpost_bundle\tsimulate_broken_path\tnote\n" >>"${log_file}" 2>/dev/null || true
  fi

  printf "%s\t%s\t%s\t%s\t%s\t%s\n" \
    "${ts}" \
    "phase2_drill" \
    "${PRE_BUNDLE}" \
    "${POST_BUNDLE}" \
    "${simulate_sanitized}" \
    "${note_sanitized}" >>"${log_file}" 2>/dev/null || true

  echo "OK: appended log: ${log_file}"
}

echo "HealthArchive hot-path staleness Phase 2 drill"
echo "--------------------------------------------"
echo "out_root=${OUT_ROOT}"
echo "since_minutes=${SINCE_MINUTES}"
echo "year=${YEAR}"
echo "simulate_broken_path=${SIMULATE_BROKEN_PATH:-<none>}"
echo ""

PRE_BUNDLE="$(capture_bundle "drill-pre")"
echo "OK: pre_bundle=${PRE_BUNDLE}"
echo ""

drill_work_dir="$(mktemp -d /tmp/healtharchive-hotpath-staleness-drill.XXXXXX)"
trap 'rm -rf "${drill_work_dir}"' EXIT

  if [[ -n "${SIMULATE_BROKEN_PATH}" ]]; then
  echo "OK: running watchdog simulation (dry-run)"
  drill_out="${drill_work_dir}/watchdog-simulation.txt"

  # Match the drill guidance in docs: keep state/lock/metrics isolated under /tmp.
  set +e
  sudo bash -lc "set -a; source /etc/healtharchive/backend.env; set +a; \
    /opt/healtharchive-backend/.venv/bin/python3 /opt/healtharchive-backend/scripts/vps-storage-hotpath-auto-recover.py \
      --confirm-runs 1 \
      --min-failure-age-seconds 0 \
      --state-file /tmp/healtharchive-storage-hotpath-drill.state.json \
      --lock-file /tmp/healtharchive-storage-hotpath-drill.lock \
      --textfile-out-dir /tmp \
      --textfile-out-file healtharchive_storage_hotpath_auto_recover.drill.prom \
      --simulate-broken-path '${SIMULATE_BROKEN_PATH}'" 2>&1 | tee "${drill_out}"
  rc="${PIPESTATUS[0]}"
  set -e
  echo "OK: watchdog simulation rc=${rc} output=${drill_out}"
else
  echo "OK: skipping watchdog simulation (no --simulate-broken-path)"
fi

POST_BUNDLE="$(capture_bundle "drill-post")"
echo "OK: post_bundle=${POST_BUNDLE}"
echo ""

if [[ -f "${drill_work_dir}/watchdog-simulation.txt" ]]; then
  cp -av "${drill_work_dir}/watchdog-simulation.txt" "${POST_BUNDLE}/watchdog-simulation.txt" >/dev/null 2>&1 || true
fi

append_log_tsv

if [[ "${NO_DIFF}" == "1" ]]; then
  echo "OK: skipping diff (--no-diff)"
else
  "${repo_dir}/scripts/vps-diff-hotpath-staleness-evidence.sh" --before "${PRE_BUNDLE}" --after "${POST_BUNDLE}" || true
fi

echo ""
echo "Done."
