#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS bootstrap: create /srv/healtharchive/ops/* directories with safe permissions.

This is a one-time (or idempotent) helper so ops scripts can write artifacts to:
  - /srv/healtharchive/ops/baseline
  - /srv/healtharchive/ops/restore-tests
  - /srv/healtharchive/ops/adoption
  - /srv/healtharchive/ops/search-eval

Usage (on the VPS):
  sudo ./scripts/vps-bootstrap-ops-dirs.sh

Options:
  --root DIR     Root healtharchive dir (default: /srv/healtharchive)
  --group NAME   Shared ops group (default: healtharchive)

Notes:
  - This script is intentionally conservative: it will not create the root dir if missing.
  - It sets the setgid bit (2770) so new files inherit the group.
EOF
}

ROOT_DIR="/srv/healtharchive"
OPS_GROUP="healtharchive"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      ROOT_DIR="$2"
      shift 2
      ;;
    --group)
      OPS_GROUP="$2"
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

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: Must be run as root (use sudo)." >&2
  exit 1
fi

if [[ ! -d "${ROOT_DIR}" ]]; then
  echo "ERROR: Root dir does not exist: ${ROOT_DIR}" >&2
  echo "Hint: ensure the production storage layout exists before bootstrapping ops dirs." >&2
  exit 1
fi

if ! getent group "${OPS_GROUP}" >/dev/null 2>&1; then
  echo "ERROR: Group does not exist: ${OPS_GROUP}" >&2
  echo "Hint: create it (and add the operator user) before running this script." >&2
  exit 1
fi

ops_dir="${ROOT_DIR%/}/ops"
subdirs=(
  baseline
  restore-tests
  adoption
  search-eval
)

mkdir -p "${ops_dir}"
for d in "${subdirs[@]}"; do
  mkdir -p "${ops_dir}/${d}"
done

chown -R "root:${OPS_GROUP}" "${ops_dir}"
chmod 2770 "${ops_dir}"
for d in "${subdirs[@]}"; do
  chmod 2770 "${ops_dir}/${d}"
done

echo "OK: bootstrapped ops dirs under: ${ops_dir}"
stat -c '%U:%G %a %n' "${ops_dir}" "${ops_dir}/"* | sed 's/^/  /'
