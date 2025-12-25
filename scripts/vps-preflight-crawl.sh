#!/usr/bin/env bash
set -u -o pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: preflight audit before large crawls (read-only).

This script is intended to run on the production VPS (or a similar host) and:
  - Checks host resources (disk, memory), service status, and backups
  - Verifies API health on loopback
  - Runs existing repo verifiers (baseline drift, public surface, admin auth)
  - Optionally dry-runs annual scheduling for a given year (no DB writes)
  - Writes a timestamped report directory (default: /srv/healtharchive/ops/preflight/)

Safe-by-default:
  - No crawl is started
  - No DB writes are performed (unless you separately run schedule-annual --apply)

Usage (on the VPS):
  cd /opt/healtharchive-backend
  ./scripts/vps-preflight-crawl.sh --year 2026

Options:
  --year YYYY                 Dry-run annual scheduler for YYYY (recommended pre-Jan-01)
  --env-file FILE             Env file to source (default: /etc/healtharchive/backend.env)
  --api-base URL              Loopback API base for health checks (default: http://127.0.0.1:8001)
  --public-api-base URL       Public API base for verifiers (default: https://api.healtharchive.ca)
  --public-frontend-base URL  Public frontend base for verifiers (default: https://www.healtharchive.ca)
  --baseline-mode MODE        Baseline drift mode: local|live (default: live)
  --out-root DIR              Report root dir (default: /srv/healtharchive/ops/preflight)
  --no-write                  Do not write report files; print only
  --skip-baseline-drift       Skip check_baseline_drift.py
  --skip-public-surface       Skip verify_public_surface.py
  --skip-security-admin       Skip verify-security-and-admin.sh
  --skip-observability        Skip vps-verify-observability.sh (if present)
  -h, --help                  Show this help

Exit codes:
  0 = all checks passed
  1 = one or more checks failed
  2 = usage error
EOF
}

YEAR=""
ENV_FILE="/etc/healtharchive/backend.env"
API_BASE="http://127.0.0.1:8001"
PUBLIC_API_BASE="https://api.healtharchive.ca"
PUBLIC_FRONTEND_BASE="https://www.healtharchive.ca"
BASELINE_MODE="live"
OUT_ROOT="/srv/healtharchive/ops/preflight"
WRITE_REPORTS="true"
SKIP_BASELINE_DRIFT="false"
SKIP_PUBLIC_SURFACE="false"
SKIP_SECURITY_ADMIN="false"
SKIP_OBSERVABILITY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --year)
      YEAR="$2"
      shift 2
      ;;
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --api-base)
      API_BASE="$2"
      shift 2
      ;;
    --public-api-base)
      PUBLIC_API_BASE="$2"
      shift 2
      ;;
    --public-frontend-base)
      PUBLIC_FRONTEND_BASE="$2"
      shift 2
      ;;
    --baseline-mode)
      BASELINE_MODE="$2"
      shift 2
      ;;
    --out-root)
      OUT_ROOT="$2"
      shift 2
      ;;
    --no-write)
      WRITE_REPORTS="false"
      shift 1
      ;;
    --skip-baseline-drift)
      SKIP_BASELINE_DRIFT="true"
      shift 1
      ;;
    --skip-public-surface)
      SKIP_PUBLIC_SURFACE="true"
      shift 1
      ;;
    --skip-security-admin)
      SKIP_SECURITY_ADMIN="true"
      shift 1
      ;;
    --skip-observability)
      SKIP_OBSERVABILITY="true"
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

if [[ -n "${YEAR}" ]]; then
  if ! [[ "${YEAR}" =~ ^[0-9]{4}$ ]]; then
    echo "ERROR: --year must be a 4-digit year (got: ${YEAR})" >&2
    exit 2
  fi
fi

if [[ "${BASELINE_MODE}" != "local" && "${BASELINE_MODE}" != "live" ]]; then
  echo "ERROR: --baseline-mode must be 'local' or 'live' (got: ${BASELINE_MODE})" >&2
  exit 2
fi

API_BASE="${API_BASE%/}"
PUBLIC_API_BASE="${PUBLIC_API_BASE%/}"
PUBLIC_FRONTEND_BASE="${PUBLIC_FRONTEND_BASE%/}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
OUT_DIR="${OUT_ROOT%/}/${timestamp}"

