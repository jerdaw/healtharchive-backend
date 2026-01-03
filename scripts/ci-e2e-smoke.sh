#!/usr/bin/env bash
set -euo pipefail

BACKEND_HOST="127.0.0.1"
BACKEND_PORT="8001"
FRONTEND_PORT="3000"
FRONTEND_DIR=""
TMP_DIR=""
PYTHON_BIN=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [--frontend-dir PATH] [--tmp-dir PATH] [--python PATH]

Runs a local end-to-end smoke check by starting:
- backend (uvicorn) on :${BACKEND_PORT}
- frontend (next start) on :${FRONTEND_PORT}

Then executes: scripts/verify_public_surface.py against both.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --frontend-dir)
      FRONTEND_DIR="${2:-}"
      shift 2
      ;;
    --tmp-dir)
      TMP_DIR="${2:-}"
      shift 2
      ;;
    --python)
      PYTHON_BIN="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
    PYTHON_BIN="${VIRTUAL_ENV}/bin/python"
  elif [[ -x "${BACKEND_DIR}/.venv/bin/python" ]]; then
    PYTHON_BIN="${BACKEND_DIR}/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    echo "ERROR: python not found" >&2
    exit 1
  fi
fi

if [[ -z "${FRONTEND_DIR}" ]]; then
  FRONTEND_DIR="${BACKEND_DIR}/../healtharchive-frontend"
fi
FRONTEND_DIR="$(cd "${FRONTEND_DIR}" && pwd)"

if [[ -z "${TMP_DIR}" ]]; then
  TMP_DIR="${BACKEND_DIR}/.tmp/ci-e2e-smoke"
fi
mkdir -p "${TMP_DIR}"

DB_PATH="${TMP_DIR}/ci-e2e.db"
WARC_PATH="${TMP_DIR}/ci-e2e.warc.gz"
ARCHIVE_ROOT="${TMP_DIR}/archive-root"
mkdir -p "${ARCHIVE_ROOT}"

export HEALTHARCHIVE_ENV="ci"
export HEALTHARCHIVE_DATABASE_URL="sqlite:///${DB_PATH}"
export HEALTHARCHIVE_ARCHIVE_ROOT="${ARCHIVE_ROOT}"
export HEALTHARCHIVE_PUBLIC_SITE_URL="http://${BACKEND_HOST}:${FRONTEND_PORT}"

export NEXT_PUBLIC_API_BASE_URL="http://${BACKEND_HOST}:${BACKEND_PORT}"
export NEXT_PUBLIC_SHOW_API_HEALTH_BANNER="false"
export NEXT_PUBLIC_LOG_API_HEALTH_FAILURE="false"
export NEXT_PUBLIC_SHOW_API_BASE_HINT="false"

"${PYTHON_BIN}" "${SCRIPT_DIR}/ci-e2e-seed.py" --db-path "${DB_PATH}" --warc-path "${WARC_PATH}"

BACKEND_LOG="${TMP_DIR}/backend.log"
FRONTEND_BUILD_LOG="${TMP_DIR}/frontend-build.log"
FRONTEND_LOG="${TMP_DIR}/frontend.log"

backend_pid=""
frontend_pid=""

cleanup() {
  set +e
  if [[ -n "${frontend_pid}" ]]; then
    kill "${frontend_pid}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${backend_pid}" ]]; then
    kill "${backend_pid}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

"${PYTHON_BIN}" -m uvicorn ha_backend.api:app --host "${BACKEND_HOST}" --port "${BACKEND_PORT}" \
  --log-level warning >"${BACKEND_LOG}" 2>&1 &
backend_pid="$!"

wait_for_url() {
  local url="$1"
  local seconds="$2"
  local deadline
  deadline=$((SECONDS + seconds))
  while (( SECONDS < deadline )); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

if ! wait_for_url "http://${BACKEND_HOST}:${BACKEND_PORT}/api/health" 30; then
  echo "Backend failed to start; log follows:" >&2
  tail -n 200 "${BACKEND_LOG}" >&2 || true
  exit 1
fi

pushd "${FRONTEND_DIR}" >/dev/null
npm run build >"${FRONTEND_BUILD_LOG}" 2>&1
npm run start -- -p "${FRONTEND_PORT}" >"${FRONTEND_LOG}" 2>&1 &
frontend_pid="$!"
popd >/dev/null

if ! wait_for_url "http://${BACKEND_HOST}:${FRONTEND_PORT}/" 60; then
  echo "Frontend failed to start; logs follow:" >&2
  tail -n 200 "${FRONTEND_BUILD_LOG}" >&2 || true
  tail -n 200 "${FRONTEND_LOG}" >&2 || true
  exit 1
fi

"${PYTHON_BIN}" "${SCRIPT_DIR}/verify_public_surface.py" \
  --api-base "http://${BACKEND_HOST}:${BACKEND_PORT}" \
  --frontend-base "http://${BACKEND_HOST}:${FRONTEND_PORT}" \
  --skip-replay
