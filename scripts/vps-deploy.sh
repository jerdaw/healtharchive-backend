#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive backend — single-VPS deploy helper (Phase 9).

Safe-by-default: this script is DRY-RUN unless you pass --apply.

Usage:
  ./scripts/vps-deploy.sh [--apply] [--ref REF] [--repo-dir DIR] [--env-file FILE] [--health-url URL]
                         [--skip-deps] [--skip-migrations] [--skip-restart] [--restart-replay]
                         [--skip-baseline-drift] [--baseline-mode MODE]
                         [--skip-public-surface-verify]
                         [--install-systemd-units] [--apply-alerting]
                         [--public-api-base URL] [--public-frontend-base URL] [--public-timeout-seconds SECONDS]
                         [--allow-dirty] [--no-pull] [--lock-file FILE]

Examples (on the VPS):
  cd /opt/healtharchive-backend

  # Dry-run (prints what would happen):
  ./scripts/vps-deploy.sh

  # Deploy latest main (fast-forward only):
  ./scripts/vps-deploy.sh --apply

  # Deploy a pinned commit:
  ./scripts/vps-deploy.sh --apply --ref 0123deadbeef...

Notes:
  - This script never prints secrets; it only sources the env file to run Alembic.
  - It refuses to run with a dirty git working tree unless you pass --allow-dirty.
  - It uses a lock file to avoid concurrent deploys (default: /tmp/healtharchive-backend-deploy.lock).
EOF
}

APPLY="false"
REF=""
REPO_DIR=""
ENV_FILE="/etc/healtharchive/backend.env"
HEALTH_URL="http://127.0.0.1:8001/api/health"
LOCK_FILE="/tmp/healtharchive-backend-deploy.lock"

ALLOW_DIRTY="false"
NO_PULL="false"
SKIP_DEPS="false"
SKIP_MIGRATIONS="false"
SKIP_RESTART="false"
RESTART_REPLAY="false"
SKIP_BASELINE_DRIFT="false"
BASELINE_MODE="local"
SKIP_PUBLIC_SURFACE_VERIFY="false"
INSTALL_SYSTEMD_UNITS="false"
APPLY_ALERTING="false"
PUBLIC_API_BASE="https://api.healtharchive.ca"
PUBLIC_FRONTEND_BASE="https://www.healtharchive.ca"
PUBLIC_TIMEOUT_SECONDS="20"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY="true"
      shift 1
      ;;
    --ref)
      REF="$2"
      shift 2
      ;;
    --repo-dir)
      REPO_DIR="$2"
      shift 2
      ;;
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --health-url)
      HEALTH_URL="$2"
      shift 2
      ;;
    --lock-file)
      LOCK_FILE="$2"
      shift 2
      ;;
    --allow-dirty)
      ALLOW_DIRTY="true"
      shift 1
      ;;
    --no-pull)
      NO_PULL="true"
      shift 1
      ;;
    --skip-deps)
      SKIP_DEPS="true"
      shift 1
      ;;
    --skip-migrations)
      SKIP_MIGRATIONS="true"
      shift 1
      ;;
    --skip-restart)
      SKIP_RESTART="true"
      shift 1
      ;;
    --restart-replay)
      RESTART_REPLAY="true"
      shift 1
      ;;
    --skip-baseline-drift)
      SKIP_BASELINE_DRIFT="true"
      shift 1
      ;;
    --baseline-mode)
      BASELINE_MODE="$2"
      shift 2
      ;;
    --skip-public-surface-verify)
      SKIP_PUBLIC_SURFACE_VERIFY="true"
      shift 1
      ;;
    --install-systemd-units)
      INSTALL_SYSTEMD_UNITS="true"
      shift 1
      ;;
    --apply-alerting)
      APPLY_ALERTING="true"
      shift 1
      ;;
    --public-api-base)
      PUBLIC_API_BASE="$2"
      shift 2
      ;;
    --public-frontend-base)
      PUBLIC_FRONTEND_BASE="$2"
      shift 2
      ;;
    --public-timeout-seconds)
      PUBLIC_TIMEOUT_SECONDS="$2"
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${REPO_DIR}" ]]; then
  REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi

if [[ ! -d "${REPO_DIR}/.git" ]]; then
  echo "ERROR: Not a git repo: ${REPO_DIR}" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: Env file not found: ${ENV_FILE}" >&2
  exit 1
fi

VENV_BIN="${REPO_DIR}/.venv/bin"
if [[ ! -x "${VENV_BIN}/python3" || ! -x "${VENV_BIN}/pip" ]]; then
  echo "ERROR: Missing venv at ${VENV_BIN} (expected python3 + pip)." >&2
  echo "Hint: create it with: python -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

run() {
  if [[ "${APPLY}" != "true" ]]; then
    echo "+ $*"
    return 0
  fi
  "$@"
}

run_shell() {
  if [[ "${APPLY}" != "true" ]]; then
    echo "+ bash -lc $1"
    return 0
  fi
  bash -lc "$1"
}

wait_for_health() {
  local url="$1"
  local attempts="${2:-30}"
  local delay_seconds="${3:-1}"

  if [[ "${APPLY}" != "true" ]]; then
    echo "+ wait_for_health \"${url}\" (attempts=${attempts}, delay=${delay_seconds}s)"
    return 0
  fi

  local i
  for ((i = 1; i <= attempts; i++)); do
    if curl -fsS --max-time 2 "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep "${delay_seconds}"
  done

  echo "ERROR: Health check failed after ${attempts} attempts: ${url}" >&2
  echo "Hint: Inspect service status and logs:" >&2
  echo "  sudo systemctl status healtharchive-api healtharchive-worker --no-pager -l" >&2
  echo "  sudo journalctl -u healtharchive-api -n 200 --no-pager" >&2
  return 1
}

lock_dir="$(dirname "${LOCK_FILE}")"
mkdir -p "${lock_dir}"

if command -v flock >/dev/null 2>&1; then
  exec 200>"${LOCK_FILE}"
  if ! flock -n 200; then
    echo "ERROR: Deploy lock is held; another deploy may be running: ${LOCK_FILE}" >&2
    exit 2
  fi
else
  if [[ -e "${LOCK_FILE}" ]]; then
    echo "ERROR: Deploy lock file exists (flock not available): ${LOCK_FILE}" >&2
    exit 2
  fi
  if [[ "${APPLY}" == "true" ]]; then
    printf 'pid=%s\nstarted_at_utc=%s\n' "$$" "$(date -u +%Y%m%dT%H%M%SZ)" > "${LOCK_FILE}"
    trap 'rm -f "${LOCK_FILE}"' EXIT
  fi
fi

cd "${REPO_DIR}"

echo "HealthArchive backend deploy"
echo "----------------------------"
echo "Mode:        $([[ "${APPLY}" == "true" ]] && echo APPLY || echo DRY-RUN)"
echo "Repo:        ${REPO_DIR}"
echo "Env file:    ${ENV_FILE}"
echo "Health URL:  ${HEALTH_URL}"
echo "Lock file:   ${LOCK_FILE}"
echo "Baseline:    $([[ "${SKIP_BASELINE_DRIFT}" == "true" ]] && echo SKIPPED || echo "ENABLED (mode=${BASELINE_MODE})")"
echo "Public verify: $([[ "${SKIP_PUBLIC_SURFACE_VERIFY}" == "true" ]] && echo SKIPPED || echo "ENABLED (api=${PUBLIC_API_BASE}, frontend=${PUBLIC_FRONTEND_BASE})")"
echo "Systemd units: $([[ "${INSTALL_SYSTEMD_UNITS}" == "true" ]] && echo INSTALL || echo SKIP)"
echo "Alerting:     $([[ "${APPLY_ALERTING}" == "true" ]] && echo APPLY || echo SKIP)"
if [[ -n "${REF}" ]]; then
  echo "Ref:         ${REF}"
fi
echo ""

current_sha="$(git rev-parse HEAD)"
echo "Current git SHA: ${current_sha}"

