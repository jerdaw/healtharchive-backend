#!/usr/bin/env bash
set -euo pipefail

# HealthArchive Phase 3: Service Restart & Recovery
# Usage: ./scripts/vps-ops-deployment-phase3.sh [--apply]

APPLY="false"
if [[ "${1:-}" == "--apply" ]]; then
  APPLY="true"
fi

LOGFILE="/tmp/ha-phase3-restart-$(date +%s).log"
echo "Starting Phase 3 Service Restart at $(date)" | tee -a "$LOGFILE"
echo "Log file: $LOGFILE"

# Ensure we are in the repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

check_cmd() {
  echo "" | tee -a "$LOGFILE"
  echo ">>> RUNNING: $1" | tee -a "$LOGFILE"
  if eval "$1" >> "$LOGFILE" 2>&1; then
    echo "    [OK]" | tee -a "$LOGFILE"
  else
    echo "    [FAIL]" | tee -a "$LOGFILE"
    return 1
  fi
}

echo "Pre-restart state baseline..."
check_cmd "sudo systemctl status healtharchive-worker.service --no-pager"

if [[ "$APPLY" != "true" ]]; then
  echo ""
  echo ">>> DRY RUN COMPLETE. Run with --apply to execute service restarts."
  echo ">>> WARNING: This will restart the running Job 6 container."
  exit 0
fi

echo ""
echo ">>> APPLYING SERVICE RESTARTS..."

# 1. Restart Worker
echo "Restarting healtharchive-worker..."
if sudo systemctl restart healtharchive-worker.service; then
    echo "  [OK] Worker restarted"
else
    echo "  [FAIL] Worker restart failed"
    exit 1
fi

# 2. Restart Timers
echo "Restarting healtharchive-crawl-auto-recover.timer..."
sudo systemctl restart healtharchive-crawl-auto-recover.timer

echo "Enabling and starting healtharchive-crawl-metrics.timer (NEW)..."
sudo systemctl enable --now healtharchive-crawl-metrics.timer

# 3. Verify Startup & Pickup (Wait for poll interval)
echo "Waiting 35s for worker poll and job pickup..."
sleep 35

echo "Checking worker logs for job pickup..."
if sudo journalctl -u healtharchive-worker.service --since "1 minute ago" | grep -E "Picked up job 6|Starting crawl|Resuming crawl" >> "$LOGFILE" 2>&1; then
    echo "  [OK] Job 6 picked up by worker"
else
    echo "  [WARN] Job 6 pickup not found in last 1m logs. Check manually: sudo journalctl -u healtharchive-worker.service -f"
fi

# 4. Check Container
echo "Checking for running zimit containers..."
if docker ps --filter "ancestor=ghcr.io/openzim/zimit" --format "table {{.ID}}\t{{.Status}}\t{{.Names}}" | grep -v "CONTAINER ID" >> "$LOGFILE" 2>&1; then
    echo "  [OK] Zimit container is running"
else
    echo "  [FAIL] No running zimit container found"
    exit 1
fi

# 5. Verify Metrics Timer
echo "Verifying timers..."
if sudo systemctl is-active healtharchive-crawl-metrics.timer >/dev/null 2>&1; then
    echo "  [OK] healtharchive-crawl-metrics.timer is active"
else
    echo "  [FAIL] healtharchive-crawl-metrics.timer is NOT active"
    exit 1
fi

echo ""
echo "Phase 3 Complete. Services restarted and job 6 resumed."
echo "Ready for Phase 4: Post-Deployment Verification (Monitoring)."
