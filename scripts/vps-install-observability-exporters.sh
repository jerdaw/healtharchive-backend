#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: install + configure exporters for private observability.

Phase: "3 — Install and configure exporters (host + Postgres)"

What this does (when run with --apply):

- Installs:
  - prometheus-node-exporter
  - prometheus-postgres-exporter
- Forces exporters to listen on loopback only:
  - node exporter: 127.0.0.1:9100
  - postgres exporter: 127.0.0.1:9187
- Enables the node_exporter textfile collector at:
    /var/lib/node_exporter/textfile_collector
  (used by HealthArchive ops scripts to emit small, high-signal health metrics).
- Creates a dedicated Postgres role for scraping DB metrics (pg_monitor).
- Writes exporter credentials to root-owned files under:
  - /etc/healtharchive/observability/

What this does NOT do:

- Does not install Prometheus or Grafana (later phases).
- Does not open any public firewall ports.
- Does not change Caddy.

Usage (on the VPS):
  cd /opt/healtharchive-backend

  # Dry-run (prints planned actions):
  ./scripts/vps-install-observability-exporters.sh

  # Apply (requires sudo):
  sudo ./scripts/vps-install-observability-exporters.sh --apply

Options:
  --apply                 Actually perform changes (default: dry-run)
  --root DIR              Root healtharchive dir (default: /srv/healtharchive)
  --etc-dir DIR           Base /etc dir for healtharchive (default: /etc/healtharchive)
  --ops-group NAME        Shared ops group (default: healtharchive)
  --db-name NAME          Postgres DB name (default: healtharchive)
  --db-host HOST          Postgres host (default: 127.0.0.1)
  --db-port PORT          Postgres port (default: 5432)
  --db-user USER          Exporter DB user (default: postgres_exporter)
  --node-listen ADDR      node exporter listen address (default: 127.0.0.1:9100)
  --pg-listen ADDR        postgres exporter listen address (default: 127.0.0.1:9187)
  --skip-db-role          Do not create/alter the Postgres role
  --skip-apt              Do not run apt install (assume packages are present)
  --no-enable             Do not enable/start services (just write config)

Notes:
  - Secrets are never written under /srv/healtharchive/ops/ (policy: ops artifacts are public-safe).
  - The postgres exporter DSN is written to:
      /etc/healtharchive/observability/postgres_exporter.env
    and includes the generated password.
EOF
}

APPLY="false"
ROOT_DIR="/srv/healtharchive"
ETC_DIR="/etc/healtharchive"
OPS_GROUP="healtharchive"

DB_NAME="healtharchive"
DB_HOST="127.0.0.1"
DB_PORT="5432"
DB_USER="postgres_exporter"

NODE_LISTEN="127.0.0.1:9100"
PG_LISTEN="127.0.0.1:9187"
NODE_TEXTFILE_DIR="/var/lib/node_exporter/textfile_collector"

SKIP_DB_ROLE="false"
SKIP_APT="false"
ENABLE_SERVICES="true"

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
    --db-name)
      DB_NAME="$2"
      shift 2
      ;;
    --db-host)
      DB_HOST="$2"
      shift 2
      ;;
    --db-port)
      DB_PORT="$2"
      shift 2
      ;;
    --db-user)
      DB_USER="$2"
      shift 2
      ;;
    --node-listen)
      NODE_LISTEN="$2"
      shift 2
      ;;
    --pg-listen)
      PG_LISTEN="$2"
      shift 2
      ;;
    --skip-db-role)
      SKIP_DB_ROLE="true"
      shift 1
      ;;
    --skip-apt)
      SKIP_APT="true"
      shift 1
      ;;
    --no-enable)
      ENABLE_SERVICES="false"
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

if [[ ! -d "${ROOT_DIR}" ]]; then
  echo "ERROR: Root dir does not exist: ${ROOT_DIR}" >&2
  exit 1
fi

if ! getent group "${OPS_GROUP}" >/dev/null 2>&1; then
  echo "ERROR: Group does not exist: ${OPS_GROUP}" >&2
  exit 1
fi

obs_dir="${ROOT_DIR%/}/ops/observability"
obs_secrets_dir="${ETC_DIR%/}/observability"
if [[ ! -d "${obs_dir}" || ! -d "${obs_secrets_dir}" ]]; then
  echo "ERROR: Observability scaffold not present." >&2
  echo "Expected:" >&2
  echo "  - ${obs_dir}" >&2
  echo "  - ${obs_secrets_dir}" >&2
  echo "Run first (VPS): sudo ./scripts/vps-bootstrap-observability-scaffold.sh" >&2
  exit 1
fi

if [[ "${SKIP_APT}" != "true" ]]; then
  run apt-get update
  run apt-get install -y prometheus-node-exporter prometheus-postgres-exporter
fi

find_unit() {
  local unit
  for unit in "$@"; do
    # Prefer systemctl introspection, but also accept common unit-file paths.
    if systemctl cat "${unit}" >/dev/null 2>&1; then
      echo "${unit}"
      return 0
    fi
    if [[ -f "/etc/systemd/system/${unit}" || -f "/usr/lib/systemd/system/${unit}" || -f "/lib/systemd/system/${unit}" ]]; then
      echo "${unit}"
      return 0
    fi
  done
  return 1
}

find_bin() {
  local b
  for b in "$@"; do
    if command -v "${b}" >/dev/null 2>&1; then
      command -v "${b}"
      return 0
    fi
  done
  return 1
}

