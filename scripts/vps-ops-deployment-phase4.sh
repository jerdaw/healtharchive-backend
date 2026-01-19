#!/usr/bin/env bash
set -euo pipefail

# HealthArchive Phase 4: Post-Deployment Verification
# Usage: ./scripts/vps-ops-deployment-phase4.sh

LOGFILE="/tmp/ha-phase4-verify-$(date +%s).log"
echo "Starting Phase 4 Verification at $(date)" | tee -a "$LOGFILE"
echo "Log file: $LOGFILE"

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

echo "1. Triggering Metrics Collection..."
# Ensure we have fresh data
if sudo systemctl start healtharchive-crawl-metrics-textfile.service; then
    echo "  [OK] Metrics collection triggered" | tee -a "$LOGFILE"
else
    echo "  [FAIL] Failed to trigger metrics collection" | tee -a "$LOGFILE"
fi

echo "2. Verifying Metrics File Content..."
METRICS_FILE="/var/lib/node_exporter/textfile_collector/healtharchive_crawl.prom"
if [[ -f "$METRICS_FILE" ]]; then
    # Check for new state-file metrics
    if grep -q "healtharchive_job_state_file_probe_ok" "$METRICS_FILE"; then
        echo "  [OK] New state-file metrics found (healtharchive_job_state_file_probe_ok)" | tee -a "$LOGFILE"
    else
        echo "  [FAIL] New state-file metrics MISSING in $METRICS_FILE" | tee -a "$LOGFILE"
    fi

    # Check for job 6 specific metrics
    if grep -q 'job_id="6"' "$METRICS_FILE"; then
        echo "  [OK] Job 6 metrics present" | tee -a "$LOGFILE"
    else
        echo "  [WARN] Job 6 metrics missing (might need more time if just restarted)" | tee -a "$LOGFILE"
    fi
else
    echo "  [FAIL] Metrics file $METRICS_FILE does not exist" | tee -a "$LOGFILE"
fi

echo "3. Verifying Job 6 Progress..."
if [[ -x "./scripts/vps-crawl-status.sh" ]]; then
    ./scripts/vps-crawl-status.sh --year 2026 --job-id 6 --recent-lines 200 >> "$LOGFILE" 2>&1
    echo "  [OK] Status script ran (check log for details)" | tee -a "$LOGFILE"
fi

echo "4. Verifying WARC Accumulation (Hot Path)..."
# Check for very recent WARCs (last 10 mins) to confirm active crawling
RECENT_WARCS=$(find /srv/healtharchive/jobs/hc -name "*.warc.gz" -type f -mmin -10 | wc -l)
echo "Recent WARCs (last 10m): $RECENT_WARCS" | tee -a "$LOGFILE"

if [[ "$RECENT_WARCS" -gt 0 ]]; then
    echo "  [OK] WARCs are accumulating" | tee -a "$LOGFILE"
else
    echo "  [WARN] No WARCs written in last 10m (monitor closely)" | tee -a "$LOGFILE"
fi

echo "" | tee -a "$LOGFILE"
echo "Phase 4 Verification Complete. Review $LOGFILE" | tee -a "$LOGFILE"
