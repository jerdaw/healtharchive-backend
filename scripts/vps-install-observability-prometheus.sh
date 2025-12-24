#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: install + configure Prometheus for private observability.

Phase: "4 — Prometheus (scrape config, retention, service hardening)"

Safe-by-default: dry-run unless you pass --apply.

What this does (when run with --apply):

- Installs Prometheus (Ubuntu package: prometheus)
- Writes /etc/prometheus/prometheus.yml to scrape:
  - healtharchive backend metrics: http://127.0.0.1:8001/metrics (token via credentials_file)
  - node exporter: http://127.0.0.1:9100/metrics
  - postgres exporter: http://127.0.0.1:9187/metrics
- Forces Prometheus web UI to bind loopback only (default: 127.0.0.1:9090)
- Sets explicit retention limits (time and, if supported, size)

What this does NOT do:

- Does not install Grafana (Phase 5).
- Does not expose Prometheus on the public internet (we bind to loopback).

Prereqs:

- Backend is running on 127.0.0.1:8001.
- Phase 2 scaffolding exists: /etc/healtharchive/observability/
- /etc/healtharchive/observability/prometheus_backend_admin_token is set to HEALTHARCHIVE_ADMIN_TOKEN.
- Phase 3 exporters are installed and loopback-only on 9100/9187.

Usage (on the VPS):
  cd /opt/healtharchive-backend

  # Dry-run:
  ./scripts/vps-install-observability-prometheus.sh

  # Apply (requires sudo):
  sudo ./scripts/vps-install-observability-prometheus.sh --apply

Options:
  --apply                  Actually perform changes (default: dry-run)
  --skip-apt               Do not run apt install (assume package is present)
  --no-enable              Do not enable/start Prometheus (write config only)
  --etc-dir DIR            Base /etc dir for healtharchive (default: /etc/healtharchive)
  --ops-group NAME         Shared ops group (default: healtharchive)
  --prom-listen ADDR       Prometheus listen address (default: 127.0.0.1:9090)
  --retention-time DUR     Retention time (default: 30d)
  --retention-size SIZE    Retention size cap (default: 2GB; omitted if unsupported)
  --scrape-interval DUR    Global scrape interval (default: 60s)
  --scrape-timeout DUR     Global scrape timeout (default: 10s)
  --backend-target HOST:PORT Backend target (default: 127.0.0.1:8001)
  --node-target HOST:PORT  Node exporter target (default: 127.0.0.1:9100)
  --pg-target HOST:PORT    Postgres exporter target (default: 127.0.0.1:9187)

Notes:
  - Prometheus reads the backend admin token from:
      /etc/healtharchive/observability/prometheus_backend_admin_token
    The file is normalized to a single line (no trailing newline) because Prometheus
    treats newline as part of the token.
EOF
}

APPLY="false"
SKIP_APT="false"
ENABLE_SERVICE="true"

ETC_DIR="/etc/healtharchive"
OPS_GROUP="healtharchive"

PROM_LISTEN="127.0.0.1:9090"
RETENTION_TIME="30d"
RETENTION_SIZE="2GB"

SCRAPE_INTERVAL="60s"
SCRAPE_TIMEOUT="10s"

BACKEND_TARGET="127.0.0.1:8001"
NODE_TARGET="127.0.0.1:9100"
PG_TARGET="127.0.0.1:9187"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY="true"
      shift 1
      ;;
    --skip-apt)
      SKIP_APT="true"
      shift 1
      ;;
    --no-enable)
      ENABLE_SERVICE="false"
      shift 1
      ;;
    --etc-dir)
      ETC_DIR="$2"
      shift 2
      ;;
    --ops-group|--group)
      OPS_GROUP="$2"
      shift 2
      ;;
    --prom-listen)
      PROM_LISTEN="$2"
      shift 2
      ;;
    --retention-time)
      RETENTION_TIME="$2"
      shift 2
      ;;
    --retention-size)
      RETENTION_SIZE="$2"
      shift 2
      ;;
    --scrape-interval)
      SCRAPE_INTERVAL="$2"
      shift 2
      ;;
    --scrape-timeout)
      SCRAPE_TIMEOUT="$2"
      shift 2
      ;;
    --backend-target)
      BACKEND_TARGET="$2"
      shift 2
      ;;
    --node-target)
      NODE_TARGET="$2"
      shift 2
      ;;
    --pg-target)
      PG_TARGET="$2"
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

