#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: install/update systemd unit templates from the repo.

Safe-by-default: dry-run unless you pass --apply.

This copies files from:
  /opt/healtharchive-backend/docs/deployment/systemd/
to:
  /etc/systemd/system/

It does NOT enable timers or create sentinel files (see docs/deployment/systemd/README.md).

Usage (on the VPS):
  cd /opt/healtharchive-backend

  # Dry-run (print what would be installed):
  ./scripts/vps-install-systemd-units.sh

  # Apply (requires sudo):
  sudo ./scripts/vps-install-systemd-units.sh --apply

Options:
  --apply            Actually install files (otherwise dry-run)
  --no-daemon-reload Skip `systemctl daemon-reload` after install
  --restart-worker   Restart healtharchive-worker after installing the priority drop-in

Notes:
  - The worker priority drop-in is installed to:
      /etc/systemd/system/healtharchive-worker.service.d/override.conf
EOF
}

APPLY="false"
DAEMON_RELOAD="true"
RESTART_WORKER="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY="true"
      shift 1
      ;;
    --no-daemon-reload)
      DAEMON_RELOAD="false"
      shift 1
      ;;
    --restart-worker)
      RESTART_WORKER="true"
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

src_dir="${REPO_ROOT}/docs/deployment/systemd"
if [[ ! -d "${src_dir}" ]]; then
  echo "ERROR: Missing systemd templates directory: ${src_dir}" >&2
  exit 1
fi

run() {
  if [[ "${APPLY}" != "true" ]]; then
    echo "+ $*"
    return 0
  fi
  "$@"
}

if [[ "${APPLY}" == "true" && "${EUID}" -ne 0 ]]; then
  echo "ERROR: --apply requires root (use sudo)." >&2
  exit 1
fi

dst_dir="/etc/systemd/system"

files=()
while IFS= read -r -d '' f; do
  files+=("${f}")
done < <(find "${src_dir}" -maxdepth 1 -type f \( -name 'healtharchive-*.service' -o -name 'healtharchive-*.timer' \) -print0 | sort -z)

if [[ ${#files[@]} -eq 0 ]]; then
  echo "ERROR: No unit files found under: ${src_dir}" >&2
  exit 1
fi

for f in "${files[@]}"; do
  base="$(basename "${f}")"
  run install -m 0644 -o root -g root "${f}" "${dst_dir}/${base}"
done

# Worker priority drop-in (safe, always-on).
dropin_dir="${dst_dir}/healtharchive-worker.service.d"
run install -d -m 0755 -o root -g root "${dropin_dir}"
run install -m 0644 -o root -g root \
  "${src_dir}/healtharchive-worker.service.override.conf" \
  "${dropin_dir}/override.conf"

if [[ "${DAEMON_RELOAD}" == "true" ]]; then
  run systemctl daemon-reload
fi

if [[ "${RESTART_WORKER}" == "true" ]]; then
  run systemctl restart healtharchive-worker
fi

echo "OK: systemd unit templates installed/updated."
echo "Next: enable timers + sentinel files per: docs/deployment/systemd/README.md"

