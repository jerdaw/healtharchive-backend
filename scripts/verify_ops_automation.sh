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
  --require-all-present          Fail if any expected timer unit is missing
  --require-all-enabled          Fail if any expected timer is not enabled (implies --require-all-present)
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
  --quiet                       Suppress human-readable output (useful for cron artifacts)
  --json                        Emit a single JSON summary to stdout (human logs go to stderr)
  --json-only                   Emit only JSON to stdout (implies --json --quiet)

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
REQUIRE_ALL_PRESENT="false"
REQUIRE_ALL_ENABLED="false"
JSON_MODE="false"
QUIET_MODE="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --require-all-present)
      REQUIRE_ALL_PRESENT="true"
      shift 1
      ;;
    --require-all-enabled)
      REQUIRE_ALL_ENABLED="true"
      shift 1
      ;;
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
    --quiet)
      QUIET_MODE="true"
      shift 1
      ;;
    --json)
      JSON_MODE="true"
      shift 1
      ;;
    --json-only)
      JSON_MODE="true"
      QUIET_MODE="true"
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

if [[ "${REQUIRE_ALL_ENABLED}" == "true" ]]; then
  REQUIRE_ALL_PRESENT="true"
fi

failures=0
warnings=0

log() {
  if [[ "${QUIET_MODE}" == "true" ]]; then
    return 0
  fi
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
  if [[ "${QUIET_MODE}" != "true" ]]; then
    echo "WARN $*" >&2
  fi
}

fail() {
  failures=$((failures + 1))
  if [[ "${QUIET_MODE}" != "true" ]]; then
    echo "FAIL $*" >&2
  fi
}