ok() {
  printf "OK   %s\n" "$1"
}

warn() {
  printf "WARN %s\n" "$1" >&2
}

failures=0
fail() {
  printf "FAIL %s\n" "$1" >&2
  failures=$((failures + 1))
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

require_venv() {
  local venv_bin="${REPO_DIR}/.venv/bin"
  if [[ -x "${venv_bin}/python3" && -x "${venv_bin}/ha-backend" && -x "${venv_bin}/archive-tool" ]]; then
    echo "${venv_bin}"
    return 0
  fi
  return 1
}

VENV_BIN=""
if VENV_BIN="$(require_venv)"; then
  ok "venv present: ${VENV_BIN}"
else
  fail "missing venv at ${REPO_DIR}/.venv/bin (expected python3 + ha-backend + archive-tool)"
  warn "Hint (VPS): cd /opt/healtharchive-backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' 'psycopg[binary]'"
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  fail "env file not found: ${ENV_FILE}"
else
  ok "env file present: ${ENV_FILE}"
  # shellcheck disable=SC1090
  set -a
  source "${ENV_FILE}"
  set +a
fi

if [[ "${WRITE_REPORTS}" == "true" ]]; then
  if mkdir -p "${OUT_DIR}" 2>/dev/null; then
    ok "report dir: ${OUT_DIR}"
  else
    warn "could not create report dir: ${OUT_DIR} (falling back to print-only)"
    WRITE_REPORTS="false"
  fi
fi

write_file() {
  local rel="$1"
  local content="$2"
  if [[ "${WRITE_REPORTS}" != "true" ]]; then
    return 0
  fi
  printf '%s\n' "${content}" > "${OUT_DIR}/${rel}"
}

run_step() {
  local label="$1"
  local outfile="$2"
  shift 2
  local cmd=("$@")

  if [[ "${WRITE_REPORTS}" == "true" ]]; then
    {
      echo "== ${label} =="
      echo "timestamp_utc=${timestamp}"
      echo "cmd=${cmd[*]}"
      echo ""
      "${cmd[@]}"
    } >"${OUT_DIR}/${outfile}" 2>&1
    rc=$?
  else
    echo "== ${label} =="
    "${cmd[@]}"
    rc=$?
  fi

  if [[ "${rc}" -eq 0 ]]; then
    ok "${label}"
    return 0
  fi

  fail "${label} (rc=${rc})"
  if [[ "${WRITE_REPORTS}" == "true" ]]; then
    warn "  see: ${OUT_DIR}/${outfile}"
    tail -n 12 "${OUT_DIR}/${outfile}" 2>/dev/null | sed 's/^/  | /' >&2 || true
  fi
  return 0
}

step_system_info() {
  set -u -o pipefail
  echo "hostname=$(hostname -f 2>/dev/null || hostname)"
  echo "uname=$(uname -a)"
  if command -v lsb_release >/dev/null 2>&1; then
    echo "lsb_release=$(lsb_release -ds)"
  fi
  echo "uptime=$(uptime)"
  echo ""
  echo "[cpu]"
  if command -v nproc >/dev/null 2>&1; then nproc; fi
  echo ""
  echo "[memory]"
  if command -v free >/dev/null 2>&1; then free -h; fi
  echo ""
  echo "[disk]"
  df -h
}

step_services() {
  set -u -o pipefail
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "NOTE: systemctl not found; skipping service checks."
    return 0
  fi

  for svc in healtharchive-api healtharchive-worker docker postgresql caddy tailscaled; do
    if systemctl cat "${svc}.service" >/dev/null 2>&1; then
      echo "${svc}.service enabled=$(systemctl is-enabled "${svc}.service" 2>/dev/null || true) active=$(systemctl is-active "${svc}.service" 2>/dev/null || true)"
    else
      echo "${svc}.service (not installed)"
    fi
  done

  echo ""
  echo "[timers]"
  if command -v rg >/dev/null 2>&1; then
    systemctl list-timers --all --no-pager --no-legend 2>/dev/null | rg "healtharchive-" || true
  else
    systemctl list-timers --all --no-pager --no-legend 2>/dev/null | grep "healtharchive-" || true
  fi
}

step_backups() {
  set -u -o pipefail
  local dir="/srv/healtharchive/backups"
  if [[ ! -d "${dir}" ]]; then
    echo "NOTE: backups dir not found: ${dir}"
    return 0
  fi
  echo "backups_dir=${dir}"
  echo ""
  echo "[latest]"
  local latest
  latest="$(ls -t "${dir}"/healtharchive_*.dump 2>/dev/null | head -n 1 || true)"
  if [[ -z "${latest}" ]]; then
    echo "ERROR: no backups found matching ${dir}/healtharchive_*.dump" >&2
    return 1
  fi
  echo "latest=${latest}"
  if command -v stat >/dev/null 2>&1; then
    local ts now age_hours
    ts="$(stat -c %Y "${latest}")"
    now="$(date -u +%s)"
    age_hours="$(( (now - ts) / 3600 ))"
    echo "age_hours=${age_hours}"
    if [[ "${age_hours}" -gt 48 ]]; then
      echo "ERROR: latest backup is older than 48h" >&2
      return 1
    elif [[ "${age_hours}" -gt 24 ]]; then
      echo "WARN: latest backup is older than 24h" >&2
    fi
  fi
  echo ""
  echo "[recent_files]"
  ls -lt "${dir}" | head -n 15
}

step_archive_tool_dry_run() {
  set -u -o pipefail
  local tmp
  tmp="$(mktemp -d)"
  cleanup() { rm -rf "${tmp}"; }
  trap cleanup EXIT

  local archive_tool="${VENV_BIN}/archive-tool"
  if [[ ! -x "${archive_tool}" ]]; then
    echo "ERROR: archive-tool not found at ${archive_tool}" >&2
    return 1
  fi

  "${archive_tool}" \
    --seeds https://example.org \
    --name ha-preflight \
    --output-dir "${tmp}" \
    --dry-run
}

step_annual_status_json() {
  set -u -o pipefail
  "${VENV_BIN}/ha-backend" annual-status --year "${YEAR}" --json
}

step_schedule_annual_dry_run() {
  set -u -o pipefail
  "${VENV_BIN}/ha-backend" schedule-annual --year "${YEAR}"
}

step_ops_automation() {
  set -u -o pipefail
  ./scripts/verify_ops_automation.sh
}

step_baseline_drift() {
  set -u -o pipefail
  "${VENV_BIN}/python3" ./scripts/check_baseline_drift.py --mode "${BASELINE_MODE}"
}

step_security_admin_public() {
  set -u -o pipefail
  ./scripts/verify-security-and-admin.sh --api-base "${PUBLIC_API_BASE}" --require-hsts
}

step_public_surface() {
  set -u -o pipefail
  "${VENV_BIN}/python3" ./scripts/verify_public_surface.py \
    --api-base "${PUBLIC_API_BASE}" \
    --frontend-base "${PUBLIC_FRONTEND_BASE}"
}

step_observability() {
  set -u -o pipefail
  ./scripts/vps-verify-observability.sh
}

disk_check_one() {
  local path="$1"
  local label="$2"

  if ! have_cmd df; then
    warn "df not found; skipping disk checks"
    return 0
  fi

  if [[ ! -e "${path}" ]]; then
    warn "${label}: path missing (${path}); skipping"
    return 0
  fi

  local line used_pct
  line="$(df -P "${path}" 2>/dev/null | tail -n 1 || true)"
  used_pct="$(printf '%s' "${line}" | awk '{print $5}' | tr -d '%' || true)"
  if [[ -z "${used_pct}" ]]; then
    warn "${label}: could not parse df output for ${path}"
    return 0
  fi

  # Project storage policy: target <70% used; review at 80% (treat as fail for preflight).
  if [[ "${used_pct}" -ge 80 ]]; then
    fail "${label}: disk usage high (${used_pct}% used at ${path}; policy review threshold is 80%)"
  elif [[ "${used_pct}" -ge 70 ]]; then
    warn "${label}: disk usage elevated (${used_pct}% used at ${path}; target <70%)"
  else
    ok "${label}: disk usage OK (${used_pct}% used at ${path})"
  fi
}

echo "HealthArchive VPS preflight"
echo "---------------------------"
echo "timestamp_utc=${timestamp}"
echo "repo_dir=${REPO_DIR}"
echo "api_base=${API_BASE}"
echo "public_api_base=${PUBLIC_API_BASE}"
echo "public_frontend_base=${PUBLIC_FRONTEND_BASE}"
echo "baseline_mode=${BASELINE_MODE}"
if [[ -n "${YEAR}" ]]; then
  echo "annual_year=${YEAR}"
fi
echo ""

write_file "00-meta.txt" "$(cat <<META
timestamp_utc=${timestamp}
repo_dir=${REPO_DIR}
api_base=${API_BASE}
public_api_base=${PUBLIC_API_BASE}
public_frontend_base=${PUBLIC_FRONTEND_BASE}
baseline_mode=${BASELINE_MODE}
annual_year=${YEAR:-}
META
)"

