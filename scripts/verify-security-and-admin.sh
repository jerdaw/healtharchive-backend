#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive — security and admin endpoint verification helper.

Checks:
  - /api/health returns 200
  - backend security headers are present (best-effort; proxies may override)
  - /metrics and /api/admin/* require an admin token
  - Strict-Transport-Security is present when --require-hsts is set

Usage:
  ./scripts/verify-security-and-admin.sh --api-base URL [--admin-token TOKEN]
                                        [--require-hsts] [--timeout-seconds N]

Examples:
  # Against production (recommended: pass token via env, not argv):
  export HEALTHARCHIVE_ADMIN_TOKEN='...'
  ./scripts/verify-security-and-admin.sh --api-base https://api.healtharchive.ca --require-hsts

  # Against local dev (HSTS not expected; token may be unset in dev):
  ./scripts/verify-security-and-admin.sh --api-base http://127.0.0.1:8001

Exit codes:
  0 = all checks passed
  1 = one or more checks failed
  2 = usage error
EOF
}

API_BASE=""
ADMIN_TOKEN="${HEALTHARCHIVE_ADMIN_TOKEN:-}"
REQUIRE_HSTS="false"
TIMEOUT_SECONDS="20"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-base)
      API_BASE="$2"
      shift 2
      ;;
    --admin-token)
      ADMIN_TOKEN="$2"
      shift 2
      ;;
    --require-hsts)
      REQUIRE_HSTS="true"
      shift 1
      ;;
    --timeout-seconds)
      TIMEOUT_SECONDS="$2"
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

if [[ -z "${API_BASE}" ]]; then
  echo "ERROR: --api-base is required" >&2
  usage >&2
  exit 2
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required but not found in PATH." >&2
  exit 1
fi

API_BASE="${API_BASE%/}"

fail_count="0"

expect_status() {
  local url="$1"
  local expected="$2"
  local extra_args=("${@:3}")

  local code
  code="$(curl -sS -o /dev/null --max-time "${TIMEOUT_SECONDS}" -w '%{http_code}' "${extra_args[@]}" "${url}" || true)"
  if [[ "${code}" != "${expected}" ]]; then
    echo "FAIL status expected=${expected} got=${code} url=${url}" >&2
    fail_count=$((fail_count + 1))
    return 1
  fi
  echo "OK   status=${code} url=${url}"
  return 0
}

get_headers() {
  local url="$1"
  curl -sS -D - -o /dev/null --max-time "${TIMEOUT_SECONDS}" "${url}"
}

require_header_contains() {
  local name="$1"
  local url="$2"
  local header_name="$3"
  local needle="$4"

  local headers
  headers="$(get_headers "${url}" || true)"
  if ! echo "${headers}" | tr -d '\r' | grep -iEq "^${header_name}:.*${needle}"; then
    echo "FAIL ${name} missing/invalid header: ${header_name} contains ${needle} url=${url}" >&2
    fail_count=$((fail_count + 1))
    return 1
  fi
  echo "OK   ${name} header ${header_name} contains ${needle}"
}

require_header_present() {
  local name="$1"
  local url="$2"
  local header_name="$3"

  local headers
  headers="$(get_headers "${url}" || true)"
  if ! echo "${headers}" | tr -d '\r' | grep -iEq "^${header_name}:"; then
    echo "FAIL ${name} missing header: ${header_name} url=${url}" >&2
    fail_count=$((fail_count + 1))
    return 1
  fi
  echo "OK   ${name} header present: ${header_name}"
}

echo "HealthArchive security + admin verification"
echo "-------------------------------------------"
echo "API base:  ${API_BASE}"
echo "Timeout:   ${TIMEOUT_SECONDS}s"
echo "HSTS req:  ${REQUIRE_HSTS}"
echo ""

health_url="${API_BASE}/api/health"
metrics_url="${API_BASE}/metrics"
admin_jobs_url="${API_BASE}/api/admin/jobs"

expect_status "${health_url}" "200"

# Security headers are injected by the backend app, but reverse proxies may
# override or strip them. Treat absence as a warning-level failure for now,
# because it still indicates "something to investigate".
require_header_present "health" "${health_url}" "X-Content-Type-Options"
require_header_present "health" "${health_url}" "Referrer-Policy"
require_header_present "health" "${health_url}" "Permissions-Policy"
require_header_contains "health" "${health_url}" "X-Frame-Options" "SAMEORIGIN"

if [[ "${REQUIRE_HSTS}" == "true" ]]; then
  require_header_present "health" "${health_url}" "Strict-Transport-Security"
fi

echo ""
echo "Auth checks"
echo "-----------"

# Without a token, we expect:
# - 403 when a token is configured
# - 500 only when HEALTHARCHIVE_ENV is production/staging AND the token is missing (misconfig)
code_metrics_no_token="$(curl -sS -o /dev/null --max-time "${TIMEOUT_SECONDS}" -w '%{http_code}' "${metrics_url}" || true)"
if [[ "${code_metrics_no_token}" == "403" ]]; then
  echo "OK   /metrics without token is 403 (as expected)"
elif [[ "${code_metrics_no_token}" == "500" ]]; then
  echo "FAIL /metrics returned 500 without token: admin token may be missing in production/staging" >&2
  fail_count=$((fail_count + 1))
else
  echo "FAIL /metrics without token expected 403 got ${code_metrics_no_token}" >&2
  fail_count=$((fail_count + 1))
fi

code_admin_no_token="$(curl -sS -o /dev/null --max-time "${TIMEOUT_SECONDS}" -w '%{http_code}' "${admin_jobs_url}" || true)"
if [[ "${code_admin_no_token}" == "403" ]]; then
  echo "OK   /api/admin/* without token is 403 (as expected)"
elif [[ "${code_admin_no_token}" == "500" ]]; then
  echo "FAIL /api/admin/* returned 500 without token: admin token may be missing in production/staging" >&2
  fail_count=$((fail_count + 1))
else
  echo "FAIL /api/admin/* without token expected 403 got ${code_admin_no_token}" >&2
  fail_count=$((fail_count + 1))
fi

if [[ -n "${ADMIN_TOKEN}" ]]; then
  echo ""
  echo "Auth checks (with token)"
  echo "------------------------"
  expect_status "${metrics_url}" "200" -H "Authorization: Bearer ${ADMIN_TOKEN}"
  expect_status "${admin_jobs_url}" "200" -H "Authorization: Bearer ${ADMIN_TOKEN}"
else
  echo ""
  echo "NOTE: No admin token provided; skipping authenticated checks."
  echo "      Provide via --admin-token or HEALTHARCHIVE_ADMIN_TOKEN env var."
fi

echo ""
if [[ "${fail_count}" -gt 0 ]]; then
  echo "One or more checks failed (count=${fail_count})." >&2
  exit 1
fi

echo "All checks passed."
