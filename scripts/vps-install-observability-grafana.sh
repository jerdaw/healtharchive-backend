#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: install + harden Grafana for private observability.

Phase: "5 — Grafana (private stats page) with tailnet-only access"

Safe-by-default: dry-run unless you pass --apply.

What this does (when run with --apply):

- Installs Grafana (Ubuntu package: grafana)
- Forces Grafana to bind loopback only (127.0.0.1:3000) via systemd env overrides
- Disables anonymous access and self-signup
- Resets the Grafana admin password from:
    /etc/healtharchive/observability/grafana_admin_password
- Creates (or updates) a Postgres read-only role for Grafana:
    grafana_readonly
  using the password stored in:
    /etc/healtharchive/observability/postgres_grafana_password
- Grants Grafana read-only access to safe tables/views for dashboards:
  - usage_metrics
  - archive_jobs, sources, snapshots
  - grafana_issue_reports_summary (redacted view; excludes email/text)

What this does NOT do:

- Does not configure Tailscale Serve (use the separate helper script).
- Does not create dashboards (Phase 6).

Usage (on the VPS):
  cd /opt/healtharchive-backend

  # Dry-run:
  ./scripts/vps-install-observability-grafana.sh

  # Apply (requires sudo):
  sudo ./scripts/vps-install-observability-grafana.sh --apply

Options:
  --apply                 Actually perform changes (default: dry-run)
  --skip-apt              Do not run apt install (assume package is present)
  --no-enable             Do not enable/start Grafana (still writes config)
  --etc-dir DIR           Base /etc dir for healtharchive (default: /etc/healtharchive)
  --ops-group NAME        Shared ops group (default: healtharchive)
  --listen HOST           Grafana listen host (default: 127.0.0.1)
  --port PORT             Grafana port (default: 3000)
  --db-name NAME          Postgres DB name (default: healtharchive)
  --db-host HOST          Postgres host (default: 127.0.0.1)
  --db-port PORT          Postgres port (default: 5432)
  --db-user USER          Grafana DB role name (default: grafana_readonly)
  --skip-db-role          Skip creating the grafana_readonly DB role/view

Notes:
  - Password files are normalized to a single line (no trailing newline).
  - The Grafana Postgres data source itself is configured in the Grafana UI (or later provisioning).
EOF
}

APPLY="false"
SKIP_APT="false"
ENABLE_SERVICE="true"

ETC_DIR="/etc/healtharchive"
OPS_GROUP="healtharchive"

GRAFANA_LISTEN="127.0.0.1"
GRAFANA_PORT="3000"

DB_NAME="healtharchive"
DB_HOST="127.0.0.1"
DB_PORT="5432"
DB_USER="grafana_readonly"
SKIP_DB_ROLE="false"

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
    --listen)
      GRAFANA_LISTEN="$2"
      shift 2
      ;;
    --port)
      GRAFANA_PORT="$2"
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
    --skip-db-role)
      SKIP_DB_ROLE="true"
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

obs_secrets_dir="${ETC_DIR%/}/observability"
grafana_pw_file="${obs_secrets_dir}/grafana_admin_password"
pg_pw_file="${obs_secrets_dir}/postgres_grafana_password"

if [[ ! -d "${obs_secrets_dir}" ]]; then
  echo "ERROR: Missing observability secrets dir: ${obs_secrets_dir}" >&2
  echo "Hint: run Phase 2 scaffold first: sudo ./scripts/vps-bootstrap-observability-scaffold.sh" >&2
  exit 1
fi

if [[ ! -f "${grafana_pw_file}" ]]; then
  echo "ERROR: Missing Grafana admin password file: ${grafana_pw_file}" >&2
  exit 1
fi

if [[ "${SKIP_DB_ROLE}" != "true" && ! -f "${pg_pw_file}" ]]; then
  echo "ERROR: Missing Postgres Grafana password file: ${pg_pw_file}" >&2
  exit 1
fi

if [[ "${SKIP_APT}" != "true" ]]; then
  run apt-get update
  run apt-get install -y grafana
fi

