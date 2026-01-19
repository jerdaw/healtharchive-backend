#!/usr/bin/env bash
set -euo pipefail

# HealthArchive Phase 2: Code Deployment Automation
# Usage: ./scripts/vps-ops-deployment-phase2.sh [--apply]

APPLY="false"
if [[ "${1:-}" == "--apply" ]]; then
  APPLY="true"
fi

echo "Start Phase 2: Code Deployment"
echo "------------------------------"

LOGFILE="/tmp/ha-phase2-deploy-$(date +%s).log"
echo "Starting Phase 2 Deployment at $(date)" | tee -a "$LOGFILE"
echo "Log file: $LOGFILE"

# Ensure we are in the repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

echo "Repo Dir: ${REPO_DIR}"

# 1. Fetch & Check
echo "Fetching origin..."
git fetch origin

local_sha=$(git rev-parse HEAD)
remote_sha=$(git rev-parse origin/main)

echo "Current HEAD: ${local_sha}"
echo "Origin HEAD:  ${remote_sha}"

if [[ "$local_sha" == "$remote_sha" ]]; then
  echo "Already up to date."
else
  echo "Pending changes:"
  git log HEAD..origin/main --oneline --color=always
fi

if [[ "$APPLY" != "true" ]]; then
  echo ""
  echo ">>> DRY RUN COMPLETE. Run with --apply to execute deployment."
  exit 0
fi

echo ""
echo ">>> APPLYING DEPLOYMENT..."

# 2. Pull
echo "Pulling changes..."
git pull origin main

# 3. Verify Integrity
new_sha=$(git rev-parse HEAD)
if [[ "$new_sha" == "$remote_sha" ]]; then
  echo "  [OK] HEAD updated to $new_sha"
else
  echo "  [FAIL] HEAD mismatch ($new_sha vs $remote_sha)"
  exit 1
fi

# 4. Update Dependencies
if [[ -f ".venv/bin/pip" ]]; then
    echo "Updating python dependencies..."
    # Match production requirements from vps-deploy.sh
    if .venv/bin/pip install -q -e ".[dev]" "psycopg[binary]" 2> /tmp/pip_deploy_err.log; then
        echo "  [OK] Dependencies synced (including psycopg[binary])"
    else
        echo "  [FAIL] Dependency install failed. See /tmp/pip_deploy_err.log"
        cat /tmp/pip_deploy_err.log
        exit 1
    fi
    # 4b. Sanity check: can we import the code?
    echo "Verifying python environment sanity..."
    if .venv/bin/python3 -c "from ha_backend import job_registry; print('Import OK')" >> "$LOGFILE" 2>&1; then
        echo "  [OK] ha_backend import verified"
    else
        echo "  [FAIL] ha_backend import FAILED. Check .venv and installation."
        exit 1
    fi
else
    echo "  [WARN] .venv/bin/pip not found, skipping dep update"
fi

# 5. Install Systemd Units (including new metrics timer)
echo "Installing/Updating systemd units..."
if [[ -x "scripts/vps-install-systemd-units.sh" ]]; then
    # This requires sudo
    sudo ./scripts/vps-install-systemd-units.sh --apply --no-daemon-reload
    echo "  [OK] Systemd units installed"
else
    echo "  [FAIL] scripts/vps-install-systemd-units.sh not found/executable"
    exit 1
fi

echo ""
echo "Phase 2 Complete. Code deployed & Unit files updated."
echo "Ready for Phase 3: Service Restart."