hint() {
  if [[ "${QUIET_MODE}" != "true" ]]; then
    echo "     Hint: $*" >&2
  fi
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

timer_results=()
ops_dir_results=()
missing_required_timers=()
missing_optional_timers=()
disabled_required_timers=()
disabled_optional_timers=()
enabled_timers_missing_sentinel=()
unexpected_timers=()
expected_timer_names=()
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

json_array_of_strings() {
  local out=""
  local first="true"
  local item
  for item in "$@"; do
    if [[ "${first}" == "true" ]]; then
      out="$(json_quote "${item}")"
      first="false"
    else
      out="${out},$(json_quote "${item}")"
    fi
  done
  printf '[%s]' "${out}"
}

in_list() {
  local needle="$1"
  shift
  local item
  for item in "$@"; do
    if [[ "${item}" == "${needle}" ]]; then
      return 0
    fi
  done
  return 1
}

check_timer() {
  local timer="$1"
  local sentinel="$2"
  local required_present="$3"
  local required_enabled="$4"
  local unit_present="true"
  local enabled=""
  local active=""
  local meets_required="true"
  local sentinel_present=""
  local entry=""

  if ! systemctl cat "${timer}" >/dev/null 2>&1; then
    unit_present="false"
    if [[ "${required_present}" == "true" ]]; then
      fail "missing timer unit: ${timer}"
      meets_required="false"
      missing_required_timers+=("${timer}")
    else
      warn "missing timer unit: ${timer} (optional)"
      missing_optional_timers+=("${timer}")
    fi
    if [[ "${JSON_MODE}" == "true" ]]; then
      entry="{\"name\":$(json_quote "${timer}"),\"required\":${required_enabled},\"required_present\":${required_present},\"required_enabled\":${required_enabled},\"unit_present\":false,\"enabled_state\":null,\"active_state\":null,\"sentinel_path\":$(json_null_or_quote "${sentinel}"),\"sentinel_present\":null,\"meets_required\":${meets_required}}"
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

  if [[ "${required_enabled}" == "true" && "${enabled}" != "enabled" ]]; then
    fail "timer not enabled (required): ${timer} (got: ${enabled:-unknown})"
    meets_required="false"
    disabled_required_timers+=("${timer}")
    if [[ -n "${sentinel}" ]]; then
      hint "sudo systemctl enable --now ${timer} && sudo touch ${sentinel}"
    else
      hint "sudo systemctl enable --now ${timer}"
    fi
  elif [[ "${enabled}" != "enabled" ]]; then
    disabled_optional_timers+=("${timer}")
  fi

  if [[ "${enabled}" == "enabled" && -n "${sentinel}" && ! -e "${sentinel}" ]]; then
    fail "timer enabled but sentinel missing: ${sentinel} (required for ${timer} to run)"
    meets_required="false"
    enabled_timers_missing_sentinel+=("${timer}")
    hint "sudo touch ${sentinel}"
  fi

  if [[ -n "${sentinel}" ]]; then
    if [[ -e "${sentinel}" ]]; then
      sentinel_present="true"
    else
      sentinel_present="false"
    fi
  fi

  if [[ "${JSON_MODE}" == "true" ]]; then
    entry="{\"name\":$(json_quote "${timer}"),\"required\":${required_enabled},\"required_present\":${required_present},\"required_enabled\":${required_enabled},\"unit_present\":${unit_present},\"enabled_state\":$(json_null_or_quote "${enabled}"),\"active_state\":$(json_null_or_quote "${active}"),\"sentinel_path\":$(json_null_or_quote "${sentinel}"),\"sentinel_present\":$(json_null_or_bool "${sentinel_present}"),\"meets_required\":${meets_required}}"
    timer_results+=("${entry}")
  fi
}

log "HealthArchive ops automation verification"
log "----------------------------------------"

sentinel_root="/etc/healtharchive"
expected_timer_specs=(
  "healtharchive-baseline-drift-check.timer|baseline-drift-enabled|true|"
  "healtharchive-change-tracking.timer|change-tracking-enabled|false|REQUIRE_CHANGE_TRACKING"
  "healtharchive-replay-reconcile.timer|replay-automation-enabled|false|REQUIRE_REPLAY_RECONCILE"
  "healtharchive-schedule-annual.timer|automation-enabled|false|REQUIRE_ANNUAL_SCHEDULE"
  "healtharchive-annual-campaign-sentinel.timer|automation-enabled|false|REQUIRE_ANNUAL_SENTINEL"
  "healtharchive-annual-search-verify.timer|automation-enabled|false|REQUIRE_ANNUAL_SEARCH_VERIFY"
  "healtharchive-coverage-guardrails.timer|coverage-guardrails-enabled|false|REQUIRE_COVERAGE_GUARDRAILS"
  "healtharchive-replay-smoke.timer|replay-smoke-enabled|false|REQUIRE_REPLAY_SMOKE"
  "healtharchive-cleanup-automation.timer|cleanup-automation-enabled|false|REQUIRE_CLEANUP_AUTOMATION"
  "healtharchive-public-surface-verify.timer|public-verify-enabled|false|REQUIRE_PUBLIC_VERIFY"
)

for spec in "${expected_timer_specs[@]}"; do
  IFS='|' read -r timer sentinel_file default_required flag_var <<<"${spec}"
  expected_timer_names+=("${timer}")

  required_enabled="${default_required}"
  if [[ -n "${flag_var}" && "${!flag_var}" == "true" ]]; then
    required_enabled="true"
  fi
  if [[ "${REQUIRE_ALL_ENABLED}" == "true" ]]; then
    required_enabled="true"
  fi

  required_present="false"
  if [[ "${default_required}" == "true" ]]; then
    required_present="true"
  fi
  if [[ "${REQUIRE_ALL_PRESENT}" == "true" ]]; then
    required_present="true"
  fi
  if [[ "${required_enabled}" == "true" ]]; then
    required_present="true"
  fi

  sentinel_path=""
  if [[ -n "${sentinel_file}" ]]; then
    sentinel_path="${sentinel_root%/}/${sentinel_file}"
  fi

  check_timer "${timer}" "${sentinel_path}" "${required_present}" "${required_enabled}"
done

installed_timer_units=()
while read -r unit _; do
  if [[ "${unit}" == *.timer ]]; then
    installed_timer_units+=("${unit}")
  fi
done < <(systemctl list-unit-files "healtharchive-*.timer" --no-pager --no-legend 2>/dev/null || true)

for unit in "${installed_timer_units[@]}"; do
  if ! in_list "${unit}" "${expected_timer_names[@]}"; then
    unexpected_timers+=("${unit}")
  fi
done

if [[ "${#unexpected_timers[@]}" -gt 0 ]]; then
  warn "unexpected timer units present: ${unexpected_timers[*]}"
fi

log ""

# Worker priority override (recommended, low-risk).
override_path="/etc/systemd/system/healtharchive-worker.service.d/override.conf"
if [[ -f "${override_path}" ]]; then
  worker_override_present="true"
  ok "worker priority override present: ${override_path}"
  if [[ "${QUIET_MODE}" != "true" ]]; then
    if [[ "${JSON_MODE}" == "true" ]]; then
      systemctl show healtharchive-worker -p Nice -p IOSchedulingClass -p IOSchedulingPriority 2>/dev/null | sed 's/^/     /' >&2 || true
    else
      systemctl show healtharchive-worker -p Nice -p IOSchedulingClass -p IOSchedulingPriority 2>/dev/null | sed 's/^/     /' || true
    fi
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
  fi
fi

log ""

# Ops artifact directories (expected by baseline drift, restore tests, search eval, etc).
ops_root="/srv/healtharchive/ops"
expected_ops_specs=(
  "baseline|true"
  "restore-tests|true"
  "adoption|true"
  "automation|false"
  "search-eval|true"
)

if [[ -d "/srv/healtharchive" ]]; then
  for spec in "${expected_ops_specs[@]}"; do
    IFS='|' read -r d required <<<"${spec}"
    dir_path="${ops_root}/${d}"
    if [[ -d "${dir_path}" ]]; then
      ok "ops dir present: ${dir_path}"
      if [[ "${JSON_MODE}" == "true" ]]; then
        ops_dir_results+=("{\"path\":$(json_quote "${dir_path}"),\"required\":${required},\"present\":true}")
      fi
    else
      if [[ "${required}" == "true" ]]; then
        fail "missing ops dir: ${dir_path}"
      else
        warn "missing ops dir: ${dir_path} (optional)"
      fi
      if [[ "${JSON_MODE}" == "true" ]]; then
        ops_dir_results+=("{\"path\":$(json_quote "${dir_path}"),\"required\":${required},\"present\":false}")
      fi
    fi
  done
else
  warn "/srv/healtharchive not present; skipping ops dir checks."
  if [[ "${JSON_MODE}" == "true" ]]; then
    for spec in "${expected_ops_specs[@]}"; do
      IFS='|' read -r d required <<<"${spec}"
      ops_dir_results+=(
        "{\"path\":$(json_quote "${ops_root}/${d}"),\"required\":${required},\"present\":null}"
      )
    done
  fi
fi

log ""

missing_optional_count="${#missing_optional_timers[@]}"
disabled_optional_count="${#disabled_optional_timers[@]}"
unexpected_timers_count="${#unexpected_timers[@]}"

log "Summary: failures=${failures} warnings=${warnings} missing_optional=${missing_optional_count} disabled_optional=${disabled_optional_count} unexpected_timers=${unexpected_timers_count}"

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
  echo -n '"settings":{'
  echo -n '"require_all_present":'${REQUIRE_ALL_PRESENT}','
  echo -n '"require_all_enabled":'${REQUIRE_ALL_ENABLED}','
  echo -n '"allow_missing_worker_override":'${ALLOW_MISSING_WORKER_OVERRIDE}
  echo -n '},'
  echo -n '"timers":['"${timer_json}"'],'
  echo -n '"unexpected_timers":'$(json_array_of_strings "${unexpected_timers[@]}")','
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
  echo -n '"summary":{'
  echo -n '"missing_required_timers":'$(json_array_of_strings "${missing_required_timers[@]}")','
  echo -n '"missing_optional_timers":'$(json_array_of_strings "${missing_optional_timers[@]}")','
  echo -n '"disabled_required_timers":'$(json_array_of_strings "${disabled_required_timers[@]}")','
  echo -n '"disabled_optional_timers":'$(json_array_of_strings "${disabled_optional_timers[@]}")','
  echo -n '"enabled_timers_missing_sentinel":'$(json_array_of_strings "${enabled_timers_missing_sentinel[@]}")','
  echo -n '"unexpected_timers":'$(json_array_of_strings "${unexpected_timers[@]}")','
  echo -n '"failures":'${failures}','
  echo -n '"warnings":'${warnings}','
  echo -n '"ok":'${ok_json}
  echo -n '},'
  echo -n '"failures":'${failures}','
  echo -n '"warnings":'${warnings}','
  echo -n '"ok":'${ok_json}
  echo '}'
fi

if [[ "${failures}" -gt 0 ]]; then
  if [[ "${QUIET_MODE}" != "true" ]]; then
    echo "FAILURES: ${failures}" >&2
  fi
  exit 1
fi
if [[ "${warnings}" -gt 0 ]]; then
  log "All required checks passed (warnings: ${warnings})."
else
  log "All checks passed."
fi
