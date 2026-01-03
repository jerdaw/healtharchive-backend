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
  --require-coverage-guardrails  Fail if coverage guardrails timer isn't enabled
  --require-replay-smoke         Fail if replay smoke timer isn't enabled
  --require-cleanup-automation   Fail if cleanup automation timer isn't enabled
  --require-public-verify        Fail if public surface verify timer isn't enabled
  --allow-missing-worker-override Do not fail if worker override isn't present
  --json                        Emit a single JSON summary to stdout (human logs go to stderr)

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
REQUIRE_COVERAGE_GUARDRAILS="false"
REQUIRE_REPLAY_SMOKE="false"
REQUIRE_CLEANUP_AUTOMATION="false"
REQUIRE_PUBLIC_VERIFY="false"
ALLOW_MISSING_WORKER_OVERRIDE="false"
JSON_MODE="false"

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
    --require-coverage-guardrails)
      REQUIRE_COVERAGE_GUARDRAILS="true"
      shift 1
      ;;
    --require-replay-smoke)
      REQUIRE_REPLAY_SMOKE="true"
      shift 1
      ;;
    --require-cleanup-automation)
      REQUIRE_CLEANUP_AUTOMATION="true"
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
    --json)
      JSON_MODE="true"
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

warnings=0

log() {
  if [[ "${JSON_MODE}" == "true" ]]; then
    echo "$*" >&2
  else
    echo "$*"
  fi
}

ok() {
  log "OK   $*"
}

warn() {
  warnings=$((warnings + 1))
  echo "WARN $*" >&2
}

fail() {
  echo "FAIL $*" >&2
}

if ! command -v systemctl >/dev/null 2>&1; then
  if [[ "${JSON_MODE}" == "true" ]]; then
    echo '{"schema_version":1,"skipped":true,"skip_reason":"systemctl not found","failures":0,"warnings":1,"ok":true}'
    exit 0
  else
    warn "systemctl not found; this script is intended to run on the VPS."
    exit 0
  fi
fi

failures=0

timer_results=()
ops_dir_results=()
worker_override_present="false"
worker_nice=""
worker_io_class=""
worker_io_priority=""

json_quote() {
  local s="${1:-}"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  printf '"%s"' "${s}"
}

json_null_or_quote() {
  local s="${1:-}"
  if [[ -z "${s}" ]]; then
    printf 'null'
  else
    json_quote "${s}"
  fi
}

json_null_or_bool() {
  local s="${1:-}"
  if [[ -z "${s}" ]]; then
    printf 'null'
    return 0
  fi
  if [[ "${s}" == "true" || "${s}" == "false" ]]; then
    printf '%s' "${s}"
  else
    printf 'null'
  fi
}

join_by() {
  local delim="$1"
  shift
  local out=""
  local first="true"
  local item
  for item in "$@"; do
    if [[ "${first}" == "true" ]]; then
      out="${item}"
      first="false"
    else
      out="${out}${delim}${item}"
    fi
  done
  printf '%s' "${out}"
}

