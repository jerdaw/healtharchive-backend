#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: install minimal, high-signal alerting (Prometheus rules + Alertmanager).

Phase: "8 — Alerting strategy (minimal, high-signal)"

Safe-by-default: dry-run unless you pass --apply.

What this does (when run with --apply):

- Installs Alertmanager (Ubuntu package: prometheus-alertmanager)
- Configures Alertmanager to bind loopback only (127.0.0.1:9093)
- Reads a single operator notification channel from:
    /etc/healtharchive/observability/alertmanager_webhook_url
  and writes an Alertmanager config that routes all alerts to that receiver.
- Installs HealthArchive alert rules into Prometheus:
    /etc/prometheus/rules/healtharchive-alerts.yml
  by copying the repo template and substituting __MOUNTPOINT__ (default: /).
- Restarts Alertmanager + Prometheus.

Why not Grafana managed alerts?
- Prometheus + Alertmanager is more reproducible as config-as-code and avoids datasource UID issues.
- Grafana remains the primary dashboards UI; alerts can still be viewed there if desired.

Prereqs:
- Phase 4 Prometheus is installed and reachable locally: curl -s http://127.0.0.1:9090/-/ready
- Phase 3 exporters installed (node exporter provides disk metrics).

Usage (on the VPS):
  cd /opt/healtharchive-backend

  # Populate the webhook secret (Discord/Slack/etc). Keep it private.
  sudoedit /etc/healtharchive/observability/alertmanager_webhook_url

  # Dry-run:
  ./scripts/vps-install-observability-alerting.sh

  # Apply (requires sudo):
  sudo ./scripts/vps-install-observability-alerting.sh --apply

Options:
  --apply                 Actually perform changes (default: dry-run)
  --root DIR              Root healtharchive dir (default: /srv/healtharchive)
  --etc-dir DIR           Base /etc dir for healtharchive (default: /etc/healtharchive)
  --ops-group NAME        Shared ops group (default: healtharchive)
  --mountpoint PATH       Filesystem mountpoint to alert on (default: /)
  --no-restart            Do not restart services (write files only)

Verify:
  - Prometheus rules load: curl -s http://127.0.0.1:9090/api/v1/rules | head
  - Alertmanager up: curl -s http://127.0.0.1:9093/-/ready
  - Test delivery with amtool (if installed): amtool alert add TestAlert severity=warning
EOF
}

APPLY="false"
ROOT_DIR="/srv/healtharchive"
ETC_DIR="/etc/healtharchive"
OPS_GROUP="healtharchive"
MOUNTPOINT="/"
RESTART="true"

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
    --etc-dir)
      ETC_DIR="$2"
      shift 2
      ;;
    --ops-group|--group)
      OPS_GROUP="$2"
      shift 2
      ;;
    --mountpoint)
      MOUNTPOINT="$2"
      shift 2
      ;;
    --no-restart)
      RESTART="false"
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

if [[ ! -d "${ROOT_DIR}" ]]; then
  echo "ERROR: Root dir does not exist: ${ROOT_DIR}" >&2
  exit 1
fi

obs_secrets_dir="${ETC_DIR%/}/observability"
webhook_file="${obs_secrets_dir}/alertmanager_webhook_url"
if [[ ! -f "${webhook_file}" ]]; then
  echo "ERROR: Missing webhook secret file: ${webhook_file}" >&2
  echo "Hint: run Phase 2 scaffold: sudo ./scripts/vps-bootstrap-observability-scaffold.sh" >&2
  exit 1
fi

normalize_secret_single_line() {
  local path="$1"
  local label="$2"
  if [[ "${APPLY}" != "true" ]]; then
    echo "+ (normalize ${label} to single line: ${path})"
    return 0
  fi
  raw="$(cat "${path}" || true)"
  value="$(printf '%s' "${raw}" | tr -d '\r\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  if [[ -z "${value}" ]]; then
    echo "ERROR: ${label} is empty: ${path}" >&2
    exit 1
  fi
  umask 027
  printf '%s' "${value}" >"${path}"
  chown "root:${OPS_GROUP}" "${path}" || chown root:root "${path}"
  chmod 0640 "${path}" || chmod 0600 "${path}"
}

