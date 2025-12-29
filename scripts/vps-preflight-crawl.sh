#!/usr/bin/env bash
set -u -o pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: preflight audit before large crawls (read-only).

This script is intended to run on the production VPS (or a similar host) and:
  - Checks host resources (disk, memory), service status, and backups
  - Forecasts campaign storage headroom (context-aware; can fail below 80% used)
  - Verifies Docker daemon access (not just docker CLI presence)
  - Checks DB connectivity + Alembic schema is at head
  - Checks seed URL reachability (annual sources)
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
  --campaign-archive-root DIR Filesystem path that will hold the upcoming campaign outputs (defaults to auto-detect; falls back to HEALTHARCHIVE_ARCHIVE_ROOT)
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
  --skip-rehearsal-evidence   Skip rehearsal evidence check (active crawl headroom)
  -h, --help                  Show this help

Exit codes:
  0 = all checks passed
  1 = one or more checks failed
  2 = usage error
EOF
}

YEAR=""
CAMPAIGN_ARCHIVE_ROOT=""
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
SKIP_REHEARSAL_EVIDENCE="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --year)
      YEAR="$2"
      shift 2
      ;;
    --campaign-archive-root)
      CAMPAIGN_ARCHIVE_ROOT="$2"
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
    --skip-rehearsal-evidence)
      SKIP_REHEARSAL_EVIDENCE="true"
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

if [[ -n "${CAMPAIGN_ARCHIVE_ROOT}" && ! -e "${CAMPAIGN_ARCHIVE_ROOT}" ]]; then
  echo "ERROR: --campaign-archive-root path does not exist: ${CAMPAIGN_ARCHIVE_ROOT}" >&2
  exit 2
fi

