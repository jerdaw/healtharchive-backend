#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Verify HealthArchive ops automation posture on the production VPS.

This checks:
  - Required baseline drift timer is installed/enabled and its sentinel exists.
  - Optional timers (annual scheduler, change tracking, replay reconcile, annual search verify).
  - Worker priority drop-in is present (recommended).
  - Ops directories exist (baseline/artifacts locations).

Usage (on the VPS):
  cd /opt/healtharchive-backend
  ./scripts/verify_ops_automation.sh

Options:
  --require-change-tracking      Fail if change tracking timer isn't enabled
  --require-replay-reconcile     Fail if replay reconcile timer isn't enabled
  --require-annual-schedule      Fail if annual schedule timer isn't enabled
  --require-annual-sentinel      Fail if annual campaign sentinel timer isn't enabled
  --require-annual-search-verify Fail if annual search verify timer isn't enabled
  --require-public-verify        Fail if public surface verify timer isn't enabled
  --allow-missing-worker-override Do not fail if worker override isn't present

Notes:
  - This script is best-effort and read-only.
  - For deep drift validation, run: ./scripts/check_baseline_drift.py --mode live
EOF
}

REQUIRE_CHANGE_TRACKING="false"
REQUIRE_REPLAY_RECONCILE="false"
REQUIRE_ANNUAL_SCHEDULE="false"
REQUIRE_ANNUAL_SENTINEL="false"
REQUIRE_ANNUAL_SEARCH_VERIFY="false"
REQUIRE_PUBLIC_VERIFY="false"
ALLOW_MISSING_WORKER_OVERRIDE="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --require-change-tracking)
      REQUIRE_CHANGE_TRACKING="true"
      shift 1
      ;;
    --require-replay-reconcile)
      REQUIRE_REPLAY_RECONCILE="true"
      shift 1
      ;;
    --require-annual-schedule)
      REQUIRE_ANNUAL_SCHEDULE="true"
      shift 1
      ;;
    --require-annual-sentinel)
      REQUIRE_ANNUAL_SENTINEL="true"
      shift 1
      ;;
    --require-annual-search-verify)
      REQUIRE_ANNUAL_SEARCH_VERIFY="true"
      shift 1
      ;;
    --require-public-verify)
      REQUIRE_PUBLIC_VERIFY="true"
      shift 1
      ;;
    --allow-missing-worker-override)
      ALLOW_MISSING_WORKER_OVERRIDE="true"
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

ok() {
  echo "OK   $*"
}

warn() {
  echo "WARN $*" >&2
}

fail() {
  echo "FAIL $*" >&2
}

if ! command -v systemctl >/dev/null 2>&1; then
  warn "systemctl not found; this script is intended to run on the VPS."
  exit 0
fi

failures=0

check_timer() {
  local timer="$1"
  local sentinel="$2"
  local required="$3"

  if ! systemctl cat "${timer}" >/dev/null 2>&1; then
    if [[ "${required}" == "true" ]]; then
      fail "missing timer unit: ${timer}"
      failures=$((failures + 1))
    else
      warn "missing timer unit: ${timer} (optional)"
    fi
    return 0
  fi

  local enabled
  enabled="$(systemctl is-enabled "${timer}" 2>/dev/null || true)"
  local active
  active="$(systemctl is-active "${timer}" 2>/dev/null || true)"

  ok "timer ${timer} enabled=${enabled:-unknown} active=${active:-unknown}"

  local line
  line="$(systemctl list-timers --all --no-pager --no-legend "${timer}" 2>/dev/null | head -n 1 || true)"
  if [[ -n "${line}" ]]; then
    echo "     ${line}"
  fi

  if [[ "${required}" == "true" && "${enabled}" != "enabled" ]]; then
    fail "timer not enabled (required): ${timer} (got: ${enabled:-unknown})"
    if [[ -n "${sentinel}" ]]; then
      echo "     Hint: sudo systemctl enable --now ${timer} && sudo touch ${sentinel}" >&2
    else
      echo "     Hint: sudo systemctl enable --now ${timer}" >&2
    fi
    failures=$((failures + 1))
  fi

  if [[ "${enabled}" == "enabled" && -n "${sentinel}" && ! -e "${sentinel}" ]]; then
    fail "timer enabled but sentinel missing: ${sentinel} (required for ${timer} to run)"
    echo "     Hint: sudo touch ${sentinel}" >&2
    failures=$((failures + 1))
  fi
}

echo "HealthArchive ops automation verification"
echo "----------------------------------------"

# Phase 11: baseline drift timer is recommended (and expected) for sustainability.
check_timer "healtharchive-baseline-drift-check.timer" "/etc/healtharchive/baseline-drift-enabled" "true"

check_timer "healtharchive-change-tracking.timer" "/etc/healtharchive/change-tracking-enabled" "${REQUIRE_CHANGE_TRACKING}"
check_timer "healtharchive-replay-reconcile.timer" "/etc/healtharchive/replay-automation-enabled" "${REQUIRE_REPLAY_RECONCILE}"
check_timer "healtharchive-schedule-annual.timer" "/etc/healtharchive/automation-enabled" "${REQUIRE_ANNUAL_SCHEDULE}"
check_timer "healtharchive-annual-campaign-sentinel.timer" "/etc/healtharchive/automation-enabled" "${REQUIRE_ANNUAL_SENTINEL}"
check_timer "healtharchive-annual-search-verify.timer" "/etc/healtharchive/automation-enabled" "${REQUIRE_ANNUAL_SEARCH_VERIFY}"
check_timer "healtharchive-public-surface-verify.timer" "/etc/healtharchive/public-verify-enabled" "${REQUIRE_PUBLIC_VERIFY}"

echo ""

# Worker priority override (recommended, low-risk).
override_path="/etc/systemd/system/healtharchive-worker.service.d/override.conf"
if [[ -f "${override_path}" ]]; then
  ok "worker priority override present: ${override_path}"
  systemctl show healtharchive-worker -p Nice -p IOSchedulingClass -p IOSchedulingPriority 2>/dev/null | sed 's/^/     /' || true
else
  if [[ "${ALLOW_MISSING_WORKER_OVERRIDE}" == "true" ]]; then
    warn "worker priority override missing (allowed): ${override_path}"
  else
    fail "worker priority override missing: ${override_path}"
    failures=$((failures + 1))
  fi
fi

echo ""

# Ops artifact directories (expected by baseline drift, restore tests, search eval, etc).
ops_root="/srv/healtharchive/ops"
if [[ -d "/srv/healtharchive" ]]; then
  for d in baseline restore-tests adoption search-eval; do
    if [[ -d "${ops_root}/${d}" ]]; then
      ok "ops dir present: ${ops_root}/${d}"
    else
      fail "missing ops dir: ${ops_root}/${d}"
      failures=$((failures + 1))
    fi
  done
else
  warn "/srv/healtharchive not present; skipping ops dir checks."
fi

echo ""
if [[ "${failures}" -gt 0 ]]; then
  echo "FAILURES: ${failures}" >&2
  exit 1
fi
echo "All checks passed."
