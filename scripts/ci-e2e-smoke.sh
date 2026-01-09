#!/usr/bin/env bash
set -euo pipefail

BACKEND_HOST="127.0.0.1"
BACKEND_PORT=""
FRONTEND_PORT=""
FRONTEND_DIR=""
TMP_DIR=""
PYTHON_BIN=""
SKIP_FRONTEND_BUILD="0"
USE_SETSID="0"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--frontend-dir PATH] [--tmp-dir PATH] [--python PATH]

Runs a local end-to-end smoke check by starting:
- backend (uvicorn) on a free local port
- frontend (next start) on a free local port

Then executes: scripts/verify_public_surface.py against both.

Options:
  --skip-frontend-build   Assume frontend is already built (.next exists); do not run 'npm run build'.
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
    --skip-frontend-build)
      SKIP_FRONTEND_BUILD="1"
      shift 1
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

if command -v setsid >/dev/null 2>&1; then
  USE_SETSID="1"
fi

if [[ -z "${FRONTEND_DIR}" ]]; then
  FRONTEND_DIR="${BACKEND_DIR}/../healtharchive-frontend"
fi
FRONTEND_DIR="$(cd "${FRONTEND_DIR}" && pwd)"
if [[ ! -f "${FRONTEND_DIR}/package.json" ]]; then
  if [[ -f "${FRONTEND_DIR}/healtharchive-frontend/package.json" ]]; then
    FRONTEND_DIR="${FRONTEND_DIR}/healtharchive-frontend"
  elif [[ -f "${FRONTEND_DIR}/frontend/package.json" ]]; then
    FRONTEND_DIR="${FRONTEND_DIR}/frontend"
  else
    echo "ERROR: frontend dir '${FRONTEND_DIR}' does not contain package.json." >&2
    echo "       Pass --frontend-dir <path-to-frontend> (e.g. ../healtharchive-frontend)." >&2
    exit 1
  fi
fi

if [[ -z "${TMP_DIR}" ]]; then
  TMP_DIR="${BACKEND_DIR}/.tmp/ci-e2e-smoke"
fi
mkdir -p "${TMP_DIR}"

pick_free_port() {
  "${PYTHON_BIN}" - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

if [[ "${SKIP_FRONTEND_BUILD}" == "1" && -z "${BACKEND_PORT}" ]]; then
  api_base="${NEXT_PUBLIC_API_BASE_URL:-}"
  if [[ -n "${api_base}" && "${api_base}" =~ ^https?://(127\.0\.0\.1|localhost):([0-9]{2,5})(/|$) ]]; then
    BACKEND_PORT="${BASH_REMATCH[2]}"
  fi
fi

BACKEND_PORT="${BACKEND_PORT:-$(pick_free_port)}"
FRONTEND_PORT="${FRONTEND_PORT:-$(pick_free_port)}"

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

tail_logs() {
  set +e
  if [[ -f "${BACKEND_LOG}" ]]; then
    echo "" >&2
    echo "== backend.log (tail) ==" >&2
    tail -n 250 "${BACKEND_LOG}" >&2 || true
  fi
  if [[ -f "${FRONTEND_BUILD_LOG}" ]]; then
    echo "" >&2
    echo "== frontend-build.log (tail) ==" >&2
    tail -n 250 "${FRONTEND_BUILD_LOG}" >&2 || true
  fi
  if [[ -f "${FRONTEND_LOG}" ]]; then
    echo "" >&2
    echo "== frontend.log (tail) ==" >&2
    tail -n 250 "${FRONTEND_LOG}" >&2 || true
  fi
}

cleanup() {
  set +e
  if [[ -n "${frontend_pid}" ]]; then
    if [[ "${USE_SETSID}" == "1" ]]; then
      kill -TERM -- "-${frontend_pid}" >/dev/null 2>&1 || true
    fi
    kill -TERM "${frontend_pid}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${backend_pid}" ]]; then
    if [[ "${USE_SETSID}" == "1" ]]; then
      kill -TERM -- "-${backend_pid}" >/dev/null 2>&1 || true
    fi
    kill -TERM "${backend_pid}" >/dev/null 2>&1 || true
  fi
}

on_exit() {
  code="$?"
  cleanup
  if [[ "${code}" -ne 0 ]]; then
    tail_logs
  fi
  exit "${code}"
}
trap on_exit EXIT

if [[ "${USE_SETSID}" == "1" ]]; then
  setsid "${PYTHON_BIN}" -m uvicorn ha_backend.api:app --host "${BACKEND_HOST}" --port "${BACKEND_PORT}" \
    --log-level warning >"${BACKEND_LOG}" 2>&1 &
else
  "${PYTHON_BIN}" -m uvicorn ha_backend.api:app --host "${BACKEND_HOST}" --port "${BACKEND_PORT}" \
    --log-level warning >"${BACKEND_LOG}" 2>&1 &
fi
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
  echo "Backend failed to start." >&2
  exit 1
fi

pushd "${FRONTEND_DIR}" >/dev/null
if [[ "${SKIP_FRONTEND_BUILD}" != "1" ]]; then
  npm run build >"${FRONTEND_BUILD_LOG}" 2>&1
else
  : >"${FRONTEND_BUILD_LOG}"
fi
if [[ "${USE_SETSID}" == "1" ]]; then
  setsid npm run start -- -p "${FRONTEND_PORT}" >"${FRONTEND_LOG}" 2>&1 &
else
  npm run start -- -p "${FRONTEND_PORT}" >"${FRONTEND_LOG}" 2>&1 &
fi
frontend_pid="$!"
popd >/dev/null

if ! wait_for_url "http://${BACKEND_HOST}:${FRONTEND_PORT}/archive" 60; then
  echo "Frontend failed to start (archive route not ready)." >&2
  exit 1
fi

"${PYTHON_BIN}" -c "import urllib.request; urllib.request.urlopen('http://${BACKEND_HOST}:${FRONTEND_PORT}/fr/archive', timeout=10).read(1)" >/dev/null 2>&1 || true

"${PYTHON_BIN}" "${SCRIPT_DIR}/verify_public_surface.py" \
  --api-base "http://${BACKEND_HOST}:${BACKEND_PORT}" \
  --frontend-base "http://${BACKEND_HOST}:${FRONTEND_PORT}" \
  --skip-replay
