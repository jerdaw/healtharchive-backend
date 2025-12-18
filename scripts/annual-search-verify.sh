#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Verify annual campaign search readiness and capture search eval artifacts.

This is a light-weight ops helper for Phase 7 (post-campaign verification):
  1) Runs `ha-backend annual-status --year YYYY --json` and verifies readyForSearch=true
  2) Writes annual-status artifacts into a year-tagged capture directory
  3) Runs ./scripts/search-eval-capture.sh with a stable --run-id so artifacts live together

Usage:
  ./scripts/annual-search-verify.sh [--year YYYY] [--out-root DIR] [--base-url URL] [--run-id ID] [--env-file FILE] [--allow-not-ready] [--allow-existing] [--] [capture args...]

Examples:
  ./scripts/annual-search-verify.sh
  ./scripts/annual-search-verify.sh --year 2027
  ./scripts/annual-search-verify.sh --base-url https://api.healtharchive.ca
  ./scripts/annual-search-verify.sh --year 2027 -- --ranking v2
  ./scripts/annual-search-verify.sh --year 2027 --run-id 20270101T000700Z -- --page-size 50

Notes:
  - Default year is current UTC year.
  - Default out-root is /srv/healtharchive/ops/search-eval if /srv/healtharchive exists,
    otherwise /tmp/ha-search-eval.
  - If HEALTHARCHIVE_DATABASE_URL is not set in your shell, this script will auto-source
    /etc/healtharchive/backend.env when it exists (or you can pass --env-file).
  - capture args after `--` are passed through to ./scripts/search-eval-capture.sh.
EOF
}

YEAR=""
OUT_ROOT=""
BASE_URL="http://127.0.0.1:8001"
RUN_ID=""
ENV_FILE=""
EFFECTIVE_ENV_FILE=""
ALLOW_NOT_READY="false"
ALLOW_EXISTING="false"

CAPTURE_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --year)
      YEAR="$2"
      shift 2
      ;;
    --out-root)
      OUT_ROOT="$2"
      shift 2
      ;;
    --base-url)
      BASE_URL="$2"
      shift 2
      ;;
    --run-id)
      RUN_ID="$2"
      shift 2
      ;;
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --allow-not-ready)
      ALLOW_NOT_READY="true"
      shift 1
      ;;
    --allow-existing)
      ALLOW_EXISTING="true"
      shift 1
      ;;
    --)
      shift 1
      CAPTURE_ARGS+=("$@")
      break
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
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PYTHON_BIN="python"
if [[ -z "${VIRTUAL_ENV:-}" && -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
fi

HA_BACKEND_BIN="ha-backend"
if ! command -v "${HA_BACKEND_BIN}" >/dev/null 2>&1; then
  if [[ -x "${REPO_ROOT}/.venv/bin/ha-backend" ]]; then
    HA_BACKEND_BIN="${REPO_ROOT}/.venv/bin/ha-backend"
  else
    echo "ERROR: ha-backend not found in PATH and no venv binary at ${REPO_ROOT}/.venv/bin/ha-backend" >&2
    echo "Hint: activate the venv or install deps: pip install -e '.[dev]'" >&2
    exit 1
  fi
fi

if [[ -z "${HEALTHARCHIVE_DATABASE_URL:-}" ]]; then
  auto_env="/etc/healtharchive/backend.env"
  if [[ -n "${ENV_FILE}" ]]; then
    if [[ ! -f "${ENV_FILE}" ]]; then
      echo "ERROR: --env-file not found: ${ENV_FILE}" >&2
      exit 2
    fi
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
    EFFECTIVE_ENV_FILE="${ENV_FILE}"
  elif [[ -f "${auto_env}" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${auto_env}"
    set +a
    EFFECTIVE_ENV_FILE="${auto_env}"
  fi
fi

if [[ -z "${YEAR}" ]]; then
  YEAR="$(date -u +%Y)"
fi
if [[ ! "${YEAR}" =~ ^[0-9]{4}$ ]]; then
  echo "ERROR: --year must be a 4-digit year (got: ${YEAR})" >&2
  exit 2
fi

if [[ -z "${OUT_ROOT}" ]]; then
  if [[ -d "/srv/healtharchive" ]]; then
    OUT_ROOT="/srv/healtharchive/ops/search-eval"
  else
    OUT_ROOT="/tmp/ha-search-eval"
  fi
fi

if [[ -z "${RUN_ID}" ]]; then
  RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
fi
if [[ ! "${RUN_ID}" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "ERROR: --run-id must match ^[A-Za-z0-9._-]+$ (got: ${RUN_ID})" >&2
  exit 2
fi

YEAR_DIR="${OUT_ROOT%/}/${YEAR}"
CAPTURE_DIR="${YEAR_DIR%/}/${RUN_ID}"

annual_json="$("${HA_BACKEND_BIN}" annual-status --year "${YEAR}" --json)"
ready="$(
  printf '%s' "${annual_json}" | "${PYTHON_BIN}" - <<'PY'
import json
import sys

data = json.load(sys.stdin)
print("true" if data.get("summary", {}).get("readyForSearch") else "false")
PY
)"

if [[ "${ready}" != "true" && "${ALLOW_NOT_READY}" != "true" ]]; then
  echo "ERROR: Annual campaign ${YEAR} is not ready for search; refusing to capture." >&2
  echo "" >&2
  "${HA_BACKEND_BIN}" annual-status --year "${YEAR}" >&2 || true
  echo "" >&2
  echo "Hint: pass --allow-not-ready to capture anyway." >&2
  exit 3
fi

if [[ -d "${CAPTURE_DIR}" && "${ALLOW_EXISTING}" != "true" ]]; then
  existing_count="$(ls -A "${CAPTURE_DIR}" 2>/dev/null | wc -l | tr -d '[:space:]' || true)"
  if [[ "${existing_count}" != "0" ]]; then
    echo "ERROR: Capture dir exists and is non-empty: ${CAPTURE_DIR}" >&2
    echo "Hint: choose a different --run-id or pass --allow-existing." >&2
    exit 2
  fi
fi

mkdir -p "${CAPTURE_DIR}"

printf '%s\n' "${annual_json}" > "${CAPTURE_DIR}/annual-status.json"
"${HA_BACKEND_BIN}" annual-status --year "${YEAR}" > "${CAPTURE_DIR}/annual-status.txt" || true

meta_file="${CAPTURE_DIR}/annual-search-verify.meta.txt"
{
  echo "campaign_year=${YEAR}"
  echo "run_id=${RUN_ID}"
  echo "verified_at_utc=$(date -u +%Y%m%dT%H%M%SZ)"
  echo "base_url=${BASE_URL}"
  echo "env_file=${EFFECTIVE_ENV_FILE:-none}"
  echo "out_root=${OUT_ROOT}"
  echo "capture_dir=${CAPTURE_DIR}"
  echo "hostname=$(hostname)"
  if git -C "${REPO_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "git_sha=$(git -C "${REPO_ROOT}" rev-parse HEAD)"
  fi
} > "${meta_file}"

capture_script="${SCRIPT_DIR}/search-eval-capture.sh"
if [[ ! -f "${capture_script}" ]]; then
  echo "ERROR: Missing capture script at ${capture_script}" >&2
  exit 1
fi

"${capture_script}" \
  --base-url "${BASE_URL}" \
  --out-dir "${YEAR_DIR}" \
  --run-id "${RUN_ID}" \
  "${CAPTURE_ARGS[@]}"

echo "Search eval capture complete: ${CAPTURE_DIR}"