obs_secrets_dir="${ETC_DIR%/}/observability"
token_file="${obs_secrets_dir}/prometheus_backend_admin_token"

if [[ ! -d "${obs_secrets_dir}" ]]; then
  echo "ERROR: Missing observability secrets dir: ${obs_secrets_dir}" >&2
  echo "Hint: run Phase 2 scaffold first: sudo ./scripts/vps-bootstrap-observability-scaffold.sh" >&2
  exit 1
fi

if [[ ! -f "${token_file}" ]]; then
  echo "ERROR: Missing backend metrics token file: ${token_file}" >&2
  exit 1
fi

if [[ "${SKIP_APT}" != "true" ]]; then
  run apt-get update
  run apt-get install -y prometheus
fi

PROM_UNIT="prometheus.service"
if [[ "${APPLY}" == "true" ]]; then
  if ! systemctl cat "${PROM_UNIT}" >/dev/null 2>&1; then
    echo "ERROR: Missing systemd unit: ${PROM_UNIT}" >&2
    exit 1
  fi
fi

PROM_BIN="$(command -v prometheus 2>/dev/null || true)"
if [[ -z "${PROM_BIN}" ]]; then
  PROM_BIN="/usr/bin/prometheus"
fi

PROMTOOL_BIN="$(command -v promtool 2>/dev/null || true)"
if [[ -z "${PROMTOOL_BIN}" ]]; then
  PROMTOOL_BIN="/usr/bin/promtool"
fi

prom_cfg="/etc/prometheus/prometheus.yml"
if [[ "${APPLY}" == "true" ]]; then
  if [[ ! -x "${PROM_BIN}" ]]; then
    echo "ERROR: prometheus binary not found/executable: ${PROM_BIN}" >&2
    exit 1
  fi
  if [[ ! -x "${PROMTOOL_BIN}" ]]; then
    echo "ERROR: promtool not found/executable: ${PROMTOOL_BIN}" >&2
    exit 1
  fi
fi

supports_retention_size="false"
if [[ "${APPLY}" == "true" ]]; then
  if "${PROM_BIN}" --help 2>/dev/null | grep -q -- '--storage.tsdb.retention.size'; then
    supports_retention_size="true"
  fi
fi

normalize_token_file() {
  if [[ "${APPLY}" != "true" ]]; then
    echo "+ (normalize token file to single line: ${token_file})"
    return 0
  fi
  raw="$(cat "${token_file}" || true)"
  token="$(printf '%s' "${raw}" | tr -d '\r\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  if [[ -z "${token}" ]]; then
    echo "ERROR: Token file is empty (or whitespace only): ${token_file}" >&2
    exit 1
  fi
  umask 027
  printf '%s' "${token}" >"${token_file}"
  chown "root:${OPS_GROUP}" "${token_file}"
  chmod 0640 "${token_file}"
}

ensure_prometheus_can_read_token() {
  if [[ "${APPLY}" != "true" ]]; then
    echo "+ (ensure ${obs_secrets_dir} perms allow Prometheus to read ${token_file})"
    echo "+ (add prometheus user to ${OPS_GROUP} if present)"
    return 0
  fi

  chown "root:${OPS_GROUP}" "${obs_secrets_dir}"
  chmod 0750 "${obs_secrets_dir}"

  prom_user="$(systemctl show -p User --value "${PROM_UNIT}" 2>/dev/null || true)"
  if [[ -z "${prom_user}" ]]; then
    prom_user="prometheus"
  fi
  if id "${prom_user}" >/dev/null 2>&1; then
    usermod -aG "${OPS_GROUP}" "${prom_user}"
  fi
}