GRAFANA_UNIT="grafana-server.service"
if [[ "${APPLY}" == "true" ]]; then
  if ! systemctl cat "${GRAFANA_UNIT}" >/dev/null 2>&1; then
    echo "ERROR: Missing systemd unit: ${GRAFANA_UNIT}" >&2
    echo "Hint: ensure the grafana package installed a systemd service." >&2
    exit 1
  fi
fi

GRAFANA_CLI="$(command -v grafana-cli 2>/dev/null || true)"
if [[ -z "${GRAFANA_CLI}" ]]; then
  GRAFANA_CLI="/usr/sbin/grafana-cli"
fi

normalize_secret_file_single_line() {
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
  umask 077
  printf '%s' "${value}" >"${path}"
  chown root:root "${path}"
  chmod 0600 "${path}"
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

dropin_body="[Service]
Environment=GF_SERVER_HTTP_ADDR=${GRAFANA_LISTEN}
Environment=GF_SERVER_HTTP_PORT=${GRAFANA_PORT}
Environment=GF_USERS_ALLOW_SIGN_UP=false
Environment=GF_USERS_ALLOW_ORG_CREATE=false
Environment=GF_AUTH_ANONYMOUS_ENABLED=false
Environment=GF_SECURITY_DISABLE_GRAVATAR=true
"

normalize_secret_file_single_line "${grafana_pw_file}" "Grafana admin password"
if [[ "${SKIP_DB_ROLE}" != "true" ]]; then
  normalize_secret_file_single_line "${pg_pw_file}" "Grafana Postgres password"
fi

write_dropin "${GRAFANA_UNIT}" "${dropin_body}"

run systemctl daemon-reload
if [[ "${ENABLE_SERVICE}" == "true" ]]; then
  run systemctl enable "${GRAFANA_UNIT}"
  run systemctl restart "${GRAFANA_UNIT}"
fi

if [[ "${APPLY}" == "true" ]]; then
  if [[ ! -x "${GRAFANA_CLI}" ]]; then
    echo "ERROR: grafana-cli not found/executable: ${GRAFANA_CLI}" >&2
    exit 1
  fi
  pw="$(cat "${grafana_pw_file}")"
  "${GRAFANA_CLI}" admin reset-admin-password "${pw}" >/dev/null
  if [[ "${ENABLE_SERVICE}" == "true" ]]; then
    systemctl restart "${GRAFANA_UNIT}"
  fi
else
  echo "+ grafana-cli admin reset-admin-password <value from ${grafana_pw_file}>"
fi

if [[ "${SKIP_DB_ROLE}" != "true" ]]; then
  if [[ "${APPLY}" == "true" ]]; then
    pg_pw="$(cat "${pg_pw_file}")"

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
  ALTER ROLE ${DB_USER} WITH LOGIN PASSWORD '${pg_pw}';
END
\$\$;
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

    "${psql_runner[@]}" -v ON_ERROR_STOP=1 -d "${DB_NAME}" <<SQL
GRANT USAGE ON SCHEMA public TO ${DB_USER};
GRANT SELECT ON TABLE usage_metrics TO ${DB_USER};
GRANT SELECT ON TABLE archive_jobs TO ${DB_USER};
GRANT SELECT ON TABLE sources TO ${DB_USER};
GRANT SELECT ON TABLE snapshots TO ${DB_USER};
SQL

    "${psql_runner[@]}" -v ON_ERROR_STOP=1 -d "${DB_NAME}" <<SQL
DO \$\$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'issue_reports'
  ) THEN
    EXECUTE \$v\$
      CREATE OR REPLACE VIEW grafana_issue_reports_summary AS
      SELECT
        id,
        category,
        status,
        created_at,
        updated_at,
        snapshot_id,
        original_url,
        page_url
      FROM issue_reports
    \$v\$;
    EXECUTE format('GRANT SELECT ON grafana_issue_reports_summary TO %I', '${DB_USER}');
  END IF;
END
\$\$;
SQL
  else
    echo "+ (create/update Postgres role ${DB_USER} and grant read-only access)"
  fi
fi

echo "OK: Grafana installed and bound to ${GRAFANA_LISTEN}:${GRAFANA_PORT}."
echo
echo "Next (Phase 5): expose Grafana via tailnet-only HTTPS:"
echo "  sudo ./scripts/vps-enable-tailscale-serve-grafana.sh --apply"
