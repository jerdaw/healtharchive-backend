#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: write a daily storage watchdog burn-in snapshot artifact.

This is a read-only summary:
  - reads watchdog state JSON
  - reads node_exporter textfile metrics
  - writes a dated JSON report under an ops artifact directory

Usage (on the VPS):
  /opt/healtharchive-backend/scripts/vps-storage-watchdog-burnin-snapshot.sh

Options:
  --out-dir DIR     Output directory (default: /srv/healtharchive/ops/burnin/storage-watchdog)
  --keep-days N     Delete matching snapshots older than N days (default: 30)
  --python PATH     Python interpreter (default: /usr/bin/python3)
EOF
}

OUT_DIR="/srv/healtharchive/ops/burnin/storage-watchdog"
KEEP_DAYS="30"
PYTHON="/usr/bin/python3"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out-dir)
      OUT_DIR="$2"
      shift 2
      ;;
    --keep-days)
      KEEP_DAYS="$2"
      shift 2
      ;;
    --python)
      PYTHON="$2"
      shift 2
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

if [[ ! -d "${OUT_DIR}" ]]; then
  echo "ERROR: Output dir does not exist: ${OUT_DIR}" >&2
  echo "Hint: run: sudo /opt/healtharchive-backend/scripts/vps-bootstrap-ops-dirs.sh" >&2
  exit 1
fi

if [[ ! "${KEEP_DAYS}" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --keep-days must be an integer (got: ${KEEP_DAYS})" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
report_py="${repo_root}/scripts/vps-storage-watchdog-burnin-report.py"
if [[ ! -f "${report_py}" ]]; then
  echo "ERROR: Missing burn-in report script: ${report_py}" >&2
  exit 1
fi

ymd="$(date -u +%Y%m%d)"
out_file="${OUT_DIR%/}/storage-watchdog-burnin-${ymd}.json"
tmp_file="${out_file}.tmp.$$"

"${PYTHON}" "${report_py}" --json >"${tmp_file}"
mv -f "${tmp_file}" "${out_file}"
ln -sfn "$(basename "${out_file}")" "${OUT_DIR%/}/latest.json"

# Best-effort retention. Only delete our own snapshot pattern.
find "${OUT_DIR%/}" -maxdepth 1 -type f -name 'storage-watchdog-burnin-*.json' -mtime +"${KEEP_DAYS}" -delete || true

echo "OK: wrote ${out_file}"
