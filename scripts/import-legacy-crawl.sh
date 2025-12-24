#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive — legacy crawl import helper (Phase 9).

This script helps you complete a legacy WARC import after the files are already
copied onto the VPS under /srv/healtharchive/jobs/imports/<IMPORT_NAME>.

Safe-by-default: this script is DRY-RUN unless you pass --apply.

Usage:
  ./scripts/import-legacy-crawl.sh --import-dir DIR --source CODE [--job-name NAME]
                                  [--env-file FILE] [--owner USER] [--group GROUP]
                                  [--job-id ID]
                                  [--skip-perms] [--skip-register] [--skip-index]
                                  [--apply]

Examples (on the VPS):
  # CIHR legacy import (new job):
  ./scripts/import-legacy-crawl.sh \
    --import-dir /srv/healtharchive/jobs/imports/legacy-cihr-2025-04 \
    --source cihr \
    --job-name legacy-cihr-2025-04 \
    --apply

  # Re-index an already-registered job ID:
  ./scripts/import-legacy-crawl.sh --job-id 42 --skip-register --apply

Notes:
  - This script does NOT copy WARCs from your NAS; see docs/operations/legacy-crawl-imports.md.
  - It normalizes filesystem permissions (to avoid world-writable rsync artifacts),
    registers the directory as an ArchiveJob, then indexes it.
  - Indexing can take hours. Consider running inside tmux.
EOF
}

APPLY="false"
IMPORT_DIR=""
SOURCE_CODE=""
JOB_NAME=""
JOB_ID=""
ENV_FILE="/etc/healtharchive/backend.env"
OWNER_USER=""
GROUP_NAME="healtharchive"

SKIP_PERMS="false"
SKIP_REGISTER="false"
SKIP_INDEX="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY="true"
      shift 1
      ;;
    --import-dir)
      IMPORT_DIR="$2"
      shift 2
      ;;
    --source)
      SOURCE_CODE="$2"
      shift 2
      ;;
    --job-name)
      JOB_NAME="$2"
      shift 2
      ;;
    --job-id)
      JOB_ID="$2"
      shift 2
      ;;
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --owner)
      OWNER_USER="$2"
      shift 2
      ;;
    --group)
      GROUP_NAME="$2"
      shift 2
      ;;
    --skip-perms)
      SKIP_PERMS="true"
      shift 1
      ;;
    --skip-register)
      SKIP_REGISTER="true"
      shift 1
      ;;
    --skip-index)
      SKIP_INDEX="true"
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_BIN="${REPO_DIR}/.venv/bin"

if [[ ! -x "${VENV_BIN}/ha-backend" ]]; then
  echo "ERROR: Missing ha-backend CLI at ${VENV_BIN}/ha-backend" >&2
  echo "Hint: create venv with: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

if [[ "${SKIP_REGISTER}" != "true" && -z "${SOURCE_CODE}" ]]; then
  echo "ERROR: --source is required unless --skip-register is set." >&2
  exit 2
fi

