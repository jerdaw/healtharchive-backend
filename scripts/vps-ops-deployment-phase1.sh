#!/usr/bin/env bash
set -euo pipefail

# HealthArchive Phase 1: Pre-Deployment State Capture
# Usage: ./scripts/vps-ops-deployment-phase1.sh

# Ensure we're in the repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

LOGFILE="/tmp/ha-phase1-state-$(date +%s).log"
echo "Starting Phase 1 Capture at $(date)" | tee -a "$LOGFILE"
echo "Log file: $LOGFILE"

check_cmd() {
  echo "" | tee -a "$LOGFILE"
  echo ">>> RUNNING: $1" | tee -a "$LOGFILE"
  if eval "$1" >> "$LOGFILE" 2>&1; then
    echo "    [OK]" | tee -a "$LOGFILE"
  else
    echo "    [FAIL]" | tee -a "$LOGFILE"
  fi
}

# 1.1 Capture Campaign Status
if [[ -f ".venv/bin/activate" ]]; then
    source .venv/bin/activate
    # Source production env for DB access/credentials
    ENV_FILE="/etc/healtharchive/backend.env"
    if [[ -f "${ENV_FILE}" ]]; then
        echo "Sourcing ${ENV_FILE} for ha-backend access..." | tee -a "$LOGFILE"
        set -a; source "${ENV_FILE}"; set +a
    fi
    check_cmd "ha-backend show-campaign 2026"
else
    echo "WARN: .venv not found, skipping ha-backend commands" | tee -a "$LOGFILE"
fi

# 1.2 Git State
check_cmd "git log -1"
check_cmd "git status"
check_cmd "git remote -v"

# 1.3 Service States
check_cmd "sudo systemctl status healtharchive-worker.service --no-pager -l"
check_cmd "sudo systemctl status healtharchive-crawl-auto-recover.timer --no-pager"
check_cmd "sudo systemctl status healtharchive-crawl-metrics-textfile.timer --no-pager"

# 1.3b Storage & Mounts (Critical for SSHFS)
check_cmd "findmnt -T /srv/healtharchive/jobs"
check_cmd "df -h /srv/healtharchive/jobs"

# 1.4 Job 6 Progress & WARCs
if [[ -x "./scripts/vps-crawl-status.sh" ]]; then
    echo "" | tee -a "$LOGFILE"
    echo ">>> RUNNING: vps-crawl-status.sh" | tee -a "$LOGFILE"
    # Run status script which does a lot of checks including WARC listing
    if ./scripts/vps-crawl-status.sh --year 2026 --job-id 6 >> "$LOGFILE" 2>&1; then
         echo "    [OK]" | tee -a "$LOGFILE"
    else
         echo "    [FAIL] (some checks failed, see log)" | tee -a "$LOGFILE"
    fi
fi

echo "" | tee -a "$LOGFILE"
echo ">>> Counting WARCs in hot path (verification)..." | tee -a "$LOGFILE"
# Check both potential locations
find /srv/healtharchive/jobs/hc -name "*.warc.gz" -type f -mmin -60 | wc -l | xargs echo "Recent WARCs (60m) in /srv/healtharchive/jobs/hc:" >> "$LOGFILE" 2>&1
find /srv/healtharchive/jobs/hc -name "*.warc.gz" -type f | wc -l | xargs echo "Total WARCs in /srv/healtharchive/jobs/hc:" >> "$LOGFILE" 2>&1

# 1.5 Baseline Metrics
METRICS_FILE="/var/lib/node_exporter/textfile_collector/healtharchive_crawl.prom"
if [[ -f "$METRICS_FILE" ]]; then
    check_cmd "cat $METRICS_FILE | head -20"
else
    echo "Metrics file not found (expected vs new deploy)" | tee -a "$LOGFILE"
fi

echo "" | tee -a "$LOGFILE"
echo "Phase 1 Capture Complete. Review $LOGFILE" | tee -a "$LOGFILE"
echo "You can view the log with: less $LOGFILE"