if have_cmd git && [[ -d "${REPO_DIR}/.git" ]]; then
write_file "00-git.txt" "$(cat <<GIT
git_head=$(git -C "${REPO_DIR}" rev-parse HEAD 2>/dev/null || echo unknown)
git_branch=$(git -C "${REPO_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)
git_dirty=$([[ -n "$(git -C "${REPO_DIR}" status --porcelain 2>/dev/null)" ]] && echo true || echo false)
GIT
)"
fi

run_step "System info" "01-system.txt" step_system_info

disk_check_one "/" "rootfs"
if [[ -n "${HEALTHARCHIVE_ARCHIVE_ROOT:-}" ]]; then
  disk_check_one "${HEALTHARCHIVE_ARCHIVE_ROOT}" "archive_root"
elif [[ -d "/srv/healtharchive" ]]; then
  disk_check_one "/srv/healtharchive" "srv_healtharchive"
fi

run_step "Services" "02-services.txt" step_services

run_step "API health (loopback)" "03-api-health.json" curl -fsS --max-time 5 "${API_BASE}/api/health"

run_step "Backups" "04-backups.txt" step_backups

run_step "archive-tool dry-run (Docker wiring)" "05-archive-tool-dry-run.txt" step_archive_tool_dry_run

if [[ -n "${YEAR}" ]]; then
  run_step "annual-status (json)" "06-annual-status.json" step_annual_status_json
  run_step "schedule-annual (dry-run)" "07-schedule-annual.txt" step_schedule_annual_dry_run
