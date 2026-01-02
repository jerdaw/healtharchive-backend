#!/usr/bin/env bash
set -u -o pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: snapshot crawl health (read-only).

This script is intended to run on the production VPS (or a similar host) to
quickly answer: "is the crawl actually making progress, and is the host OK?"

Safe-by-default:
  - No DB writes
  - No service restarts
  - No destructive filesystem operations
  - Does NOT print secrets from /etc/healtharchive/backend.env

Usage (on the VPS):
  cd /opt/healtharchive-backend
  ./scripts/vps-crawl-status.sh --year 2026

Options:
  --year YYYY       Annual campaign year for annual-status (default: current UTC year)
  --job-id ID       Inspect a specific job id (default: first running job)
  --env-file FILE   Env file to source (default: /etc/healtharchive/backend.env)
  --recent-lines N  Only scan last N log lines for timeout warnings (default: 5000)
  -h, --help        Show this help

Exit codes:
  0 = no hard failures detected
  1 = one or more failures detected (still prints best-effort output)
  2 = usage error
EOF
}

YEAR="$(date -u '+%Y')"
JOB_ID=""
ENV_FILE="/etc/healtharchive/backend.env"
RECENT_LINES="5000"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --year)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --year requires a 4-digit year (e.g. --year 2026)" >&2
        usage >&2
        exit 2
      fi
      YEAR="$2"
      shift 2
      ;;
    --job-id)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --job-id requires an integer id (e.g. --job-id 6)" >&2
        usage >&2
        exit 2
      fi
      JOB_ID="$2"
      shift 2
      ;;
    --env-file)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --env-file requires a file path (e.g. --env-file /etc/healtharchive/backend.env)" >&2
        usage >&2
        exit 2
      fi
      ENV_FILE="$2"
      shift 2
      ;;
    --recent-lines)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --recent-lines requires an integer (e.g. --recent-lines 5000)" >&2
        usage >&2
        exit 2
      fi
      RECENT_LINES="$2"
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

if ! [[ "${YEAR}" =~ ^[0-9]{4}$ ]]; then
  echo "ERROR: --year must be a 4-digit year (got: ${YEAR})" >&2
  exit 2
fi
if ! [[ "${RECENT_LINES}" =~ ^[0-9]+$ ]] || [[ "${RECENT_LINES}" -le 0 ]]; then
  echo "ERROR: --recent-lines must be a positive integer (got: ${RECENT_LINES})" >&2
  exit 2
fi

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

ok() {
  printf "OK   %s\n" "$1"
}

warn() {
  printf "WARN %s\n" "$1" >&2
}