ensure_service_user_in_group() {
  local unit="$1"
  local group="$2"

  if [[ "${APPLY}" != "true" ]]; then
    echo "+ (ensure ${unit} user can read ${obs_secrets_dir} via ${group})"
    return 0
  fi

  chown "root:${group}" "${obs_secrets_dir}"
  chmod 0750 "${obs_secrets_dir}"

  user="$(systemctl show -p User --value "${unit}" 2>/dev/null || true)"
  if [[ -z "${user}" ]]; then
    return 0
  fi
  if id "${user}" >/dev/null 2>&1; then
    usermod -aG "${group}" "${user}"
  fi
}

normalize_secret_single_line "${webhook_file}" "alertmanager webhook URL"

run apt-get update
run apt-get install -y prometheus-alertmanager

AM_UNIT=""
for cand in "prometheus-alertmanager.service" "alertmanager.service"; do
  if systemctl cat "${cand}" >/dev/null 2>&1; then
    AM_UNIT="${cand}"
    break
  fi
done
if [[ -z "${AM_UNIT}" ]]; then
  echo "ERROR: Could not find Alertmanager systemd unit after install." >&2
  exit 1
fi

ensure_service_user_in_group "${AM_UNIT}" "${OPS_GROUP}"

# Ubuntu's prometheus-alertmanager package conventionally uses /etc/prometheus/alertmanager.yml.
# We follow that convention to minimize surprises.
am_dir="/etc/prometheus"
am_cfg="${am_dir}/alertmanager.yml"
run install -d -m 0755 -o root -g root "${am_dir}"

if [[ "${APPLY}" != "true" ]]; then
  echo "+ (write ${am_cfg})"
else
  webhook_url="$(cat "${webhook_file}")"
  cat >"${am_cfg}" <<EOF
global:
  resolve_timeout: 5m

route:
  receiver: healtharchive-webhook
  group_by: ["alertname"]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 12h

receivers:
  - name: healtharchive-webhook
    webhook_configs:
      - url: ${webhook_url}
        send_resolved: true
EOF

  am_user="$(systemctl show -p User --value "${AM_UNIT}" 2>/dev/null || true)"
  am_group="$(systemctl show -p Group --value "${AM_UNIT}" 2>/dev/null || true)"
  if [[ -z "${am_user}" ]]; then
    am_user="root"
  fi
  if [[ -z "${am_group}" ]]; then
    am_group="${am_user}"
  fi
  # Prefer root-owned config (service can read but cannot modify).
  chown "root:${am_group}" "${am_cfg}" || chown root:root "${am_cfg}"
  chmod 0640 "${am_cfg}" || chmod 0600 "${am_cfg}"
fi

AM_BIN="$(command -v alertmanager 2>/dev/null || true)"
if [[ -z "${AM_BIN}" ]]; then
  AM_BIN="$(command -v prometheus-alertmanager 2>/dev/null || true)"
fi
if [[ -z "${AM_BIN}" ]]; then
  for cand in "/usr/bin/prometheus-alertmanager" "/usr/bin/alertmanager" "/usr/sbin/alertmanager"; do
    if [[ -x "${cand}" ]]; then
      AM_BIN="${cand}"
      break
    fi
  done
fi
if [[ -z "${AM_BIN}" ]]; then
  echo "ERROR: Could not locate an Alertmanager binary (tried alertmanager/prometheus-alertmanager)." >&2
  exit 1
fi

am_user="$(systemctl show -p User --value "${AM_UNIT}" 2>/dev/null || true)"
am_group="$(systemctl show -p Group --value "${AM_UNIT}" 2>/dev/null || true)"
if [[ -z "${am_user}" ]]; then
  am_user="prometheus"