NODE_UNIT="$(find_unit prometheus-node-exporter.service node-exporter.service || true)"
NODE_BIN="$(find_bin prometheus-node-exporter node_exporter /usr/bin/prometheus-node-exporter /usr/bin/node_exporter || true)"
if [[ "${APPLY}" != "true" ]]; then
  # Dry-run should not fail if packages aren't installed yet.
  NODE_UNIT="${NODE_UNIT:-prometheus-node-exporter.service}"
  NODE_BIN="${NODE_BIN:-/usr/bin/prometheus-node-exporter}"
else
  if [[ -z "${NODE_UNIT}" ]]; then
    echo "ERROR: Could not find node exporter systemd unit (expected prometheus-node-exporter.service)." >&2
    exit 1
  fi
  if [[ -z "${NODE_BIN}" ]]; then
    echo "ERROR: Could not find node exporter binary." >&2
    exit 1
  fi
fi

PG_UNIT="$(find_unit prometheus-postgres-exporter.service postgres-exporter.service prometheus-postgresql-exporter.service || true)"
PG_BIN="$(find_bin prometheus-postgres-exporter postgres_exporter /usr/bin/prometheus-postgres-exporter /usr/bin/postgres_exporter || true)"
if [[ "${APPLY}" != "true" ]]; then
  PG_UNIT="${PG_UNIT:-prometheus-postgres-exporter.service}"
  PG_BIN="${PG_BIN:-/usr/bin/prometheus-postgres-exporter}"
else
  if [[ -z "${PG_UNIT}" ]]; then
    echo "ERROR: Could not find postgres exporter systemd unit (expected prometheus-postgres-exporter.service)." >&2
    exit 1
  fi
  if [[ -z "${PG_BIN}" ]]; then
    echo "ERROR: Could not find postgres exporter binary." >&2
    exit 1
  fi
fi

pg_pw_file="${obs_secrets_dir}/postgres_exporter_password"
pg_env_file="${obs_secrets_dir}/postgres_exporter.env"

if [[ "${SKIP_DB_ROLE}" != "true" ]]; then
  if [[ "${APPLY}" == "true" ]]; then
    # Allow services to read only the files they need. Keep other secrets root-only.
    chown "root:${OPS_GROUP}" "${obs_secrets_dir}"
    chmod 0750 "${obs_secrets_dir}"

    if [[ ! -s "${pg_pw_file}" ]]; then
      if command -v openssl >/dev/null 2>&1; then
        pw="$(openssl rand -hex 24)"
      else
        pw="$(tr -dc 'a-f0-9' </dev/urandom | head -c 48)"
      fi
      umask 077
      printf '%s' "${pw}" >"${pg_pw_file}"
      chown "root:${OPS_GROUP}" "${pg_pw_file}"
      chmod 0640 "${pg_pw_file}"
    else
      pw="$(cat "${pg_pw_file}")"
    fi

    if [[ ! -s "${pg_env_file}" ]]; then
      umask 077
      printf 'DATA_SOURCE_NAME=postgresql://%s:%s@%s:%s/%s?sslmode=disable\n' \
        "${DB_USER}" \
        "${pw}" \
        "${DB_HOST}" \
        "${DB_PORT}" \
        "${DB_NAME}" >"${pg_env_file}"
      chown "root:${OPS_GROUP}" "${pg_env_file}"
      chmod 0640 "${pg_env_file}"
    fi

    # Ensure the postgres exporter service user can read the env file.
    pg_service_user="$(systemctl show -p User --value "${PG_UNIT}" 2>/dev/null || true)"
    if [[ -n "${pg_service_user}" && "${pg_service_user}" != "root" ]]; then
      if id "${pg_service_user}" >/dev/null 2>&1; then
        usermod -aG "${OPS_GROUP}" "${pg_service_user}"
      fi
    fi

    psql_runner=()
    if command -v runuser >/dev/null 2>&1; then
      psql_runner=(runuser -u postgres -- psql)
    else
      psql_runner=(sudo -u postgres psql)
    fi

    "${psql_runner[@]}" -v ON_ERROR_STOP=1 -d postgres <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN;
  END IF;
  ALTER ROLE ${DB_USER} WITH LOGIN PASSWORD '${pw}';
END
\$\$;
GRANT pg_monitor TO ${DB_USER};
SQL

    "${psql_runner[@]}" -v ON_ERROR_STOP=1 -d postgres <<SQL
DO \$\$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}') THEN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', '${DB_NAME}', '${DB_USER}');
  END IF;
END
\$\$;
SQL
  else
    echo "+ (generate postgres exporter password if missing)"
    echo "+ (write ${pg_env_file} if missing)"
    echo "+ (create role ${DB_USER} with pg_monitor; grant connect on ${DB_NAME})"
  fi
fi

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

run install -d -m 0755 -o root -g root "${NODE_TEXTFILE_DIR}"

write_dropin "${NODE_UNIT}" "[Service]
ExecStart=
ExecStart=${NODE_BIN} --web.listen-address=${NODE_LISTEN} --collector.textfile.directory=${NODE_TEXTFILE_DIR}
"

write_dropin "${PG_UNIT}" "[Service]
EnvironmentFile=${pg_env_file}
ExecStart=
ExecStart=${PG_BIN} --web.listen-address=${PG_LISTEN}
"

run systemctl daemon-reload

if [[ "${ENABLE_SERVICES}" == "true" ]]; then
  run systemctl enable "${NODE_UNIT}" "${PG_UNIT}"
  run systemctl restart "${NODE_UNIT}" "${PG_UNIT}"
fi

echo "OK: exporters configured."
echo
echo "Verify locally on the VPS:"
echo "  curl -s http://${NODE_LISTEN}/metrics | head"
echo "  curl -s http://${PG_LISTEN}/metrics | head"
echo
echo "Confirm they are loopback-only:"
echo "  ss -lntp | rg ':9100|:9187' || ss -lntp | grep -E ':9100|:9187'"
