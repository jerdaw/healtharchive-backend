#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: provision Grafana dashboards for observability + private usage.

Phases:
  - "6 — Dashboards: ops health"
  - "7 — Dashboards: expanded private usage"

Safe-by-default: dry-run unless you pass --apply.

What this does (when run with --apply):

- Copies dashboard JSON from this repo:
    ops/observability/dashboards/*.json
  to the VPS ops path:
    /srv/healtharchive/ops/observability/dashboards/healtharchive/*.json
- Writes a Grafana dashboards provisioning config:
    /etc/grafana/provisioning/dashboards/healtharchive.yaml
  pointing at the ops dashboards folder (no secrets).
- Restarts Grafana so dashboards load automatically.

Notes:
- Dashboards are treated as public-safe artifacts; they contain queries and panel config only.
- This script does NOT configure Grafana data sources (do that once in the Grafana UI).

Usage (on the VPS):
  cd /opt/healtharchive-backend

  # Dry-run:
  ./scripts/vps-install-observability-dashboards.sh

  # Apply (requires sudo):
  sudo ./scripts/vps-install-observability-dashboards.sh --apply

Options:
  --apply            Actually perform changes (default: dry-run)
  --root DIR         Root healtharchive dir (default: /srv/healtharchive)
  --ops-group NAME   Shared ops group (default: healtharchive)
  --no-restart       Do not restart Grafana (write files only)
EOF
}

APPLY="false"
ROOT_DIR="/srv/healtharchive"
OPS_GROUP="healtharchive"
RESTART_GRAFANA="true"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY="true"
      shift 1
      ;;
    --root)
      ROOT_DIR="$2"
      shift 2
      ;;
    --ops-group|--group)
      OPS_GROUP="$2"
      shift 2
      ;;
    --no-restart)
      RESTART_GRAFANA="false"
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

if ! getent group "${OPS_GROUP}" >/dev/null 2>&1; then
  echo "ERROR: Group does not exist: ${OPS_GROUP}" >&2
  exit 1
fi

GRAFANA_UNIT="grafana-server.service"
if [[ "${APPLY}" == "true" ]]; then
  if ! systemctl cat "${GRAFANA_UNIT}" >/dev/null 2>&1; then
    echo "ERROR: Missing systemd unit: ${GRAFANA_UNIT}" >&2
    echo "Hint: run Phase 5 first: sudo ./scripts/vps-install-observability-grafana.sh --apply" >&2
    exit 1
  fi
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"

dashboards_src="${repo_root}/ops/observability/dashboards"
if [[ ! -d "${dashboards_src}" ]]; then
  echo "ERROR: Missing dashboards source dir in repo: ${dashboards_src}" >&2
  exit 1
fi

dashboards_dest="${ROOT_DIR%/}/ops/observability/dashboards/healtharchive"
provisioning_dir="/etc/grafana/provisioning/dashboards"
provisioning_file="${provisioning_dir}/healtharchive.yaml"

run install -d -m 2775 -o root -g "${OPS_GROUP}" "${dashboards_dest}"

if [[ "${APPLY}" != "true" ]]; then
  echo "+ rm -f ${dashboards_dest}/*.json"
else
  rm -f "${dashboards_dest}"/*.json
fi

shopt -s nullglob
src_files=("${dashboards_src}"/*.json)
if [[ ${#src_files[@]} -eq 0 ]]; then
  echo "ERROR: No dashboard JSON files found in: ${dashboards_src}" >&2
  exit 1
fi

for path in "${src_files[@]}"; do
  base="$(basename "${path}")"
  run install -m 0664 -o root -g "${OPS_GROUP}" "${path}" "${dashboards_dest}/${base}"
done

provisioning_body="apiVersion: 1

providers:
  - name: 'healtharchive'
    orgId: 1
    folder: 'HealthArchive'
    type: file
    disableDeletion: true
    editable: false
    updateIntervalSeconds: 60
    options:
      path: ${dashboards_dest}
      foldersFromFilesStructure: false
"

run install -d -m 0755 -o root -g root "${provisioning_dir}"
if [[ "${APPLY}" != "true" ]]; then
  echo "+ cat > ${provisioning_file} <<'EOF'"
  echo "${provisioning_body}"
  echo "+ EOF"
else
  cat >"${provisioning_file}" <<EOF
${provisioning_body}
EOF
  chown root:root "${provisioning_file}"
  chmod 0644 "${provisioning_file}"
fi

if [[ "${RESTART_GRAFANA}" == "true" ]]; then
  run systemctl restart "${GRAFANA_UNIT}"
fi

if [[ "${APPLY}" == "true" ]]; then
  echo "OK: dashboards provisioned."
else
  echo "DRY-RUN: no changes applied."
fi
echo
echo "Next:"
echo "  - Open Grafana and look for the 'HealthArchive' folder under Dashboards."
echo "  - If dashboards load but panels show errors, confirm data sources exist and match names."