fi
if [[ -z "${am_group}" ]]; then
  am_group="${am_user}"
fi

am_storage_dir="/var/lib/prometheus/alertmanager"
run install -d -m 0750 -o "${am_user}" -g "${am_group}" "${am_storage_dir}"

am_args=(
  "--config.file=${am_cfg}"
  "--storage.path=${am_storage_dir}"
  "--web.listen-address=127.0.0.1:9093"
  "--cluster.listen-address=127.0.0.1:9094"
  "--cluster.advertise-address=127.0.0.1:9094"
)

dropin_dir="/etc/systemd/system/${AM_UNIT}.d"
dropin_path="${dropin_dir}/override.conf"
dropin_body="[Service]
ExecStart=
ExecStart=${AM_BIN} ${am_args[*]}
"
run install -d -m 0755 -o root -g root "${dropin_dir}"
if [[ "${APPLY}" != "true" ]]; then
  echo "+ cat > ${dropin_path} <<'EOF'"
  echo "${dropin_body}"
  echo "+ EOF"
else
  cat >"${dropin_path}" <<EOF
${dropin_body}
EOF
fi

# Install alert rules into Prometheus.
rules_dest="/etc/prometheus/rules/healtharchive-alerts.yml"
ops_rules_dest="${ROOT_DIR%/}/ops/observability/alerting/healtharchive-alerts.yml"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
rules_src="${repo_root}/ops/observability/alerting/healtharchive-alerts.yml"
if [[ ! -f "${rules_src}" ]]; then
  echo "ERROR: Missing alert rules template in repo: ${rules_src}" >&2
  exit 1
fi

run install -d -m 0755 -o root -g root "/etc/prometheus/rules"
if [[ "${APPLY}" != "true" ]]; then
  echo "+ (render ${rules_src} -> ${rules_dest} with mountpoint=${MOUNTPOINT})"
else
  sed "s|__MOUNTPOINT__|${MOUNTPOINT}|g" "${rules_src}" >"${rules_dest}"
  chown root:root "${rules_dest}"
  chmod 0644 "${rules_dest}"
fi

run install -d -m 2770 -o root -g "${OPS_GROUP}" "${ROOT_DIR%/}/ops/observability/alerting"
if [[ "${APPLY}" != "true" ]]; then
  echo "+ (copy ${rules_dest} -> ${ops_rules_dest} for operator visibility)"
else
  install -m 0664 -o root -g "${OPS_GROUP}" "${rules_dest}" "${ops_rules_dest}"
fi

PROM_UNIT="prometheus.service"
if [[ "${APPLY}" == "true" ]]; then
  if ! systemctl cat "${PROM_UNIT}" >/dev/null 2>&1; then
    echo "ERROR: Missing systemd unit: ${PROM_UNIT}" >&2
    exit 1
  fi
fi

# Ensure Prometheus is configured with rule_files + alertmanager target (Phase 4 script owns prometheus.yml).
if [[ "${APPLY}" != "true" ]]; then
  echo "+ ./scripts/vps-install-observability-prometheus.sh --apply --skip-apt (to refresh prometheus.yml with alerting + rule_files)"
else
  "${repo_root}/scripts/vps-install-observability-prometheus.sh" --apply --skip-apt
fi

run systemctl daemon-reload
if [[ "${RESTART}" == "true" ]]; then
  run systemctl enable "${AM_UNIT}"
  run systemctl restart "${AM_UNIT}"
  run systemctl restart "${PROM_UNIT}"
fi

if [[ "${APPLY}" == "true" ]]; then
  echo "OK: alerting configured."
else
  echo "DRY-RUN: no changes applied."
fi
echo
echo "Verify locally on the VPS:"
echo "  curl -s http://127.0.0.1:9093/-/ready"
echo "  curl -s http://127.0.0.1:9090/api/v1/rules | head"
echo
echo "Confirm loopback-only:"
echo "  ss -lntp | grep -E ':9093|:9094'"