write_prometheus_config() {
  if [[ "${APPLY}" != "true" ]]; then
    echo "+ install -d -m 0755 -o root -g root /etc/prometheus"
    echo "+ (write ${prom_cfg})"
    return 0
  fi

  if [[ -f "${prom_cfg}" ]]; then
    ts="$(date -u +%Y%m%dT%H%M%SZ)"
    cp -a "${prom_cfg}" "${prom_cfg}.bak.${ts}"
  fi

  install -d -m 0755 -o root -g root /etc/prometheus
  cat >"${prom_cfg}" <<EOF
global:
  scrape_interval: ${SCRAPE_INTERVAL}
  scrape_timeout: ${SCRAPE_TIMEOUT}

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["${PROM_LISTEN}"]

  - job_name: healtharchive_backend
    metrics_path: /metrics
    scheme: http
    static_configs:
      - targets: ["${BACKEND_TARGET}"]
    authorization:
      type: Bearer
      credentials_file: ${token_file}

  - job_name: node_exporter
    static_configs:
      - targets: ["${NODE_TARGET}"]

  - job_name: postgres_exporter
    static_configs:
      - targets: ["${PG_TARGET}"]
EOF

  chown root:root "${prom_cfg}"
  chmod 0644 "${prom_cfg}"

  "${PROMTOOL_BIN}" check config "${prom_cfg}"
}

write_dropin() {
  local unit="$1"
  local content="$2"
  local dir="/etc/systemd/system/${unit}.d"
  local path="${dir}/override.conf"
  run install -d -m 0755 -o root -g root "${dir}"
  if [[ "${APPLY}" != "true" ]]; then
    echo "+ cat > ${path} <<'EOF'"
    echo "${content}"
    echo "+ EOF"
    return 0
  fi
  cat >"${path}" <<EOF
${content}
EOF
}

console_args=()
if [[ "${APPLY}" == "true" ]]; then
  if [[ -d "/usr/share/prometheus/consoles" && -d "/usr/share/prometheus/console_libraries" ]]; then
    console_args+=("--web.console.templates=/usr/share/prometheus/consoles")
    console_args+=("--web.console.libraries=/usr/share/prometheus/console_libraries")
  fi
fi

prom_args=(
  "--config.file=${prom_cfg}"
  "--storage.tsdb.path=/var/lib/prometheus"
  "--web.listen-address=${PROM_LISTEN}"
  "--storage.tsdb.retention.time=${RETENTION_TIME}"
)
if [[ "${supports_retention_size}" == "true" && -n "${RETENTION_SIZE}" ]]; then
  prom_args+=("--storage.tsdb.retention.size=${RETENTION_SIZE}")
fi
prom_args+=("${console_args[@]}")

dropin_body="[Service]
ExecStart=
ExecStart=${PROM_BIN} ${prom_args[*]}
"

normalize_token_file
ensure_prometheus_can_read_token
write_prometheus_config
write_dropin "${PROM_UNIT}" "${dropin_body}"

run systemctl daemon-reload
if [[ "${ENABLE_SERVICE}" == "true" ]]; then
  run systemctl enable "${PROM_UNIT}"
  run systemctl restart "${PROM_UNIT}"
fi

if [[ "${APPLY}" == "true" ]]; then
  echo "OK: Prometheus configured."
else
  echo "DRY-RUN: no changes applied."
fi
echo
echo "Verify locally on the VPS:"
echo "  curl -s http://${PROM_LISTEN}/-/ready"
echo "  curl -s http://${PROM_LISTEN}/api/v1/targets | head"
echo
echo "Confirm it is loopback-only:"
prom_port="${PROM_LISTEN##*:}"
echo "  ss -lntp | grep -E ':${prom_port}\\b'"