if [[ "${ALLOW_DIRTY}" != "true" ]]; then
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "ERROR: Working tree is dirty. Commit/stash changes or pass --allow-dirty." >&2
    git status --porcelain >&2
    exit 2
  fi
fi

if [[ "${NO_PULL}" != "true" ]]; then
  if [[ -n "${REF}" ]]; then
    run git fetch --prune origin
    run git checkout --detach "${REF}"
  else
    run git pull --ff-only
  fi
else
  echo "Skipping git pull (--no-pull)."
fi

new_sha="$(git rev-parse HEAD)"
echo "Target git SHA:  ${new_sha}"
echo ""

if [[ "${SKIP_DEPS}" != "true" ]]; then
  run "${VENV_BIN}/pip" install -e ".[dev]" "psycopg[binary]"
else
  echo "Skipping dependency install (--skip-deps)."
fi

if [[ "${SKIP_MIGRATIONS}" != "true" ]]; then
  run_shell "set -a; source \"${ENV_FILE}\"; set +a; \"${VENV_BIN}/alembic\" -c alembic.ini upgrade head"
else
  echo "Skipping migrations (--skip-migrations)."
fi

if [[ "${INSTALL_SYSTEMD_UNITS}" == "true" ]]; then
  run sudo "${REPO_DIR}/scripts/vps-install-systemd-units.sh" --apply
fi

if [[ "${SKIP_RESTART}" != "true" ]]; then
  run sudo systemctl daemon-reload
  run sudo systemctl restart healtharchive-api healtharchive-worker
  run sudo systemctl status healtharchive-api healtharchive-worker --no-pager -l

  if [[ "${RESTART_REPLAY}" == "true" ]]; then
    BANNER_SRC="${REPO_DIR}/docs/deployment/pywb/custom_banner.html"
    if [[ -f "${BANNER_SRC}" ]]; then
      run sudo mkdir -p /srv/healtharchive/replay/templates
      run sudo install -o hareplay -g healtharchive -m 0640 \
        "${BANNER_SRC}" /srv/healtharchive/replay/templates/custom_banner.html
    else
      echo "WARN: Replay banner template not found at ${BANNER_SRC}" >&2
    fi
    run sudo systemctl restart healtharchive-replay.service
    run bash -lc "sleep 1; curl -fsSI http://127.0.0.1:8090/ | head"
  fi
else
  echo "Skipping service restart (--skip-restart)."
fi

wait_for_health "${HEALTH_URL}" 30 1
run bash -lc "curl -fsS \"${HEALTH_URL}\" | head -c 2000; echo"

if [[ "${SKIP_BASELINE_DRIFT}" != "true" ]]; then
  case "${BASELINE_MODE}" in
    local|live)
      ;;
    *)
      echo "ERROR: --baseline-mode must be 'local' or 'live' (got: ${BASELINE_MODE})" >&2
      exit 2
      ;;
  esac

  # Capture and enforce production baseline invariants (security posture, perms,
  # systemd enablement, etc.). This is intentionally a post-deploy gate so we
  # catch drift before calling the deploy "done".
  run "${VENV_BIN}/python3" ./scripts/check_baseline_drift.py --mode "${BASELINE_MODE}"
else
  echo "Skipping baseline drift check (--skip-baseline-drift)."
fi

if [[ "${SKIP_PUBLIC_SURFACE_VERIFY}" != "true" ]]; then
  run "${VENV_BIN}/python3" ./scripts/verify_public_surface.py \
    --api-base "${PUBLIC_API_BASE}" \
    --frontend-base "${PUBLIC_FRONTEND_BASE}" \
    --timeout-seconds "${PUBLIC_TIMEOUT_SECONDS}"
else
  echo "Skipping public surface verification (--skip-public-surface-verify)."
fi

if [[ "${APPLY_ALERTING}" == "true" ]]; then
  run sudo "${REPO_DIR}/scripts/vps-install-observability-alerting.sh" --apply
fi

echo ""
echo "Deploy complete."