else
  warn "annual scheduler dry-run skipped (pass --year YYYY)"
fi

run_step "Ops automation posture" "08-verify-ops-automation.txt" step_ops_automation

if [[ "${SKIP_BASELINE_DRIFT}" != "true" ]]; then
  run_step "Baseline drift" "09-baseline-drift.txt" step_baseline_drift
else
  warn "baseline drift check skipped (--skip-baseline-drift)"
fi

if [[ "${SKIP_SECURITY_ADMIN}" != "true" ]]; then
  run_step "Security + admin auth (public)" "10-verify-security-and-admin.txt" step_security_admin_public
else
  warn "security/admin check skipped (--skip-security-admin)"
fi

if [[ "${SKIP_PUBLIC_SURFACE}" != "true" ]]; then
  run_step "Public surface" "11-verify-public-surface.txt" step_public_surface
else
  warn "public surface verify skipped (--skip-public-surface)"
fi

if [[ "${SKIP_OBSERVABILITY}" != "true" ]]; then
  if [[ -x "./scripts/vps-verify-observability.sh" ]]; then
    run_step "Observability" "12-verify-observability.txt" step_observability
  else
    warn "observability verifier not present; skipping (scripts/vps-verify-observability.sh)"
  fi
else
  warn "observability check skipped (--skip-observability)"
fi

echo ""
if [[ "${WRITE_REPORTS}" == "true" ]]; then
  echo "Report dir: ${OUT_DIR}"
fi

if [[ "${failures}" -gt 0 ]]; then
  echo "ERROR: ${failures} check(s) failed." >&2
  exit 1
fi

echo "OK: all preflight checks passed."
exit 0
