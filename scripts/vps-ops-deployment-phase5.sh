#!/usr/bin/env bash
set -euo pipefail

# HealthArchive Phase 5: Indexing Investigation
# Usage: ./scripts/vps-ops-deployment-phase5.sh

LOGFILE="/tmp/ha-phase5-investigate-$(date +%s).log"
echo "Starting Phase 5 Investigation at $(date)" | tee -a "$LOGFILE"
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
  fi
}

echo "1. Searching for Indexing Trigger Logic in Code..."
# Look for where 'indexed_pages' is updated
grep -r "indexed_pages" src/ --include="*.py" | grep -v "test" | head -20 >> "$LOGFILE" 2>&1
echo "   (Check log for 'indexed_pages' references)" | tee -a "$LOGFILE"

echo "2. Searching for Copy/Move Logic (WARC discovery)..."
grep -r "shutil.move" src/ --include="*.py" | head -20 >> "$LOGFILE" 2>&1
grep -r "\.warc\.gz" src/ --include="*.py" | head -20 >> "$LOGFILE" 2>&1
echo "   (Check log for file movement logic)" | tee -a "$LOGFILE"

echo "3. Identifying Indexing Service/Units..."
if sudo systemctl list-unit-files | grep -i "index"; then
    echo "  [INFO] Found indexing-related systemd units" | tee -a "$LOGFILE"
    sudo systemctl list-unit-files | grep -i "index" >> "$LOGFILE" 2>&1
else
    echo "  [INFO] No explicit 'index' service found in systemd" | tee -a "$LOGFILE"
fi

echo "4. Checking Worker Logs for Indexing keywords..."
echo "Last 20 matches for 'index' in worker logs:" >> "$LOGFILE"
sudo journalctl -u healtharchive-worker.service --since "1 day ago" | grep -i "index" | tail -20 >> "$LOGFILE" 2>&1

echo "5. Checking Environment Variables for Indexing Config..."
if grep -i "index" /etc/healtharchive/backend.env; then
    echo "  [INFO] Indexing config found in backend.env" | tee -a "$LOGFILE"
    grep -i "index" /etc/healtharchive/backend.env >> "$LOGFILE" 2>&1
else
    echo "  [INFO] No 'index' text in backend.env" | tee -a "$LOGFILE"
fi

echo "6. Checking Job Completion Hooks in jobs.py..."
# Quick grep to see if we can find on_complete
grep -n "def on_complete" src/ha_backend/jobs.py >> "$LOGFILE" 2>&1 || echo "No on_complete method found" >> "$LOGFILE"

echo "" | tee -a "$LOGFILE"
echo "Phase 5 Investigation Complete. Review $LOGFILE for clues." | tee -a "$LOGFILE"