failures=0
fail() {
  printf "FAIL %s\n" "$1" >&2
  failures=$((failures + 1))
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

echo "HealthArchive crawl status snapshot"
echo "----------------------------------"
echo "timestamp_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "repo_dir=${REPO_DIR}"
echo "year=${YEAR}"
echo ""

VENV_BIN="${REPO_DIR}/.venv/bin"
HA_BIN="${VENV_BIN}/ha-backend"

if [[ -x "${HA_BIN}" ]]; then
  ok "ha-backend present: ${HA_BIN}"
else
  fail "missing ha-backend at ${HA_BIN} (expected VPS deploy at /opt/healtharchive-backend)"
fi

if [[ -f "${ENV_FILE}" ]]; then
  ok "env file present: ${ENV_FILE}"
  # shellcheck disable=SC1090
  set -a; source "${ENV_FILE}"; set +a
else
  fail "env file not found: ${ENV_FILE}"
fi

echo ""

worker_active="unknown"
if have_cmd systemctl; then
  worker_active="$(systemctl is-active healtharchive-worker.service 2>/dev/null || true)"
  if [[ "${worker_active}" == "active" ]]; then
    ok "worker service active"
  else
    fail "worker service is not active (is-active=${worker_active})"
  fi
else
  warn "systemctl not available; skipping service checks"
fi

echo ""

if [[ -x "${HA_BIN}" ]]; then
  echo "[annual-status]"
  "${HA_BIN}" annual-status --year "${YEAR}" || warn "annual-status failed (DB/env issue?)"
  echo ""

  echo "[running jobs]"
  "${HA_BIN}" list-jobs --status running --limit 10 || warn "list-jobs failed (DB/env issue?)"
  echo ""

  if [[ -z "${JOB_ID}" ]]; then
    JOB_ID="$("${HA_BIN}" list-jobs --status running --limit 10 2>/dev/null | awk 'NR>1 && $1 ~ /^[0-9]+$/ {print $1; exit}')"
  fi

  if [[ -z "${JOB_ID}" ]]; then
    warn "no running job detected"
  else
    echo "[job ${JOB_ID}]"
    "${HA_BIN}" show-job --id "${JOB_ID}" || warn "show-job failed for id=${JOB_ID}"
    echo ""

    JOBDIR="$("${HA_BIN}" show-job --id "${JOB_ID}" 2>/dev/null | awk -F': +' '/^Output dir:/ {print $2}')"
    if [[ -n "${JOBDIR}" && -d "${JOBDIR}" ]]; then
      LOG="$(ls -t "${JOBDIR}"/archive_*.combined.log 2>/dev/null | head -n 1)"
      if [[ -n "${LOG}" && -f "${LOG}" ]]; then
        ok "latest combined log: ${LOG}"
        if have_cmd rg; then
          echo ""
          echo "[crawlStatus tail]"
          rg -n '"context":"crawlStatus"' "${LOG}" | tail -n 3 || true
          echo ""
          echo "[recent timeouts (last ${RECENT_LINES} log lines)]"
          # Keep this "recent" to avoid confusing operators with old matches in large logs.
          recent_timeouts="$(tail -n "${RECENT_LINES}" "${LOG}" | rg -n "Navigation timeout|Page load timed out" | tail -n 10 || true)"
          if [[ -n "${recent_timeouts}" ]]; then
            echo "${recent_timeouts}"
          else
            echo "OK   no recent timeouts found"
          fi
        else
          warn "rg not available; skipping crawlStatus/timeouts grep"
        fi

        echo ""
        echo "[recent warc.gz]"
        if have_cmd python3; then
          python3 - <<PY 2>/dev/null || true
from __future__ import annotations

import heapq
import os
from pathlib import Path

root = Path(${JOBDIR@Q})
items: list[tuple[float, int, str]] = []
for path in root.rglob("*.warc.gz"):
    try:
        st = path.stat()
    except OSError:
        continue
    item = (st.st_mtime, st.st_size, str(path))
    if len(items) < 5:
        heapq.heappush(items, item)
    else:
        heapq.heappushpop(items, item)

for mtime, size, path in sorted(items):
    print(f"{mtime:.0f} {size} {path}")
PY
          echo "Note: mtime is UNIX seconds; newest should be last."
        else
          warn "python3 not available; skipping WARC listing"
        fi
      else
        warn "no combined log found under job output dir: ${JOBDIR}"
      fi
    else
      warn "job output dir not found/readable: ${JOBDIR:-'(missing)'}"
    fi
  fi
fi

echo ""
echo "[crawl metrics]"
if have_cmd curl && have_cmd rg; then
  if curl -fsS http://127.0.0.1:9100/metrics >/dev/null 2>&1; then
    ok "node_exporter reachable on 127.0.0.1:9100"
    curl -s http://127.0.0.1:9100/metrics | rg '^healtharchive_crawl_' || true
  else
    fail "node_exporter not reachable on 127.0.0.1:9100"
  fi
else
  warn "missing curl/rg; skipping node_exporter metrics"
fi

echo ""
echo "[auto-recover watchdog]"
if have_cmd systemctl; then
  watchdog_timer="$(systemctl is-active healtharchive-crawl-auto-recover.timer 2>/dev/null || true)"
  if [[ "${watchdog_timer}" == "active" ]]; then
    ok "healtharchive-crawl-auto-recover.timer active"
  else
    warn "healtharchive-crawl-auto-recover.timer not active (is-active=${watchdog_timer})"
  fi
  if [[ -f /etc/healtharchive/crawl-auto-recover-enabled ]]; then
    ok "sentinel present: /etc/healtharchive/crawl-auto-recover-enabled"
  else
    warn "sentinel missing: /etc/healtharchive/crawl-auto-recover-enabled"
  fi
else
  warn "systemctl not available; skipping watchdog timer check"
fi

STATE_FILE="/srv/healtharchive/ops/watchdog/crawl-auto-recover.json"
if [[ -f "${STATE_FILE}" ]]; then
  ok "state file present: ${STATE_FILE}"
  cat "${STATE_FILE}" || true
else
  warn "state file not present (no recoveries yet?): ${STATE_FILE}"
fi

echo ""
echo "[disk]"
df -h / /srv/healtharchive/jobs /srv/healtharchive/storagebox 2>/dev/null || true

if [[ ${failures} -gt 0 ]]; then
  echo ""
  echo "ERROR: ${failures} failure(s) detected."
  exit 1
fi

echo ""
echo "OK: snapshot complete."
exit 0
