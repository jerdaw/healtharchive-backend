#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: alert pipeline drill (Prometheus -> Alertmanager).

This writes a temporary node_exporter textfile metric:
  healtharchive_alert_pipeline_drill 1

That metric triggers the Prometheus alert:
  HealthArchiveAlertPipelineDrill (severity=drill)

By default, Alertmanager should route severity=drill to a null receiver
to avoid paging operators.

Safe-by-default: dry-run unless you pass --apply.

Usage:
  cd /opt/healtharchive-backend

  # Dry-run:
  ./scripts/vps-alert-pipeline-drill.sh

  # Apply for ~10 minutes (requires sudo):
  sudo ./scripts/vps-alert-pipeline-drill.sh --apply --duration-seconds 600

Options:
  --apply                 Actually write/remove the metric file (default: dry-run)
  --out-dir DIR           node_exporter textfile collector dir (default: /var/lib/node_exporter/textfile_collector)
  --out-file FILE         Output filename (default: healtharchive_alert_pipeline_drill.prom)
  --duration-seconds N    How long to keep the metric file before removing it (default: 600; use 0 to keep)

Verify (on the VPS):
  curl -s http://127.0.0.1:9090/api/v1/alerts | head
  curl -s http://127.0.0.1:9093/api/v2/alerts | head

Cleanup (if needed):
  sudo rm -f /var/lib/node_exporter/textfile_collector/healtharchive_alert_pipeline_drill.prom
EOF
}

APPLY="false"
OUT_DIR="/var/lib/node_exporter/textfile_collector"
OUT_FILE="healtharchive_alert_pipeline_drill.prom"
# Default: 10 minutes. This intentionally exceeds conservative Prometheus scrape intervals
# so the metric is very likely to be scraped at least once.
DURATION_SECONDS="600"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY="true"
      shift 1
      ;;
    --out-dir)
      OUT_DIR="$2"
      shift 2
      ;;
    --out-file)
      OUT_FILE="$2"
      shift 2
      ;;
    --duration-seconds)
      DURATION_SECONDS="$2"
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

run() {
  if [[ "${APPLY}" != "true" ]]; then
    echo "+ $*"
    return 0
  fi
  "$@"
}

if [[ "${APPLY}" == "true" && "${EUID}" -ne 0 ]]; then
  echo "ERROR: --apply requires root (use sudo)." >&2
  exit 1
fi

if ! [[ "${DURATION_SECONDS}" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --duration-seconds must be a non-negative integer." >&2
  exit 2
fi

out_path="${OUT_DIR%/}/${OUT_FILE}"

write_metric() {
  local tmp
  tmp="$(mktemp "${out_path}.tmp.XXXXXX")"
  now_epoch="$(date +%s)"
  cat >"${tmp}" <<EOF
# HELP healtharchive_alert_pipeline_drill 1 when the drill is active (operator-triggered).
# TYPE healtharchive_alert_pipeline_drill gauge
healtharchive_alert_pipeline_drill 1
# HELP healtharchive_alert_pipeline_drill_last_run_timestamp_seconds Unix timestamp of the last drill write.
# TYPE healtharchive_alert_pipeline_drill_last_run_timestamp_seconds gauge
healtharchive_alert_pipeline_drill_last_run_timestamp_seconds ${now_epoch}
EOF
  chmod 0644 "${tmp}"
  mv "${tmp}" "${out_path}"
}

remove_metric() {
  rm -f "${out_path}"
}

run install -d -m 0755 -o root -g root "${OUT_DIR}"
if [[ "${APPLY}" != "true" ]]; then
  echo "+ (write drill metric to ${out_path})"
else
  write_metric
  echo "OK: drill metric written: ${out_path}"
fi

if [[ "${DURATION_SECONDS}" == "0" ]]; then
  echo "NOTE: duration=0; leaving drill metric in place until you remove it."
  exit 0
fi

if [[ "${APPLY}" != "true" ]]; then
  echo "+ sleep ${DURATION_SECONDS}"
  echo "+ rm -f ${out_path}"
  exit 0
fi

cleanup() {
  remove_metric
}
trap cleanup EXIT INT TERM

echo "Waiting ${DURATION_SECONDS}s before removing drill metric..."
sleep "${DURATION_SECONDS}"
echo "OK: removing drill metric."
exit 0
