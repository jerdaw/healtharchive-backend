#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive — baseline inventory capture helper.

Purpose:
  Capture a reproducible "what is currently configured/installed" snapshot
  without leaking secrets. Intended for Phase 0 of the sequential plan.

Usage:
  ./scripts/capture-baseline-inventory.sh [--out FILE] [--env-file FILE]

Options:
  --out FILE       Write the report to FILE (default: /tmp/healtharchive-baseline-inventory-<ts>.txt)
  --env-file FILE  Parse FILE for known env vars and include them (default: none).
                  This is read as text (not sourced).

Exit codes:
  0 = success
  2 = usage error
EOF
}

OUT_FILE=""
ENV_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)
      OUT_FILE="$2"
      shift 2
      ;;
    --env-file)
      ENV_FILE="$2"
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

timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
if [[ -z "${OUT_FILE}" ]]; then
  OUT_FILE="/tmp/healtharchive-baseline-inventory-${timestamp}.txt"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

redact_value() {
  local key="$1"
  local value="$2"

  case "${key}" in
    *TOKEN|*PASSWORD|*SECRET|*PRIVATE_KEY|*API_KEY|*ACCESS_KEY)
      echo "<redacted>"
      return 0
      ;;
  esac

  if [[ "${key}" == "HEALTHARCHIVE_DATABASE_URL" ]]; then
    if [[ "${value}" == sqlite:///* ]]; then
      echo "${value}"
      return 0
    fi
    if [[ "${value}" == *"://"* && "${value}" == *"@"* ]]; then
      local prefix="${value%%://*}://"
      local rest="${value#*://}"
      local after_creds="${rest#*@}"
      echo "${prefix}<redacted>@${after_creds}"
      return 0
    fi
  fi

  echo "${value}"
}

print_kv() {
  local key="$1"
  local value="$2"
  printf '%s=%s\n' "${key}" "$(redact_value "${key}" "${value}")"
}

capture_env_from_current_process() {
  env | LC_ALL=C sort | while IFS='=' read -r k v; do
    case "${k}" in
      HEALTHARCHIVE_*|HA_*|NEXT_PUBLIC_*)
        print_kv "${k}" "${v}"
        ;;
    esac
  done
}

capture_env_from_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    echo "NOTE: env file not found: ${path}"
    return 0
  fi

  # Parse simple KEY=VALUE lines (ignores export, comments, and complex shells).
  # This is deliberate: we do not want to execute arbitrary shell code.
  sed -n 's/^[[:space:]]*export[[:space:]]\+//; /^[[:space:]]*#/d; /^[[:space:]]*$/d; p' "${path}" \
    | while IFS='=' read -r k v; do
        k="$(echo "${k}" | xargs)"
        v="$(echo "${v}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        v="${v%\"}"
        v="${v#\"}"
        v="${v%\'}"
        v="${v#\'}"
        case "${k}" in
          HEALTHARCHIVE_*|HA_*|NEXT_PUBLIC_*)
            print_kv "${k}" "${v}"
            ;;
        esac
      done
}

{
  echo "HealthArchive baseline inventory"
  echo "timestamp_utc=${timestamp}"
  echo ""

  echo "[host]"
  echo "hostname=$(hostname -f 2>/dev/null || hostname)"
  echo "uname=$(uname -a)"
  if command -v lsb_release >/dev/null 2>&1; then
    echo "lsb_release=$(lsb_release -ds)"
  fi
  echo ""

  echo "[repo]"
  echo "repo_dir=${REPO_DIR}"
  if command -v git >/dev/null 2>&1 && [[ -d "${REPO_DIR}/.git" ]]; then
    echo "git_head=$(git -C "${REPO_DIR}" rev-parse HEAD)"
    echo "git_branch=$(git -C "${REPO_DIR}" rev-parse --abbrev-ref HEAD)"
    dirty="$(git -C "${REPO_DIR}" status --porcelain)"
    if [[ -n "${dirty}" ]]; then
      echo "git_dirty=true"
    else
      echo "git_dirty=false"
    fi
  else
    echo "git_head=(unknown)"
  fi
  echo ""

  echo "[runtimes]"
  if command -v python3 >/dev/null 2>&1; then
    echo "python3=$(python3 --version 2>&1)"
  fi
  if [[ -x "${REPO_DIR}/.venv/bin/python" ]]; then
    echo "venv_python=$("${REPO_DIR}/.venv/bin/python" --version 2>&1)"
  fi
  if command -v node >/dev/null 2>&1; then
    echo "node=$(node --version 2>&1)"
  fi
  if command -v npm >/dev/null 2>&1; then
    echo "npm=$(npm --version 2>&1)"
  fi
  echo ""

  echo "[env_current_process]"
  capture_env_from_current_process
  echo ""

  if [[ -n "${ENV_FILE}" ]]; then
    echo "[env_file]"
    echo "env_file_path=${ENV_FILE}"
    capture_env_from_file "${ENV_FILE}"
    echo ""
  fi
} >"${OUT_FILE}"

echo "Wrote: ${OUT_FILE}"