if [[ -n "${CAMPAIGN_ARCHIVE_ROOT}" && "${CAMPAIGN_ARCHIVE_ROOT}" == /srv/healtharchive/storagebox/* ]]; then
  if ! is_mounted "/srv/healtharchive/storagebox"; then
    echo "ERROR: --campaign-archive-root points at Storage Box but /srv/healtharchive/storagebox is not mounted" >&2
    echo "Hint: mount the Storage Box (sshfs) first, then re-run preflight." >&2
    exit 2
  fi
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

is_mounted() {
  local path="$1"
  if have_cmd mountpoint; then
    mountpoint -q "${path}"
    return $?
  fi
  mount | grep -q " on ${path} " 2>/dev/null
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
    local fail_lines
    if have_cmd rg; then
      fail_lines="$(rg -n "^(FAIL|ERROR):" "${OUT_DIR}/${outfile}" 2>/dev/null | head -n 12 || true)"
    else
      fail_lines="$(grep -nE "^(FAIL|ERROR):" "${OUT_DIR}/${outfile}" 2>/dev/null | head -n 12 || true)"
    fi
    if [[ -n "${fail_lines}" ]]; then
      echo "  | [failure_lines]" >&2
      printf '%s\n' "${fail_lines}" | sed 's/^/  | /' >&2
    fi
    tail -n 18 "${OUT_DIR}/${outfile}" 2>/dev/null | sed 's/^/  | /' >&2 || true
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
  echo ""
  echo "[disk_inodes]"
  df -ih
  echo ""
  echo "[limits]"
  echo "ulimit_nofile=$(ulimit -n 2>/dev/null || echo unknown)"
  if [[ -r /proc/sys/fs/file-max ]]; then
    echo "fs_file_max=$(cat /proc/sys/fs/file-max)"
  fi
  if [[ -r /proc/sys/fs/file-nr ]]; then
    echo "fs_file_nr=$(cat /proc/sys/fs/file-nr)"
  fi
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
  if [[ -z "${tmp}" || ! -d "${tmp}" ]]; then
    echo "ERROR: failed to create temp dir" >&2
    return 1
  fi

  local archive_tool="${VENV_BIN}/archive-tool"
  if [[ ! -x "${archive_tool}" ]]; then
    echo "ERROR: archive-tool not found at ${archive_tool}" >&2
    rm -rf "${tmp}"
    return 1
  fi

  "${archive_tool}" \
    --seeds https://example.org \
    --name ha-preflight \
    --output-dir "${tmp}" \
    --dry-run
  local rc=$?
  rm -rf "${tmp}"
  return "${rc}"
}

step_campaign_storage_forecast() {
  set -u -o pipefail
  local archive_root="${1:-}"
  if [[ -z "${archive_root}" ]]; then
    echo "ERROR: missing archive_root argument to step_campaign_storage_forecast" >&2
    return 1
  fi
  "${VENV_BIN}/python3" ./scripts/campaign_storage_forecast.py --year "${YEAR}" --archive-root "${archive_root}"
}

step_resource_headroom() {
  set -u -o pipefail
  "${VENV_BIN}/python3" ./scripts/vps_resource_headroom.py
}

step_rehearsal_evidence() {
  set -u -o pipefail
  "${VENV_BIN}/python3" ./scripts/vps_rehearsal_evidence_check.py --require
}

step_time_sync() {
  set -u -o pipefail
  if ! have_cmd timedatectl; then
    echo "NOTE: timedatectl not found; skipping time sync check."
    return 0
  fi
  timedatectl status
  local ntp
  ntp="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
  if [[ "${ntp}" != "yes" ]]; then
    echo "ERROR: NTP is not synchronized (timedatectl NTPSynchronized=${ntp:-unknown})" >&2
    return 1
  fi
}

step_docker_daemon() {
  set -u -o pipefail
  if ! have_cmd docker; then
    echo "ERROR: docker not found in PATH" >&2
    return 1
  fi
  echo "[docker_version]"
  docker --version
  echo ""
  echo "[docker_info]"
  docker info
  echo ""
  echo "[docker_ps]"
  docker ps --no-trunc --format '{{.ID}}\t{{.Image}}\t{{.Status}}' | head -n 25 || true
}

step_campaign_tier_docker_probe() {
  set -u -o pipefail
  local archive_root="${1:-}"
  if [[ -z "${archive_root}" ]]; then
    echo "ERROR: missing archive_root argument to step_campaign_tier_docker_probe" >&2
    return 1
  fi
  if ! have_cmd docker; then
    echo "ERROR: docker not found in PATH" >&2
    return 1
  fi
  if ! docker image inspect alpine >/dev/null 2>&1; then
    echo "NOTE: docker image 'alpine' not present; skipping docker mount probe (pre-pull images on the VPS for this check)." >&2
    return 0
  fi
  if [[ ! -d "${archive_root}" ]]; then
    echo "ERROR: archive_root not a directory: ${archive_root}" >&2
    return 1
  fi

  local tmp
  tmp="$(mktemp -d -p "${archive_root}" ha-preflight-docker-probe.XXXXXX)"
  if [[ -z "${tmp}" || ! -d "${tmp}" ]]; then
    echo "ERROR: failed to create temp dir under: ${archive_root}" >&2
    return 1
  fi

  docker run --rm --pull=never -v "${tmp}:/probe" alpine sh -c 'set -e; echo ok >/probe/ok.txt; ls -la /probe'
  local rc=$?
  rm -rf "${tmp}" || true
  return "${rc}"
}

step_db_check() {
  set -u -o pipefail
  "${VENV_BIN}/ha-backend" check-db
}

step_alembic_head_check() {
  set -u -o pipefail
  local alembic="${VENV_BIN}/alembic"
  if [[ ! -x "${alembic}" ]]; then
    echo "ERROR: alembic not found at ${alembic}" >&2
    return 1
  fi

  local cfg="${REPO_DIR}/alembic.ini"
  if [[ ! -f "${cfg}" ]]; then
    echo "ERROR: alembic config not found: ${cfg}" >&2
    return 1
  fi

  echo "alembic_bin=${alembic}"
  echo "alembic_cfg=${cfg}"
  "${alembic}" --version

  echo ""
  echo "[alembic_heads_raw]"
  local heads_raw
  heads_raw="$("${alembic}" -c "${cfg}" heads 2>&1)"
  local rc_heads=$?
  printf '%s\n' "${heads_raw}"
  if [[ "${rc_heads}" -ne 0 ]]; then
    echo "ERROR: alembic heads failed (rc=${rc_heads})" >&2
    return 1
  fi

  echo ""
  echo "[alembic_current_raw]"
  local current_raw
  current_raw="$("${alembic}" -c "${cfg}" current 2>&1)"
  local rc_current=$?
  printf '%s\n' "${current_raw}"
  if [[ "${rc_current}" -ne 0 ]]; then
    echo "ERROR: alembic current failed (rc=${rc_current})" >&2
    return 1
  fi

  local heads current
  heads="$(printf '%s\n' "${heads_raw}" | grep -Eo '[0-9]{4}_[A-Za-z0-9_]+' | sort -u || true)"
  current="$(printf '%s\n' "${current_raw}" | grep -Eo '[0-9]{4}_[A-Za-z0-9_]+' | sort -u || true)"

  echo "alembic_heads=${heads}"
  echo "alembic_current=${current}"

  if [[ -z "${heads}" ]]; then
    echo "ERROR: could not determine alembic heads from output" >&2
    return 1
  fi
  if [[ -z "${current}" ]]; then
    echo "ERROR: could not determine current alembic revision (DB may be uninitialized)" >&2
    echo "Hint: ${alembic} -c ${cfg} upgrade head" >&2
    return 1
  fi

  local missing=()
  while IFS= read -r h; do
    [[ -z "${h}" ]] && continue
    if ! printf '%s\n' "${current}" | grep -qx "${h}"; then
      missing+=("${h}")
    fi
  done <<<"${heads}"

  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "ERROR: database schema is not at Alembic head (missing: ${missing[*]})" >&2
    echo "Hint: ${alembic} -c ${cfg} upgrade head" >&2
    return 1
  fi
}

step_annual_status_json() {
  set -u -o pipefail
  local tmp
  tmp="$(mktemp)"
  "${VENV_BIN}/ha-backend" annual-status --year "${YEAR}" --json >"${tmp}"
  cat "${tmp}"

  # Fail preflight if annual-status reports structural errors or blocking jobs
  # for any annual source (these will prevent a clean annual run).
  "${VENV_BIN}/python3" - "${tmp}" <<'PY'
import json
import sys
from pathlib import Path

raw = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
data = json.loads(raw) if raw.strip() else {}
sources = data.get("sources") or []
summary = data.get("summary") or {}

errors = int(summary.get("errors") or 0)
if errors:
    print(f"ERROR: annual-status reported errors={errors}", file=sys.stderr)
    sys.exit(1)

blockers = []
for item in sources:
    if not isinstance(item, dict):
        continue
    code = item.get("sourceCode")
    blocking = item.get("blockingJob")
    if blocking:
        blockers.append(f"{code}:{blocking.get('status')} id={blocking.get('jobId')} name={blocking.get('jobName')}")

if blockers:
    print("ERROR: blocking jobs exist for annual sources (clear them before annual scheduling/run):", file=sys.stderr)
    for b in blockers:
        print(f"  - {b}", file=sys.stderr)
    sys.exit(1)
PY
  local rc=$?
  rm -f "${tmp}"
  return "${rc}"
}

step_schedule_annual_dry_run() {
  set -u -o pipefail
  local tmp
  tmp="$(mktemp)"
  "${VENV_BIN}/ha-backend" schedule-annual --year "${YEAR}" >"${tmp}"
  cat "${tmp}"

  # schedule-annual is read-only in dry-run mode but may still report errors (e.g. duplicates);
  # treat those as a failed preflight.
  local summary
  summary="$(tail -n 5 "${tmp}" | grep -Eo 'errors=[0-9]+' | head -n 1 || true)"
  if [[ -n "${summary}" ]]; then
    local errs
    errs="$(printf '%s' "${summary}" | cut -d= -f2)"
    if [[ "${errs}" != "0" ]]; then
      echo "ERROR: schedule-annual dry-run reported errors=${errs}" >&2
      rm -f "${tmp}"
      return 1
    fi
  fi
  rm -f "${tmp}"
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

step_seed_reachability() {
  set -u -o pipefail
  local urls=(
    "https://www.canada.ca/en/health-canada.html"
    "https://www.canada.ca/fr/sante-canada.html"
    "https://www.canada.ca/en/public-health.html"
    "https://www.canada.ca/fr/sante-publique.html"
    "https://cihr-irsc.gc.ca/e/193.html"
    "https://cihr-irsc.gc.ca/f/193.html"
  )

  local failed=0
  for u in "${urls[@]}"; do
    echo "GET ${u}"
    if curl -fsSL --max-time 15 --retry 2 --retry-delay 1 --output /dev/null "${u}"; then
      echo "OK ${u}"
    else
      echo "ERROR ${u}" >&2
      failed=$((failed + 1))
    fi
  done

  if [[ "${failed}" -gt 0 ]]; then
    echo "ERROR: ${failed} seed URL(s) failed reachability checks." >&2
    return 1
  fi
}

step_job_queue_hygiene() {
  set -u -o pipefail
  "${VENV_BIN}/python3" ./scripts/vps_job_queue_hygiene.py
}

step_temp_cleanup_candidates() {
  set -u -o pipefail
  "${VENV_BIN}/python3" ./scripts/vps_temp_cleanup_candidates.py
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

inode_check_one() {
  local path="$1"
  local label="$2"

  if ! have_cmd df; then
    warn "df not found; skipping inode checks"
    return 0
  fi

  if [[ ! -e "${path}" ]]; then
    warn "${label}: path missing (${path}); skipping inode check"
    return 0
  fi

  local line used_pct
  line="$(df -Pi "${path}" 2>/dev/null | tail -n 1 || true)"
  used_pct="$(printf '%s' "${line}" | awk '{print $5}' | tr -d '%' || true)"
  if [[ -z "${used_pct}" ]]; then
    warn "${label}: could not parse inode df output for ${path}"
    return 0
  fi

  if [[ "${used_pct}" -ge 80 ]]; then
    fail "${label}: inode usage high (${used_pct}% used at ${path}; policy review threshold is 80%)"
  elif [[ "${used_pct}" -ge 70 ]]; then
    warn "${label}: inode usage elevated (${used_pct}% used at ${path}; target <70%)"
  else
    ok "${label}: inode usage OK (${used_pct}% used at ${path})"
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
campaign_archive_root=${CAMPAIGN_ARCHIVE_ROOT:-}
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
inode_check_one "/" "rootfs"

# Determine the filesystem that will hold the upcoming campaign outputs.
# In a tiered setup (small local SSD + Storage Box), HEALTHARCHIVE_ARCHIVE_ROOT may
# still point at /srv/healtharchive/jobs even though most bytes live on a nested
# mount. For campaign forecasting we want the *actual* tier that new jobs will land on.
campaign_root="${CAMPAIGN_ARCHIVE_ROOT}"
if [[ -z "${campaign_root}" ]]; then
  # Auto-detect: prefer Storage Box tier if present.
  if [[ -d "/srv/healtharchive/storagebox/jobs" ]] && is_mounted "/srv/healtharchive/storagebox"; then
    campaign_root="/srv/healtharchive/storagebox/jobs"
  else
    if [[ -d "/srv/healtharchive/storagebox/jobs" ]] && ! is_mounted "/srv/healtharchive/storagebox"; then
      warn "storagebox dir exists but is not mounted; campaign forecast will use ${HEALTHARCHIVE_ARCHIVE_ROOT:-/srv/healtharchive/jobs}"
    fi
    campaign_root="${HEALTHARCHIVE_ARCHIVE_ROOT:-/srv/healtharchive/jobs}"
  fi
fi

write_file "01-storage-tier.txt" "$(cat <<TIER
timestamp_utc=${timestamp}
campaign_archive_root=${campaign_root}
healtharchive_archive_root=${HEALTHARCHIVE_ARCHIVE_ROOT:-}
storagebox_mounted=$([[ -d "/srv/healtharchive/storagebox" ]] && is_mounted "/srv/healtharchive/storagebox" && echo true || echo false)
TIER
)"

if [[ -n "${HEALTHARCHIVE_ARCHIVE_ROOT:-}" ]]; then
  disk_check_one "${HEALTHARCHIVE_ARCHIVE_ROOT}" "archive_root"
  inode_check_one "${HEALTHARCHIVE_ARCHIVE_ROOT}" "archive_root"
elif [[ -d "/srv/healtharchive" ]]; then
  disk_check_one "/srv/healtharchive" "srv_healtharchive"
  inode_check_one "/srv/healtharchive" "srv_healtharchive"
fi

if [[ "${campaign_root}" != "${HEALTHARCHIVE_ARCHIVE_ROOT:-/srv/healtharchive/jobs}" ]]; then
  disk_check_one "${campaign_root}" "campaign_archive_root"
  inode_check_one "${campaign_root}" "campaign_archive_root"
fi

if [[ -n "${YEAR}" ]]; then
  run_step "Campaign storage forecast" "02-storage-forecast.txt" step_campaign_storage_forecast "${campaign_root}"
else
  warn "campaign storage forecast skipped (pass --year YYYY)"
fi

run_step "CPU/RAM headroom" "03-resource-headroom.txt" step_resource_headroom

if [[ -n "${YEAR}" ]]; then
  if [[ "${SKIP_REHEARSAL_EVIDENCE}" != "true" ]]; then
    run_step "Rehearsal evidence (active crawl headroom)" "03a-rehearsal-evidence.txt" step_rehearsal_evidence
  else
    warn "rehearsal evidence check skipped (--skip-rehearsal-evidence)"
  fi
else
  warn "rehearsal evidence check skipped (pass --year YYYY)"
fi

run_step "Time sync (NTP)" "04-time-sync.txt" step_time_sync

run_step "Services" "05-services.txt" step_services

run_step "Docker daemon access" "06-docker-daemon.txt" step_docker_daemon

if [[ "${campaign_root}" != "${HEALTHARCHIVE_ARCHIVE_ROOT:-/srv/healtharchive/jobs}" ]]; then
  run_step "Campaign tier docker mount probe" "06a-campaign-tier-docker-probe.txt" step_campaign_tier_docker_probe "${campaign_root}"
fi

run_step "DB connectivity" "07-db-check.txt" step_db_check

run_step "DB schema (Alembic at head)" "08-alembic-head.txt" step_alembic_head_check

run_step "API health (loopback)" "09-api-health.json" curl -fsS --max-time 5 "${API_BASE}/api/health"

run_step "Backups" "10-backups.txt" step_backups

run_step "archive-tool dry-run (Docker wiring)" "11-archive-tool-dry-run.txt" step_archive_tool_dry_run

if [[ -n "${YEAR}" ]]; then
  run_step "Seed reachability (annual)" "12-seed-reachability.txt" step_seed_reachability
else
  warn "seed reachability check skipped (pass --year YYYY)"
fi

if [[ -n "${YEAR}" ]]; then
  run_step "annual-status (json)" "13-annual-status.json" step_annual_status_json
  run_step "schedule-annual (dry-run)" "14-schedule-annual.txt" step_schedule_annual_dry_run
else
  warn "annual scheduler dry-run skipped (pass --year YYYY)"
fi

run_step "Job queue hygiene" "15-job-queue-hygiene.txt" step_job_queue_hygiene

run_step "Temp cleanup candidates" "16-temp-cleanup-candidates.txt" step_temp_cleanup_candidates

run_step "Ops automation posture" "17-verify-ops-automation.txt" step_ops_automation

if [[ "${SKIP_BASELINE_DRIFT}" != "true" ]]; then
  run_step "Baseline drift" "18-baseline-drift.txt" step_baseline_drift
else
  warn "baseline drift check skipped (--skip-baseline-drift)"
fi

if [[ "${SKIP_SECURITY_ADMIN}" != "true" ]]; then
  run_step "Security + admin auth (public)" "19-verify-security-and-admin.txt" step_security_admin_public
else
  warn "security/admin check skipped (--skip-security-admin)"
fi

if [[ "${SKIP_PUBLIC_SURFACE}" != "true" ]]; then
  run_step "Public surface" "20-verify-public-surface.txt" step_public_surface
else
  warn "public surface verify skipped (--skip-public-surface)"
fi

if [[ "${SKIP_OBSERVABILITY}" != "true" ]]; then
  if [[ -x "./scripts/vps-verify-observability.sh" ]]; then
    run_step "Observability" "21-verify-observability.txt" step_observability
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