if [[ -n "${JOB_ID}" ]]; then
  if ! [[ "${JOB_ID}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: --job-id must be numeric (got: ${JOB_ID})" >&2
    exit 2
  fi
fi

if [[ -z "${JOB_ID}" && -z "${IMPORT_DIR}" ]]; then
  echo "ERROR: --import-dir is required unless --job-id is provided." >&2
  exit 2
fi

if [[ -n "${IMPORT_DIR}" ]]; then
  if [[ ! -d "${IMPORT_DIR}" ]]; then
    echo "ERROR: --import-dir does not exist or is not a directory: ${IMPORT_DIR}" >&2
    exit 2
  fi
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: Env file not found: ${ENV_FILE}" >&2
  exit 1
fi

if [[ -z "${OWNER_USER}" ]]; then
  if id -u habackup >/dev/null 2>&1; then
    OWNER_USER="habackup"
  else
    OWNER_USER="$(id -un)"
  fi
fi

echo "HealthArchive legacy import"
echo "---------------------------"
echo "Mode:       $([[ "${APPLY}" == "true" ]] && echo APPLY || echo DRY-RUN)"
echo "Repo:       ${REPO_DIR}"
echo "Env file:   ${ENV_FILE}"
if [[ -n "${IMPORT_DIR}" ]]; then
  echo "Import dir:  ${IMPORT_DIR}"
fi
if [[ -n "${SOURCE_CODE}" ]]; then
  echo "Source:     ${SOURCE_CODE}"
fi
if [[ -n "${JOB_NAME}" ]]; then
  echo "Job name:   ${JOB_NAME}"
fi
if [[ -n "${JOB_ID}" ]]; then
  echo "Job ID:     ${JOB_ID}"
fi
echo "Owner:      ${OWNER_USER}"
echo "Group:      ${GROUP_NAME}"
echo "Perms:      $([[ "${SKIP_PERMS}" == "true" ]] && echo SKIPPED || echo ENABLED)"
echo "Register:   $([[ "${SKIP_REGISTER}" == "true" ]] && echo SKIPPED || echo ENABLED)"
echo "Index:      $([[ "${SKIP_INDEX}" == "true" ]] && echo SKIPPED || echo ENABLED)"
echo ""

if [[ -n "${IMPORT_DIR}" && "${SKIP_PERMS}" != "true" ]]; then
  echo "Normalizing filesystem permissions..."
  run sudo chown -R "${OWNER_USER}:${GROUP_NAME}" "${IMPORT_DIR}"
  run sudo find "${IMPORT_DIR}" -type d -exec chmod 2770 {} +
  run sudo find "${IMPORT_DIR}" -type f \( -name '*.warc.gz' -o -name '*.warc' \) -exec chmod 640 {} +
  echo ""
fi

job_id="${JOB_ID}"

if [[ -z "${job_id}" && "${SKIP_REGISTER}" != "true" ]]; then
  name_arg=()
  if [[ -n "${JOB_NAME}" ]]; then
    name_arg=(--name "${JOB_NAME}")
  fi

  cmd=(
    sudo systemd-run --wait --pipe
    --property=User=haadmin
    --property="Group=${GROUP_NAME}"
    --property="EnvironmentFile=${ENV_FILE}"
    "${VENV_BIN}/ha-backend" register-job-dir
    --source "${SOURCE_CODE}"
    --output-dir "${IMPORT_DIR}"
    "${name_arg[@]}"
  )

  if [[ "${APPLY}" != "true" ]]; then
    echo "+ ${cmd[*]}"
  else
    echo "Registering job dir in DB..."
    out="$("${cmd[@]}")"
    echo "${out}"
    job_id="$(echo "${out}" | sed -n 's/^  ID:[[:space:]]*//p' | head -n 1 | tr -d '\r')"
    if [[ -z "${job_id}" ]]; then
      echo "ERROR: Failed to parse job ID from register-job-dir output." >&2
      exit 1
    fi
    echo "Registered job ID: ${job_id}"
    echo ""
  fi
fi

if [[ "${SKIP_INDEX}" != "true" ]]; then
  if [[ "${APPLY}" != "true" && -z "${job_id}" ]]; then
    echo "+ sudo systemd-run --wait --pipe --property=User=haadmin --property=Group=${GROUP_NAME} --property=EnvironmentFile=${ENV_FILE} ${VENV_BIN}/ha-backend index-job --id <JOB_ID>"
  else
    if [[ -z "${job_id}" ]]; then
      echo "ERROR: No job ID available to index. Provide --job-id or allow register step." >&2
      exit 2
    fi
    run sudo systemd-run --wait --pipe \
      --property=User=haadmin \
      --property="Group=${GROUP_NAME}" \
      --property="EnvironmentFile=${ENV_FILE}" \
      "${VENV_BIN}/ha-backend" index-job --id "${job_id}"
  fi
fi

echo ""
echo "Done."