check_timer() {
  local timer="$1"
  local sentinel="$2"
  local required="$3"
  local unit_present="true"
  local enabled=""
  local active=""
  local meets_required="true"
  local sentinel_present=""
  local entry=""

  if ! systemctl cat "${timer}" >/dev/null 2>&1; then
    unit_present="false"
    if [[ "${required}" == "true" ]]; then
      fail "missing timer unit: ${timer}"
      failures=$((failures + 1))
      meets_required="false"
    else
      warn "missing timer unit: ${timer} (optional)"
    fi
    if [[ "${JSON_MODE}" == "true" ]]; then
      entry="{\"name\":$(json_quote "${timer}"),\"required\":${required},\"unit_present\":false,\"enabled_state\":null,\"active_state\":null,\"sentinel_path\":$(json_null_or_quote "${sentinel}"),\"sentinel_present\":null,\"meets_required\":${meets_required}}"
      timer_results+=("${entry}")
    fi
    return 0
  fi

  enabled="$(systemctl is-enabled "${timer}" 2>/dev/null || true)"
  active="$(systemctl is-active "${timer}" 2>/dev/null || true)"

  ok "timer ${timer} enabled=${enabled:-unknown} active=${active:-unknown}"

  local line
  line="$(systemctl list-timers --all --no-pager --no-legend "${timer}" 2>/dev/null | head -n 1 || true)"
  if [[ -n "${line}" ]]; then
    log "     ${line}"
  fi

  if [[ "${required}" == "true" && "${enabled}" != "enabled" ]]; then
    fail "timer not enabled (required): ${timer} (got: ${enabled:-unknown})"
    meets_required="false"
    if [[ -n "${sentinel}" ]]; then
      echo "     Hint: sudo systemctl enable --now ${timer} && sudo touch ${sentinel}" >&2
    else
      echo "     Hint: sudo systemctl enable --now ${timer}" >&2
    fi
    failures=$((failures + 1))
  fi

  if [[ "${enabled}" == "enabled" && -n "${sentinel}" && ! -e "${sentinel}" ]]; then
    fail "timer enabled but sentinel missing: ${sentinel} (required for ${timer} to run)"
    meets_required="false"
    echo "     Hint: sudo touch ${sentinel}" >&2
    failures=$((failures + 1))
  fi

  if [[ -n "${sentinel}" ]]; then
    if [[ -e "${sentinel}" ]]; then
      sentinel_present="true"
    else
      sentinel_present="false"
    fi
  fi

  if [[ "${JSON_MODE}" == "true" ]]; then
    entry="{\"name\":$(json_quote "${timer}"),\"required\":${required},\"unit_present\":${unit_present},\"enabled_state\":$(json_null_or_quote "${enabled}"),\"active_state\":$(json_null_or_quote "${active}"),\"sentinel_path\":$(json_null_or_quote "${sentinel}"),\"sentinel_present\":$(json_null_or_bool "${sentinel_present}"),\"meets_required\":${meets_required}}"
    timer_results+=("${entry}")
  fi
}

log "HealthArchive ops automation verification"
log "----------------------------------------"

# Phase 11: baseline drift timer is recommended (and expected) for sustainability.
check_timer "healtharchive-baseline-drift-check.timer" "/etc/healtharchive/baseline-drift-enabled" "true"

check_timer "healtharchive-change-tracking.timer" "/etc/healtharchive/change-tracking-enabled" "${REQUIRE_CHANGE_TRACKING}"
check_timer "healtharchive-replay-reconcile.timer" "/etc/healtharchive/replay-automation-enabled" "${REQUIRE_REPLAY_RECONCILE}"
check_timer "healtharchive-schedule-annual.timer" "/etc/healtharchive/automation-enabled" "${REQUIRE_ANNUAL_SCHEDULE}"
check_timer "healtharchive-annual-campaign-sentinel.timer" "/etc/healtharchive/automation-enabled" "${REQUIRE_ANNUAL_SENTINEL}"
check_timer "healtharchive-annual-search-verify.timer" "/etc/healtharchive/automation-enabled" "${REQUIRE_ANNUAL_SEARCH_VERIFY}"
check_timer "healtharchive-coverage-guardrails.timer" "/etc/healtharchive/coverage-guardrails-enabled" "${REQUIRE_COVERAGE_GUARDRAILS}"
check_timer "healtharchive-replay-smoke.timer" "/etc/healtharchive/replay-smoke-enabled" "${REQUIRE_REPLAY_SMOKE}"
check_timer "healtharchive-cleanup-automation.timer" "/etc/healtharchive/cleanup-automation-enabled" "${REQUIRE_CLEANUP_AUTOMATION}"
check_timer "healtharchive-public-surface-verify.timer" "/etc/healtharchive/public-verify-enabled" "${REQUIRE_PUBLIC_VERIFY}"

log ""

