#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS bootstrap: prepare private observability scaffolding (dirs + secret files).

This script is intentionally limited to "Phase 2" scaffolding:
  - Creates safe, public-safe-ish directories for dashboard exports/provisioning under:
      /srv/healtharchive/ops/observability/
  - Creates root-owned secret files under:
      /etc/healtharchive/observability/

It does NOT install Prometheus/Grafana/exporters and does NOT open any ports.

Usage (on the VPS):
  cd /opt/healtharchive-backend
  sudo ./scripts/vps-bootstrap-observability-scaffold.sh

Options:
  --root DIR         Root healtharchive dir (default: /srv/healtharchive)
  --ops-group NAME   Shared ops group (default: healtharchive)
  --etc-dir DIR      Base /etc dir for healtharchive (default: /etc/healtharchive)

Notes:
  - Secret files are created as root-only (0600) by default.
    Later phases may adjust group ownership so services can read them.
  - Do not store secrets under /srv/healtharchive/ops/ (policy: ops artifacts are public-safe).
EOF
}

ROOT_DIR="/srv/healtharchive"
OPS_GROUP="healtharchive"
ETC_DIR="/etc/healtharchive"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      ROOT_DIR="$2"
      shift 2
      ;;
    --ops-group|--group)
      OPS_GROUP="$2"
      shift 2
      ;;
    --etc-dir)
      ETC_DIR="$2"
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
  echo "Hint: ensure the production storage layout exists before bootstrapping." >&2
  exit 1
fi

if ! getent group "${OPS_GROUP}" >/dev/null 2>&1; then
  echo "ERROR: Group does not exist: ${OPS_GROUP}" >&2
  echo "Hint: create it (and add the operator user) before running this script." >&2
  exit 1
fi

ops_dir="${ROOT_DIR%/}/ops"
obs_dir="${ops_dir}/observability"

install -d -m 2770 -o root -g "${OPS_GROUP}" "${ops_dir}"
install -d -m 2770 -o root -g "${OPS_GROUP}" "${obs_dir}"
install -d -m 2770 -o root -g "${OPS_GROUP}" "${obs_dir}/dashboards"
install -d -m 2770 -o root -g "${OPS_GROUP}" "${obs_dir}/provisioning"
install -d -m 2770 -o root -g "${OPS_GROUP}" "${obs_dir}/notes"

# /etc layout
install -d -m 0750 -o root -g "${OPS_GROUP}" "${ETC_DIR}"
install -d -m 0700 -o root -g root "${ETC_DIR%/}/observability"

secret_files=(
  prometheus_backend_admin_token
  grafana_admin_password
  postgres_grafana_password
)

for f in "${secret_files[@]}"; do
  path="${ETC_DIR%/}/observability/${f}"
  if [[ ! -e "${path}" ]]; then
    install -m 0600 -o root -g root /dev/null "${path}"
  fi
done

echo "OK: bootstrapped observability scaffolding."
echo
echo "Ops dirs:"
stat -c '%U:%G %a %n' \
  "${ops_dir}" \
  "${obs_dir}" \
  "${obs_dir}/dashboards" \
  "${obs_dir}/provisioning" \
  "${obs_dir}/notes" | sed 's/^/  /'
echo
echo "Secret files (populate later; do not commit secrets to git):"
stat -c '%U:%G %a %n' "${ETC_DIR%/}/observability/"* | sed 's/^/  /'
