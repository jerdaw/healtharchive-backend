#!/usr/bin/env bash
set -euo pipefail

# Helper for migrating job lock files out of /tmp safely.
#
# Default mode is read-only: prints the current state and the exact commands to
# run during a maintenance window.
#
# If you pass --apply-env, it will back up and update the env file, but it will
# still NOT restart services (you do that explicitly once crawls are idle).

ENV_FILE="/etc/healtharchive/backend.env"
NEW_LOCK_DIR="/srv/healtharchive/ops/locks/jobs"
OLD_LOCK_DIR="/tmp/healtharchive-job-locks"
APPLY_ENV=0

usage() {
  cat <<'EOF'
HealthArchive VPS helper: job lock-dir cutover (safe by default)

Usage:
  ./scripts/vps-job-lock-dir-cutover.sh [--env-file FILE] [--new-lock-dir DIR] [--apply-env]

Defaults:
  --env-file      /etc/healtharchive/backend.env
  --new-lock-dir  /srv/healtharchive/ops/locks/jobs

Notes:
  - Read-only unless --apply-env is passed.
  - Even with --apply-env, this script does NOT restart services.
  - Perform the restart only during a safe window (no running crawls).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --new-lock-dir) NEW_LOCK_DIR="${2:-}"; shift 2 ;;
    --apply-env) APPLY_ENV=1; shift ;;
    *)
      echo "ERROR: Unknown arg: $1" >&2
      echo "Run with --help for usage." >&2
      exit 2
      ;;
  esac
done

echo "HealthArchive – Job Lock Directory Cutover"
echo "-----------------------------------------"
echo "Env file:       ${ENV_FILE}"
echo "Old lock dir:   ${OLD_LOCK_DIR}"
echo "New lock dir:   ${NEW_LOCK_DIR}"
echo "Mode:           $([[ "${APPLY_ENV}" -eq 1 ]] && echo APPLY-ENV || echo READ-ONLY)"
echo ""

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: env file not found: ${ENV_FILE}" >&2
  exit 1
fi

current_env_val="$(rg -n '^export HEALTHARCHIVE_JOB_LOCK_DIR=' "${ENV_FILE}" 2>/dev/null | tail -n 1 || true)"
if [[ -n "${current_env_val}" ]]; then
  echo "Current env setting:"
  echo "  ${current_env_val}"
else
  echo "Current env setting:"
  echo "  (not set)"
fi
echo ""

echo "Filesystem state:"
if [[ -d "${OLD_LOCK_DIR}" ]]; then
  echo "  old_dir_exists=1"
  echo "  old_lock_files=$(find "${OLD_LOCK_DIR}" -maxdepth 1 -type f -name 'job-*.lock' 2>/dev/null | wc -l | tr -d ' ')"
else
  echo "  old_dir_exists=0"
  echo "  old_lock_files=0"
fi
if [[ -d "${NEW_LOCK_DIR}" ]]; then
  echo "  new_dir_exists=1"
  echo "  new_lock_files=$(find "${NEW_LOCK_DIR}" -maxdepth 1 -type f -name 'job-*.lock' 2>/dev/null | wc -l | tr -d ' ')"
else
  echo "  new_dir_exists=0"
  echo "  new_lock_files=0"
fi
echo ""

echo "Recommended maintenance-window plan (copy/paste):"
cat <<EOF

# 0) Preflight: confirm no deploy is running
pgrep -af 'vps-deploy\\.sh' || true

# 1) Preflight: confirm the worker is idle (no running jobs)
set -a; source "${ENV_FILE}"; set +a
/opt/healtharchive-backend/.venv/bin/ha-backend list-jobs --status running --limit 5

# 2) Ensure ops dirs exist (creates ${NEW_LOCK_DIR} with expected perms)
cd /opt/healtharchive-backend
sudo ./scripts/vps-bootstrap-ops-dirs.sh

# 3) Backup env file
sudo cp -av "${ENV_FILE}" "${ENV_FILE}.bak.\$(date -u +%Y%m%dT%H%M%SZ)"

# 4) Set lock dir env var (idempotent: replace if present, append if missing)
sudo rg -n '^export HEALTHARCHIVE_JOB_LOCK_DIR=' "${ENV_FILE}" >/dev/null \\
  && sudo sed -i 's|^export HEALTHARCHIVE_JOB_LOCK_DIR=.*$|export HEALTHARCHIVE_JOB_LOCK_DIR=${NEW_LOCK_DIR}|g' "${ENV_FILE}" \\
  || echo 'export HEALTHARCHIVE_JOB_LOCK_DIR=${NEW_LOCK_DIR}' | sudo tee -a "${ENV_FILE}" >/dev/null

# 5) Restart services that read backend.env (safe window only)
sudo systemctl restart healtharchive-worker.service
sudo systemctl restart healtharchive-api.service

# 6) Post-check: ensure services are healthy
sudo systemctl is-active healtharchive-worker.service healtharchive-api.service
curl -fsS http://127.0.0.1:8001/api/health >/dev/null && echo OK

# 7) Optional: confirm new lock dir is being used (should create locks as jobs run)
ls -la "${NEW_LOCK_DIR}" | head
EOF
echo ""

echo "Rollback plan (copy/paste):"
cat <<EOF

# Restore the prior env file and restart services (safe window only)
sudo ls -1 "${ENV_FILE}.bak."* | tail -n 1
sudo cp -av "\$(sudo ls -1 "${ENV_FILE}.bak."* | tail -n 1)" "${ENV_FILE}"
sudo systemctl restart healtharchive-worker.service
sudo systemctl restart healtharchive-api.service
EOF
echo ""

if [[ "${APPLY_ENV}" -ne 1 ]]; then
  exit 0
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: --apply-env requires root (re-run with sudo)." >&2
  exit 1
fi

if ! getent group healtharchive >/dev/null 2>&1; then
  echo "ERROR: group 'healtharchive' does not exist; cannot create ${NEW_LOCK_DIR} with correct perms." >&2
  echo "Hint: create the group and add the operator user, or run: sudo ./scripts/vps-bootstrap-ops-dirs.sh" >&2
  exit 1
fi

# Make the lock dir exist even if the operator hasn't run vps-bootstrap-ops-dirs.sh yet.
if [[ ! -d "${NEW_LOCK_DIR}" ]]; then
  install -d -m 2770 -o root -g healtharchive "$(dirname "${NEW_LOCK_DIR}")"
  install -d -m 2770 -o root -g healtharchive "${NEW_LOCK_DIR}"
fi

ts="$(date -u +%Y%m%dT%H%M%SZ)"
backup="${ENV_FILE}.bak.${ts}"
cp -av "${ENV_FILE}" "${backup}" >/dev/null

if rg -n '^export HEALTHARCHIVE_JOB_LOCK_DIR=' "${ENV_FILE}" >/dev/null 2>&1; then
  sed -i "s|^export HEALTHARCHIVE_JOB_LOCK_DIR=.*$|export HEALTHARCHIVE_JOB_LOCK_DIR=${NEW_LOCK_DIR}|g" "${ENV_FILE}"
else
  echo "export HEALTHARCHIVE_JOB_LOCK_DIR=${NEW_LOCK_DIR}" >>"${ENV_FILE}"
fi

echo "OK: updated env file. Backup: ${backup}"
echo "Next: restart services during a safe window (see printed commands above)."