# Worker priority override (recommended, low-risk).
override_path="/etc/systemd/system/healtharchive-worker.service.d/override.conf"
if [[ -f "${override_path}" ]]; then
  worker_override_present="true"
  ok "worker priority override present: ${override_path}"
  if [[ "${JSON_MODE}" == "true" ]]; then
    systemctl show healtharchive-worker -p Nice -p IOSchedulingClass -p IOSchedulingPriority 2>/dev/null | sed 's/^/     /' >&2 || true
  else
    systemctl show healtharchive-worker -p Nice -p IOSchedulingClass -p IOSchedulingPriority 2>/dev/null | sed 's/^/     /' || true
  fi
  if [[ "${JSON_MODE}" == "true" ]]; then
    worker_nice="$(systemctl show -p Nice --value healtharchive-worker 2>/dev/null || true)"
    worker_io_class="$(systemctl show -p IOSchedulingClass --value healtharchive-worker 2>/dev/null || true)"
    worker_io_priority="$(systemctl show -p IOSchedulingPriority --value healtharchive-worker 2>/dev/null || true)"
  fi
else
  if [[ "${ALLOW_MISSING_WORKER_OVERRIDE}" == "true" ]]; then
    warn "worker priority override missing (allowed): ${override_path}"
  else
    fail "worker priority override missing: ${override_path}"
    failures=$((failures + 1))
  fi
fi

log ""

# Ops artifact directories (expected by baseline drift, restore tests, search eval, etc).
ops_root="/srv/healtharchive/ops"
if [[ -d "/srv/healtharchive" ]]; then
  for d in baseline restore-tests adoption search-eval; do
    if [[ -d "${ops_root}/${d}" ]]; then
      ok "ops dir present: ${ops_root}/${d}"
      if [[ "${JSON_MODE}" == "true" ]]; then
        ops_dir_results+=("{\"path\":$(json_quote "${ops_root}/${d}"),\"present\":true}")
      fi
    else
      fail "missing ops dir: ${ops_root}/${d}"
      failures=$((failures + 1))
      if [[ "${JSON_MODE}" == "true" ]]; then
        ops_dir_results+=("{\"path\":$(json_quote "${ops_root}/${d}"),\"present\":false}")
      fi
    fi
  done
else
  warn "/srv/healtharchive not present; skipping ops dir checks."
  if [[ "${JSON_MODE}" == "true" ]]; then
    for d in baseline restore-tests adoption search-eval; do
      ops_dir_results+=("{\"path\":$(json_quote "${ops_root}/${d}"),\"present\":null}")
    done
  fi
fi

log ""

if [[ "${JSON_MODE}" == "true" ]]; then
  timer_json="$(join_by ',' "${timer_results[@]}")"
  dirs_json="$(join_by ',' "${ops_dir_results[@]}")"
  worker_required="true"
  if [[ "${ALLOW_MISSING_WORKER_OVERRIDE}" == "true" ]]; then
    worker_required="false"
  fi
  ok_json="false"
  if [[ "${failures}" -eq 0 ]]; then
    ok_json="true"
  fi
  echo -n '{'
  echo -n '"schema_version":1,'
  echo -n '"skipped":false,'
  echo -n '"timers":['"${timer_json}"'],'
  echo -n '"worker_override":{'
  echo -n '"path":'$(json_quote "${override_path}")','
  echo -n '"present":'${worker_override_present}','
  echo -n '"required":'${worker_required}
  if [[ "${worker_override_present}" == "true" ]]; then
    echo -n ',"nice":'$(json_null_or_quote "${worker_nice}")','
    echo -n '"io_class":'$(json_null_or_quote "${worker_io_class}")','
    echo -n '"io_priority":'$(json_null_or_quote "${worker_io_priority}")
  fi
  echo -n '},'
  echo -n '"ops_dirs":['"${dirs_json}"'],'
  echo -n '"failures":'${failures}','
  echo -n '"warnings":'${warnings}','
  echo -n '"ok":'${ok_json}
  echo '}'
fi

if [[ "${failures}" -gt 0 ]]; then
  echo "FAILURES: ${failures}" >&2
  exit 1
fi
log "All checks passed."
