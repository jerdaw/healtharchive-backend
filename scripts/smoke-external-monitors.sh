#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive — external monitor smoke checks (Phase 3 helper).

This script is meant to quickly verify the HTTP endpoints you typically monitor
via UptimeRobot (or similar), before you create/update external monitors.

Usage:
  ./scripts/smoke-external-monitors.sh [--api-url URL] [--frontend-url URL] [--replay-url URL]
                                      [--timeout-seconds N] [--skip-replay]

Defaults:
  --api-url        https://api.healtharchive.ca/api/health
  --frontend-url   https://www.healtharchive.ca/archive
  --replay-url     https://replay.healtharchive.ca/
  --timeout-seconds 20

Exit codes:
  0 = all checks passed
  1 = one or more checks failed
  2 = usage error
EOF
}

API_URL="https://api.healtharchive.ca/api/health"
FRONTEND_URL="https://www.healtharchive.ca/archive"
REPLAY_URL="https://replay.healtharchive.ca/"
TIMEOUT_SECONDS="20"
SKIP_REPLAY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-url)
      API_URL="$2"
      shift 2
      ;;
    --frontend-url)
      FRONTEND_URL="$2"
      shift 2
      ;;
    --replay-url)
      REPLAY_URL="$2"
      shift 2
      ;;
    --timeout-seconds)
      TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    --skip-replay)
      SKIP_REPLAY="true"
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

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required but not found in PATH." >&2
  exit 1
fi

check_url() {
  local name="$1"
  local url="$2"
  local tmp_file

  tmp_file="$(mktemp)"
  trap 'rm -f "${tmp_file}"' RETURN

  # We use GET (not HEAD) because many “integration” checks are better modeled
  # as a real request, and some CDNs behave differently for HEAD.
  if curl -fsSL --max-time "${TIMEOUT_SECONDS}" -o /dev/null -w '%{http_code} %{url_effective} %{time_total}\n' "${url}" >"${tmp_file}"; then
    local http_code
    local effective_url
    local time_total
    http_code="$(awk '{print $1}' <"${tmp_file}")"
    effective_url="$(awk '{print $2}' <"${tmp_file}")"
    time_total="$(awk '{print $3}' <"${tmp_file}")"
    printf 'OK   %-8s %s  time=%ss  url=%s\n' "${name}" "${http_code}" "${time_total}" "${effective_url}"
    return 0
  fi

  printf 'FAIL %-8s url=%s\n' "${name}" "${url}" >&2
  return 1
}

echo "HealthArchive external monitor smoke"
echo "-----------------------------------"
echo "API:      ${API_URL}"
echo "Frontend: ${FRONTEND_URL}"
if [[ "${SKIP_REPLAY}" == "true" ]]; then
  echo "Replay:   (skipped)"
else
  echo "Replay:   ${REPLAY_URL}"
fi
echo "Timeout:  ${TIMEOUT_SECONDS}s"
echo ""

failed="0"
check_url "api" "${API_URL}" || failed="1"
check_url "frontend" "${FRONTEND_URL}" || failed="1"
if [[ "${SKIP_REPLAY}" != "true" ]]; then
  check_url "replay" "${REPLAY_URL}" || failed="1"
fi

if [[ "${failed}" != "0" ]]; then
  echo "" >&2
  echo "One or more checks failed." >&2
  exit 1
fi

echo ""
echo "All checks passed."
